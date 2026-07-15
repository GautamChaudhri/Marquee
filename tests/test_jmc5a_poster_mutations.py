from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
from PIL import Image

from marquee.config import settings
from marquee.core.jobs.handlers_poster_mutations import (
    PosterMutationError,
    _boundary,
    _execute_copy,
    execute_poster_backup_subject,
    execute_poster_reset,
    execute_poster_restore,
)
from marquee.core.jobs.manifest import JOB_DEFINITION_REGISTRY
from marquee.core.jobs.mutation_documents import (
    PosterCandidateSelectionV1,
    PosterDeployRequestV1,
)
from marquee.core.jobs.poster_submission import poster_child_idempotency_key
from marquee.core.jobs.publication import file_signature
from marquee.core.poster_subjects import PosterSubject
from marquee.database import _get_session_factory
from marquee.models import Job, MediaOperationDetail, Movie, Season, Series


def test_poster_child_idempotency_key_is_deterministic_and_path_free():
    first = poster_child_idempotency_key("bulk:one", "series/../../42")
    assert first == poster_child_idempotency_key("bulk:one", "series/../../42")
    assert first.startswith("poster_deploy:")
    assert "/" not in first


class _Fence:
    def __init__(self) -> None:
        self.intents: list[dict] = []
        self.publications: list[dict] = []

    async def owns_current_attempt(self, _session) -> bool:
        return True

    async def record_publish_intent(self, intent: dict) -> str:
        self.intents.append(intent)
        return "applied"

    async def publish_atomic(self, action, evidence: dict) -> str:
        action()
        self.publications.append(evidence)
        return "applied"


async def _context(db, *, job_type: str, request: dict):
    job_id = uuid4().hex
    db.add(
        Job(
            id=job_id,
            type=job_type,
            payload_version=1,
            request=request,
            phase="running",
            desired_state="run",
            fence_token=7,
            root_id=job_id,
            trigger_kind="manual",
            feature_area="ai_posters",
            presentation_family="ai_posters",
            subject_kind=request["target_kind"],
            subject_reference=str(request["target_id"]),
            subject_snapshot={"version": 1, "kind": request["target_kind"]},
        )
    )
    await db.commit()
    return SimpleNamespace(
        delivery=SimpleNamespace(canonical_job_id=job_id),
        attempt=SimpleNamespace(attempt_id=1, fence_token=7),
        request=request,
        definition=JOB_DEFINITION_REGISTRY.get(job_type),
        cancellation=SimpleNamespace(cancel_called=False),
        writer=_Fence(),
        session_factory=_get_session_factory(),
    )


@pytest.mark.asyncio
async def test_movie_deploy_no_change_reset_restore_round_trip(
    db, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    media_root = tmp_path / "media"
    movie_folder = media_root / "Movie"
    movie_folder.mkdir(parents=True)
    monkeypatch.setattr(settings, "MEDIA_ROOTS", [str(media_root)])
    monkeypatch.setattr(settings, "MOVIE_POSTER_FORMAT", "poster.jpg")

    movie = Movie(title="Movie", year=2026, radarr_id=901, folder_path=str(movie_folder))
    db.add(movie)
    await db.commit()

    candidate_path = Path(settings.DATA_DIR) / "jmc5a-tests" / "candidate.jpg"
    candidate_path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (32, 48), (12, 34, 56)).save(candidate_path, format="JPEG")

    subject = await db.get(Movie, movie.id)
    boundary, _destination = _boundary(PosterSubject.from_movie(subject))
    candidate = boundary.classify(candidate_path, roots=("data",), require_file=True)
    signature = file_signature(boundary, candidate)
    selection = PosterCandidateSelectionV1(
        source="pipeline_run",
        storage_key="jmc5a-tests/candidate.jpg",
        run_id="run-jmc5a",
        candidate_reference="candidate.jpg",
        expected_checksum=signature.sha256,
    )
    deploy_request = PosterDeployRequestV1(
        target_kind="movie",
        target_id=movie.id,
        candidate=selection,
        ai_selected=True,
        user_approved=True,
    )

    deploy_context = await _context(
        db, job_type="poster_deploy", request=deploy_request.model_dump(mode="json")
    )
    deployed = await _execute_copy(deploy_context, deploy_request, candidate, "pipeline_run")
    assert deployed["outcome"] == "succeeded"
    assert (movie_folder / "poster.jpg").read_bytes() == candidate_path.read_bytes()

    no_change_context = await _context(
        db, job_type="poster_deploy", request=deploy_request.model_dump(mode="json")
    )
    no_change = await _execute_copy(no_change_context, deploy_request, candidate, "pipeline_run")
    assert no_change["outcome"] == "no_change"
    assert no_change["target_outcomes"][0]["bytes_changed"] is False
    assert no_change["target_outcomes"][0]["product_state_changed"] is False

    reset_request = {"target_kind": "movie", "target_id": movie.id}
    reset_context = await _context(db, job_type="poster_reset", request=reset_request)
    reset = await execute_poster_reset(reset_context)
    assert reset["outcome"] == "succeeded"
    assert not (movie_folder / "poster.jpg").exists()

    restored_movie = await db.get(Movie, movie.id, populate_existing=True)
    assert restored_movie.poster_local_backup_path
    restore_request = {"target_kind": "movie", "target_id": movie.id}
    restore_context = await _context(db, job_type="poster_restore", request=restore_request)
    restored = await execute_poster_restore(restore_context)
    assert restored["outcome"] == "succeeded"
    assert (movie_folder / "poster.jpg").read_bytes() == candidate_path.read_bytes()

    detail = await db.get(MediaOperationDetail, restore_context.delivery.canonical_job_id)
    assert detail.actual_target["checksum"] == signature.sha256
    assert detail.atomicity["published"] is True


