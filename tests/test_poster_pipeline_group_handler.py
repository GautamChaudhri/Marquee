from __future__ import annotations

import asyncio
import importlib
import json
import uuid
from pathlib import Path
from types import MappingProxyType, SimpleNamespace
from typing import Any

import pytest

from marquee.core.jobs.delivery import EXECUTION_HANDLERS
from marquee.core.jobs.documents import PosterPipelineGroupRequestV1
from marquee.core.jobs.poster_group_retry import poster_subject_key
from marquee.models import Job

group_handler = importlib.import_module("marquee.core.jobs.poster_pipeline_group")


def _request() -> PosterPipelineGroupRequestV1:
    return PosterPipelineGroupRequestV1.model_validate(
        {
            "library": "movies",
            "chunk_index": 3,
            "members": [
                {"movie_id": 11, "title": "Eleven"},
                {"movie_id": 12, "title": "Twelve"},
            ],
        }
    )


def _snapshots(request: PosterPipelineGroupRequestV1) -> dict[str, dict[str, Any]]:
    return {
        poster_subject_key(member): {
            "kind": "movie",
            "movie_id": member.movie_id,
            "title": member.title,
        }
        for member in request.members
    }


def _context(
    request: PosterPipelineGroupRequestV1,
    *,
    cancelled: bool = False,
    io: Any | None = None,
) -> SimpleNamespace:
    snapshots = _snapshots(request)
    return SimpleNamespace(
        cancellation=SimpleNamespace(cancel_called=cancelled),
        subject=MappingProxyType(
            {
                "members": tuple(
                    MappingProxyType(
                        {
                            "subject_key": key,
                            "subject": MappingProxyType(snapshot),
                        }
                    )
                for key, snapshot in snapshots.items()
                )
            }
        ),
        process_launcher=object(),
        progress=None,
        io=io,
        delivery=SimpleNamespace(canonical_job_id=uuid.uuid4()),
        attempt=SimpleNamespace(attempt_id=uuid.uuid4(), fence_token=7),
        workspace=SimpleNamespace(boundary=object()),
        configuration={},
    )


def test_member_snapshots_accept_delivery_immutable_shape() -> None:
    request = _request()

    resolved = group_handler._member_snapshots(_context(request))

    assert resolved == _snapshots(request)
    assert all(isinstance(snapshot, dict) for snapshot in resolved.values())


def _result_document(
    request: PosterPipelineGroupRequestV1,
    run_ids: tuple[str, ...],
) -> dict[str, Any]:
    return {
        "version": 1,
        "library": request.library,
        "chunk_index": request.chunk_index,
        "member_count": len(request.members),
        "members": [
            {
                "member_index": index,
                "subject_key": poster_subject_key(member),
                "run_id": run_id,
                "status": "completed",
                "candidate_count": 1,
                "counts": {"ranked": 1},
                "recommendation": {"orig_filename": f"poster-{index}.jpg"},
                "archive_file": f"run-{index:03}.json",
                "candidate_files": {},
                "personalization_mode": "personalized",
            }
            for index, (member, run_id) in enumerate(
                zip(request.members, run_ids, strict=True)
            )
        ],
    }


class _ResultIO:
    def __init__(self, document: dict[str, Any]) -> None:
        self.document = document

    async def read(self, _path: Any, *, maximum_bytes: int) -> SimpleNamespace:
        payload = json.dumps(self.document).encode()
        assert len(payload) <= maximum_bytes
        return SimpleNamespace(payload=payload)


