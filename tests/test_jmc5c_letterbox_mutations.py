"""C1 real-media gates for candidate-first letterbox metadata mutation."""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import select

from marquee.api.routes.letterbox import _tv_mutation_intents
from marquee.core.jobs import handlers_letterbox_mutations
from marquee.core.jobs.handlers_letterbox_mutations import (
    execute_letterbox_apply,
    execute_letterbox_remove,
)
from marquee.core.jobs.submission import Initiator
from marquee.models import (
    Episode,
    EpisodeMediaFile,
    LetterboxEvent,
    LetterboxState,
    Movie,
    Series,
)
from tests.support.jmc5b_harness import execution_context as _context
from tests.support.jmc5b_harness import media_file_row as _media_file
from tests.support.jmc5b_harness import real_signature
from tests.support.media_fixtures import build_mkv, require_media_tools

pytestmark = pytest.mark.usefixtures("jmc5b_media_roots")


async def _fixture(db, tmp_path: Path):
    require_media_tools()
    path = build_mkv(tmp_path / "library", "letterbox.mkv")
    movie = Movie(
        title="Letterbox Fixture",
        year=2026,
        folder_path=str(path.parent),
        movie_file_path=str(path),
        video_width=64,
        video_height=48,
        container="mkv",
    )
    db.add(movie)
    await db.flush()
    row = await _media_file(db, path)
    row.movie_id = movie.id
    state = LetterboxState(
        movie_id=movie.id,
        media_type="movie",
        status="candidate",
        confidence="high",
        source_width=64,
        source_height=48,
        recommended_crop_top=4,
        recommended_crop_bottom=4,
        variable_ar=False,
    )
    db.add(state)
    await db.commit()
    return path, movie, row, state


def _before(path: Path, state: LetterboxState) -> dict[str, object]:
    return {
        "source_signature": real_signature(path),
        "status": state.status,
        "confidence": state.confidence,
        "variable_ar": state.variable_ar,
        "source_width": state.source_width,
        "source_height": state.source_height,
        "current_crop_top": state.applied_crop_top,
        "current_crop_bottom": state.applied_crop_bottom,
        "recommended_crop_top": state.recommended_crop_top,
        "recommended_crop_bottom": state.recommended_crop_bottom,
    }


@pytest.mark.asyncio
async def test_tv_parent_planning_seals_one_child_per_shared_physical_file(
    db, tmp_path: Path
) -> None:
    require_media_tools()
    path = build_mkv(tmp_path / "library", "shared-episodes.mkv")
    series = Series(
        title="Shared Episodes",
        year=2026,
        series_path=str(path.parent),
        sonarr_id=9501,
    )
    db.add(series)
    await db.flush()
    episodes = [
        Episode(
            series_id=series.id,
            season_number=1,
            episode_number=number,
            title=f"Episode {number}",
            episode_file_path=str(path),
            video_width=64,
            video_height=48,
        )
        for number in (1, 2)
    ]
    db.add_all(episodes)
    await db.flush()
    media_file = await _media_file(db, path)
    states = [
        LetterboxState(
            media_type="episode",
            episode_id=episode.id,
            status="candidate",
            confidence="high",
            source_width=64,
            source_height=48,
            recommended_crop_top=4,
            recommended_crop_bottom=4,
            variable_ar=False,
        )
        for episode in episodes
    ]
    db.add_all(
        [
            EpisodeMediaFile(episode_id=episode.id, media_file_id=media_file.id)
            for episode in episodes
        ]
        + states
    )
    await db.commit()

    intents = await _tv_mutation_intents(
        db,
        [
            (episode, series, state, media_file.id)
            for episode, state in zip(episodes, states, strict=True)
        ],
        operation="apply",
        confidence_levels=("high",),
        initiator=Initiator(kind="system", identifier="jmc5c-test"),
    )

    assert len(intents) == 1
    assert intents[0].job_type == "letterbox_apply"
    assert intents[0].subject.reference == str(media_file.id)
    assert intents[0].request["subject_ids"] == [episode.id for episode in episodes]


@pytest.mark.asyncio
async def test_apply_and_remove_publish_only_freshly_verified_crop_metadata(
    db, tmp_path: Path, data_dir: Path
) -> None:
    path, movie, row, state = await _fixture(db, tmp_path)
    movie_id = movie.id
    media_file_id = row.id
    original = path.read_bytes()
    apply_request = {
        "media_file_id": media_file_id,
        "subject_kind": "movie",
        "subject_ids": [movie.id],
        "before": _before(path, state),
        "crop_top": 4,
        "crop_bottom": 4,
        "source": "api",
    }
    apply_context = await _context(
        db, tmp_path, job_type="letterbox_apply", request=apply_request
    )
    applied = await execute_letterbox_apply(apply_context)

    assert applied["outcome"] == "succeeded", (
        applied["reason_code"],
        applied["message"],
    )
    assert applied["actual_probe"]["crop_present"] is True
    assert applied["actual_probe"]["crop_top"] == 4
    assert applied["actual_probe"]["crop_bottom"] == 4
    assert applied["atomicity"]["published"] is True
    assert len(apply_context.writer.publications) == 1
    assert (data_dir / applied["backup"]["artifact_key"]).read_bytes() == original

    db.expire_all()
    state = await db.scalar(select(LetterboxState).where(LetterboxState.movie_id == movie_id))
    assert state.status == "tagged"
    assert (state.applied_crop_top, state.applied_crop_bottom) == (4, 4)

    remove_request = {
        "media_file_id": media_file_id,
        "subject_kind": "movie",
        "subject_ids": [movie_id],
        "before": _before(path, state),
        "source": "api",
    }
    remove_context = await _context(
        db, tmp_path, job_type="letterbox_remove", request=remove_request
    )
    removed = await execute_letterbox_remove(remove_context)

    assert removed["outcome"] == "succeeded"
    assert removed["actual_probe"]["crop_present"] is False
    assert removed["atomicity"]["published"] is True
    db.expire_all()
    state = await db.scalar(select(LetterboxState).where(LetterboxState.movie_id == movie_id))
    assert state.status == "candidate"
    assert state.applied_crop_top is None
    events = (
        await db.scalars(
            select(LetterboxEvent)
            .where(LetterboxEvent.movie_id == movie_id)
            .order_by(LetterboxEvent.id)
        )
    ).all()
    assert [event.action for event in events] == ["apply", "remove"]


