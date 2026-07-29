from __future__ import annotations

import hashlib
import json
from contextlib import asynccontextmanager
from types import SimpleNamespace

import numpy as np
import pytest
from PIL import Image

from marquee.core.jobs.documents import PosterPipelineRequestV1
from marquee.core.jobs.internal_runner import (
    _bounded_poster_group_members,
    _poster_feature_runtime,
    _poster_group_subject,
    _run_poster_group,
)
from marquee.core.jobs.poster_pipeline import _subject_params
from marquee.core.jobs.runner_progress import RunnerProgressBridge
from marquee.core.poster_sources.tmdb import PosterCandidate
from marquee.core.text_profiles import OcrGateContext
from marquee.pipeline.features import FeatureExtractor
from marquee.pipeline.poster_group_runner import (
    GroupProgressEvent,
    PosterGroupMemberInput,
    PosterGroupMemberOutput,
    PosterGroupOutput,
    _detail_union,
    _MemberState,
    _ocr_union,
    materialize_group_output,
    run_poster_group,
)
from marquee.pipeline.runner import FetchOutcome, run_sync_stages
from marquee.pipeline.scorer import ResidualCompatibilityError
from marquee.pipeline.types import CandidateScore, FeatureVector, OCRCandidateResult


def _feature() -> FeatureVector:
    return FeatureVector(
        knn_sim=0.5,
        aesthetic=0.5,
        title_colorfulness=0.5,
        text_residual=0.0,
        resolution=1.0,
        sharpness=0.5,
        face_area=0.0,
        provenance=0.5,
        lang_match=1.0,
    )


def test_group_subject_uses_bare_series_title_for_season_ocr() -> None:
    key, subject, _gate, run_id = _poster_group_subject(
        {
            "subject_key": "season:8",
            "run_id": "run-season-8",
            "request": {
                "season_id": 8,
                "tmdb_id": 99,
                "title": "Andor · Season 2",
            },
            "subject": {
                "series_id": 7,
                "series_title": "Andor",
                "season_number": 2,
            },
        }
    )
    assert key == "season:8"
    assert run_id == "run-season-8"
    assert subject.title == "Andor · Season 2"
    assert subject.ocr_title == "Andor"


def test_group_subject_accepts_host_serialized_season_identity() -> None:
    request = PosterPipelineRequestV1(
        season_id=8,
        tmdb_id=99,
        title="Andor · Season 2",
    )
    serialized = _subject_params(
        request,
        {
            "kind": "season",
            "season_id": 8,
            "series_id": 7,
            "series_title": "Andor",
            "season_number": 2,
        },
    )

    key, subject, _gate, _run_id = _poster_group_subject(
        {
            "subject_key": "season:8",
            "run_id": "run-season-8",
            "request": request.model_dump(mode="json", exclude_none=True),
            "subject": serialized,
        }
    )

    assert key == "season:8"
    assert subject.series_id == 7
    assert subject.season_id == 8
    assert subject.season_number == 2
    assert subject.tmdb_id == 99
    assert subject.title == "Andor · Season 2"
    assert subject.ocr_title == "Andor"


