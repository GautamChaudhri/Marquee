"""Bootstrap one owned real-app browser fixture, then replace this process with Uvicorn.

The fixture resets only the JMC7C disposable database selected by ``DB_URL`` and
submits a canonical ``system_noop`` job.  The FastAPI lifespan then starts the
embedded PgQueuer worker, so Playwright observes the real API, worker, durable
job/event records, SSE tailer, and built Svelte proxy without route interception.
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import sys
from datetime import UTC, datetime
from hashlib import sha256
from io import BytesIO
from pathlib import Path

from PIL import Image
from sqlalchemy import select, text

from marquee.config import settings
from marquee.core.configuration_cache import configuration_provider
from marquee.core.jobs.commands import create_system_noop
from marquee.core.onboarding_review import bind_onboarding_decision, load_onboarding_review
from marquee.core.taste_preferences import (
    append_preference_event,
    create_pending_exemplar,
    record_deployment_effect,
)
from marquee.database import _get_session_factory, close_db, reset_database
from marquee.db_migration import migrate_database
from marquee.models import Job, JobArtifact, Movie, Season, Series, TasteExemplar


async def _materialize_seeded_evidence(data_dir: Path, session) -> None:
    """Give the reusable 49-subject fixture real, checksum-matched JPEGs.

    The coordinator later reads every retained exemplar from the artifact boundary.
    A database-only fixture would let the onboarding page pass while making the
    real PgQueuer profile workers fail after the threshold is crossed.
    """
    artifacts = list(
        (
            await session.scalars(
                select(JobArtifact)
                .where(JobArtifact.storage_key.like("jmc6k/profile/%.jpg"))
                .order_by(JobArtifact.storage_key)
            )
        ).all()
    )
    artifact_ids: list[int] = []
    checksums: dict[int, str] = {}
    for index, artifact in enumerate(artifacts):
        image = Image.new("RGB", (8, 12), color=(index, 40, 60))
        buffer = BytesIO()
        image.save(buffer, format="JPEG")
        # JPEG decoders accept trailing bytes; this makes the retained payload
        # deterministically distinct even when compression quantizes nearby
        # fixture colours to identical encoded pixels.
        payload = buffer.getvalue() + f"\nJMC7C evidence {index}\n".encode()
        path = data_dir / artifact.storage_key
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(payload)
        artifact.size_bytes = len(payload)
        artifact.checksum = sha256(payload).hexdigest()
        artifact_ids.append(artifact.id)
        checksums[artifact.id] = artifact.checksum
    seed_job = await session.get(Job, artifacts[0].job_id)
    assert seed_job is not None
    seed_job.subject_snapshot = {
        "version": 1,
        "kind": "movie",
        "display_id": "movie:jmc7c-evidence",
        "display_name": "JMC7C evidence fixture",
        "movie_id": 990_000,
        "title": "JMC7C evidence fixture",
        "media_kind": "movie",
    }
    exemplars = list(
        (
            await session.scalars(
                select(TasteExemplar).where(TasteExemplar.retained_artifact_id.in_(artifact_ids))
            )
        ).all()
    )
    for exemplar in exemplars:
        exemplar.checksum = checksums[exemplar.retained_artifact_id]
    await session.flush()


async def _seed_review_archive_edges(session, review_runs) -> None:
    """Add immutable mixed and corrupt archive cases for the real review route."""
    mixed, corrupt = review_runs
    mixed_archive = await session.get(JobArtifact, mixed.archive_artifact_id)
    corrupt_archive = await session.get(JobArtifact, corrupt.archive_artifact_id)
    assert mixed_archive is not None
    assert corrupt_archive is not None

    mixed_path = Path(settings.DATA_DIR) / str(mixed_archive.storage_key)
    archive = json.loads(mixed_path.read_text(encoding="utf-8"))
    archive["diagnostic_ledger"]["candidates"].append(
        {
            "orig_filename": "rejected.jpg",
            "gate_decision": "gated",
            "rejection_reason": "ocr_residual_text",
            "raw_features": {"knn_sim": 0.2},
            "width": 1000,
            "height": 1500,
            "language": "en",
        }
    )
    archive["review"]["archived_count"] = 2
    payload = json.dumps(archive, separators=(",", ":"), sort_keys=True).encode()
    mixed_path.write_bytes(payload)
    mixed_archive.size_bytes = len(payload)
    mixed_archive.checksum = sha256(payload).hexdigest()

    corrupt_candidate = await session.scalar(
        select(JobArtifact).where(
            JobArtifact.job_id == corrupt.job_id,
            JobArtifact.kind == "evidence_image",
        )
    )
    assert corrupt_candidate is not None
    (Path(settings.DATA_DIR) / str(corrupt_candidate.storage_key)).write_bytes(b"corrupt fixture")
    await session.flush()


async def _replace_review_candidate_bytes(session, run, payload: bytes) -> JobArtifact:
    """Keep a candidate artifact and its signed immutable review archive in sync."""
    candidate = await session.scalar(
        select(JobArtifact).where(
            JobArtifact.job_id == run.job_id,
            JobArtifact.kind == "evidence_image",
        )
    )
    archive_artifact = await session.get(JobArtifact, run.archive_artifact_id)
    assert candidate is not None
    assert archive_artifact is not None

    candidate_path = Path(settings.DATA_DIR) / str(candidate.storage_key)
    candidate_path.write_bytes(payload)
    candidate.size_bytes = len(payload)
    candidate.checksum = sha256(payload).hexdigest()

    archive_path = Path(settings.DATA_DIR) / str(archive_artifact.storage_key)
    archive = json.loads(archive_path.read_text(encoding="utf-8"))
    archive["review"]["survivors"][0]["artifact_checksum"] = candidate.checksum
    archive["diagnostic_ledger"]["candidates"][0]["artifact_checksum"] = candidate.checksum
    review = archive["review"]
    review["checksum"] = sha256(
        json.dumps(
            {
                "version": review["version"],
                "order_algorithm": review["order_algorithm"],
                "survivors": review["survivors"],
            },
            separators=(",", ":"),
            sort_keys=True,
        ).encode()
    ).hexdigest()
    archive_payload = json.dumps(archive, separators=(",", ":"), sort_keys=True).encode()
    archive_path.write_bytes(archive_payload)
    archive_artifact.size_bytes = len(archive_payload)
    archive_artifact.checksum = sha256(archive_payload).hexdigest()
    await session.flush()
    return candidate


async def _materialize_review_candidate(session, run, index: int) -> None:
    """Replace the fixture's placeholder candidate with distinct valid image bytes."""
    image = Image.new("RGB", (8, 12), color=(index * 17, 80, 120))
    buffer = BytesIO()
    image.save(buffer, format="JPEG")
    await _replace_review_candidate_bytes(
        session,
        run,
        buffer.getvalue() + f"\nJMC7C review candidate {index}\n".encode(),
    )