async def _configure_host(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Any,
    request: PosterPipelineGroupRequestV1,
    *,
    runner_outcome: str = "succeeded",
) -> tuple[tuple[str, ...], list[list[dict[str, Any]]]]:
    run_ids = ("run-eleven", "run-twelve")
    projected_calls: list[list[dict[str, Any]]] = []

    async def write_group(*_args: Any, **_kwargs: Any) -> tuple[dict[str, str], tuple[str, ...]]:
        return {"group_file": "group.json", "group_checksum": "a" * 64}, run_ids

    async def stage(*_args: Any, **_kwargs: Any) -> tuple[dict[str, str], None, None]:
        return {"personalization_mode": "personalized"}, None, None

    async def run(*_args: Any, **_kwargs: Any) -> SimpleNamespace:
        return SimpleNamespace(
            outcome=runner_outcome,
            error=None,
            warnings=(),
            files=(
                SimpleNamespace(key="group-result.json"),
                SimpleNamespace(key="run-000.json"),
                SimpleNamespace(key="run-001.json"),
            ),
        )

    async def register_candidates(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        return {}

    async def project(*_args: Any, projected: list[dict[str, Any]], **_kwargs: Any) -> None:
        projected_calls.append(projected)

    monkeypatch.setattr(group_handler, "_workspace_dir", lambda _context: tmp_path)
    monkeypatch.setattr(group_handler, "_write_group_file", write_group)
    monkeypatch.setattr(group_handler, "_stage_personalization", stage)
    monkeypatch.setattr(group_handler, "_runner_runtime_options", lambda _context: {})
    monkeypatch.setattr(group_handler, "_register_candidate_files", register_candidates)
    monkeypatch.setattr(group_handler, "_attach_candidate_artifacts", lambda *_a, **_k: None)
    monkeypatch.setattr(group_handler, "_project_group_runs", project)

    from marquee.core.jobs import internal_runner_host

    monkeypatch.setattr(internal_runner_host, "run_internal_operation", run)
    return run_ids, projected_calls


class _Transaction:
    def __init__(self, session: _ProjectionSession) -> None:
        self.session = session

    async def __aenter__(self) -> _Transaction:
        self.session.begin_calls += 1
        return self

    async def __aexit__(self, exc_type: Any, _exc: Any, _tb: Any) -> None:
        if exc_type is None:
            self.session.committed.extend(self.session.pending)
        self.session.pending.clear()


class _ProjectionSession:
    def __init__(self, *, fail_add_at: int | None = None) -> None:
        self.fail_add_at = fail_add_at
        self.begin_calls = 0
        self.pending: list[Any] = []
        self.committed: list[Any] = []

    async def __aenter__(self) -> _ProjectionSession:
        return self

    async def __aexit__(self, _exc_type: Any, _exc: Any, _tb: Any) -> None:
        return None

    def begin(self) -> _Transaction:
        return _Transaction(self)

    async def get(self, model: type[Any], _identity: Any) -> Any:
        assert model is Job
        return SimpleNamespace(parent_id=uuid.uuid4(), correlation_id=uuid.uuid4())

    def add(self, value: Any) -> None:
        if self.fail_add_at is not None and len(self.pending) == self.fail_add_at:
            raise RuntimeError("synthetic projection failure")
        self.pending.append(value)


class _Writer:
    def __init__(self, owns_fence: bool) -> None:
        self.owns_fence = owns_fence

    async def owns_current_attempt(self, _session: Any) -> bool:
        return self.owns_fence


def _projection_context(
    request: PosterPipelineGroupRequestV1,
    session: _ProjectionSession,
    *,
    owns_fence: bool = True,
) -> SimpleNamespace:
    context = _context(request)
    context.session_factory = lambda: session
    context.writer = _Writer(owns_fence)
    return context


def _projected_members(request: PosterPipelineGroupRequestV1) -> list[dict[str, Any]]:
    return [
        {
            "subject_key": poster_subject_key(member),
            "run_id": f"run-{index}",
            "status": "completed",
            "counts": {"ranked": 1},
            "archive_artifact": SimpleNamespace(id=uuid.uuid4()),
            "selected_artifact": None,
            "recommendation": None,
        }
        for index, member in enumerate(request.members)
    ]


@pytest.mark.asyncio
async def test_projection_inserts_every_member_in_one_transaction() -> None:
    request = _request()
    session = _ProjectionSession()
    assert "poster_pipeline_group" in EXECUTION_HANDLERS

    await group_handler._project_group_runs(
        _projection_context(request, session),
        request,
        snapshots=_snapshots(request),
        projected=_projected_members(request),
    )

    assert session.begin_calls == 1
    assert len(session.committed) == len(request.members)
    assert [run.subject_key for run in session.committed] == [
        poster_subject_key(member) for member in request.members
    ]
    assert all(run.job_id == session.committed[0].job_id for run in session.committed)


@pytest.mark.asyncio
async def test_projection_failure_rolls_back_every_member() -> None:
    request = _request()
    session = _ProjectionSession(fail_add_at=1)

    with pytest.raises(RuntimeError, match="synthetic projection failure"):
        await group_handler._project_group_runs(
            _projection_context(request, session),
            request,
            snapshots=_snapshots(request),
            projected=_projected_members(request),
        )

    assert session.begin_calls == 1
    assert session.committed == []
    assert session.pending == []


@pytest.mark.asyncio
async def test_fence_loss_prevents_all_projection() -> None:
    request = _request()
    session = _ProjectionSession()

    with pytest.raises(RuntimeError, match="lost its fence"):
        await group_handler._project_group_runs(
            _projection_context(request, session, owns_fence=False),
            request,
            snapshots=_snapshots(request),
            projected=_projected_members(request),
        )

    assert session.committed == []
    assert session.pending == []


@pytest.mark.asyncio
async def test_required_group_fallback_failure_creates_zero_projections(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Any
) -> None:
    request = _request()
    run_ids, projected_calls = await _configure_host(monkeypatch, tmp_path, request)
    context = _context(request, io=_ResultIO(_result_document(request, run_ids)))

    async def fail_register(*_args: Any, **_kwargs: Any) -> Any:
        raise RuntimeError("artifact store unavailable")

    monkeypatch.setattr(group_handler, "_register_file", fail_register)

    with pytest.raises(RuntimeError, match="fallback evidence could not be registered"):
        await group_handler.execute_poster_pipeline_group(context, request)

    assert projected_calls == []


@pytest.mark.asyncio
async def test_member_archive_failure_isolated_and_uses_group_fallback(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Any
) -> None:
    request = _request()
    run_ids, projected_calls = await _configure_host(monkeypatch, tmp_path, request)
    context = _context(request, io=_ResultIO(_result_document(request, run_ids)))
    group_artifact = SimpleNamespace(id=101)
    second_archive = SimpleNamespace(id=102)

    async def register(*_args: Any, filename: str, **_kwargs: Any) -> Any:
        if filename == "group-result.json":
            return group_artifact
        if filename == "run-000.json":
            raise RuntimeError("first archive failed")
        assert filename == "run-001.json"
        return second_archive

    monkeypatch.setattr(group_handler, "_register_file", register)

    result = await group_handler.execute_poster_pipeline_group(context, request)

    assert len(projected_calls) == 1
    first, second = projected_calls[0]
    assert first["status"] == "failed"
    assert first["archive_artifact"] is group_artifact
    assert second["status"] == "completed"
    assert second["archive_artifact"] is second_archive
    assert result["outcome"] == "review_required"
    assert result["failed_count"] == 1
    assert result["failed_subject_keys"] == [poster_subject_key(request.members[0])]
    assert result["artifact_ids"] == [group_artifact.id, second_archive.id]


@pytest.mark.asyncio
@pytest.mark.parametrize("already_cancelled", [True, False])
async def test_cancellation_creates_zero_projections(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Any,
    already_cancelled: bool,
) -> None:
    request = _request()
    run_ids, projected_calls = await _configure_host(
        monkeypatch,
        tmp_path,
        request,
        runner_outcome="succeeded" if already_cancelled else "cancelled",
    )
    context = _context(
        request,
        cancelled=already_cancelled,
        io=_ResultIO(_result_document(request, run_ids)),
    )

    with pytest.raises(asyncio.CancelledError):
        await group_handler.execute_poster_pipeline_group(context, request)

    assert projected_calls == []


@pytest.mark.parametrize(
    "corruption",
    ["subject_order", "run_id", "member_index", "archive_file", "candidate_file"],
)
def test_group_result_requires_exact_member_and_run_identity(corruption: str) -> None:
    request = _request()
    run_ids = ("run-eleven", "run-twelve")
    document = _result_document(request, run_ids)
    if corruption == "subject_order":
        document["members"].reverse()
        expected = "member identity or order"
    elif corruption == "run_id":
        document["members"][1]["run_id"] = "foreign-run"
        expected = "run identity or order"
    elif corruption == "member_index":
        document["members"][1]["member_index"] = 0
        expected = "member ordinal"
    elif corruption == "archive_file":
        document["members"][1]["archive_file"] = "run-000.json"
        expected = "archive attribution"
    else:
        document["members"][1]["candidate_files"] = {
            "poster-1.jpg": "s000-candidate-000.jpg"
        }
        expected = "candidate artifact attribution"

    with pytest.raises(RuntimeError, match=expected):
        group_handler._validated_member_results(document, request, run_ids)


def test_member_archive_requires_its_planned_run_identity(tmp_path: Any) -> None:
    archive = tmp_path / "run-000.json"
    archive.write_text(
        json.dumps({"run_id": "foreign-run", "review": {"survivors": []}}),
        encoding="utf-8",
    )

    with pytest.raises(RuntimeError, match="run identity"):
        group_handler._attach_candidate_artifacts(
            tmp_path,
            archive_file=archive.name,
            run_id="planned-run",
            artifacts={},
        )


def test_group_result_requires_every_referenced_artifact_to_be_announced() -> None:
    request = _request()
    run_ids = ("run-eleven", "run-twelve")
    document = _result_document(request, run_ids)

    with pytest.raises(RuntimeError, match="announced artifacts differ"):
        group_handler._validated_member_results(
            document,
            request,
            run_ids,
            announced_files={"group-result.json", "run-000.json"},
        )


class _WriteIO:
    """Capture the sealed group document instead of touching the workspace."""

    def __init__(self) -> None:
        self.payload: bytes | None = None

    async def write(self, payload: bytes, _path: Any) -> SimpleNamespace:
        self.payload = payload
        return SimpleNamespace(sha256="b" * 64)


class _SessionFactory:
    def __init__(self, session: Any) -> None:
        self._session = session

    def __call__(self) -> _SessionFactory:
        return self

    async def __aenter__(self) -> Any:
        return self._session

    async def __aexit__(self, *_exc: Any) -> None:
        return None


@pytest.mark.asyncio
async def test_group_file_heals_a_legacy_season_snapshot(db) -> None:
    """A snapshot predating the season fields must not fail the whole chunk.

    The single path fills these from the live rows; the group path used to be
    strict, so one stale field would fail every subject in the group before any
    of them reached the GPU.
    """
    from marquee.models import Season, Series

    series = Series(title="A Show", year=2014, series_path="/t/s", tmdb_id=196322)
    db.add(series)
    await db.flush()
    season = Season(series_id=series.id, season_number=2)
    db.add(season)
    await db.commit()

    request = PosterPipelineGroupRequestV1.model_validate(
        {
            "library": "tv",
            "chunk_index": 0,
            "members": [
                {"series_id": series.id, "tmdb_id": 196322, "title": "A Show"},
                {
                    "season_id": season.id,
                    "tmdb_id": 196322,
                    "title": "A Show · Season 2",
                },
            ],
        }
    )
    # Only season_id survived; series_id, season_number, and series_title are gone.
    snapshots = {
        f"series:{series.id}": {"kind": "series", "series_id": series.id},
        f"season:{season.id}": {"kind": "season", "season_id": season.id},
    }
    io = _WriteIO()
    context = SimpleNamespace(session_factory=_SessionFactory(db), io=io)

    reference, run_ids = await group_handler._write_group_file(
        context,
        request,
        workspace_dir=Path("/unused"),
        snapshots=snapshots,
    )

    assert reference == {"group_file": "group.json", "group_checksum": "b" * 64}
    assert len(run_ids) == 2
    assert io.payload is not None
    members = json.loads(io.payload)["members"]
    by_key = {member["subject_key"]: member["subject"] for member in members}
    healed = by_key[f"season:{season.id}"]
    assert healed["series_id"] == series.id
    assert healed["season_number"] == 2
    # The display title keeps its suffix; OCR gets the bare series title.
    assert healed["title"] == "A Show · Season 2"
    assert healed["ocr_title"] == "A Show"
    assert by_key[f"series:{series.id}"]["media_type"] == "series"