def test_single_season_collecting_keeps_display_title_outside_ocr(
    tmp_path, monkeypatch
) -> None:
    from marquee.pipeline import runner

    paths = []
    candidates = {}
    records = {}
    resolutions = {}
    for index in range(2):
        path = tmp_path / f"poster-{index}.png"
        Image.new("RGB", (900, 1350), color=(20 + index, 30, 40)).save(path)
        paths.append(path)
        candidates[path.name] = PosterCandidate(
            file_path=f"/{path.name}",
            width=900,
            height=1350,
            aspect_ratio=2 / 3,
            language=None,
            vote_average=0.0,
            vote_count=0,
        )
        records[path.name] = CandidateScore(image_path=path, orig_filename=path.name)
        resolutions[path.name] = (900, 1350)

    observed = {}

    def no_dedup(_self, items):
        return SimpleNamespace(survivors=list(items), removals=[], final=len(items))

    def fake_ocr(text_filter, items, **_kwargs):
        observed["ocr_title"] = text_filter.title
        return [OCRCandidateResult(path, True, "ANDOR", None, None) for path in items]

    def neutral_order(items, *, movie_title, candidate_map):
        del candidate_map
        observed["order_title"] = movie_title
        for rank, item in enumerate(items, 1):
            item.rank = rank
        return items

    class Extractor:
        @staticmethod
        def extract_style_batch(items, **_kwargs):
            return [_feature() for _item in items]

        @staticmethod
        def complete_batch(items, **_kwargs):
            return [features for features, _ocr in items]

    class Gate:
        @staticmethod
        def evaluate_metadata(**_kwargs):
            return SimpleNamespace(passed=True, reason=None, detail=None)

        @staticmethod
        def evaluate_style(*_args, **_kwargs):
            return SimpleNamespace(passed=True, reason=None, detail=None)

        @staticmethod
        def evaluate_detail(*_args, **_kwargs):
            return SimpleNamespace(passed=True, reason=None, detail=None)

    monkeypatch.setattr(runner.PosterDeduper, "deduplicate", no_dedup)
    monkeypatch.setattr(runner.PosterTextFilter, "filter_batch", fake_ocr)
    monkeypatch.setattr(runner, "PosterGate", Gate)
    monkeypatch.setattr(runner, "_neutral_candidate_order", neutral_order)

    result = run_sync_stages(
        movie_title="Andor · Season 2",
        ocr_title="Andor",
        out_dir=tmp_path,
        records=records,
        candidate_map=candidates,
        all_files=paths,
        resolution_by_name=resolutions,
        timings={},
        feature_extractor=Extractor(),
        personalization_mode="collecting",
    )

    assert observed == {"ocr_title": "andor", "order_title": "Andor · Season 2"}
    assert len(result.ranked) == 2


def test_union_ocr_keeps_colliding_series_and_season_ids_isolated(
    tmp_path, monkeypatch
) -> None:
    from marquee.pipeline.orchestrator import PosterSubjectInput

    contexts = []
    for index, subject in enumerate(
        (
            PosterSubjectInput(
                title="Andor",
                media_type="series",
                tmdb_id=99,
                series_id=7,
            ),
            PosterSubjectInput(
                title="Andor · Season 2",
                media_type="season",
                tmdb_id=99,
                series_id=7,
                season_id=7,
                season_number=2,
                ocr_title="Andor",
            ),
        )
    ):
        out_dir = tmp_path / f"s{index:03d}"
        out_dir.mkdir()
        (out_dir / "errored").mkdir()
        image_path = out_dir / f"poster-{index}.jpg"
        image_path.write_bytes(b"not-read-by-fake-ocr")
        record = CandidateScore(image_path=image_path, orig_filename=image_path.name)
        member = PosterGroupMemberInput(
            subject_key=f"member:{index}",
            subject=subject,
            ocr_gate=OcrGateContext(),
            run_id=f"run-{index}",
        )
        context = _MemberState(member=member, index=index, out_dir=out_dir)
        context.fetch = FetchOutcome({}, {image_path.name: record}, {}, [image_path], None, {})
        context.style_survivors = [image_path]
        contexts.append(context)

    calls = []

    pool = object()

    def fake_ocr(actual_pool, items, **_kwargs):
        assert actual_pool is pool
        calls.append(items)
        return [
            OCRCandidateResult(
                image_path=item[0],
                accepted=True,
                detected_text="ANDOR",
                reason=None,
                title_bbox=None,
            )
            for item in items
        ]

    monkeypatch.setattr(
        "marquee.pipeline.poster_group_runner.PosterTextFilter.run_ocr_tasks", fake_ocr
    )
    monkeypatch.setattr(
        "marquee.pipeline.poster_group_runner.PosterTextFilter.run_ocr_batch",
        lambda *_args, **_kwargs: pytest.fail("ready group OCR pool must be reused"),
    )
    _ocr_union(contexts, None, pool=pool)

    assert len(calls) == 1
    assert [item[1] for item in calls[0]] == ["andor", "andor"]
    assert [len(context.ocr_survivors) for context in contexts] == [1, 1]


