from __future__ import annotations

import asyncio

import asyncpg
import pytest

from marquee.core.jobs.safety_gates import (
    GateKind,
    GateMode,
    GateRequirement,
    InvalidSafetyRequirementsError,
    SafetyGateCancelledError,
    SafetyGateService,
    SafetyGateTimeoutError,
    SafetyPolicy,
    SafetyRequirements,
    advisory_key,
    requirements_for_policy,
)
from marquee.db_migration import asyncpg_dsn


def test_advisory_keys_are_stable_domain_separated_signed_bigints() -> None:
    media = GateRequirement(GateKind.MEDIA_FILE, "media:42")
    maintenance = GateRequirement(GateKind.MAINTENANCE, "global", mode=GateMode.SHARED)

    assert advisory_key(media) == -5370365677438561025
    assert advisory_key(maintenance) == -7702939639969922338
    assert -(2**63) <= advisory_key(media) < 2**63
    assert advisory_key(media) != advisory_key(GateRequirement(GateKind.GPU, "media:42"))


def test_requirements_sort_globally_and_reject_contradictions() -> None:
    requirements = SafetyRequirements.validate(
        (
            GateRequirement(GateKind.MAINTENANCE, "global", GateMode.SHARED),
            GateRequirement(GateKind.GPU, "0"),
            GateRequirement(GateKind.MEDIA_FILE, "media:7"),
            GateRequirement(GateKind.MEDIA_WRITE, "0"),
        )
    )
    assert [gate.kind for gate in requirements.gates] == [
        GateKind.MEDIA_FILE,
        GateKind.MEDIA_WRITE,
        GateKind.GPU,
        GateKind.MAINTENANCE,
    ]

    with pytest.raises(InvalidSafetyRequirementsError, match="cannot mix"):
        SafetyRequirements.validate(
            (
                GateRequirement(GateKind.MEDIA_FILE, "media:7"),
                GateRequirement(GateKind.MAINTENANCE, "global"),
            )
        )
    with pytest.raises(InvalidSafetyRequirementsError, match="duplicate"):
        SafetyRequirements.validate(
            (
                GateRequirement(GateKind.MEDIA_FILE, "media:7"),
                GateRequirement(GateKind.MEDIA_FILE, "media:7"),
                GateRequirement(GateKind.MAINTENANCE, "global", GateMode.SHARED),
            )
        )


def test_policy_resolves_only_server_owned_identities_and_capacities() -> None:
    policy = SafetyPolicy(media_file=True, media_write=True, gpu=True)
    first = requirements_for_policy(
        policy,
        allocation_identity="job:attempt:fence",
        media_file_identity="media:42",
        media_write_capacity=2,
        gpu_capacity=3,
    )
    second = requirements_for_policy(
        policy,
        allocation_identity="job:attempt:fence",
        media_file_identity="media:42",
        media_write_capacity=2,
        gpu_capacity=3,
    )
    assert first == second
    assert first.gates[0].identity == "media:42"
    assert 0 <= int(first.gates[1].identity) < 2
    assert 0 <= int(first.gates[2].identity) < 3

    with pytest.raises(InvalidSafetyRequirementsError, match="canonical identity"):
        requirements_for_policy(
            SafetyPolicy(media_file=True), allocation_identity="job:attempt:fence"
        )


async def _connection() -> asyncpg.Connection:
    return await asyncpg.connect(asyncpg_dsn())


@pytest.mark.asyncio
async def test_same_media_gate_contends_and_connection_close_releases() -> None:
    service = SafetyGateService(connection_factory=_connection, poll_seconds=0.02)
    requirements = SafetyRequirements.ordinary(media_file_identity="media:contended")
    first = await service.acquire(requirements, cancelled=lambda: False, deadline_seconds=1)
    try:
        with pytest.raises(SafetyGateTimeoutError):
            await service.acquire(requirements, cancelled=lambda: False, deadline_seconds=0.08)
        await first.connection.close()
        replacement = await service.acquire(
            requirements, cancelled=lambda: False, deadline_seconds=1
        )
        await replacement.release()
    finally:
        if not first.connection.is_closed():
            await first.release()