async def _make_cancel_fixture_copy_multichunk(session, run) -> None:
    """Keep the cancellation case before the durable publication-intent boundary."""
    candidate = await session.scalar(
        select(JobArtifact).where(
            JobArtifact.job_id == run.job_id,
            JobArtifact.kind == "evidence_image",
        )
    )
    assert candidate is not None

    candidate_path = Path(settings.DATA_DIR) / str(candidate.storage_key)
    payload = candidate_path.read_bytes() + (b"\nJMC7C cancellation copy fixture\n" * (12 * 1024 * 1024 // 32))
    await _replace_review_candidate_bytes(session, run, payload)


async def _seed_identical_deployment_target(session, run) -> None:
    """Preinstall verified candidate bytes at the canonical poster destination."""
    candidate = await session.scalar(
        select(JobArtifact).where(
            JobArtifact.job_id == run.job_id,
            JobArtifact.kind == "evidence_image",
        )
    )
    assert candidate is not None
    source = Path(settings.DATA_DIR) / str(candidate.storage_key)
    payload = source.read_bytes() + b"\nJMC7C no-change evidence fixture\n"
    await _replace_review_candidate_bytes(session, run, payload)

    destination = Path(settings.DATA_DIR) / "jmc7c-real-browser" / run.job_id / "poster.jpg"
    destination.write_bytes(payload)
    await session.flush()


async def _seed_post_effect_recovery(session, run) -> None:
    """Persist the durable crash window for the real status-reconciliation route.

    The effect and its checksum proof exist, but the canonical deployment is terminally failed as
    it would be if the process died after the domain effect and before its terminal projection.
    The browser never receives a mocked result: its first real status load must reconcile this
    durable state and activate the valid positive exemplar.
    """
    pipeline = await session.get(Job, run.job_id)
    candidate = await session.scalar(
        select(JobArtifact).where(
            JobArtifact.job_id == run.job_id,
            JobArtifact.kind == "evidence_image",
        )
    )
    assert pipeline is not None
    assert candidate is not None
    candidate_path = Path(settings.DATA_DIR) / str(candidate.storage_key)
    payload = candidate_path.read_bytes() + b"\nJMC7C post-effect recovery fixture\n"
    candidate = await _replace_review_candidate_bytes(session, run, payload)

    from tests.test_jmc6j_taste_authority import _deployment_job

    deployment, _attempt = await _deployment_job(session, pipeline)
    event = await append_preference_event(
        session,
        idempotency_key="jmc7c:post-effect-recovery",
        namespace="global",
        subject_kind="movie",
        subject_reference="movie:jmc7c-post-effect-recovery",
        subject_snapshot={"title": "JMC7C Post-effect Recovery Fixture", "year": 2026},
        action="selection",
        exposed_candidates=[{"candidate_id": "candidate-1", "artifact_id": candidate.id}],
        presentation_order=["candidate-1"],
        training_context={"personalization_mode": "collecting"},
        confidence="explicit",
        initiator={"kind": "user", "identifier": "onboarding-api"},
        candidate_artifact_id=candidate.id,
    )
    exemplar = await create_pending_exemplar(
        session,
        event=event,
        polarity="positive",
        evidence_source="explicit_selection",
        evidence_weight=1.0,
        deployment_job_id=deployment.id,
    )
    _recorded, validation = await record_deployment_effect(
        session,
        deployment_job_id=deployment.id,
        result={
            "outcome": "succeeded",
            "reason_code": "copied",
            "validation": {"verdict": "passed"},
            "target_outcomes": [
                {
                    "expected": {"checksum": candidate.checksum},
                    "actual": {"checksum": candidate.checksum},
                }
            ],
        },
    )
    assert validation is not None
    deployment.phase = "terminal"
    deployment.outcome = "failed"
    deployment.terminal_at = datetime.now(UTC)
    assert exemplar.status == "pending_deploy"
    await session.flush()


async def _seed_unvalidated_no_change(session, run) -> None:
    """Preserve a historical terminal no-change result that lacks post-effect proof."""
    review = await load_onboarding_review(session, run.run_id)
    candidate = review["candidates"][0]
    bound = await bind_onboarding_decision(
        session,
        run_id=run.run_id,
        candidate_id=candidate["candidate_id"],
        review_revision=review["review_revision"],
        idempotency_key="jmc7c-unvalidated-no-change",
        decision="choose",
    )
    deployment = await session.get(Job, bound["deployment_job_id"])
    assert deployment is not None
    deployment.phase = "terminal"
    deployment.outcome = "no_change"
    deployment.terminal_at = datetime.now(UTC)
    deployment.result = {"outcome": "no_change", "reason_code": "target_missing"}
    await session.flush()


async def _seed_tv_consumer_subject(session, data_dir: Path) -> None:
    """Seed one visible TV subject for the real profile-consumer acknowledgement.

    The browser later submits the regular TV route. Its contained poster worker
    loads the active TV profile before attempting external candidate discovery,
    leaving the same durable acknowledgement production relies on.
    """
    root = data_dir / "jmc7c-tv-consumer"
    root.mkdir(parents=True, exist_ok=True)
    series = Series(
        id=7001,
        title="JMC7C TV Consumer Fixture",
        year=2026,
        tmdb_id=7001,
        tvdb_id=8001,
        sonarr_id=7001,
        series_path=str(root),
        season_count=1,
    )
    session.add(series)
    await session.flush()
    session.add(
        Season(
            id=7001,
            series_id=series.id,
            season_number=1,
            episode_count=1,
            episode_file_count=1,
        )
    )
    await session.flush()


async def _seed_movie_consumer_subject(session, data_dir: Path) -> None:
    """Seed one downloaded movie for the normal movie-consumer acknowledgement."""
    root = data_dir / "jmc7c-movie-consumer"
    root.mkdir(parents=True, exist_ok=True)
    (root / "fixture.mkv").write_bytes(b"jmc7c movie consumer fixture")
    session.add(
        Movie(
            id=7002,
            title="JMC7C Movie Consumer Fixture",
            year=2026,
            folder_path=str(root),
            movie_file_path="fixture.mkv",
            tmdb_id=7002,
        )
    )
    await session.flush()


async def _seed() -> None:
    data_dir = Path(settings.DATA_DIR).resolve()
    expected_data_dir = Path("/tmp/marquee-jmc7c-browser-data").resolve()
    if data_dir != expected_data_dir:
        raise RuntimeError(f"refusing to reset unexpected JMC7C data directory: {data_dir}")
    shutil.rmtree(data_dir, ignore_errors=True)
    data_dir.mkdir(parents=True, exist_ok=True)

    factory = _get_session_factory()
    async with factory() as session:
        # PgQueuer owns these durable transport tables outside SQLAlchemy's
        # metadata, so reset them explicitly with the owned application rows.
        await session.execute(
            text(
                "TRUNCATE pgqueuer, pgqueuer_log, pgqueuer_schedules, pgqueuer_statistics "
                "RESTART IDENTITY"
            )
        )
        await session.commit()
        await reset_database(session)
    await migrate_database()
    await configuration_provider.start(role="test")
    try:
        async with factory() as session:
            await create_system_noop(
                session,
                payload={"echo": "jmc7c-real-browser"},
                idempotency_key="system_noop:jmc7c-real-browser",
            )
            # Reuse the lower-level canonical review fixture as a deterministic
            # artifact-service boundary. The browser still reaches the real
            # onboarding review and decision routes; no response is intercepted.
            from tests.test_jmc6k_contract_freeze import _canonical_review_run
            from tests.test_jmc6k_profile_coordination import _seed_required_positive_subjects

            await _seed_required_positive_subjects(session, count=48)
            await _materialize_seeded_evidence(data_dir, session)
            review_runs = (
                await _canonical_review_run(session),
                await _canonical_review_run(
                    session,
                    run_id="jmc7c-choice-run",
                    job_id="b" * 32,
                    title="JMC7C Choice Fixture",
                    tmdb_id=6002,
                    deployable=True,
                ),
                await _canonical_review_run(
                    session,
                    run_id="jmc7c-cancel-choice-run",
                    job_id="c" * 32,
                    title="JMC7C Cancel Fixture",
                    tmdb_id=6003,
                    deployable=True,
                ),
                await _canonical_review_run(
                    session,
                    run_id="jmc7c-mixed-review-run",
                    job_id="d" * 32,
                    title="JMC7C Mixed Review Fixture",
                    tmdb_id=6004,
                ),
                await _canonical_review_run(
                    session,
                    run_id="jmc7c-corrupt-review-run",
                    job_id="e" * 32,
                    title="JMC7C Corrupt Review Fixture",
                    tmdb_id=6005,
                ),
                await _canonical_review_run(
                    session,
                    run_id="jmc7c-no-change-run",
                    job_id="f" * 32,
                    title="JMC7C No-change Fixture",
                    tmdb_id=6006,
                    deployable=True,
                ),
                await _canonical_review_run(
                    session,
                    run_id="jmc7c-post-effect-recovery-run",
                    job_id="1" * 32,
                    title="JMC7C Post-effect Recovery Fixture",
                    tmdb_id=6007,
                ),
                await _canonical_review_run(
                    session,
                    run_id="jmc7c-unvalidated-no-change-run",
                    job_id="2" * 32,
                    title="JMC7C Unvalidated No-change Fixture",
                    tmdb_id=6008,
                    deployable=True,
                ),
            )
            for run in review_runs:
                job = await session.get(Job, run.job_id)
                movie = await session.get(Movie, run.movie_id)
                assert job is not None
                assert movie is not None
                job.initiator = {"kind": "user", "identifier": "onboarding-api"}
                job.subject_snapshot = {
                    "version": 1,
                    "kind": "movie",
                    "display_id": f"movie:{movie.id}",
                    "display_name": movie.title,
                    "movie_id": movie.id,
                    "title": movie.title,
                    "year": movie.year,
                    "tmdb_id": movie.tmdb_id,
                    "file_name": movie.movie_file_path,
                    "media_kind": "movie",
                }
            for index, run in enumerate(review_runs):
                await _materialize_review_candidate(session, run, index)
            await _make_cancel_fixture_copy_multichunk(session, review_runs[2])
            await _seed_review_archive_edges(session, review_runs[3:5])
            await _seed_identical_deployment_target(session, review_runs[5])
            await _seed_post_effect_recovery(session, review_runs[6])
            await _seed_unvalidated_no_change(session, review_runs[7])
            await _seed_tv_consumer_subject(session, data_dir)
            await _seed_movie_consumer_subject(session, data_dir)
            await session.commit()
    finally:
        await configuration_provider.stop()
    await close_db()


def main() -> None:
    asyncio.run(_seed())
    port = os.environ.get("JMC7C_API_PORT", "3201")
    os.execv(
        sys.executable,
        [
            sys.executable,
            "-m",
            "uvicorn",
            "marquee.main:app",
            "--host",
            "127.0.0.1",
            "--port",
            port,
        ],
    )


if __name__ == "__main__":
    main()