def test_ocr_postprocessing_failure_is_member_local(tmp_path, monkeypatch) -> None:
    from marquee.pipeline import poster_group_runner
    from marquee.pipeline.orchestrator import PosterSubjectInput

    contexts = []
    for index, name in enumerate(("bad.jpg", "good.jpg")):
        out_dir = tmp_path / f"s{index:03d}"
        out_dir.mkdir()
        (out_dir / "errored").mkdir()
        path = out_dir / name
        path.write_bytes(b"poster")
        context = _MemberState(
            member=PosterGroupMemberInput(
                subject_key=f"movie:{index + 1}",
                subject=PosterSubjectInput(title="Example", movie_id=index + 1, tmdb_id=10),
                ocr_gate=OcrGateContext(),
            ),
            index=index,
            out_dir=out_dir,
        )
        context.fetch = FetchOutcome(
            {},
            {name: CandidateScore(path, name, features=_feature())},
            {},
            [path],
            None,
            {},
        )
        context.style_survivors = [path]
        contexts.append(context)

    monkeypatch.setattr(
        poster_group_runner.PosterTextFilter,
        "run_ocr_batch",
        lambda items, **_kwargs: [
            OCRCandidateResult(item[0], True, "EXAMPLE", None, None) for item in items
        ],
    )
    real_attach = poster_group_runner._attach_ocr_diagnostics

    def attach(record, result):
        if record.orig_filename == "bad.jpg":
            raise OSError("bad member diagnostics")
        real_attach(record, result)

    monkeypatch.setattr(poster_group_runner, "_attach_ocr_diagnostics", attach)

    _ocr_union(contexts, None)

    assert contexts[0].status == "failed"
    assert contexts[1].status == "running"
    assert [result.image_path.name for result in contexts[1].ocr_survivors] == ["good.jpg"]


def test_dino_preprocessing_is_streamed_in_effective_slices(tmp_path, monkeypatch) -> None:
    paths = []
    for index in range(5):
        path = tmp_path / f"poster-{index}.png"
        Image.new("RGB", (32, 48), color=(index, index, index)).save(path)
        paths.append(path)

    batch_sizes = []

    class Encoder:
        def encode_batch(self, images):
            batch_sizes.append(len(images))
            return np.stack(
                [np.asarray([float(index), 1.0], dtype=np.float32) for index in range(len(images))]
            )

    class Taste:
        @staticmethod
        def dino_style_score(vector, *, k):
            return float(vector[0] + k)

    fake = SimpleNamespace(
        _dino_on=True,
        taste_store=Taste(),
        dino_encoder=Encoder(),
        config=SimpleNamespace(K_NEIGHBORS=3),
        _complete_one=lambda features, _ocr, *, dino_knn: (features, dino_knn),
    )
    items = [
        (
            _feature(),
            OCRCandidateResult(path, True, "TITLE", None, None),
        )
        for path in paths
    ]
    vectors = {}
    monkeypatch.setattr("marquee.pipeline.features.effective_clip_batch_size", lambda: 2)

    results = FeatureExtractor.complete_batch(fake, items, dino_vectors_out=vectors)

    assert batch_sizes == [2, 2, 1]
    assert len(results) == 5
    assert list(vectors) == [0, 1, 2, 3, 4]


def test_contained_feature_cache_is_memory_only_and_preserves_stack_vector(tmp_path) -> None:
    extractor = FeatureExtractor.__new__(FeatureExtractor)
    extractor.config = SimpleNamespace(
        AI_MODEL="clip-test",
        EMBEDDING_CACHE_DIR=tmp_path / "global-cache-must-stay-empty",
    )
    extractor.embedding_cache_enabled = False
    extractor._memory_embeddings = {}
    vector = np.asarray([0.25, 0.75], dtype=np.float32)

    extractor._save_cached_embedding("poster.jpg", vector)

    assert np.array_equal(extractor.load_run_embedding("poster.jpg"), vector)
    assert not extractor.config.EMBEDDING_CACHE_DIR.exists()


def test_internal_poster_feature_runtime_disables_disk_cache(monkeypatch) -> None:
    from marquee.pipeline import features

    constructed = []

    class Extractor:
        def __init__(self, **kwargs):
            constructed.append(kwargs)

    monkeypatch.setattr(features, "FeatureExtractor", Extractor)

    extractor, residual = _poster_feature_runtime("collecting")

    assert isinstance(extractor, Extractor)
    assert residual is None
    assert constructed == [
        {"personalization_mode": "collecting", "embedding_cache_enabled": False}
    ]


