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
    assert len(JOB_DEFINITION_REGISTRY) == registry["definition_count"] + 12
    assert JOB_DEFINITION_REGISTRY.enabled_types == set(registry["enabled_types"]) | {
        "subtitle_policy",
        "subtitle_restore",
        "subtitle_generate",
        "subtitle_extract",
        "subtitle_embed",
        # JMC5B B2 track mutations.
        "audio_remove",
        "track_remove",
        "subtitle_remove",
        "audio_reorder",
        "subtitle_metadata",
        "poster_deploy",
        "poster_restore",
        "poster_reset",
        "poster_backup_subject",
        "backup_create",
        "poster_maintenance",
        "pipeline_cache_clear",
        "job_retention_purge",
        "system_metrics_purge",
        "letterbox_apply",
        "letterbox_remove",
            "letterbox_reencode",
            "letterbox_reencode_publish",
            "letterbox_reencode_restore",
                "letterbox_reencode_discard",
                "dovi_convert",
                "dovi_publish",
                "dovi_restore",
                "dovi_discard",
    }
    definition = JOB_DEFINITION_REGISTRY.for_dispatch("system_noop", entrypoint="control")
    assert definition.execution_class.value == registry["entrypoint"]
    assert sorted(TRANSPORT_KEYS) == contract["transport_envelope_keys"]


def test_jmc3a_a0_inventory_paths_exist_and_are_project_relative() -> None:
    contract = _contract()
    retired_in_jmc5c = {
        "marquee/core/dovi_conversion.py",
        "marquee/core/jobs/builtin_handlers.py",
        "marquee/core/subtitles/mutation.py",
    }
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
            if value in retired_in_jmc5c:
                assert not (ROOT / path).exists()
                continue
            assert (ROOT / path).is_file(), f"stale A0 inventory entry: {value}"


def test_jmc3a_a0_freeze_records_unsafe_contracts_without_approving_them() -> None:
    contracts = _contract()["known_unsafe_contracts"]
    assert contracts == [
        "library poster deletion retries a raw database path after validation failure",
        "media-root and serving checks include string-prefix containment",
        "subtitle EXDEV publication includes a copy-and-replace fallback",
    ]
