from __future__ import annotations

import json
from pathlib import Path

from marquee.core.jobs.delivery import TRANSPORT_KEYS
from marquee.core.jobs.manifest import JOB_DEFINITION_REGISTRY

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests" / "fixtures" / "jmc3a" / "a0_contract_freeze.json"


def _contract() -> dict[str, object]:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def test_jmc3a_starts_from_certified_registry_and_transport_contract() -> None:
    contract = _contract()
    registry = contract["registry"]
    assert isinstance(registry, dict)
    assert len(JOB_DEFINITION_REGISTRY) == registry["definition_count"] + 4
    assert JOB_DEFINITION_REGISTRY.enabled_types == set(registry["enabled_types"]) | {
        "poster_deploy",
        "poster_restore",
        "poster_reset",
        "poster_backup_subject",
        "backup_create",
        "poster_maintenance",
        "pipeline_cache_clear",
        "job_retention_purge",
        "system_metrics_purge",
    }
    definition = JOB_DEFINITION_REGISTRY.for_dispatch("system_noop", entrypoint="control")
    assert definition.execution_class.value == registry["entrypoint"]
    assert sorted(TRANSPORT_KEYS) == contract["transport_envelope_keys"]


def test_jmc3a_a0_inventory_paths_exist_and_are_project_relative() -> None:
    contract = _contract()
    inventory_keys = (
        "direct_process_launch_files",
        "security_prefix_hotspots",
        "file_response_routes",
    )
    for key in inventory_keys:
        values = contract[key]
        assert isinstance(values, list)
        assert values == sorted(set(values))
        for value in values:
            path = Path(value)
            assert not path.is_absolute()
            assert ".." not in path.parts
            assert (ROOT / path).is_file(), f"stale A0 inventory entry: {value}"


def test_jmc3a_a0_freeze_records_unsafe_contracts_without_approving_them() -> None:
    contracts = _contract()["known_unsafe_contracts"]
    assert contracts == [
        "library poster deletion retries a raw database path after validation failure",
        "media-root and serving checks include string-prefix containment",
        "subtitle EXDEV publication includes a copy-and-replace fallback",
    ]