def test_group_outputs_are_flat_and_group_report_is_authoritative(tmp_path) -> None:
    member_dir = tmp_path / "s000"
    member_dir.mkdir()
    image_path = member_dir / "poster.jpg"
    image_path.write_bytes(b"poster")
    member = PosterGroupMemberOutput(
        subject_key="movie:1",
        run_id="run-1",
        status="completed",
        counts={"ranked": 1},
        source_count=1,
        candidate_count=1,
        recommendation={"orig_filename": "poster.jpg", "rank": 1},
        scorer_name="weighted",
        personalization_mode="personalized",
        payload={
            "diagnostic_ledger": {
                "candidates": [
                    {"orig_filename": "poster.jpg", "image_path": str(image_path)}
                ]
            },
            "review": {"survivors": [{"reference": "poster.jpg"}]},
        },
        member_index=0,
        title="Movie",
    )
    group = PosterGroupOutput(library="movies", chunk_index=3, members=[member])

    produced = materialize_group_output(group, tmp_path)

    assert produced == [
        "group-result.json",
        "run-000.json",
        "s000-candidate-000.jpg",
    ]
    assert all("/" not in key for key in produced)
    report = json.loads((tmp_path / "group-result.json").read_text())
    assert report["members"][0]["archive_file"] == "run-000.json"
    assert report["members"][0]["candidate_files"] == {
        "poster.jpg": "s000-candidate-000.jpg"
    }
    assert report["members"][0]["member_index"] == 0
    assert report["members"][0]["title"] == "Movie"
    assert report["members"][0]["timings"] == {}
    assert "duration_seconds" in report["members"][0]


def test_missing_member_survivor_artifact_uses_group_fallback_without_stopping_sibling(
    tmp_path,
) -> None:
    good_dir = tmp_path / "s001"
    good_dir.mkdir()
    good_image = good_dir / "good.jpg"
    good_image.write_bytes(b"poster")

    def member(index, *, image_path):
        reference = f"member-{index}.jpg"
        return PosterGroupMemberOutput(
            subject_key=f"movie:{index + 1}",
            run_id=f"run-{index}",
            status="completed",
            counts={"ranked": 1},
            source_count=1,
            candidate_count=1,
            recommendation={"orig_filename": reference, "rank": 1},
            scorer_name="weighted",
            personalization_mode="personalized",
            payload={
                "diagnostic_ledger": {
                    "candidates": [
                        {"orig_filename": reference, "image_path": str(image_path)}
                    ]
                },
                "review": {"survivors": [{"reference": reference}]},
            },
            member_index=index,
            title=f"Movie {index}",
        )

    bad = member(0, image_path=tmp_path / "s000" / "missing.jpg")
    good = member(1, image_path=good_image)
    group = PosterGroupOutput(library="movies", chunk_index=0, members=[bad, good])

    produced = materialize_group_output(group, tmp_path)

    assert produced == [
        "group-result.json",
        "run-001.json",
        "s001-candidate-000.jpg",
    ]
    assert bad.status == "failed"
    assert bad.archive_file is None
    assert good.status == "completed"
    assert good.archive_file == "run-001.json"
    report = json.loads((tmp_path / "group-result.json").read_text())
    assert [item["status"] for item in report["members"]] == ["failed", "completed"]
    assert report["failed_subject_keys"] == ["movie:1"]


@pytest.mark.asyncio
async def test_runner_progress_subject_becomes_current_scope_label() -> None:
    observations = []

    class Progress:
        definition = SimpleNamespace(
            progress_policy=SimpleNamespace(
                stages=(("fetch", "Fetch"),),
                overall_unit="stages",
            )
        )

        async def observe(self, stage, **kwargs):
            observations.append((stage, kwargs))

    bridge = RunnerProgressBridge(Progress(), stage_map={"fetch": "fetch"})
    await bridge.on_frame(
        {
            "type": "progress",
            "stage": "fetch",
            "state": "progress",
            "scope": "m03",
            "subject": "Andor · Season 2",
            "done": 1,
            "total": 4,
            "cursor": 1,
        }
    )

    assert observations[0][1]["current"].label == "Andor · Season 2"
    assert observations[0][1]["current"].scope_key == "fetch:m03"