@pytest.mark.asyncio
async def test_shared_maintenance_excludes_exclusive_maintenance() -> None:
    service = SafetyGateService(connection_factory=_connection, poll_seconds=0.02)
    shared_one = await service.acquire(
        SafetyRequirements.ordinary(), cancelled=lambda: False, deadline_seconds=1
    )
    shared_two = await service.acquire(
        SafetyRequirements.ordinary(), cancelled=lambda: False, deadline_seconds=1
    )
    try:
        with pytest.raises(SafetyGateTimeoutError):
            await service.acquire(
                SafetyRequirements.exclusive_maintenance(),
                cancelled=lambda: False,
                deadline_seconds=0.08,
            )
    finally:
        await shared_two.release()
        await shared_one.release()

    exclusive = await service.acquire(
        SafetyRequirements.exclusive_maintenance(),
        cancelled=lambda: False,
        deadline_seconds=1,
    )
    await exclusive.release()


@pytest.mark.asyncio
async def test_partial_acquisition_is_released_when_cancelled() -> None:
    service = SafetyGateService(connection_factory=_connection, poll_seconds=0.02)
    blocker = await service.acquire(
        SafetyRequirements.ordinary(media_file_identity="media:second"),
        cancelled=lambda: False,
        deadline_seconds=1,
    )
    cancelled = asyncio.Event()
    wait_reasons: list[str] = []
    requirements = SafetyRequirements.validate(
        (
            GateRequirement(GateKind.MEDIA_FILE, "media:first"),
            GateRequirement(GateKind.MEDIA_FILE, "media:second"),
            GateRequirement(GateKind.MAINTENANCE, "global", GateMode.SHARED),
        )
    )

    async def cancel_soon() -> None:
        await asyncio.sleep(0.05)
        cancelled.set()

    task = asyncio.create_task(cancel_soon())
    try:
        with pytest.raises(SafetyGateCancelledError):
            await service.acquire(
                requirements,
                cancelled=cancelled.is_set,
                deadline_seconds=1,
                publish_wait=wait_reasons.append,
            )
    finally:
        await task
        await blocker.release()

    assert wait_reasons == ["Waiting for this media file"]
    first_only = await service.acquire(
        SafetyRequirements.ordinary(media_file_identity="media:first"),
        cancelled=lambda: False,
        deadline_seconds=1,
    )
    await first_only.release()


@pytest.mark.asyncio
async def test_uncontended_gates_are_attempted_even_after_the_deadline_elapsed() -> None:
    """The deadline bounds waiting for a contended gate, not the first attempt.

    A cancellation check is one database round trip; on a slow host it can outlast a small
    admission budget. Admission must still take a free gate instead of deferring a job that
    nothing was holding.
    """
    service = SafetyGateService(connection_factory=_connection, poll_seconds=0.02)

    async def slow_cancellation_check() -> bool:
        await asyncio.sleep(0.2)
        return False

    handle = await service.acquire(
        SafetyRequirements.ordinary(media_file_identity="media:uncontended"),
        cancelled=slow_cancellation_check,
        deadline_seconds=0.05,
    )
    try:
        assert len(handle.acquired) == 2
    finally:
        await handle.release()


@pytest.mark.asyncio
async def test_numbered_permits_are_independent() -> None:
    service = SafetyGateService(connection_factory=_connection, poll_seconds=0.02)
    first = await service.acquire(
        SafetyRequirements.ordinary(media_write_permit=0),
        cancelled=lambda: False,
        deadline_seconds=1,
    )
    second = await service.acquire(
        SafetyRequirements.ordinary(media_write_permit=1),
        cancelled=lambda: False,
        deadline_seconds=1,
    )
    await second.release()
    await first.release()
