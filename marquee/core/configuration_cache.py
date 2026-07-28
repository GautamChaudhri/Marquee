"""Cross-process last-valid cache for versioned application configuration."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

import asyncpg
from sqlalchemy.ext.asyncio import AsyncSession

from marquee.core.configuration import (
    CONFIGURATION_CATALOG,
    CONFIGURATION_CHANNEL,
    ConfigurationError,
    ConfigurationState,
    ConfigurationUnavailableError,
    effective_owner_values,
    read_current_configuration,
)
from marquee.database import _get_session_factory
from marquee.db_migration import asyncpg_dsn

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class ExecutionConfigurationSnapshot:
    version: int
    values: dict[str, Any]


class ConfigurationProvider:
    """One provider implementation shared by API, worker, and scheduler roles."""

    def __init__(self, *, repair_interval_seconds: float = 30.0) -> None:
        if not 0 < repair_interval_seconds <= 30:
            raise ValueError("configuration repair interval must be in (0, 30] seconds")
        self.repair_interval_seconds = repair_interval_seconds
        self._state: ConfigurationState | None = None
        self._status = "uninitialized"
        self._last_error_code: str | None = None
        self._last_checked_monotonic: float | None = None
        self._listener: asyncpg.Connection | None = None
        self._listener_connected = False
        self._repair_task: asyncio.Task[None] | None = None
        self._notification_tasks: set[asyncio.Task[None]] = set()
        self._lock = asyncio.Lock()
        self._ignored_notifications = 0

    @property
    def initialized(self) -> bool:
        return self._state is not None

    @property
    def state(self) -> ConfigurationState:
        if self._state is None:
            raise ConfigurationUnavailableError("configuration provider has no valid revision")
        return self._state

    def effective(self, owner: str) -> dict[str, Any]:
        if owner not in {"app", "pipeline"}:
            raise ConfigurationError(f"unknown configuration owner: {owner}")
        return effective_owner_values(self.state, owner)  # type: ignore[arg-type]

    def health(self) -> dict[str, Any]:
        return {
            "status": self._status,
            "version": self._state.version if self._state else None,
            "etag": self._state.etag if self._state else None,
            "listener_connected": self._listener_connected,
            "repair_interval_seconds": self.repair_interval_seconds,
            "last_error_code": self._last_error_code,
            "ignored_notifications": self._ignored_notifications,
        }

    async def refresh_from_session(
        self,
        session: AsyncSession,
        *,
        initial: bool = False,
    ) -> ConfigurationState:
        """Load source-of-truth state, retaining the previous valid revision on failure."""
        async with self._lock:
            self._last_checked_monotonic = time.monotonic()
            try:
                loaded = await read_current_configuration(session)
            except ConfigurationError as exc:
                self._last_error_code = type(exc).__name__
                self._status = "invalid" if self._state else "unavailable"
                if initial or self._state is None:
                    raise ConfigurationUnavailableError(
                        "no valid initial configuration revision"
                    ) from exc
                logger.error(
                    "Configuration reload rejected; retaining version %s", self._state.version
                )
                return self._state
            self._state = loaded
            self._status = "valid"
            self._last_error_code = None
            return loaded

    async def repair(self, *, initial: bool = False) -> ConfigurationState:
        factory = _get_session_factory()
        try:
            async with factory() as session:
                return await self.refresh_from_session(session, initial=initial)
        except ConfigurationError:
            raise
        except Exception as exc:
            self._last_error_code = type(exc).__name__
            self._status = "stale" if self._state else "unavailable"
            if initial or self._state is None:
                raise ConfigurationUnavailableError(
                    "database unavailable during initial configuration load"
                ) from exc
            logger.warning("Configuration repair failed; retaining version %s", self._state.version)
            return self._state

    def _on_notification(
        self,
        _connection: asyncpg.Connection,
        _pid: int,
        _channel: str,
        payload: str,
    ) -> None:
        task = asyncio.create_task(self.handle_notification(payload))
        self._notification_tasks.add(task)
        task.add_done_callback(self._notification_tasks.discard)

    async def handle_notification(self, payload: str) -> bool:
        """Reload only for a strictly newer decimal version hint."""
        try:
            hinted_version = int(payload)
        except (TypeError, ValueError):
            self._ignored_notifications += 1
            return False
        if hinted_version < 1 or (self._state and hinted_version <= self._state.version):
            self._ignored_notifications += 1
            return False
        before = self._state.version if self._state else 0
        loaded = await self.repair(initial=self._state is None)
        return loaded.version > before

    async def _repair_loop(self) -> None:
        while True:
            await asyncio.sleep(self.repair_interval_seconds)
            with contextlib.suppress(ConfigurationError):
                await self.repair(initial=False)

    async def start(self, *, role: str) -> None:
        if self._repair_task is not None:
            return
        await self.repair(initial=True)
        try:
            listener = await asyncpg.connect(
                asyncpg_dsn(),
                server_settings={"application_name": f"marquee:{role}:configuration"},
            )
            await listener.add_listener(CONFIGURATION_CHANNEL, self._on_notification)
        except (OSError, asyncpg.PostgresError):
            logger.warning(
                "Configuration LISTEN unavailable; periodic repair remains active",
                exc_info=True,
            )
        else:
            self._listener = listener
            self._listener_connected = True
        self._repair_task = asyncio.create_task(
            self._repair_loop(), name=f"configuration-repair:{role}"
        )

    async def stop(self) -> None:
        task = self._repair_task
        self._repair_task = None
        if task is not None:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        pending = tuple(self._notification_tasks)
        for notification_task in pending:
            notification_task.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
        self._notification_tasks.clear()
        listener = self._listener
        self._listener = None
        self._listener_connected = False
        if listener is not None:
            with contextlib.suppress(OSError, asyncpg.PostgresError):
                await listener.remove_listener(CONFIGURATION_CHANNEL, self._on_notification)
                await listener.close()

    def snapshot_for(self, keys: Iterable[str]) -> ExecutionConfigurationSnapshot:
        requested = tuple(sorted(set(keys)))
        values = self.effective("pipeline")
        snapshot: dict[str, Any] = {}
        for key in requested:
            entry = CONFIGURATION_CATALOG.get(key)
            if entry is None:
                raise ConfigurationError(f"unknown configuration snapshot key: {key}")
            if (
                entry.sensitivity != "public"
                or not entry.database_owned
                or entry.scope != "execution"
            ):
                raise ConfigurationError(f"configuration key cannot enter a job snapshot: {key}")
            snapshot[key] = values[key]
        return ExecutionConfigurationSnapshot(version=self.state.version, values=snapshot)


configuration_provider = ConfigurationProvider()