@pytest.mark.asyncio
@pytest.mark.parametrize("personalization_mode", ["collecting", "personalized"])
async def test_one_member_group_matches_single_fixture_modes(
    tmp_path, monkeypatch, personalization_mode
) -> None:
    from marquee.pipeline import orchestrator, poster_group_runner, runner

    class Gate:
        @staticmethod
        def evaluate_metadata(**_kwargs):
            return SimpleNamespace(passed=True, reason=None, detail=None)

        @staticmethod
        def evaluate_style(*_args, **_kwargs):
            return SimpleNamespace(passed=True, reason=None, detail=None)

        @staticmethod
        def evaluate_detail(*_args, **_kwargs):
            return SimpleNamespace(passed=True, reason=None, detail=None)

    class Extractor:
        def __init__(self):
            self.personalization_mode = personalization_mode

        @staticmethod
        def extract_style_batch(items, **_kwargs):
            return [_feature() for _item in items]

        @staticmethod
        def complete_batch(items, **_kwargs):
            return [features for features, _ocr in items]

    class Scorer:
        name = "weighted"

        @staticmethod
        def score(_features):
            return 0.75, {"aesthetic": 0.75}

        @staticmethod
        def rank(records):
            for rank, record in enumerate(records, 1):
                record.rank = rank
                record.final_score = 0.75
                record.contributions = {"aesthetic": 0.75}
            return records

    def ocr_results(paths):
        return [
            OCRCandidateResult(path, True, "EXAMPLE", None, None)
            for path in paths
        ]

    def fake_filter(self, paths, **_kwargs):
        del self
        return ocr_results(paths)

    def fake_union(items, **_kwargs):
        return ocr_results([item[0] for item in items])

    monkeypatch.setattr(runner, "PosterGate", Gate)
    monkeypatch.setattr(poster_group_runner, "PosterGate", Gate)
    monkeypatch.setattr(poster_group_runner.settings, "TMDB_READ_ACCESS_TOKEN", "token")

    @asynccontextmanager
    async def no_ocr_preload(*, enabled):
        assert enabled is True

        async def resolve():
            return None

        yield resolve

    monkeypatch.setattr(poster_group_runner, "_preloaded_ocr_pool", no_ocr_preload)
    monkeypatch.setattr(runner.pipeline_settings, "STACK_ENABLED", False)
    monkeypatch.setattr(runner, "select_scorer", lambda **_kwargs: Scorer())
    monkeypatch.setattr(orchestrator, "select_scorer", lambda **_kwargs: Scorer())
    monkeypatch.setattr(poster_group_runner, "select_scorer", lambda **_kwargs: Scorer())

    async def fake_place_outputs(*_args, **_kwargs):
        return None

    monkeypatch.setattr(orchestrator, "place_outputs", fake_place_outputs)
    monkeypatch.setattr(poster_group_runner, "place_outputs", fake_place_outputs)
    monkeypatch.setattr(runner.PosterTextFilter, "filter_batch", fake_filter)
    monkeypatch.setattr(
        poster_group_runner.PosterTextFilter,
        "run_ocr_batch",
        staticmethod(fake_union),
    )

    source_image = tmp_path / "source.png"
    Image.new("RGB", (900, 1350), color=(20, 30, 40)).save(source_image)
    single_dir = tmp_path / "single"
    fixture_dir = single_dir / "candidates"
    fixture_dir.mkdir(parents=True)
    (fixture_dir / source_image.name).write_bytes(source_image.read_bytes())
    subject = orchestrator.PosterSubjectInput(
        title="Example",
        movie_id=1,
        tmdb_id=2,
    )
    single = await orchestrator.run_poster_pipeline(
        subject=subject,
        source=orchestrator.PosterSourceInput(mode="fixture"),
        out_dir=single_dir,
        feature_extractor=Extractor(),
        personalization_mode=personalization_mode,
    )

    async def fake_fetch(contexts, _progress):
        context = contexts[0]
        context.out_dir.mkdir()
        destination = context.out_dir / source_image.name
        destination.write_bytes(source_image.read_bytes())
        candidate = PosterCandidate(
            file_path=f"/{source_image.name}",
            width=900,
            height=1350,
            aspect_ratio=2 / 3,
            language=None,
            vote_average=0.0,
            vote_count=0,
        )
        context.fetch = FetchOutcome(
            candidate_map={destination.name: candidate},
            records={
                destination.name: CandidateScore(
                    image_path=destination,
                    orig_filename=destination.name,
                )
            },
            resolution_by_name={destination.name: (900, 1350)},
            all_files=[destination],
            primary_name=None,
            counts={
                "posters_found": 1,
                "downloaded": 0,
                "skipped": 1,
                "errors": 0,
                "metadata_gated": 0,
            },
        )
        context.counts.update(context.fetch.counts)

    monkeypatch.setattr(poster_group_runner, "_fetch_all", fake_fetch)
    group_dir = tmp_path / "group"
    group_dir.mkdir()
    group = await run_poster_group(
        library="movies",
        chunk_index=0,
        members=[
            PosterGroupMemberInput(
                subject_key="movie:1",
                subject=subject,
                ocr_gate=OcrGateContext(),
                run_id=single.run_id,
            )
        ],
        out_dir=group_dir,
        feature_extractor=Extractor(),
        personalization_mode=personalization_mode,
    )
    grouped = group.members[0]

    assert grouped.status == single.status
    assert grouped.counts == single.counts
    assert grouped.recommendation == single.recommendation
    assert grouped.scorer_name == single.scorer_name
    assert grouped.payload["review"]["order_algorithm"] == single.payload["review"][
        "order_algorithm"
    ]


