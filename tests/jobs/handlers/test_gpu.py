"""The gpu kinds' functions, called directly with fakes: no GPU, no model, no network."""

from __future__ import annotations

import hashlib
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from marquee.jobs.handlers import gpu
from marquee.jobs.registry import PermanentJobError
from marquee.pipeline.runner import ProgressEvent


@dataclass
class _Output:
    run_id: str = "e" * 32
    status: str = "completed"
    scorer_name: str | None = "weighted"
    counts: dict[str, int] = field(default_factory=lambda: {"posters_found": 3, "ranked": 2})
    ranked: list[dict] = field(default_factory=lambda: [{}, {}])
    recommendation: dict = field(
        default_factory=lambda: {
            "rank": 1,
            "orig_filename": "a.jpg",
            "final_score": 0.9,
            "stack_label": "A",
        }
    )
    payload: dict = field(
        default_factory=lambda: {
            "diagnostic_ledger": {"candidates": [{"orig_filename": "a.jpg", "stack_rank": 1}]}
        }
    )


class _FakeSession:
    def get_providers(self) -> list[str]:
        return ["CUDAExecutionProvider", "CPUExecutionProvider"]


class _FakeEncoder:
    def __init__(self, path: object, session: object) -> None:
        self.session = session

    def encode_batch(self, images: list) -> np.ndarray:
        return np.zeros((len(images), 512), dtype=np.float32)


def test_gpu_selftest_loads_once_per_process(monkeypatch: pytest.MonkeyPatch) -> None:
    loads: list[object] = []

    def create_session(path: object) -> _FakeSession:
        loads.append(path)
        return _FakeSession()

    monkeypatch.setattr("marquee.ml.hardware.create_onnx_session", create_session)
    monkeypatch.setattr("marquee.ml.embedding.CLIPImageEncoder", _FakeEncoder)
    monkeypatch.setattr(gpu, "_clip", None)

    first = gpu.gpu_selftest({}, threading.Event(), lambda *_: None)
    second = gpu.gpu_selftest({}, threading.Event(), lambda *_: None)

    assert len(loads) == 1
    assert (first["already_loaded"], second["already_loaded"]) == (False, True)
    assert first["provider"] == "CUDAExecutionProvider"
    assert first["batch"] == 8


# ── poster_run, in the child ──────────────────────────────────────────────


def _run_payload(**values: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "run_folder": "00000007-die-hard",
        "movie_id": 7,
        "title": "Die Hard",
        "tmdb_id": 562,
        "director": "John McTiernan",
        "production_companies": ["Twentieth Century Fox", "Gordon Company"],
        "tagline": "Twelve terrorists. One cop.",
    }
    payload.update(values)
    return payload


