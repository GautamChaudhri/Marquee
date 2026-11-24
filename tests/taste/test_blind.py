"""The mask: a derived profile without one movie's exemplars, loaded by the unchanged store."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from marquee.config import pipeline_settings
from marquee.ml.artifact_codec import decode_unicode_list, unicode_array, unicode_scalar
from marquee.ml.taste_store import NumpyTasteStore
from marquee.taste import blind
from marquee.taste.trainer import compute_centroid

POSITIVES = ["Alien (1979).png", "Brazil (1985).png", "Heat (1995).png", "Ran (1985).png"]
NEGATIVES = [f"{'a' * 64}.jpg", f"{'b' * 64}.jpg"]


def _unit(rng: np.random.Generator, rows: int, dim: int) -> np.ndarray:
    values = rng.standard_normal((rows, dim)).astype(np.float32)
    return values / np.linalg.norm(values, axis=1, keepdims=True)


def _write(path: Path, *, dino: bool = True, negatives: bool = True) -> dict[str, np.ndarray]:
    """A profile as the trainer writes one, with known rows."""
    rng = np.random.default_rng(7)
    count = len(POSITIVES)
    embeddings = _unit(rng, count, 512)
    weights = np.asarray([1.0, 0.75, 1.0, 0.5], dtype=np.float32)
    payload: dict[str, np.ndarray] = {
        "embeddings": embeddings,
        "poster_names": unicode_array(POSITIVES),
        "asset_kinds": unicode_array(["movie"] * count),
        "poster_sha256s": unicode_array([f"{i:064x}" for i in range(count)]),
        "embedding_weights": weights,
        "centroid_emb": compute_centroid(embeddings, weights),
        "model_name": unicode_scalar(pipeline_settings.AI_MODEL),
        "calib_feature_names": unicode_array(["aesthetic", "colorfulness"]),
        "calib_feature_values": rng.standard_normal((2, count)),
    }
    if negatives:
        payload["neg_embeddings"] = _unit(rng, len(NEGATIVES), 512)
        payload["neg_poster_names"] = unicode_array(NEGATIVES)
        payload["neg_embedding_weights"] = np.ones(len(NEGATIVES), dtype=np.float32)
    if dino:
        payload["dino_embeddings"] = _unit(rng, count, 384)
        payload["dino_model_name"] = unicode_scalar("dinov2-vits14")
        payload["dino_self_knn"] = rng.random(count)
        if negatives:
            payload["neg_dino_embeddings"] = _unit(rng, len(NEGATIVES), 384)
    np.savez(path, **payload)
    return payload


def _load(path: Path) -> dict[str, np.ndarray]:
    with np.load(path, allow_pickle=False) as data:
        return {key: data[key] for key in data.files}


def test_masking_nothing_scores_exactly_as_the_original(tmp_path: Path) -> None:
    source, derived = tmp_path / "profile.npz", tmp_path / "derived.npz"
    _write(source)
    result = blind.mask_profile(source, derived)
    assert (result.positives_dropped, result.negatives_dropped) == (0, 0)
    original, masked = NumpyTasteStore(source), NumpyTasteStore(derived)
    rng = np.random.default_rng(11)
    for vector in _unit(rng, 20, 512):
        assert masked.style_score(vector) - original.style_score(vector) == 0.0
    for vector in _unit(rng, 20, 384):
        assert masked.dino_style_score(vector) - original.dino_style_score(vector) == 0.0
    assert np.array_equal(masked.centroid_emb, original.centroid_emb)
    assert masked.dino_knn_norm_range() is None and original.dino_knn_norm_range() is None


def test_masking_a_movie_drops_exactly_its_rows(tmp_path: Path) -> None:
    source, derived = tmp_path / "profile.npz", tmp_path / "derived.npz"
    original = _write(source)
    result = blind.mask_profile(
        source, derived, positives=["Brazil (1985).png"], negatives=[NEGATIVES[0]]
    )
    assert (result.positives, result.negatives) == (3, 1)
    out = _load(derived)
    keep = [0, 2, 3]
    assert decode_unicode_list(out["poster_names"]) == [POSITIVES[i] for i in keep]
    for key in blind.POSITIVE_ROWS:
        if key in original:
            assert np.array_equal(out[key], original[key][keep]), key
    assert np.array_equal(out["calib_feature_values"], original["calib_feature_values"][:, keep])
    for key in blind.NEGATIVE_ROWS:
        assert np.array_equal(out[key], original[key][[1]]), key
    for key in blind.KEPT_AS_IS:
        assert np.array_equal(out[key], original[key]), key
    assert set(out) == set(original)


def test_the_centroid_is_the_trainers_of_what_is_left(tmp_path: Path) -> None:
    source, derived = tmp_path / "profile.npz", tmp_path / "derived.npz"
    original = _write(source)
    blind.mask_profile(source, derived, positives=["Alien (1979).png", "Ran (1985).png"])
    out = _load(derived)
    expected = compute_centroid(
        original["embeddings"][[1, 2]], original["embedding_weights"][[1, 2]]
    )
    assert np.array_equal(out["centroid_emb"], expected)
    assert not np.array_equal(out["centroid_emb"], original["centroid_emb"])


def test_a_negative_of_the_movie_is_dropped_and_none_left_drops_the_arrays(
    tmp_path: Path,
) -> None:
    source, derived = tmp_path / "profile.npz", tmp_path / "derived.npz"
    _write(source)
    result = blind.mask_profile(source, derived, negatives=NEGATIVES)
    assert (result.negatives_dropped, result.negatives) == (2, 0)
    out = _load(derived)
    assert not set(blind.NEGATIVE_ROWS) & set(out)
    assert NumpyTasteStore(derived).negative_size == 0


def test_the_derived_file_loads_through_the_store(tmp_path: Path) -> None:
    source = tmp_path / "profile.npz"
    _write(source, dino=False, negatives=False)
    derived = tmp_path / "blind" / f"{'c' * 64}-12.npz"
    blind.mask_profile(source, derived, positives=["Heat (1995).png"])
    store = NumpyTasteStore(derived)
    assert store.size == 3
    assert [row["filename"] for row in store.metadata if row] == [
        "Alien (1979).png",
        "Brazil (1985).png",
        "Ran (1985).png",
    ]
    assert store.calibration is not None


def test_an_unknown_name_is_an_error(tmp_path: Path) -> None:
    source, derived = tmp_path / "profile.npz", tmp_path / "derived.npz"
    _write(source)
    with pytest.raises(blind.MaskError, match="no positive named"):
        blind.mask_profile(source, derived, positives=["Nope (2000).png"])
    with pytest.raises(blind.MaskError, match="no negative named"):
        blind.mask_profile(source, derived, negatives=["Alien (1979).png"])
    assert not derived.exists()


def test_masking_every_positive_is_refused(tmp_path: Path) -> None:
    source, derived = tmp_path / "profile.npz", tmp_path / "derived.npz"
    _write(source)
    with pytest.raises(blind.MaskError, match="without a positive"):
        blind.mask_profile(source, derived, positives=POSITIVES)
    assert not derived.exists()


def test_an_array_the_mask_does_not_know_is_refused(tmp_path: Path) -> None:
    source, derived = tmp_path / "profile.npz", tmp_path / "derived.npz"
    payload = _write(source)
    np.savez(source, **payload, something_new=np.zeros(len(POSITIVES)))
    with pytest.raises(blind.MaskError, match="something_new"):
        blind.mask_profile(source, derived)


def test_the_path_is_named_by_profile_and_movie() -> None:
    path = blind.blind_profile_path("d" * 64, 42)
    assert path.name == f"{'d' * 64}-42.npz"
    assert path.parent.name == "blind-profiles"
    with pytest.raises(blind.MaskError):
        blind.blind_profile_path("../x", 42)
