"""Intentional-red JMC6J contracts frozen at the jmc6i-complete base."""

from __future__ import annotations

import inspect
import json
from pathlib import Path
from types import SimpleNamespace

from marquee.core.pipeline_config import pipeline_settings
from marquee.ml.residual import build_residual_pairs

_ROOT = Path(__file__).resolve().parents[1]
_RETIRED_MODULES = (
    "marquee/ml/feedback_store.py",
    "marquee/ml/head_trainer.py",
    "marquee/ml/knn_eval.py",
    "marquee/ml/learned_head.py",
    "marquee/ml/migrate_artifacts.py",
    "marquee/onboarding/build_seed_profile.py",
    "marquee/onboarding/build_taste_test.py",
)
_RETIRED_SETTINGS = (
    "FEEDBACK_LABELS_PATH",
    "TRAINING_DATA_DIR",
    "ONBOARDING_STATE_PATH",
    "ONBOARDING_TASTE_TEST_PATH",
    "ONBOARDING_SEED_DIR",
    "LEARNED_HEAD_PATH",
    "HEAD_AUTO_RETRAIN",
)


def test_fresh_poster_analysis_has_no_profile_or_head_precondition() -> None:
    from marquee.core.jobs import internal_runner

    source = inspect.getsource(internal_runner._run_poster_single)
    assert "NumpyTasteStore" not in source
    assert 'Path("profile.npz")' not in source
    assert 'Path("head.npz")' not in source


def test_onboarding_completion_never_schedules_residual_training_or_sets_a_flag() -> None:
    from marquee.api.routes import onboarding

    source = inspect.getsource(onboarding.onboarding_complete)
    assert "learned_head_train" not in source
    assert "mark_complete" not in source


def test_learned_replacement_scorer_is_retired() -> None:
    from marquee.pipeline import scorer

    assert not hasattr(scorer, "LearnedScorer")
    assert "learned" not in inspect.getsource(scorer.select_scorer)


def test_baseline_agreement_produces_residual_training_evidence() -> None:
    events = [
        SimpleNamespace(
            action="approval",
            subject_kind="movie",
            subject_reference="101",
            revoked_event_id=None,
            training_context={"selected_candidate": "chosen.jpg"},
            exposed_candidates=[
                {
                    "candidate_id": "chosen.jpg",
                    "baseline_rank": 1,
                    "baseline_score": 0.9,
                    "normalized_features": {"aesthetic": 0.9},
                },
                {
                    "candidate_id": "alternative.jpg",
                    "baseline_rank": 2,
                    "baseline_score": 0.4,
                    "normalized_features": {"aesthetic": 0.4},
                },
            ],
        )
    ]

    pairs = build_residual_pairs(events)
    subjects = {pair.subject for pair in pairs}

    assert len(subjects) == 1
    assert len(pairs) == 1


def test_readiness_is_derived_from_database_publications_not_files() -> None:
    from marquee.onboarding import service

    source = inspect.getsource(service.status)
    assert "profile_present" not in source
    assert "head_active" not in source
    assert "MlActivePublication" in source


def test_retired_mutable_authorities_are_absent_from_production() -> None:
    assert all(not (_ROOT / relative_path).exists() for relative_path in _RETIRED_MODULES)
    assert all(not hasattr(pipeline_settings, name) for name in _RETIRED_SETTINGS)

    production = "\n".join(
        path.read_text(encoding="utf-8")
        for path in (_ROOT / "marquee").rglob("*.py")
    )
    for token in (
        "feedback_store",
        "LearnedHead",
        "learned_head",
        "TRAINING_DATA_DIR",
        "ONBOARDING_TASTE_TEST",
        "HEAD_AUTO_RETRAIN",
    ):
        assert token not in production


def test_retired_taste_test_routes_are_absent_from_generated_contract() -> None:
    schema = json.loads((_ROOT / "design" / "api-schema.json").read_text(encoding="utf-8"))
    assert not any(path.startswith("/api/onboarding/taste-test") for path in schema["paths"])