def test_shared_ocr_failure_is_systemic(tmp_path, monkeypatch) -> None:
    from marquee.pipeline.orchestrator import PosterSubjectInput

    out_dir = tmp_path / "s000"
    out_dir.mkdir()
    (out_dir / "errored").mkdir()
    image_path = out_dir / "poster.jpg"
    image_path.write_bytes(b"poster")
    context = _MemberState(
        member=PosterGroupMemberInput(
            subject_key="movie:1",
            subject=PosterSubjectInput(title="Example", movie_id=1, tmdb_id=2),
            ocr_gate=OcrGateContext(),
        ),
        index=0,
        out_dir=out_dir,
    )
    context.fetch = FetchOutcome(
        {},
        {image_path.name: CandidateScore(image_path, image_path.name, features=_feature())},
        {},
        [image_path],
        None,
        {},
    )
    context.style_survivors = [image_path]
    monkeypatch.setattr(
        "marquee.pipeline.poster_group_runner.PosterTextFilter.run_ocr_batch",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("worker init failed")),
    )

    with pytest.raises(RuntimeError, match="worker init failed"):
        _ocr_union([context], None)


def test_shared_dino_failure_is_systemic(tmp_path) -> None:
    from marquee.pipeline.orchestrator import PosterSubjectInput

    out_dir = tmp_path / "s000"
    out_dir.mkdir()
    (out_dir / "errored").mkdir()
    image_path = out_dir / "poster.jpg"
    image_path.write_bytes(b"poster")
    context = _MemberState(
        member=PosterGroupMemberInput(
            subject_key="movie:1",
            subject=PosterSubjectInput(title="Example", movie_id=1, tmdb_id=2),
            ocr_gate=OcrGateContext(),
        ),
        index=0,
        out_dir=out_dir,
    )
    context.fetch = FetchOutcome(
        {},
        {image_path.name: CandidateScore(image_path, image_path.name, features=_feature())},
        {},
        [image_path],
        None,
        {},
    )
    context.ocr_survivors = [OCRCandidateResult(image_path, True, "TITLE", None, None)]
    extractor = SimpleNamespace(
        complete_batch=lambda *_args, **_kwargs: (_ for _ in ()).throw(
            RuntimeError("onnx session failed")
        )
    )

    with pytest.raises(RuntimeError, match="onnx session failed"):
        _detail_union(
            [context],
            extractor,
            residual_path=None,
            residual_context=None,
            personalization_mode="collecting",
            progress=None,
        )


