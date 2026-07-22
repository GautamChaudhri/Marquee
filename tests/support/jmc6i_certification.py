"""JMC6I §7 — executable behavioral certification support.

Behavioral closure claims must reference concrete collected test nodes or typed
scenario callables, and the certification runner must prove the referenced node
actually executed (and passed) in the *current* pytest session. Static manifest
strings, source substrings, and test-file existence are inventory, never proof.
"""

from __future__ import annotations

import importlib
import json
from dataclasses import dataclass, field
from pathlib import Path

_TESTS_ROOT = Path(__file__).resolve().parents[1]
CLASSIFICATION_PATH = _TESTS_ROOT / "fixtures" / "jmc6i" / "executable_certification.json"
CLOSURE_MANIFEST_PATH = _TESTS_ROOT / "fixtures" / "jmc6h" / "enabled_definition_closure.json"


def normalize_node(nodeid: str) -> str:
    """One canonical spelling for a test node id (repo-relative, tests/ prefixed)."""
    node = nodeid.strip().replace("\\", "/")
    if node.startswith("tests/"):
        return node
    return f"tests/{node}"


class ExecutionEvidence:
    """Executed-node evidence collected while the session runs."""

    def __init__(self) -> None:
        self._selected: set[str] = set()
        self._outcomes: dict[str, str] = {}

    # -- collection hooks -------------------------------------------------- #

    def mark_selected(self, nodeid: str) -> None:
        self._selected.add(normalize_node(nodeid))

    def record(self, nodeid: str, outcome: str) -> None:
        node = normalize_node(nodeid)
        previous = self._outcomes.get(node)
        # A later failure must never be hidden by an earlier pass record.
        if previous == "failed":
            return
        self._outcomes[node] = outcome

    # -- queries ----------------------------------------------------------- #

    def selected(self, nodeid: str) -> bool:
        return normalize_node(nodeid) in self._selected

    def node_outcome(self, nodeid: str) -> str | None:
        return self._outcomes.get(normalize_node(nodeid))

    def node_executed(self, nodeid: str) -> bool:
        """True only when the node ran to a *passing* call in this session."""
        return self._outcomes.get(normalize_node(nodeid)) == "passed"


def resolve_node(nodeid: str) -> bool:
    """Prove a node reference names a real collected-importable test callable."""
    node = normalize_node(nodeid)
    if "::" not in node:
        return False
    file_part, _, item_part = node.partition("::")
    path = _TESTS_ROOT.parent / file_part
    if not path.is_file():
        return False
    module_name = file_part.removesuffix(".py").replace("/", ".")
    try:
        module = importlib.import_module(module_name)
    except Exception:
        return False
    attribute = item_part.split("[", 1)[0]
    target = module
    for piece in attribute.split("::"):
        target = getattr(target, piece, None)
        if target is None:
            return False
    return callable(target)


@dataclass(slots=True)
class CertificationReport:
    """The honest per-claim disposition of the executable certification run."""

    executed: dict[str, list[str]] = field(default_factory=dict)
    unavailable: dict[str, list[str]] = field(default_factory=dict)
    deferred_jmc6j: list[str] = field(default_factory=list)
    failed: list[str] = field(default_factory=list)

    def to_json(self) -> str:
        return json.dumps(
            {
                "executed": self.executed,
                "unavailable_live_capability": self.unavailable,
                "deferred_jmc6j": self.deferred_jmc6j,
                "failed_evidence": self.failed,
            },
            indent=2,
            sort_keys=True,
        )


def load_classification() -> dict:
    return json.loads(CLASSIFICATION_PATH.read_text())


def load_closure_manifest() -> dict:
    return json.loads(CLOSURE_MANIFEST_PATH.read_text())


def verify_claims(
    evidence: ExecutionEvidence, classification: dict, *, manifest: dict | None = None
) -> CertificationReport:
    """Evaluate every in-scope claim against executed session evidence.

    - a scenario node must resolve, be selected in this session, and have passed;
      anything else is failed evidence (a skipped/xfailed node is *not* executed);
    - a live-capability node must resolve; when selected it must either pass
      (executed) or be an opt-in skip (unavailable — truthfully not certified);
      a selected failure is failed evidence;
    - deferred JMC6J items are recorded verbatim and never counted as executed.
    """
    report = CertificationReport()
    manifest = manifest or load_closure_manifest()
    manifest_types = {entry["job_type"] for entry in manifest["definitions"]}
    manifest_entries = {entry["job_type"]: entry for entry in manifest["definitions"]}

    for job_type, claims in sorted(classification["in_scope"].items()):
        if job_type not in manifest_types:
            report.failed.append(f"{job_type}: stale claim — not an enabled manifest definition")
            continue
        manifest_node = manifest_entries[job_type].get("certification_test")
        scenario_nodes = claims.get("scenarios", ())
        normalized_scenarios = {normalize_node(node) for node in scenario_nodes}
        if not isinstance(manifest_node, str) or normalize_node(manifest_node) not in normalized_scenarios:
            report.failed.append(
                f"{job_type}: manifest certification node is not an executable scenario claim: "
                f"{manifest_node!r}"
            )
            continue
        for node in claims.get("scenarios", ()):
            if not resolve_node(node):
                report.failed.append(f"{job_type}: scenario node does not resolve: {node}")
                continue
            if not evidence.selected(node):
                report.failed.append(
                    f"{job_type}: scenario node was not selected in this session: {node}"
                )
                continue
            if not evidence.node_executed(node):
                outcome = evidence.node_outcome(node) or "not executed"
                report.failed.append(f"{job_type}: scenario evidence is {outcome}: {node}")
                continue
            report.executed.setdefault(job_type, []).append(node)
        for node in claims.get("live_capability", ()):
            if not resolve_node(node):
                report.failed.append(f"{job_type}: live node does not resolve: {node}")
                continue
            outcome = evidence.node_outcome(node)
            if outcome == "passed":
                report.executed.setdefault(job_type, []).append(node)
            elif outcome in {None, "skipped"}:
                report.unavailable.setdefault(job_type, []).append(node)
            else:
                report.failed.append(f"{job_type}: live evidence is {outcome}: {node}")

    report.deferred_jmc6j = sorted(classification.get("deferred_jmc6j", ()))
    return report
