"""Ordered PostgreSQL session advisory gates for physical job attempts."""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import time
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from enum import IntEnum, StrEnum
from typing import Any

import asyncpg

from marquee.db_migration import asyncpg_dsn


class SafetyGateError(RuntimeError):
    """Base error for invalid, cancelled, or lost safety-gate admission."""


class InvalidSafetyRequirementsError(SafetyGateError, ValueError):
    pass


class SafetyGateCancelledError(SafetyGateError):
    pass


class SafetyGateTimeoutError(SafetyGateError, TimeoutError):
    pass


class SafetyGateConnectionLostError(SafetyGateError):
    pass


class GateKind(IntEnum):
    MEDIA_FILE = 10
    MEDIA_WRITE = 20
    GPU = 30
    MAINTENANCE = 40


class GateMode(StrEnum):
    SHARED = "shared"
    EXCLUSIVE = "exclusive"


@dataclass(frozen=True, slots=True)
class SafetyPolicy:
    media_file: bool = False
    media_write: bool = False
    gpu: bool = False
    exclusive_maintenance: bool = False


@dataclass(frozen=True, slots=True)
class GateRequirement:
    kind: GateKind
    identity: str
    mode: GateMode = GateMode.EXCLUSIVE

    def __post_init__(self) -> None:
        if not self.identity or len(self.identity) > 300 or "\0" in self.identity:
            raise InvalidSafetyRequirementsError("gate identity is empty or unbounded")
        if self.kind != GateKind.MAINTENANCE and self.mode != GateMode.EXCLUSIVE:
            raise InvalidSafetyRequirementsError("only maintenance supports a shared gate")


@dataclass(frozen=True, slots=True)
class SafetyRequirements:
    gates: tuple[GateRequirement, ...]

    @classmethod
    def ordinary(
        cls,
        *,
        media_file_identity: str | None = None,
        media_write_permit: int | None = None,
        gpu_permit: int | None = None,
    ) -> SafetyRequirements:
        gates: list[GateRequirement] = []
        if media_file_identity is not None:
            gates.append(GateRequirement(GateKind.MEDIA_FILE, media_file_identity))
        if media_write_permit is not None:
            gates.append(_permit(GateKind.MEDIA_WRITE, media_write_permit))
        if gpu_permit is not None:
            gates.append(_permit(GateKind.GPU, gpu_permit))
        gates.append(
            GateRequirement(GateKind.MAINTENANCE, "global", mode=GateMode.SHARED)
        )
        return cls.validate(gates)

    @classmethod
    def exclusive_maintenance(cls) -> SafetyRequirements:
        return cls((GateRequirement(GateKind.MAINTENANCE, "global"),))

    @classmethod
    def validate(cls, gates: Sequence[GateRequirement]) -> SafetyRequirements:
        items = tuple(gates)
        if not items:
            raise InvalidSafetyRequirementsError("ordinary execution requires maintenance sharing")
        if len(set(items)) != len(items):
            raise InvalidSafetyRequirementsError("duplicate safety gate")
        exclusive_maintenance = any(
            item.kind == GateKind.MAINTENANCE and item.mode == GateMode.EXCLUSIVE
            for item in items
        )
        if exclusive_maintenance and (
            len(items) != 1 or items[0].kind != GateKind.MAINTENANCE
        ):
            raise InvalidSafetyRequirementsError(
                "exclusive maintenance cannot mix with lower-order gates"
            )
        if not exclusive_maintenance and not any(
            item.kind == GateKind.MAINTENANCE and item.mode == GateMode.SHARED
            for item in items
        ):
            raise InvalidSafetyRequirementsError("ordinary execution requires shared maintenance")
        ordered = tuple(sorted(items, key=lambda item: (item.kind, item.identity)))
        return cls(ordered)


def _permit(kind: GateKind, number: int) -> GateRequirement:
    if isinstance(number, bool) or number < 0 or number > 1023:
        raise InvalidSafetyRequirementsError(
            "permit number is outside the snapshotted capacity"
        )
    return GateRequirement(kind, str(number))


def advisory_key(requirement: GateRequirement) -> int:
    """Return a domain-separated stable signed PostgreSQL bigint key."""
    domain = f"marquee:jmc3a:v1:{requirement.kind.name.lower()}:{requirement.identity}"
    digest = hashlib.blake2b(domain.encode("utf-8"), digest_size=8).digest()
    return int.from_bytes(digest, byteorder="big", signed=True)


def requirements_for_policy(
    policy: SafetyPolicy,
    *,
    allocation_identity: str,
    media_file_identity: str | None = None,
    media_write_capacity: int = 1,
    gpu_capacity: int = 1,
) -> SafetyRequirements:
    """Resolve definition-owned policy against snapshotted server capacities."""
    if not allocation_identity or len(allocation_identity) > 300:
        raise InvalidSafetyRequirementsError("allocation identity is empty or unbounded")
    if policy.exclusive_maintenance:
        if policy.media_file or policy.media_write or policy.gpu:
            raise InvalidSafetyRequirementsError(
                "exclusive maintenance policy cannot request lower-order gates"
            )
        return SafetyRequirements.exclusive_maintenance()
    if policy.media_file and not media_file_identity:
        raise InvalidSafetyRequirementsError("media-file policy requires canonical identity")
    if not 1 <= media_write_capacity <= 1024 or not 1 <= gpu_capacity <= 1024:
        raise InvalidSafetyRequirementsError("snapshotted permit capacity is invalid")
    seed = hashlib.blake2b(
        f"marquee:jmc3a:allocation:v1:{allocation_identity}".encode(), digest_size=8
    ).digest()
    number = int.from_bytes(seed, "big")
    return SafetyRequirements.ordinary(
        media_file_identity=media_file_identity if policy.media_file else None,
        media_write_permit=number % media_write_capacity if policy.media_write else None,
        gpu_permit=number % gpu_capacity if policy.gpu else None,
    )