@pytest.mark.asyncio
async def test_poster_reset_rejects_poisoned_projection_path(
    db, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    media_root = tmp_path / "media"
    movie_folder = media_root / "Movie"
    movie_folder.mkdir(parents=True)
    poster = movie_folder / "poster.jpg"
    Image.new("RGB", (16, 24), (1, 2, 3)).save(poster, format="JPEG")
    monkeypatch.setattr(settings, "MEDIA_ROOTS", [str(media_root)])
    monkeypatch.setattr(settings, "MOVIE_POSTER_FORMAT", "poster.jpg")
    outside = tmp_path / "outside.jpg"
    outside.write_bytes(b"keep")
    movie = Movie(
        title="Poisoned",
        year=2026,
        radarr_id=902,
        folder_path=str(movie_folder),
        poster_path=str(outside),
    )
    db.add(movie)
    await db.commit()
    request = {"target_kind": "movie", "target_id": movie.id}
    context = await _context(db, job_type="poster_reset", request=request)

    with pytest.raises(PosterMutationError, match="canonical destination"):
        await execute_poster_reset(context)
    assert poster.exists()
    assert outside.read_bytes() == b"keep"


@pytest.mark.asyncio
async def test_poster_backup_subject_copies_once_and_then_reports_no_change(
    db, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    media_root = tmp_path / "backup-media"
    movie_folder = media_root / "Movie"
    movie_folder.mkdir(parents=True)
    poster = movie_folder / "poster.jpg"
    Image.new("RGB", (16, 24), (7, 8, 9)).save(poster, format="JPEG")
    monkeypatch.setattr(settings, "MEDIA_ROOTS", [str(media_root)])
    monkeypatch.setattr(settings, "MOVIE_POSTER_FORMAT", "poster.jpg")
    movie = Movie(
        title="Backup",
        year=2026,
        radarr_id=903,
        folder_path=str(movie_folder),
        poster_path=str(poster),
    )
    db.add(movie)
    await db.commit()
    request = {"target_kind": "movie", "target_id": movie.id}

    first = await execute_poster_backup_subject(
        await _context(db, job_type="poster_backup_subject", request=request)
    )
    second = await execute_poster_backup_subject(
        await _context(db, job_type="poster_backup_subject", request=request)
    )

    assert first["outcome"] == "succeeded"
    assert first["backup"]["checksum"] == first["target_outcomes"][0]["actual"]["checksum"]
    assert first["backup"]["size_bytes"] > 0
    assert second["outcome"] == "no_change"
    assert second["reason_code"] == "backup_already_identical"
    assert second["target_outcomes"][0]["bytes_changed"] is False


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("target_kind", "filename"), (("series", "poster.jpg"), ("season", "season01.jpg"))
)
async def test_tv_subject_deploy_uses_canonical_leaf(
    db,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    target_kind: str,
    filename: str,
) -> None:
    media_root = tmp_path / "media"
    show_folder = media_root / "Show"
    show_folder.mkdir(parents=True)
    monkeypatch.setattr(settings, "MEDIA_ROOTS", [str(media_root)])
    monkeypatch.setattr(settings, "SERIES_POSTER_FORMAT", "poster.jpg")
    monkeypatch.setattr(settings, "SEASON_POSTER_FORMAT", "season{season:02d}.jpg")
    series = Series(title="Show", sonarr_id=950, series_path=str(show_folder))
    db.add(series)
    await db.flush()
    if target_kind == "series":
        target_id = series.id
        subject = PosterSubject.from_series(series)
    else:
        season = Season(series_id=series.id, season_number=1, episode_file_count=1)
        db.add(season)
        await db.flush()
        target_id = season.id
        subject = PosterSubject.from_season(season, series)
    await db.commit()

    candidate_path = Path(settings.DATA_DIR) / "jmc5a-tests" / f"{target_kind}.jpg"
    candidate_path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (24, 36), (90, 80, 70)).save(candidate_path, format="JPEG")
    boundary, _destination = _boundary(subject)
    candidate = boundary.classify(candidate_path, roots=("data",), require_file=True)
    signature = file_signature(boundary, candidate)
    request = PosterDeployRequestV1(
        target_kind=target_kind,
        target_id=target_id,
        candidate=PosterCandidateSelectionV1(
            source="pipeline_run",
            storage_key=f"jmc5a-tests/{target_kind}.jpg",
            run_id=f"run-{target_kind}",
            candidate_reference=f"{target_kind}.jpg",
            expected_checksum=signature.sha256,
        ),
    )
    context = await _context(
        db, job_type="poster_deploy", request=request.model_dump(mode="json")
    )

    result = await _execute_copy(context, request, candidate, "pipeline_run")

    assert result["outcome"] == "succeeded"
    assert (show_folder / filename).read_bytes() == candidate_path.read_bytes()