@pytest.fixture
def child_setup(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> dict[str, Any]:
    """A taste profile and a TMDB token, and a fake engine that records what it was given."""
    from pydantic import SecretStr

    from marquee.config import pipeline_settings, settings

    profile = tmp_path / "profile.npz"
    profile.write_bytes(b"a profile")
    monkeypatch.setattr(pipeline_settings, "TASTE_PROFILE_PATH", profile)
    monkeypatch.setattr(settings, "TMDB_READ_ACCESS_TOKEN", SecretStr("token"))
    seen: dict[str, Any] = {"profile": profile}

    async def fake_run(**kwargs: Any) -> _Output:
        seen.update(kwargs)
        kwargs["progress"](ProgressEvent(stage="fetch", state="end", survivors=3, elapsed_s=0.5))
        assert kwargs["should_cancel"]() is False
        return _Output()

    monkeypatch.setattr("marquee.pipeline.single_run.run_single_subject", fake_run)
    return seen


def test_poster_run_hands_the_engine_the_gate_fields_by_its_names(
    child_setup: dict[str, Any],
) -> None:
    import hashlib

    from marquee.config import settings

    progress: list[tuple] = []
    result = gpu.poster_run(_run_payload(), threading.Event(), lambda *v: progress.append(v))

    subject = child_setup["subject"]
    assert (subject.title, subject.tmdb_id, subject.movie_id) == ("Die Hard", 562, 7)
    assert child_setup["run_dir"] == settings.runs_dir / "00000007-die-hard"
    gate = child_setup["ocr_gate"]
    assert gate.director == "John McTiernan"
    assert gate.studios == ["Twentieth Century Fox", "Gordon Company"]
    assert gate.tagline == "Twelve terrorists. One cop."
    assert result["pick_file"] == "a.jpg"
    assert result["status"] == "completed"
    assert result["stages"] == [{"stage": "fetch", "survivors": 3, "seconds": 0.5}]
    assert result["profile_sha256"] == hashlib.sha256(b"a profile").hexdigest()
    assert progress == [(None, None, "fetch end")]


def test_poster_run_gives_no_studios_as_an_empty_list(child_setup: dict[str, Any]) -> None:
    # TMDB's details give [] when a movie lists no studio; the engine must see the same.
    gpu.poster_run(_run_payload(production_companies=None), threading.Event(), lambda *_: None)
    assert child_setup["ocr_gate"].studios == []


@pytest.mark.parametrize(
    "values",
    [
        {"run_folder": "../escape"},
        {"run_folder": ""},
        {"movie_id": None},
        {"tmdb_id": "562"},
        {"title": " "},
    ],
)
def test_poster_run_refuses_a_bad_payload(child_setup: dict[str, Any], values: dict) -> None:
    with pytest.raises(PermanentJobError):
        gpu.poster_run(_run_payload(**values), threading.Event(), lambda *_: None)


def test_poster_run_needs_a_taste_profile(
    child_setup: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    from marquee.config import pipeline_settings

    monkeypatch.setattr(
        pipeline_settings, "TASTE_PROFILE_PATH", child_setup["profile"].parent / "none"
    )
    with pytest.raises(PermanentJobError, match="taste seed"):
        gpu.poster_run(_run_payload(), threading.Event(), lambda *_: None)


@pytest.mark.parametrize(
    "payload",
    [
        {"positive_weights": {}, "negative_weights": {}},
        {"build": "../x", "positive_weights": {}, "negative_weights": {}},
        {"build": "7", "positive_weights": {}},
        {"build": "7", "positive_weights": [], "negative_weights": {}},
    ],
)
def test_taste_rebuild_refuses_a_bad_payload(payload: dict) -> None:
    with pytest.raises(PermanentJobError):
        gpu.taste_rebuild(payload, threading.Event(), lambda *_: None)


def test_taste_rebuild_hands_the_trainer_the_staged_folders(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from marquee.config import settings
    from marquee.taste import trainer

    monkeypatch.setattr(settings, "DATA_DIR", tmp_path)
    build = tmp_path / "cache" / "taste-build" / "7"
    (build / "positive").mkdir(parents=True)
    (build / "negative").mkdir()
    calls: list[dict[str, Any]] = []

    def fake_rebuild_profile(**kwargs: Any) -> Path:
        calls.append(kwargs)
        kwargs["progress_callback"]({"stage": "clip", "processed": 1, "total": 2})
        kwargs["output"].write_bytes(b"profile")
        return kwargs["output"]

    monkeypatch.setattr(trainer, "rebuild_profile", fake_rebuild_profile)
    progress: list[tuple] = []
    cancel = threading.Event()
    result = gpu.taste_rebuild(
        {"build": "7", "positive_weights": {"A (2001).png": 0.75}, "negative_weights": {}},
        cancel,
        lambda *args: progress.append(args),
    )

    (call,) = calls
    assert call["training_dir"] == build / "positive"
    assert call["negative_dir"] is None  # an empty negative folder is no negatives
    assert call["output"] == build / "profile.npz"
    assert call["cancel_event"] is cancel
    assert call["positive_weights"] == {"A (2001).png": 0.75}
    assert progress == [(1, 2, "clip")]
    assert result["sha256"] == hashlib.sha256(b"profile").hexdigest()


# ── Masked runs and the residual ──────────────────────────────────────────


def _kept_profile(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> dict[str, Any]:
    """A real two-exemplar profile kept in ``profiles/`` under its hash."""
    import hashlib

    from marquee.config import pipeline_settings, settings
    from marquee.ml.artifact_codec import unicode_array, unicode_scalar
    from marquee.taste.trainer import compute_centroid

    monkeypatch.setattr(settings, "DATA_DIR", tmp_path / "data")
    rng = np.random.default_rng(3)
    embeddings = rng.standard_normal((2, 512)).astype(np.float32)
    embeddings /= np.linalg.norm(embeddings, axis=1, keepdims=True)
    source = tmp_path / "built.npz"
    np.savez(
        source,
        embeddings=embeddings,
        poster_names=unicode_array(["Die Hard (1988).png", "Heat (1995).png"]),
        embedding_weights=np.ones(2, dtype=np.float32),
        centroid_emb=compute_centroid(embeddings),
        model_name=unicode_scalar(pipeline_settings.AI_MODEL),
    )
    sha = hashlib.sha256(source.read_bytes()).hexdigest()
    settings.profiles_dir.mkdir(parents=True)
    (settings.profiles_dir / f"{sha}.npz").write_bytes(source.read_bytes())
    return {"id": 4, "sha256": sha, "file_name": f"{sha}.npz"}


def test_a_masked_run_scores_against_the_derived_profile_and_deletes_it(
    child_setup: dict[str, Any], monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from marquee.taste import blind

    active = _kept_profile(monkeypatch, tmp_path)
    seen_store: dict[str, Any] = {}

    class _Extractor:
        def __init__(self, *, personalization_mode: str, taste_store: Any) -> None:
            seen_store["names"] = [row["filename"] for row in taste_store.metadata]
            seen_store["path"] = taste_store.profile_path
            assert taste_store.profile_path.is_file()

    monkeypatch.setattr("marquee.pipeline.features.FeatureExtractor", _Extractor)
    result = gpu.poster_run(
        _run_payload(profile=active, mask={"positives": ["Die Hard (1988).png"], "negatives": []}),
        threading.Event(),
        lambda *_: None,
    )
    assert seen_store["names"] == ["Heat (1995).png"]
    assert seen_store["path"] == blind.blind_profile_path(active["sha256"], 7)
    assert not seen_store["path"].exists()  # deleted when the run ended
    assert isinstance(child_setup["feature_extractor"], _Extractor)
    assert result["masked"] is True
    assert result["profile_sha256"] == active["sha256"]  # the active one, not the derived


def test_the_derived_profile_is_deleted_when_the_run_fails(
    child_setup: dict[str, Any], monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from marquee.taste import blind

    active = _kept_profile(monkeypatch, tmp_path)
    monkeypatch.setattr(
        "marquee.pipeline.features.FeatureExtractor", lambda **kwargs: kwargs["taste_store"]
    )

    async def broken(**kwargs: Any) -> Any:
        assert blind.blind_profile_path(active["sha256"], 7).is_file()
        raise RuntimeError("the engine broke")

    monkeypatch.setattr("marquee.pipeline.single_run.run_single_subject", broken)
    with pytest.raises(RuntimeError, match="the engine broke"):
        gpu.poster_run(
            _run_payload(profile=active, mask={"positives": ["Heat (1995).png"], "negatives": []}),
            threading.Event(),
            lambda *_: None,
        )
    assert not blind.blind_profile_path(active["sha256"], 7).exists()


@pytest.mark.parametrize(
    ("mask", "message"),
    [
        ({"positives": ["Nope (2000).png"], "negatives": []}, "cannot be masked"),
        ({"positives": [], "negatives": []}, "at least one positive"),
        ({"positives": "x"}, "positives and negatives"),
    ],
)
def test_a_mask_that_cannot_be_applied_fails_for_good(
    child_setup: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    mask: dict[str, Any],
    message: str,
) -> None:
    active = _kept_profile(monkeypatch, tmp_path)
    with pytest.raises(PermanentJobError, match=message):
        gpu.poster_run(_run_payload(profile=active, mask=mask), threading.Event(), lambda *_: None)


def test_a_masked_run_needs_the_kept_copy_of_the_active_profile(
    child_setup: dict[str, Any], monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    active = _kept_profile(monkeypatch, tmp_path)
    wrong = {**active, "sha256": "f" * 64, "file_name": f"{'f' * 64}.npz"}
    with pytest.raises(PermanentJobError, match="missing or does not hold its own bytes"):
        gpu.poster_run(
            _run_payload(profile=wrong, mask={"positives": ["Heat (1995).png"], "negatives": []}),
            threading.Event(),
            lambda *_: None,
        )


def test_the_active_residual_goes_to_the_engine_with_its_context(
    child_setup: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    import hashlib

    from marquee.config import pipeline_settings, settings
    from marquee.ml.residual import baseline_signature

    profile_sha = hashlib.sha256(b"a profile").hexdigest()
    residual = {"id": 9, "sha256": "d" * 64, "file_name": f"{'d' * 64}.npz"}
    import marquee.pipeline.single_run as single_run

    recorded = single_run.run_single_subject

    async def ranked_by_residual(**kwargs: Any) -> _Output:
        await recorded(**kwargs)
        return _Output(scorer_name="residual")

    monkeypatch.setattr(single_run, "run_single_subject", ranked_by_residual)
    result = gpu.poster_run(
        _run_payload(
            profile={"id": 4, "sha256": profile_sha, "file_name": f"{profile_sha}.npz"},
            residual=residual,
        ),
        threading.Event(),
        lambda *_: None,
    )
    assert child_setup["residual_path"] == settings.residuals_dir / residual["file_name"]
    context = child_setup["residual_context"]
    assert context.library == "movies"
    assert context.baseline_signature == baseline_signature(pipeline_settings.scorer_weights)
    assert (context.profile_checksum, context.profile_generation) == (profile_sha, 4)
    assert (context.artifact_id, context.artifact_checksum) == (9, "d" * 64)
    assert result["scorer"] == "residual" and result["residual_id"] == 9


def test_without_an_active_profile_or_artifact_no_residual_is_passed(
    child_setup: dict[str, Any],
) -> None:
    result = gpu.poster_run(_run_payload(), threading.Event(), lambda *_: None)
    assert child_setup["residual_path"] is None and child_setup["residual_context"] is None
    assert result["scorer"] == "weighted" and result["residual_id"] is None


@pytest.mark.parametrize(
    "bad",
    [
        {"id": "4", "sha256": "a" * 64, "file_name": f"{'a' * 64}.npz"},
        {"id": 4, "sha256": "a" * 63, "file_name": f"{'a' * 63}.npz"},
        {"id": 4, "sha256": "a" * 64, "file_name": "../x.npz"},
    ],
)
def test_a_malformed_identity_fails_for_good(
    child_setup: dict[str, Any], bad: dict[str, Any]
) -> None:
    with pytest.raises(PermanentJobError, match="malformed"):
        gpu.poster_run(_run_payload(residual=bad), threading.Event(), lambda *_: None)