CancellationCheck = Callable[[], bool | Awaitable[bool]]
WaitPublisher = Callable[[str], None | Awaitable[None]]
ConnectionFactory = Callable[[], Awaitable[asyncpg.Connection]]


async def _value(value: bool | Awaitable[bool]) -> bool:
    if isinstance(value, bool):
        return value
    return bool(await value)


async def _publish(callback: WaitPublisher | None, reason: str) -> None:
    if callback is None:
        return
    result = callback(reason)
    if result is not None:
        await result


@dataclass(slots=True)
class SafetyGateHandle:
    connection: asyncpg.Connection
    acquired: list[GateRequirement]
    released: bool = False

    async def release(self) -> None:
        if self.released:
            return
        try:
            for requirement in reversed(self.acquired):
                query = (
                    "SELECT pg_advisory_unlock_shared($1)"
                    if requirement.mode == GateMode.SHARED
                    else "SELECT pg_advisory_unlock($1)"
                )
                await self.connection.fetchval(query, advisory_key(requirement))
        except (OSError, asyncpg.PostgresError) as exc:
            raise SafetyGateConnectionLostError("safety-gate connection was lost") from exc
        finally:
            self.released = True
            await self.connection.close()

    async def __aenter__(self) -> SafetyGateHandle:
        return self

    async def __aexit__(self, *_: Any) -> None:
        await self.release()


def _is_transient_connection_error(exc: BaseException) -> bool:
    """Keep admission deferral limited to actual PostgreSQL transport loss."""
    return isinstance(
        exc,
        (
            OSError,
            asyncpg.InterfaceError,
            asyncpg.CannotConnectNowError,
            asyncpg.ConnectionDoesNotExistError,
            asyncpg.ConnectionFailureError,
        ),
    )


class SafetyGateService:
    def __init__(
        self,
        *,
        connection_factory: ConnectionFactory | None = None,
        poll_seconds: float = 0.1,
    ) -> None:
        if not 0.01 <= poll_seconds <= 5:
            raise ValueError("gate polling must remain bounded")
        self._connection_factory = connection_factory or self._connect
        self._poll_seconds = poll_seconds

    @staticmethod
    async def _connect() -> asyncpg.Connection:
        return await asyncpg.connect(
            asyncpg_dsn(),
            server_settings={"application_name": "marquee:worker:safety-gate"},
        )

    async def acquire(
        self,
        requirements: SafetyRequirements,
        *,
        cancelled: CancellationCheck,
        deadline_seconds: float,
        publish_wait: WaitPublisher | None = None,
    ) -> SafetyGateHandle:
        requirements = SafetyRequirements.validate(requirements.gates)
        if deadline_seconds <= 0:
            raise SafetyGateTimeoutError("safety-gate admission deadline elapsed")
        try:
            connection = await self._connection_factory()
        except (OSError, asyncpg.PostgresError) as exc:
            if _is_transient_connection_error(exc):
                raise SafetyGateConnectionLostError(
                    "safety-gate connection was lost before admission"
                ) from exc
            raise
        acquired: list[GateRequirement] = []
        deadline = time.monotonic() + deadline_seconds
        try:
            for requirement in requirements.gates:
                waiting_published = False
                query = (
                    "SELECT pg_try_advisory_lock_shared($1)"
                    if requirement.mode == GateMode.SHARED
                    else "SELECT pg_try_advisory_lock($1)"
                )
                while True:
                    if await _value(cancelled()):
                        raise SafetyGateCancelledError("safety-gate admission cancelled")
                    if time.monotonic() >= deadline:
                        raise SafetyGateTimeoutError("safety-gate admission deadline elapsed")
                    try:
                        locked = bool(
                            await connection.fetchval(query, advisory_key(requirement))
                        )
                    except (OSError, asyncpg.PostgresError) as exc:
                        if _is_transient_connection_error(exc):
                            raise SafetyGateConnectionLostError(
                                "safety-gate connection was lost during admission"
                            ) from exc
                        raise
                    if locked:
                        acquired.append(requirement)
                        break
                    if not waiting_published:
                        await _publish(publish_wait, _friendly_wait_reason(requirement))
                        waiting_published = True
                    await asyncio.sleep(
                        min(self._poll_seconds, max(0, deadline - time.monotonic()))
                    )
            return SafetyGateHandle(connection=connection, acquired=acquired)
        except BaseException:
            handle = SafetyGateHandle(connection=connection, acquired=acquired)
            with contextlib.suppress(SafetyGateConnectionLostError):
                await handle.release()
            raise


def _friendly_wait_reason(requirement: GateRequirement) -> str:
    return {
        GateKind.MEDIA_FILE: "Waiting for this media file",
        GateKind.MEDIA_WRITE: "Waiting for media-write capacity",
        GateKind.GPU: "Waiting for GPU capacity",
        GateKind.MAINTENANCE: "Waiting for maintenance safety",
    }[requirement.kind]
