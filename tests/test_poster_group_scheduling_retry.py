"""Model-free coverage for grouped poster scheduling and retry expansion."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

import marquee.api.routes.pipeline as movie_pipeline_routes
import marquee.core.jobs.batches as job_batches
import marquee.core.jobs.control as job_control
import marquee.core.jobs.poster_group_retry as poster_group_retry
from marquee.api.routes.pipeline_tv import _active_tv_asset_jobs, _tv_group_chunks
from marquee.core.jobs.documents import PosterPipelineGroupRequestV1
from marquee.core.jobs.manifest import JOB_DEFINITION_REGISTRY
from marquee.core.jobs.retry_capability import resolve_retry_capability
from marquee.core.jobs.submission import SubmissionInvariantError


class _ScalarRows:
    def __init__(self, values: list[object] | tuple[object, ...]) -> None:
        self._values = tuple(values)

    def all(self) -> list[object]:
        return list(self._values)

    def __iter__(self):
        return iter(self._values)


class _Rows:
    def __init__(self, values: list[object] | tuple[object, ...]) -> None:
        self._values = values

    def scalars(self) -> _ScalarRows:
        return _ScalarRows(self._values)


class _Transaction:
    async def __aenter__(self) -> None:
        return None

    async def __aexit__(self, *_args: object) -> None:
        return None


class _MovieSession:
    """Serves the scope query first, then the active-poster-job guard query."""

    def __init__(self, movies: list[object], jobs: list[object] | None = None) -> None:
        self.movies = movies
        self.jobs = jobs or []
        self.calls = 0

    async def execute(self, _statement: object) -> _Rows:
        self.calls += 1
        return _Rows(self.movies if self.calls == 1 else self.jobs)

    def in_transaction(self) -> bool:
        return False

    def begin(self) -> _Transaction:
        return _Transaction()


def _movie(movie_id: int) -> SimpleNamespace:
    return SimpleNamespace(id=movie_id, tmdb_id=10_000 + movie_id, title=f"Movie {movie_id}")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("enabled", "expected_types"),
    ((False, ["poster_pipeline"] * 5), (True, ["poster_pipeline_group"] * 3)),
)
async def test_movie_rollout_preserves_singles_or_builds_stable_group_chunks(
    monkeypatch: pytest.MonkeyPatch,
    enabled: bool,
    expected_types: list[str],
) -> None:
    captured: dict[str, object] = {}

    async def capture_batch(_session: object, **kwargs: object) -> SimpleNamespace:
        captured.update(kwargs)
        return SimpleNamespace(parent=SimpleNamespace(job_id="parent"))

    monkeypatch.setattr(
        movie_pipeline_routes.configuration_provider,
        "effective",
        lambda owner: {
            "POSTER_GROUP_ENABLED": enabled,
            "POSTER_GROUP_CHUNK_SIZE": 2,
        }
        if owner == "pipeline"
        else {},
    )
    monkeypatch.setattr(movie_pipeline_routes, "create_fixed_batch", capture_batch)
    monkeypatch.setattr(movie_pipeline_routes, "submission_response", lambda parent: parent)
    monkeypatch.setattr(
        movie_pipeline_routes,
        "uuid4",
        lambda: SimpleNamespace(hex="0123456789abcdef0123456789abcdef"),
    )

    movies = [_movie(movie_id) for movie_id in range(1, 6)]
    body = movie_pipeline_routes.BatchRunRequest(
        scope="selected",
        movie_ids=[5, 4, 3, 2, 1],
    )
    await movie_pipeline_routes.run_pipeline_batch(body, _MovieSession(movies))

    children = list(captured["children"])
    assert [child.job_type for child in children] == expected_types
    if not enabled:
        assert [child.request["movie_id"] for child in children] == [1, 2, 3, 4, 5]
        assert [child.subject.kind for child in children] == ["movie"] * 5
        return

    assert [child.request["chunk_index"] for child in children] == [0, 1, 2]
    assert [
        [member["movie_id"] for member in child.request["members"]]
        for child in children
    ] == [[1, 2], [3, 4], [5]]
    assert [child.subject.kind for child in children] == ["poster_subject_group"] * 3


def _series_asset(series_id: int) -> dict[str, int | str]:
    return {"media_type": "series", "series_id": series_id}


def _season_asset(series_id: int, season_id: int) -> dict[str, int | str]:
    return {"media_type": "season", "series_id": series_id, "season_id": season_id}


def test_tv_group_chunks_keep_a_nine_to_sixteen_member_show_intact() -> None:
    seasons = {
        season_id: SimpleNamespace(season_number=season_id)
        for season_id in range(1, 9)
    }
    assets = [_series_asset(1), *[_season_asset(1, season_id) for season_id in seasons]]

    chunks = _tv_group_chunks(assets, season_by_id=seasons, target_size=8)

    assert chunks == [assets]


def test_tv_group_chunks_greedily_pack_small_whole_show_groups() -> None:
    seasons = {
        11: SimpleNamespace(season_number=1),
        12: SimpleNamespace(season_number=2),
        21: SimpleNamespace(season_number=1),
        22: SimpleNamespace(season_number=2),
        23: SimpleNamespace(season_number=3),
        31: SimpleNamespace(season_number=1),
    }
    assets = [
        _series_asset(1),
        _season_asset(1, 11),
        _season_asset(1, 12),
        _series_asset(2),
        _season_asset(2, 21),
        _season_asset(2, 22),
        _season_asset(2, 23),
        _series_asset(3),
        _season_asset(3, 31),
    ]

    chunks = _tv_group_chunks(assets, season_by_id=seasons, target_size=8)

    assert [[asset["series_id"] for asset in chunk] for chunk in chunks] == [
        [1, 1, 1, 2, 2, 2, 2],
        [3, 3],
    ]


def test_tv_group_chunks_split_show_plus_seventeen_seasons_as_sixteen_plus_two() -> None:
    seasons = {
        100 + number: SimpleNamespace(season_number=number) for number in range(1, 18)
    }
    assets = [
        *[_season_asset(9, 100 + number) for number in range(17, 8, -1)],
        _series_asset(9),
        *[_season_asset(9, 100 + number) for number in range(8, 0, -1)],
    ]

    chunks = _tv_group_chunks(assets, season_by_id=seasons, target_size=8)

    assert [len(chunk) for chunk in chunks] == [16, 2]
    assert chunks[0][0] == _series_asset(9)
    ordered_numbers = [
        seasons[asset["season_id"]].season_number
        for chunk in chunks
        for asset in chunk
        if asset["media_type"] == "season"
    ]
    assert ordered_numbers == list(range(1, 18))


@pytest.mark.asyncio
async def test_active_tv_assets_expand_group_snapshot_member_wrappers() -> None:
    grouped = SimpleNamespace(
        type="poster_pipeline_group",
        subject_snapshot={
            "kind": "poster_subject_group",
            "members": [
                {
                    "subject_key": "series:41",
                    "subject": {"kind": "series", "series_id": 41},
                },
                {
                    "subject_key": "season:411",
                    "subject": {"kind": "season", "season_id": 411},
                },
                {"subject": {"kind": "season", "season_id": 412}},
            ],
        },
    )
    session = SimpleNamespace(execute=lambda _statement: None)

    async def execute(_statement: object) -> _Rows:
        return _Rows([grouped])

    session.execute = execute

    active = await _active_tv_asset_jobs(session)

    assert active == {
        ("series", 41): grouped,
        ("season", 411): grouped,
        ("season", 412): grouped,
    }


def _movie_group_request() -> dict[str, object]:
    return {
        "library": "movies",
        "chunk_index": 0,
        "members": [
            {"movie_id": 1, "tmdb_id": 101, "title": "One"},
            {"movie_id": 2, "tmdb_id": 102, "title": "Two"},
            {"movie_id": 3, "tmdb_id": 103, "title": "Three"},
        ],
    }


class _ProjectionSession:
    def __init__(self, projections: list[object]) -> None:
        self.projections = projections

    async def scalars(self, _statement: object) -> _ScalarRows:
        return _ScalarRows(self.projections)


@pytest.mark.asyncio
async def test_partial_group_retry_selects_only_failed_atomic_projections() -> None:
    group = SimpleNamespace(
        id="group-job",
        request=_movie_group_request(),
        outcome="partially_succeeded",
    )
    session = _ProjectionSession(
        [
            SimpleNamespace(subject_key="movie:1", status="completed"),
            SimpleNamespace(subject_key="movie:2", status="failed"),
            SimpleNamespace(subject_key="movie:3", status="flagged_manual"),
        ]
    )

    selected = await poster_group_retry.retryable_group_members(session, group_job=group)

    assert [member.movie_id for member in selected] == [2]


@pytest.mark.asyncio
@pytest.mark.parametrize("outcome", ("failed", "cancelled"))
async def test_systemic_group_retry_selects_all_members_with_zero_projection(
    outcome: str,
) -> None:
    group = SimpleNamespace(id="group-job", request=_movie_group_request(), outcome=outcome)

    selected = await poster_group_retry.retryable_group_members(
        _ProjectionSession([]),
        group_job=group,
    )

    assert [member.movie_id for member in selected] == [1, 2, 3]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("outcome", "projections"),
    (
        (
            "partially_succeeded",
            [SimpleNamespace(subject_key="movie:1", status="failed")],
        ),
        ("failed", [SimpleNamespace(subject_key="movie:1", status="failed")]),
    ),
)
async def test_group_retry_rejects_intermediate_projection_counts(
    outcome: str,
    projections: list[object],
) -> None:
    group = SimpleNamespace(id="group-job", request=_movie_group_request(), outcome=outcome)

    with pytest.raises(SubmissionInvariantError):
        await poster_group_retry.retryable_group_members(
            _ProjectionSession(projections),
            group_job=group,
        )


def test_group_member_intents_flatten_to_ordered_single_subject_leaves() -> None:
    request = PosterPipelineGroupRequestV1.model_validate(
        {
            "library": "tv",
            "chunk_index": 4,
            "members": [
                {"series_id": 8, "tmdb_id": 80, "title": "Show"},
                {"season_id": 81, "tmdb_id": 80, "title": "Show · Season 1"},
            ],
        }
    )

    intents = poster_group_retry.group_member_intents(
        request.members,
        source_job=SimpleNamespace(priority=73),
        initiator=None,
        key_prefix="retry-group",
    )

    assert [intent.job_type for intent in intents] == ["poster_pipeline", "poster_pipeline"]
    assert [(intent.subject.kind, intent.subject.reference) for intent in intents] == [
        ("series", "8"),
        ("season", "81"),
    ]
    assert [intent.priority for intent in intents] == [73, 73]
    assert [intent.request["title"] for intent in intents] == ["Show", "Show · Season 1"]


def test_partial_grouped_parent_is_retryable_through_batch_flattening() -> None:
    definition = JOB_DEFINITION_REGISTRY.get("poster_pipeline_batch")
    parent = SimpleNamespace(
        type="poster_pipeline_batch",
        phase="terminal",
        outcome="partially_succeeded",
        result=None,
    )

    capability = resolve_retry_capability(parent, definition)

    assert capability.available is True
    assert capability.mode.value == "domain_coordinated"


def test_review_only_partial_group_is_not_selected_for_parent_retry() -> None:
    child = SimpleNamespace(
        type="poster_pipeline_group",
        outcome="partially_succeeded",
        result={"failed_count": 0},
    )

    assert job_batches._retryable_batch_child(child) is False


class _RetrySession:
    def __init__(
        self,
        *,
        projection: object | None = None,
        children: tuple[object, ...] = (),
        jobs: dict[str, object] | None = None,
    ) -> None:
        self.projection = projection
        self.children = children
        self.jobs = jobs or {}
        self.flushed = False

    async def scalar(self, _statement: object) -> object | None:
        return self.projection

    async def scalars(self, _statement: object) -> _ScalarRows:
        return _ScalarRows(self.children)

    async def get(self, _model: object, identity: str) -> object | None:
        return self.jobs.get(identity)

    async def flush(self) -> None:
        self.flushed = True


@pytest.mark.asyncio
async def test_direct_group_retry_creates_fixed_parent_of_single_leaves(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request = PosterPipelineGroupRequestV1.model_validate(_movie_group_request())
    original = SimpleNamespace(
        id="group-original",
        request=_movie_group_request(),
        priority=61,
        initiator=None,
    )
    replacement = SimpleNamespace(id="retry-parent", outcome=None, phase="queued")
    successors = [SimpleNamespace(id=f"retry-child-{index}") for index in range(3)]
    session = _RetrySession(
        jobs={
            replacement.id: replacement,
            **{successor.id: successor for successor in successors},
        }
    )
    captured: dict[str, object] = {}

    async def create_fixed(_session: object, **kwargs: object) -> SimpleNamespace:
        captured.update(kwargs)
        return SimpleNamespace(
            parent=SimpleNamespace(job_id=replacement.id),
            children=tuple(
                SimpleNamespace(job_id=successor.id) for successor in successors
            ),
        )

    async def append_event(*_args: object, **_kwargs: object) -> None:
        return None

    async def select_members(_session: object, *, group_job: object):
        assert group_job is original
        return request.members

    monkeypatch.setattr(job_batches, "create_fixed_batch", create_fixed)
    monkeypatch.setattr(poster_group_retry, "retryable_group_members", select_members)
    monkeypatch.setattr(job_control.job_event_writer, "append", append_event)

    result = await job_control._retry_poster_group(
        session,
        original=original,
        expected_fence_token=7,
    )

    assert result is replacement
    assert captured["parent_job_type"] == "poster_pipeline_batch"
    children = list(captured["children"])
    assert [child.job_type for child in children] == ["poster_pipeline"] * 3
    assert [child.subject.reference for child in children] == ["1", "2", "3"]
    assert replacement.retry_of_job_id == original.id
    assert [successor.retry_of_job_id for successor in successors] == [original.id] * 3
    assert session.flushed is True


@pytest.mark.asyncio
async def test_fixed_parent_retry_flattens_group_and_preserves_group_lineage(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request = PosterPipelineGroupRequestV1.model_validate(_movie_group_request())
    original = SimpleNamespace(
        id="batch-original",
        type="poster_pipeline_batch",
        phase="terminal",
        request={"scope": "selected", "selection_count": 1},
        priority=55,
        initiator=None,
        subject_reference="batch-scope",
        subject_snapshot={"display_name": "Movie batch", "scope_summary": "three"},
        trigger_kind="batch",
    )
    grouped_child = SimpleNamespace(
        id="group-child",
        type="poster_pipeline_group",
        outcome="partially_succeeded",
        result={"failed_count": 1},
        priority=44,
    )
    replacement = SimpleNamespace(id="batch-retry", outcome=None, phase="queued")
    successors = [SimpleNamespace(id=f"single-{index}") for index in range(3)]
    session = _RetrySession(
        projection=SimpleNamespace(mode="fixed"),
        children=(grouped_child,),
        jobs={
            replacement.id: replacement,
            **{successor.id: successor for successor in successors},
        },
    )
    captured: dict[str, object] = {}

    async def create_fixed(_session: object, **kwargs: object) -> SimpleNamespace:
        captured.update(kwargs)
        return SimpleNamespace(
            parent=SimpleNamespace(job_id=replacement.id),
            children=tuple(
                SimpleNamespace(job_id=successor.id) for successor in successors
            ),
        )

    async def append_event(*_args: object, **_kwargs: object) -> None:
        return None

    async def select_members(_session: object, *, group_job: object):
        assert group_job is grouped_child
        return request.members

    monkeypatch.setattr(job_batches, "create_fixed_batch", create_fixed)
    monkeypatch.setattr(poster_group_retry, "retryable_group_members", select_members)
    monkeypatch.setattr(job_batches.job_event_writer, "append", append_event)

    result = await job_batches.retry_batch(
        session,
        original=original,
        expected_fence_token=9,
    )

    assert result.job_id == replacement.id
    assert captured["parent_request"] == {"scope": "selected", "selection_count": 3}
    children = list(captured["children"])
    assert [child.job_type for child in children] == ["poster_pipeline"] * 3
    assert replacement.retry_of_job_id == original.id
    assert [successor.retry_of_job_id for successor in successors] == [grouped_child.id] * 3
