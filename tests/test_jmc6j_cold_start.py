from pathlib import Path

from marquee.core.pipeline_config import PipelineSettings
from marquee.core.poster_sources.tmdb import PosterCandidate
from marquee.pipeline.features import FeatureExtractor
from marquee.pipeline.gate import PosterGate
from marquee.pipeline.runner import _neutral_candidate_order
from marquee.pipeline.types import CandidateScore, FeatureVector


def _candidate(name: str, language: str | None) -> PosterCandidate:
    return PosterCandidate(
        file_path=f"/{name}",
        width=1000,
        height=1500,
        aspect_ratio=2 / 3,
        language=language,
        vote_average=5.0,
        vote_count=1,
    )


def _record(name: str) -> CandidateScore:
    return CandidateScore(image_path=Path(name), orig_filename=name)


def _features(*, knn_sim: float, aesthetic: float) -> FeatureVector:
    return FeatureVector(
        knn_sim=knn_sim,
        aesthetic=aesthetic,
        title_colorfulness=0.0,
        text_residual=0.0,
        resolution=1.5,
        sharpness=1.0,
        face_area=0.0,
        provenance=0.5,
        lang_match=1.0,
    )


def test_collecting_extractor_has_no_taste_authority() -> None:
    extractor = FeatureExtractor(personalization_mode="collecting")

    assert extractor.taste_store is None
    assert extractor.personalization_mode == "collecting"


def test_collecting_style_gate_omits_taste_floor_and_rescue() -> None:
    config = PipelineSettings(
        GATE_MIN_AESTHETIC=0.4,
        GATE_MIN_AESTHETIC_RESCUED=0.2,
        GATE_AESTHETIC_RESCUE_KNN=0.9,
        GATE_MIN_KNN_SIM=0.8,
    )
    gate = PosterGate(config)
    off_style = _features(knn_sim=0.0, aesthetic=0.6)
    taste_rescued = _features(knn_sim=1.0, aesthetic=0.3)

    assert gate.evaluate_style(off_style, personalization_mode="collecting").passed
    decision = gate.evaluate_style(taste_rescued, personalization_mode="collecting")
    assert not decision.passed
    assert decision.reason == "aesthetic_floor"


def test_neutral_order_is_permutation_invariant_and_source_interleaved() -> None:
    candidates = {
        "a.jpg": _candidate("a.jpg", "en"),
        "b.jpg": _candidate("b.jpg", "en"),
        "c.jpg": _candidate("c.jpg", "fr"),
        "d.jpg": _candidate("d.jpg", "fr"),
    }

    forward = _neutral_candidate_order(
        [_record(name) for name in candidates],
        movie_title="Arrival",
        candidate_map=candidates,
    )
    reverse = _neutral_candidate_order(
        [_record(name) for name in reversed(candidates)],
        movie_title="Arrival",
        candidate_map=candidates,
    )

    assert [item.orig_filename for item in forward] == [item.orig_filename for item in reverse]
    assert [candidates[item.orig_filename].language for item in forward] == ["en", "fr", "en", "fr"]
    assert all(item.final_score is None and not item.contributions for item in forward)


def test_neutral_order_ignores_features_and_prior_scores() -> None:
    candidates = {
        "one.jpg": _candidate("one.jpg", None),
        "two.jpg": _candidate("two.jpg", None),
    }
    first = [_record("one.jpg"), _record("two.jpg")]
    second = [_record("one.jpg"), _record("two.jpg")]
    first[0].features = _features(knn_sim=0.0, aesthetic=0.0)
    first[1].features = _features(knn_sim=1.0, aesthetic=1.0)
    second[0].features = _features(knn_sim=1.0, aesthetic=1.0)
    second[1].features = _features(knn_sim=0.0, aesthetic=0.0)
    second[0].final_score = 1.0

    order_a = _neutral_candidate_order(
        first, movie_title="Heat", candidate_map=candidates
    )
    order_b = _neutral_candidate_order(
        second, movie_title="Heat", candidate_map=candidates
    )

    assert [item.orig_filename for item in order_a] == [item.orig_filename for item in order_b]
