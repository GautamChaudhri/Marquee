"""B4 policy application, restore safety, and sealed batches (JMC5B §6.4/B12/B18).

Policy children execute a frozen plan and never re-read live policy.  Restore
consumes only a confined checksummed artifact, refuses a destination that changed
since planning, and never consumes its only verified backup.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from pgqueuer import Queries
from sqlalchemy import func, select

from marquee.core.jobs.handlers_policy_restore import (
    execute_subtitle_policy,
    execute_subtitle_restore,
)
from marquee.core.jobs.media_backups import create_media_backup
from marquee.core.jobs.media_mutation_support import confined_boundary
from marquee.core.jobs.publication import file_signature
from marquee.core.jobs.submission import Initiator
from marquee.core.jobs.subtitle_parents import (
    FrozenFilePlan,
    create_subtitle_policy_batch,
    policy_child_key,
)
from marquee.core.jobs.track_selectors import selector_for
from marquee.database import _get_engine
from marquee.models import Job
from tests.support.jmc5b_harness import execution_context as _context
from tests.support.jmc5b_harness import inventory_of as _inventory
from tests.support.jmc5b_harness import media_file_row as _media_file
from tests.support.jmc5b_harness import real_signature
from tests.support.media_fixtures import (
    DEFAULT_AUDIO,
    DEFAULT_SUBTITLES,
    build_mkv,
    require_media_tools,
)

pytestmark = pytest.mark.usefixtures("jmc5b_media_roots")


async def _fixture(db, tmp_path: Path):
    require_media_tools()
    path = build_mkv(
        tmp_path / "library", "show.mkv", audio=DEFAULT_AUDIO, subtitles=DEFAULT_SUBTITLES
    )
    return path, await _media_file(db, path)


# --- policy ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_policy_executes_its_frozen_plan(db, tmp_path: Path, data_dir: Path) -> None:
    path, row = await _fixture(db, tmp_path)
    before = _inventory(path)
    target = [e for e in before.entries if e.facts.kind == "subtitle" and e.facts.is_forced][0]

    result = await execute_subtitle_policy(
        await _context(
            db, tmp_path, job_type="subtitle_policy",
            request={
                "media_file_id": row.id,
                "policy_id": 7,
                "policy_revision": 3,
                "remove_selectors": [selector_for(target, before).model_dump(mode="json")],
            },
        )
    )
    assert result["outcome"] == "succeeded"
    actual = result["actual_inventory"]
    subtitles = [e for e in actual["entries"] if e["facts"]["kind"] == "subtitle"]
    assert len(subtitles) == 1
    assert not [e for e in subtitles if e["facts"]["is_forced"]]


@pytest.mark.asyncio
async def test_policy_that_selected_nothing_is_a_reasoned_no_change(
    db, tmp_path: Path, data_dir: Path
) -> None:
    path, row = await _fixture(db, tmp_path)
    original = path.read_bytes()

    result = await execute_subtitle_policy(
        await _context(
            db, tmp_path, job_type="subtitle_policy",
            request={
                "media_file_id": row.id,
                "policy_id": 7,
                "policy_revision": 3,
                "remove_selectors": [],
            },
        )
    )
    assert result["outcome"] == "no_change"
    assert result["reason_code"] == "policy_selected_nothing"
    assert result["atomicity"]["published"] is False
    assert path.read_bytes() == original


# --- sealed batch (B18) ------------------------------------------------------


@pytest.fixture
async def installed_pgqueuer(db):
    async with _get_engine().connect() as connection:
        raw = await connection.get_raw_connection()
        queries = Queries.from_asyncpg_connection(raw.driver_connection)
        await queries.install()
        await db.begin()
        try:
            yield queries
        finally:
            await db.rollback()
            await queries.uninstall()


@pytest.mark.asyncio
async def test_batch_freezes_the_evaluated_plan_into_each_child(
    db, tmp_path: Path, installed_pgqueuer
) -> None:
    """B18: a policy edited after sealing cannot be adopted by the batch."""
    require_media_tools()
    path = build_mkv(tmp_path / "lib", "a.mkv", audio=DEFAULT_AUDIO, subtitles=DEFAULT_SUBTITLES)
    row_a = await _media_file(db, path)
    row_b = await _media_file(db, path)
    inventory = _inventory(path)
    forced = [e for e in inventory.entries if e.facts.kind == "subtitle" and e.facts.is_forced][0]
    selector = selector_for(forced, inventory)

    result = await create_subtitle_policy_batch(
        db,
        policy_id=7,
        policy_revision=3,
        plans=[
            FrozenFilePlan(media_file_id=row_a.id, remove_selectors=(selector,)),
            FrozenFilePlan(media_file_id=row_b.id, remove_selectors=()),
        ],
        idempotency_key="subtitle_policy_batch:seal-1",
        initiator=Initiator(kind="user", identifier="operator"),
    )

    parent = await db.get(Job, result.parent.job_id)
    assert parent.type == "subtitle_policy_batch"
    # A parent-only aggregate carries no transport ticket.
    assert parent.pgq_job_id is None
    assert parent.request["selection_count"] == 2
    assert parent.request["policy_revision"] == 3

    children = (
        await db.scalars(select(Job).where(Job.parent_id == parent.id).order_by(Job.id))
    ).all()
    assert len(children) == 2
    assert all(child.type == "subtitle_policy" for child in children)
    # Each child carries the frozen decision and the evaluated revision.
    plans = {child.request["media_file_id"]: child.request for child in children}
    assert len(plans[row_a.id]["remove_selectors"]) == 1
    assert plans[row_a.id]["remove_selectors"][0]["track_key"] == selector.track_key
    assert plans[row_b.id]["remove_selectors"] == []
    assert all(child.request["policy_revision"] == 3 for child in children)


@pytest.mark.asyncio
async def test_batch_with_no_files_seals_zero_children(db, tmp_path: Path, installed_pgqueuer) -> None:
    result = await create_subtitle_policy_batch(
        db,
        policy_id=9,
        policy_revision=1,
        plans=[],
        idempotency_key="subtitle_policy_batch:empty",
        initiator=Initiator(kind="user", identifier="operator"),
    )
    parent = await db.get(Job, result.parent.job_id)
    assert parent.request["selection_count"] == 0
    count = await db.scalar(
        select(func.count()).select_from(Job).where(Job.parent_id == parent.id)
    )
    assert count == 0


@pytest.mark.asyncio
async def test_batch_rejects_duplicate_files_and_unbounded_scope(db, tmp_path: Path) -> None:
    plan = FrozenFilePlan(media_file_id=1, remove_selectors=())
    with pytest.raises(ValueError, match="only appear once"):
        await create_subtitle_policy_batch(
            db, policy_id=1, policy_revision=1, plans=[plan, plan],
            idempotency_key="k", initiator=Initiator(kind="user", identifier="o"),
        )
    too_many = [FrozenFilePlan(media_file_id=i, remove_selectors=()) for i in range(1, 502)]
    with pytest.raises(ValueError, match="bounded child cap"):
        await create_subtitle_policy_batch(
            db, policy_id=1, policy_revision=1, plans=too_many,
            idempotency_key="k", initiator=Initiator(kind="user", identifier="o"),
        )


def test_policy_child_keys_are_deterministic_and_path_free() -> None:
    first = policy_child_key("batch:one", 42)
    assert first == policy_child_key("batch:one", 42)
    assert first != policy_child_key("batch:two", 42)
    assert "/" not in first


# --- restore (B12) -----------------------------------------------------------


async def _backed_up(db, tmp_path: Path):
    """A file with a canonical backup of its original bytes."""
    path, row = await _fixture(db, tmp_path)
    boundary, source = confined_boundary(path)
    signature = file_signature(boundary, source)
    backup = create_media_backup(
        boundary, source=source, subject_key=f"media-file-{row.id}",
        signature=signature, suffix="mkv",
    )
    return path, row, backup


@pytest.mark.asyncio
async def test_restore_refuses_a_destination_that_changed_since_planning(
    db, tmp_path: Path, data_dir: Path
) -> None:
    path, row, backup = await _backed_up(db, tmp_path)
    current = path.read_bytes()

    result = await execute_subtitle_restore(
        await _context(
            db, tmp_path, job_type="subtitle_restore",
            request={
                "media_file_id": row.id,
                "artifact_key": backup.artifact_key,
                "checksum": backup.checksum,
                "source_signature": backup.source_signature,
                "expected_destination_signature": "sha256:stale-plan",
            },
        )
    )
    assert result["outcome"] == "failed"
    assert result["target_outcomes"][0]["reason_code"] == "destination_changed"
    assert result["atomicity"]["published"] is False
    assert path.read_bytes() == current


@pytest.mark.asyncio
async def test_restore_refuses_a_tampered_backup_artifact(
    db, tmp_path: Path, data_dir: Path
) -> None:
    path, row, backup = await _backed_up(db, tmp_path)
    stored = data_dir / backup.artifact_key
    stored.write_bytes(b"tampered backup")
    current = path.read_bytes()

    result = await execute_subtitle_restore(
        await _context(
            db, tmp_path, job_type="subtitle_restore",
            request={
                "media_file_id": row.id,
                "artifact_key": backup.artifact_key,
                "checksum": backup.checksum,
                "source_signature": backup.source_signature,
                "expected_destination_signature": real_signature(path),
            },
        )
    )
    assert result["outcome"] == "failed"
    assert result["target_outcomes"][0]["stage"] == "validate"
    assert path.read_bytes() == current


@pytest.mark.asyncio
async def test_restore_of_identical_bytes_is_a_reasoned_no_change(
    db, tmp_path: Path, data_dir: Path
) -> None:
    path, row, backup = await _backed_up(db, tmp_path)
    result = await execute_subtitle_restore(
        await _context(
            db, tmp_path, job_type="subtitle_restore",
            request={
                "media_file_id": row.id,
                "artifact_key": backup.artifact_key,
                "checksum": backup.checksum,
                "source_signature": backup.source_signature,
                "expected_destination_signature": real_signature(path),
            },
        )
    )
    assert result["outcome"] == "no_change"
    assert result["reason_code"] == "already_restored"


@pytest.mark.asyncio
async def test_restore_republishes_original_bytes_and_keeps_its_backup(
    db, tmp_path: Path, data_dir: Path
) -> None:
    path, row, backup = await _backed_up(db, tmp_path)
    original = path.read_bytes()

    # Mutate the file so the restore has real work to do.
    inventory = _inventory(path)
    target = [e for e in inventory.entries if e.facts.kind == "audio" and e.facts.channels == 2][0]
    from marquee.core.jobs.handlers_track_mutations import execute_audio_remove

    removed = await execute_audio_remove(
        await _context(
            db, tmp_path, job_type="audio_remove",
            request={
                "media_file_id": row.id,
                "selectors": [selector_for(target, inventory).model_dump(mode="json")],
            },
        )
    )
    assert removed["outcome"] == "succeeded"
    assert path.read_bytes() != original

    result = await execute_subtitle_restore(
        await _context(
            db, tmp_path, job_type="subtitle_restore",
            request={
                "media_file_id": row.id,
                "artifact_key": backup.artifact_key,
                "checksum": backup.checksum,
                "source_signature": backup.source_signature,
                "expected_destination_signature": real_signature(path),
            },
        )
    )
    assert result["outcome"] == "succeeded"
    assert path.read_bytes() == original  # the original bytes are back
    # The restore must never consume its only verified backup.
    assert (data_dir / backup.artifact_key).exists()
    # The rescan proves both audio tracks returned.
    actual = result["actual_inventory"]
    assert len([e for e in actual["entries"] if e["facts"]["kind"] == "audio"]) == 2