def test_detail_postprocessing_failure_is_member_local(tmp_path, monkeypatch) -> None:
    from marquee.pipeline import poster_group_runner
    from marquee.pipeline.orchestrator import PosterSubjectInput

    contexts = []
    for index, name in enumerate(("bad.jpg", "good.jpg")):
        out_dir = tmp_path / f"s{index:03d}"
        out_dir.mkdir()
        (out_dir / "errored").mkdir()
        path = out_dir / name
        path.write_bytes(b"poster")
        context = _MemberState(
            member=PosterGroupMemberInput(
                subject_key=f"movie:{index + 1}",
                subject=PosterSubjectInput(title="Example", movie_id=index + 1, tmdb_id=10),
                ocr_gate=OcrGateContext(),
            ),
            index=index,
            out_dir=out_dir,
        )
        context.fetch = FetchOutcome(
            {},
            {name: CandidateScore(path, name, features=_feature())},
            {},
            [path],
            None,
            {},
        )
        context.ocr_survivors = [OCRCandidateResult(path, True, "EXAMPLE", None, None)]
        contexts.append(context)

    extractor = SimpleNamespace(
        complete_batch=lambda *_args, **_kwargs: [OSError("bad candidate"), _feature()]
    )
    monkeypatch.setattr(
        poster_group_runner,
        "_copy_with_reason",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("bad member copy")),
    )

    _detail_union(
        contexts,
        extractor,
        residual_path=None,
        residual_context=None,
        personalization_mode="collecting",
        progress=None,
    )

    assert contexts[0].status == "failed"
    assert contexts[1].status == "running"
    assert [record.orig_filename for record in contexts[1].passed] == ["good.jpg"]


def test_forced_residual_score_failure_is_systemic(tmp_path, monkeypatch) -> None:
    from marquee.pipeline import poster_group_runner
    from marquee.pipeline.orchestrator import PosterSubjectInput

    out_dir = tmp_path / "s000"
    out_dir.mkdir()
    (out_dir / "errored").mkdir()
    path = out_dir / "poster.jpg"
    path.write_bytes(b"poster")
    context = _MemberState(
        member=PosterGroupMemberInput(
            subject_key="movie:1",
            subject=PosterSubjectInput(title="Example", movie_id=1, tmdb_id=10),
            ocr_gate=OcrGateContext(),
        ),
        index=0,
        out_dir=out_dir,
    )
    context.fetch = FetchOutcome(
        {},
        {path.name: CandidateScore(path, path.name, features=_feature())},
        {},
        [path],
        None,
        {},
    )
    context.ocr_survivors = [OCRCandidateResult(path, True, "EXAMPLE", None, None)]
    extractor = SimpleNamespace(complete_batch=lambda *_args, **_kwargs: [_feature()])
    scorer = SimpleNamespace(
        score=lambda _features: (_ for _ in ()).throw(RuntimeError("schema mismatch"))
    )
    monkeypatch.setattr(poster_group_runner, "select_scorer", lambda **_kwargs: scorer)
    monkeypatch.setattr(poster_group_runner.pipeline_settings, "SCORER", "residual")

    with pytest.raises(ResidualCompatibilityError, match="forced residual"):
        _detail_union(
            [context],
            extractor,
            residual_path=tmp_path / "residual.npz",
            residual_context=SimpleNamespace(),
            personalization_mode="personalized",
            progress=None,
        )


def test_empty_detail_union_does_not_load_shared_scorer(tmp_path, monkeypatch) -> None:
    from marquee.pipeline import poster_group_runner
    from marquee.pipeline.orchestrator import PosterSubjectInput

    context = _MemberState(
        member=PosterGroupMemberInput(
            subject_key="movie:1",
            subject=PosterSubjectInput(title="Example", movie_id=1, tmdb_id=10),
            ocr_gate=OcrGateContext(),
        ),
        index=0,
        out_dir=tmp_path,
    )
    monkeypatch.setattr(
        poster_group_runner,
        "select_scorer",
        lambda **_kwargs: pytest.fail("empty union must not load a scorer"),
    )

    scorer = _detail_union(
        [context],
        SimpleNamespace(),
        residual_path=tmp_path / "missing.npz",
        residual_context=SimpleNamespace(),
        personalization_mode="personalized",
        progress=None,
    )

    assert scorer is None
    assert context.counts["feature_survivors"] == 0


