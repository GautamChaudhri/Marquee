"""JMC7B proofs for frozen polarity-complete native taste profiles."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import numpy as np

from marquee.core.jobs.internal_runner import _run_taste_profile
from marquee.core.pipeline_config import pipeline_settings
from marquee.core.taste_preferences import (
    evidence_revision,
    profile_exemplar_manifest,
    profile_input_ids,
)
from marquee.ml.artifact_codec import save_npz_atomic, unicode_array, unicode_scalar
from marquee.ml.taste_store import NumpyTasteStore


def _basis(index: int) -> np.ndarray:
    vector = np.zeros(512, dtype=np.float32)
    vector[index] = 1.0
    return vector


def _save_profile(path: Path, *, negative: bool) -> None:
    positive = _basis(0).reshape(1, 512)
    payload: dict[str, np.ndarray] = {
        "embeddings": positive,
        "embedding_weights": np.asarray([0.4], dtype=np.float32),
        "poster_names": unicode_array(["liked.jpg"]),
        "asset_kinds": unicode_array(["movie"]),
        "centroid_emb": positive[0],
        "model_name": unicode_scalar(pipeline_settings.AI_MODEL),
    }
    if negative:
        payload.update(
            {
                "neg_embeddings": _basis(1).reshape(1, 512),
                "neg_embedding_weights": np.asarray([0.9], dtype=np.float32),
                "neg_poster_names": unicode_array(["hated.jpg"]),
            }
        )
    save_npz_atomic(path, payload)


def test_hate_changes_the_native_production_similarity_penalty(tmp_path: Path):
    without_hate = tmp_path / "without-hate.npz"
    with_hate = tmp_path / "with-hate.npz"
    _save_profile(without_hate, negative=False)
    _save_profile(with_hate, negative=True)

    baseline = NumpyTasteStore(without_hate)
    contrastive = NumpyTasteStore(with_hate)

    hated_candidate = _basis(1)
    unrelated_candidate = _basis(2)
    assert contrastive.negative_size == 1
    assert contrastive.style_score(hated_candidate) < baseline.style_score(hated_candidate)
    assert contrastive.style_score(unrelated_candidate) == baseline.style_score(unrelated_candidate)


def test_frozen_manifest_preserves_polarity_namespace_weight_and_lineage():
    exemplar = SimpleNamespace(
        id="negative-global",
        namespace="global",
        polarity="negative",
        evidence_weight=0.75,
        retained_artifact_id=42,
        checksum="a" * 64,
        embedding_identity={"model": "clip", "revision": "v1"},
        supersedes_exemplar_id="positive-old",
    )
    assert profile_exemplar_manifest(exemplar) == {
        "exemplar_id": "negative-global",
        "namespace": "global",
        "polarity": "negative",
        "weight": 0.75,
        "retained_artifact_id": 42,
        "checksum": "a" * 64,
        "embedding_identity": {"model": "clip", "revision": "v1"},
        "supersedes_exemplar_id": "positive-old",
    }
    rows = [
        SimpleNamespace(id="global-negative", namespace="global"),
        SimpleNamespace(id="movie-positive", namespace="movies"),
        SimpleNamespace(id="tv-negative", namespace="tv"),
    ]
    assert profile_input_ids(rows, "movies") == {"global-negative", "movie-positive"}

    original = SimpleNamespace(
        id="negative-global",
        namespace="global",
        polarity="negative",
        subject_kind="movie",
        subject_reference="movie-42",
        checksum="a" * 64,
        evidence_weight=0.75,
        retained_artifact_id=42,
        embedding_identity={"model": "clip", "revision": "v1"},
        supersedes_exemplar_id="positive-old",
        status="active",
    )
    replacement = SimpleNamespace(
        **{**original.__dict__, "retained_artifact_id": 43}
    )
    assert evidence_revision([original]) != evidence_revision([replacement])


def test_runner_passes_negative_staging_and_frozen_weights_to_native_builder(
    monkeypatch, tmp_path: Path
):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "training").mkdir()
    (tmp_path / "negative").mkdir()
    captured: dict[str, object] = {}

    def fake_rebuild_profile(**kwargs):
        captured.update(kwargs)
        _save_profile(Path(kwargs["output"]), negative=True)
        return Path(kwargs["output"])

    monkeypatch.setattr("marquee.ml.taste_trainer.rebuild_profile", fake_rebuild_profile)
    result = _run_taste_profile(
        {
            "params": {
                "library": "movies",
                "source": {
                    "mode": "fixture",
                    "positive_weights": {"liked.jpg": 0.4},
                    "negative_weights": {"hated.jpg": 0.9},
                },
            }
        },
        SimpleNamespace(emit=lambda _frame: None),
    )

    assert captured["training_dir"] == Path("training")
    assert captured["negative_dir"] == Path("negative")
    assert captured["positive_weights"] == {"liked.jpg": 0.4}
    assert captured["negative_weights"] == {"hated.jpg": 0.9}
    assert result["summary"]["negatives"] == 1
