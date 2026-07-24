"""JMC7C executable enabled-definition certification (7C11–7C15)."""

from __future__ import annotations

import json
from pathlib import Path

from marquee.core.jobs.delivery import EXECUTION_HANDLERS
from marquee.core.jobs.manifest import JOB_DEFINITION_REGISTRY
from tests.support.jmc6i_certification import execute_nodes, normalize_node

_MANIFEST = Path(__file__).parent / "fixtures" / "jmc6h" / "enabled_definition_closure.json"


def test_all_enabled_definition_certification_nodes_execute_and_pass() -> None:
    """Exercise every declared producer-to-consumer scenario, not just its file.

    The manifest remains the single registry-aligned inventory.  Its scenarios
    own the real product boundary appropriate to each definition; this outer
    C3 gate proves they were selected and passed in fresh isolated pytest
    sessions rather than treating a node string or collection result as proof.
    """
    document = json.loads(_MANIFEST.read_text(encoding="utf-8"))
    entries = {entry["job_type"]: entry for entry in document["definitions"]}

    assert set(entries) == set(JOB_DEFINITION_REGISTRY.enabled_types)
    assert set(entries) == set(EXECUTION_HANDLERS)

    executed = execute_nodes([entry["certification_test"] for entry in entries.values()])
    assert executed == {normalize_node(entry["certification_test"]) for entry in entries.values()}
