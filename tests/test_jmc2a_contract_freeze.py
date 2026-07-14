"""JMC2A Phase A0 contract freeze.

Pins the JMC1 schema surface that JMC2A deliberately replaces. Phase A1
updates the frozen expectations to the final canonical vocabulary in the
same commit that changes the schema; unexplained drift fails here first.
"""

from __future__ import annotations

import json
from pathlib import Path

from marquee.database import Base
from marquee.models.deployment import EXCLUDED_DEPLOYMENT_TABLES, get_deployment_metadata

FIXTURE = Path(__file__).parent / "fixtures" / "jmc2a" / "jmc1_contract_freeze.json"


def _fixture() -> dict:
    return json.loads(FIXTURE.read_text())


def _columns(table_name: str) -> list[str]:
    return sorted(column.name for column in Base.metadata.tables[table_name].columns)


def test_deployment_table_set_matches_freeze() -> None:
    frozen = _fixture()
    assert sorted(get_deployment_metadata().tables) == frozen["deployment_tables"]
    assert sorted(EXCLUDED_DEPLOYMENT_TABLES) == frozen["excluded_tables"]


def test_job_platform_columns_match_freeze() -> None:
    frozen = _fixture()
    assert _columns("jobs") == frozen["jobs_columns"]
    assert _columns("job_attempts") == frozen["job_attempts_columns"]
    assert _columns("job_dispatches") == frozen["job_dispatches_columns"]
    assert _columns("job_events") == frozen["job_events_columns"]


def test_lifecycle_vocabulary_matches_freeze() -> None:
    frozen = _fixture()
    jobs = Base.metadata.tables["jobs"]
    checks = {constraint.name: str(constraint.sqltext) for constraint in jobs.constraints if hasattr(constraint, "sqltext")}
    for value in frozen["phase_values"]:
        assert f"'{value}'" in checks["ck_jobs_phase"]
    for value in frozen["outcome_values"]:
        assert f"'{value}'" in checks["ck_jobs_outcome"]
    for value in frozen["desired_state_values"]:
        assert f"'{value}'" in checks["ck_jobs_desired_state"]
    dispatches = Base.metadata.tables["job_dispatches"]
    dispatch_checks = {
        constraint.name: str(constraint.sqltext)
        for constraint in dispatches.constraints
        if hasattr(constraint, "sqltext")
    }
    for value in frozen["dispatch_disposition_values"]:
        assert f"'{value}'" in dispatch_checks["ck_job_dispatches_disposition"]
