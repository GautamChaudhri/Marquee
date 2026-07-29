from __future__ import annotations

from sqlalchemy import UniqueConstraint

from marquee.models.pipeline_run import PipelineRun, _default_subject_key


class _InsertContext:
    def __init__(self, values: dict[str, object]) -> None:
        self._values = values

    def get_current_parameters(self) -> dict[str, object]:
        return self._values


def test_pipeline_run_uses_job_and_subject_composite_uniqueness() -> None:
    constraints = {
        constraint.name: tuple(column.name for column in constraint.columns)
        for constraint in PipelineRun.__table__.constraints
        if isinstance(constraint, UniqueConstraint)
    }
    assert constraints["uq_pipeline_runs_job_subject"] == ("job_id", "subject_key")
    assert "uq_pipeline_runs_job_id" not in constraints
    assert PipelineRun.__table__.c.subject_key.nullable is False


def test_pipeline_run_subject_key_default_preserves_legacy_single_insertions() -> None:
    assert (
        _default_subject_key(
            _InsertContext(
                {
                    "run_id": "run-1",
                    "media_type": "movie",
                    "movie_id": 7,
                    "subject_snapshot": {"display_id": "movie:7"},
                }
            )
        )
        == "movie:7"
    )
    assert (
        _default_subject_key(
            _InsertContext(
                {
                    "run_id": "run-2",
                    "media_type": "season",
                    "season_id": 9,
                    "subject_snapshot": {},
                }
            )
        )
        == "season:9"
    )