@pytest.mark.asyncio
async def test_stale_or_variable_plan_never_launches_a_mutation(
    db, tmp_path: Path, data_dir: Path
) -> None:
    path, movie, row, state = await _fixture(db, tmp_path)
    request = {
        "media_file_id": row.id,
        "subject_kind": "movie",
        "subject_ids": [movie.id],
        "before": {**_before(path, state), "source_signature": "stale-signature"},
        "crop_top": 4,
        "crop_bottom": 4,
        "source": "api",
    }
    original = path.read_bytes()
    context = await _context(db, tmp_path, job_type="letterbox_apply", request=request)
    result = await execute_letterbox_apply(context)

    assert result["outcome"] == "failed"
    assert result["reason_code"] == "stale_plan"
    assert context.writer.publications == []
    assert path.read_bytes() == original
    assert not any(data_dir.rglob("*.mkv"))


@pytest.mark.asyncio
async def test_heal_child_is_no_change_when_intact_and_repairs_verified_drift(
    db, tmp_path: Path, data_dir: Path
) -> None:
    path, movie, row, state = await _fixture(db, tmp_path)
    movie_id = movie.id
    state.status = "tagged"
    state.applied_crop_top = 4
    state.applied_crop_bottom = 4
    await db.commit()
    request = {
        "media_file_id": row.id,
        "subject_kind": "movie",
        "subject_ids": [movie.id],
        "before": _before(path, state),
        "crop_top": 4,
        "crop_bottom": 4,
        "source": "heal",
    }
    context = await _context(db, tmp_path, job_type="letterbox_apply", request=request)
    result = await execute_letterbox_apply(context)

    assert result["outcome"] == "succeeded", (result["reason_code"], result["message"])
    assert result["actual_probe"]["crop_top"] == 4
    assert len(context.writer.publications) == 1

    db.expire_all()
    state = await db.scalar(select(LetterboxState).where(LetterboxState.movie_id == movie_id))
    intact_request = {**request, "before": _before(path, state)}
    intact_context = await _context(
        db, tmp_path, job_type="letterbox_apply", request=intact_request
    )
    intact = await execute_letterbox_apply(intact_context)
    assert intact["outcome"] == "no_change"
    assert intact["reason_code"] == "already_applied"
    assert intact_context.writer.publications == []


@pytest.mark.asyncio
async def test_cancellation_before_metadata_tool_never_publishes(
    db, tmp_path: Path, data_dir: Path
) -> None:
    path, movie, row, state = await _fixture(db, tmp_path)
    request = {
        "media_file_id": row.id,
        "subject_kind": "movie",
        "subject_ids": [movie.id],
        "before": _before(path, state),
        "crop_top": 4,
        "crop_bottom": 4,
        "source": "api",
    }
    original = path.read_bytes()
    context = await _context(db, tmp_path, job_type="letterbox_apply", request=request)
    context.cancellation.is_cancelled = lambda: True
    result = await execute_letterbox_apply(context)

    assert result["outcome"] == "cancelled"
    assert result["reason_code"] == "cancelled"
    assert context.writer.publications == []
    assert path.read_bytes() == original


@pytest.mark.asyncio
async def test_failure_after_publication_is_quarantined_as_unsafe(
    db, tmp_path: Path, data_dir: Path, monkeypatch
) -> None:
    path, movie, row, state = await _fixture(db, tmp_path)
    request = {
        "media_file_id": row.id,
        "subject_kind": "movie",
        "subject_ids": [movie.id],
        "before": _before(path, state),
        "crop_top": 4,
        "crop_bottom": 4,
        "source": "api",
    }
    context = await _context(db, tmp_path, job_type="letterbox_apply", request=request)

    async def fail_persist(*_args, **_kwargs):
        raise RuntimeError("injected database death after publication")

    monkeypatch.setattr(handlers_letterbox_mutations, "_persist_verified", fail_persist)
    result = await execute_letterbox_apply(context)

    assert result["outcome"] == "unsafe"
    assert result["reason_code"] == "post_publish_uncertain"
    assert result["atomicity"]["uncertain_state"] is True
    assert len(context.writer.publications) == 1
    assert result["actual_probe"]["crop_top"] == 4