def test_max_group_member_summary_stays_below_result_frame_bound() -> None:
    members = []
    for member_index in range(16):
        members.append(
            PosterGroupMemberOutput(
                subject_key=f"movie:{member_index + 1}",
                run_id=f"run-{member_index}",
                status="completed",
                counts={"ranked": 100},
                source_count=100,
                candidate_count=100,
                recommendation=None,
                scorer_name="weighted",
                personalization_mode="personalized",
                payload={},
                member_index=member_index,
                title="\U0001f4fa" * 1_000,
                error="\U0001f4a5" * 10_000,
                warnings=["\u26a0\ufe0f" * 10_000 for _ in range(20)],
                candidate_files={
                    f"{'r' * 180}-{candidate_index}": (
                        f"s{member_index:03d}-candidate-{candidate_index:03d}.jpg"
                    )
                    for candidate_index in range(100)
                },
            )
        )

    summary = {
        "library": "movies",
        "chunk_index": 0,
        "outcome": "succeeded",
        "member_count": len(members),
        "succeeded_count": len(members),
        "no_change_count": 0,
        "review_required_count": 0,
        "failed_count": 0,
        "run_ids": [member.run_id for member in members],
        "failed_subject_keys": [],
        "members": _bounded_poster_group_members(members),
        "group_result_file": "group-result.json",
    }
    assert all("title" not in member for member in summary["members"])
    assert [member["member_index"] for member in summary["members"]] == list(range(16))
    assert set(summary["members"][0]) == {
        "member_index",
        "subject_key",
        "run_id",
        "status",
        "outcome",
        "archive_file",
    }
    assert len(json.dumps(summary, separators=(",", ":")).encode("utf-8")) < 64 * 1024


def test_internal_group_operation_authenticates_reference_and_emits_monotonic_progress(
    tmp_path, monkeypatch
) -> None:
    from marquee.core.jobs import internal_runner
    from marquee.pipeline import poster_group_runner

    document = {
        "version": 1,
        "library": "movies",
        "chunk_index": 0,
        "members": [
            {
                "subject_key": "movie:1",
                "run_id": "planned-run-1",
                "request": {"movie_id": 1, "tmdb_id": 2, "title": "Example"},
                "subject": {
                    "movie_id": 1,
                    "tmdb_id": 2,
                    "title": "Example",
                    "media_type": "movie",
                },
                "text_gate": {"scope": "movie"},
            }
        ],
    }
    payload = json.dumps(document, separators=(",", ":"), sort_keys=True).encode()
    (tmp_path / "group.json").write_bytes(payload)
    monkeypatch.chdir(tmp_path)

    preflights = []
    monkeypatch.setattr(
        internal_runner,
        "_poster_feature_runtime",
        lambda _mode: (SimpleNamespace(preflight=lambda: preflights.append(True)), None),
    )

    async def fake_group(*, members, progress, **_kwargs):
        progress(GroupProgressEvent("fetch", "start", "m00", "Example", total=1))
        progress(GroupProgressEvent("fetch", "end", "m00", "Example", survivors=0))
        return PosterGroupOutput(
            library="movies",
            chunk_index=0,
            members=[
                PosterGroupMemberOutput(
                    subject_key=members[0].subject_key,
                    run_id=members[0].run_id,
                    status="no_candidates",
                    counts={"ranked": 0},
                    source_count=0,
                    candidate_count=0,
                    recommendation=None,
                    scorer_name=None,
                    personalization_mode="collecting",
                    payload={},
                    member_index=0,
                    title="Example",
                )
            ],
        )

    monkeypatch.setattr(poster_group_runner, "run_poster_group", fake_group)

    class Control:
        def __init__(self):
            self.frames = []

        def emit(self, frame):
            self.frames.append(frame)

    control = Control()
    result = _run_poster_group(
        {
            "params": {
                "group_file": "group.json",
                "group_checksum": hashlib.sha256(payload).hexdigest(),
                "personalization_mode": "collecting",
            }
        },
        control,
    )

    assert preflights == [True]
    progress_frames = [frame for frame in control.frames if frame["type"] == "progress"]
    assert [frame["cursor"] for frame in progress_frames] == [1, 2]
    assert all(frame["scope"] == "m00" for frame in progress_frames)
    assert result["files"] == []
    assert result["summary"]["run_ids"] == ["planned-run-1"]
    assert (tmp_path / "group-result.json").is_file()
