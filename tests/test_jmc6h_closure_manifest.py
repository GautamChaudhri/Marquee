"""JMC6H behavioral closure manifest contract (H23).

The manifest names, for every enabled definition, its producer, executor, real
product effect, terminal contract, evidence, downstream consumer, safety, and
certification test.  This contract proves the checked-in manifest stays complete
against the live registry and carries the JMC6H behavioral fields; the placeholder
poster/ML product effects themselves are proven (and made real) by
``test_jmc6h_product_effect.py`` across phases H2 and H4.
"""

from __future__ import annotations

import json
from pathlib import Path

from marquee.core.jobs.delivery import EXECUTION_HANDLERS
from marquee.core.jobs.manifest import JOB_DEFINITION_REGISTRY
from tests.support.jmc6i_certification import collect_nodes, normalize_node, resolve_node

_MANIFEST = Path(__file__).parent / "fixtures" / "jmc6h" / "enabled_definition_closure.json"

_PLAN_BASE = "93a695b5843ffaaf26eae98e67669538c32b1a56"  # jmc6g-complete

_PLACEHOLDER_STATES = {"placeholder_pending_h2", "placeholder_pending_h4"}
_PRODUCT_STATES = _PLACEHOLDER_STATES | {"real", "real_certified_jmc6g"}

# H4 closes the last placeholder leaves; this set must stay empty.
_FOCUS_PLACEHOLDER_LEAVES: set[str] = set()
# The stable JMC6H focus set (made-real leaves plus the non-deploying rescan).
_FOCUS_DEFINITIONS = _FOCUS_PLACEHOLDER_LEAVES | {
    "poster_pipeline",
    "poster_rescan",
    "taste_map",
    "taste_rebuild",
    "taste_enrich",
    "ranking_residual_train",
}

_REQUIRED_FIELDS = {
    # carried forward from the JMC6G execution closure
    "handler",
    "result_model",
    "allowed_outcomes",
    "retry",
    "timeout_seconds",
    "configuration_keys",
    "progress",
    "external_tools",
    "large_file_io",
    "projection",
    "artifacts",
    "presenter",
    "tests",
    # JMC6H behavioral closure fields
    "feature_area",
    "execution_class",
    "product_state",
    "producer",
    "consumer",
    "real_product_effect",
    "certification_test",
}


def _document() -> dict:
    return json.loads(_MANIFEST.read_text(encoding="utf-8"))


def test_jmc6h_manifest_covers_exactly_the_enabled_registry() -> None:
    document = _document()
    entries = {entry["job_type"]: entry for entry in document["definitions"]}

    assert document["version"] == 1
    assert document["plan"] == "jmc6h"
    assert document["plan_base"] == _PLAN_BASE
    assert document["predecessor_tag"] == "jmc6g-complete"
    assert document["audit_state"] == "h6_certified"
    assert set(entries) == set(JOB_DEFINITION_REGISTRY.enabled_types)
    assert set(entries) == set(EXECUTION_HANDLERS)


def test_jmc6h_manifest_entries_carry_behavioral_fields_matching_the_registry() -> None:
    document = _document()
    entries = {entry["job_type"]: entry for entry in document["definitions"]}
    declared_nodes = [entry["certification_test"] for entry in entries.values()]
    collected_nodes = collect_nodes(declared_nodes)

    for job_type, entry in entries.items():
        definition = JOB_DEFINITION_REGISTRY.get(job_type)
        handler = EXECUTION_HANDLERS[job_type]

        assert set(entry) >= _REQUIRED_FIELDS, job_type
        assert f"{handler.__module__}.{handler.__name__}".endswith(entry["handler"]), job_type
        assert entry["execution_class"] == definition.execution_class.value, job_type
        assert entry["feature_area"] == definition.feature_area.value, job_type
        assert entry["presenter"] == definition.presenter_key, job_type
        assert entry["product_state"] in _PRODUCT_STATES, job_type
        assert entry["producer"], job_type
        assert entry["consumer"], job_type
        assert entry["real_product_effect"], job_type
        assert entry["certification_test"], job_type
        node = entry["certification_test"]
        assert "::" in node, job_type
        assert resolve_node(node), (job_type, node)
        assert normalize_node(node) in collected_nodes, (job_type, node)


def test_jmc6h_placeholder_focus_leaves_are_flagged_and_have_certification_tests() -> None:
    document = _document()
    entries = {entry["job_type"]: entry for entry in document["definitions"]}

    flagged = {
        job_type
        for job_type, entry in entries.items()
        if entry["product_state"] in _PLACEHOLDER_STATES
    }
    assert flagged == _FOCUS_PLACEHOLDER_LEAVES

    for job_type in _FOCUS_PLACEHOLDER_LEAVES:
        entry = entries[job_type]
        assert entry["certification_test"].startswith("test_jmc6h_product_effect.py::"), job_type
        # A placeholder leaf may not claim a real, consumed product effect yet.
        assert entry["real_product_effect"], job_type

    # The manifest's declared focus set stays stable as leaves are made real.
    assert set(document["focus_definitions"]) == _FOCUS_DEFINITIONS
    for job_type in _FOCUS_DEFINITIONS:
        assert entries[job_type]["product_state"] == "real"
