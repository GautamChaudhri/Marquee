"""JMC6A A0 freeze for the shared Activity client and consumer surface.

These assertions describe the exact wire contract and legacy-consumer inventory
that JMC6A builds its shared client, store, and progress components on top of.
They are static (no database, no app import) so they run in every environment.

Two things are frozen:

1. The backend job/activity wire contract (`design/api-schema.json`): the set of
   canonical job paths and the shape of the schemas the client and store consume.
   The checks use superset semantics -- additive backend changes are allowed, but
   a path or field JMC6A depends on cannot be removed or renamed silently. This
   upholds the JMC6A stop gate "a generated contract materially contradicts the
   current canonical route implementation".

2. The frontend legacy-consumer inventory: every file that still imports the
   legacy `trackJob` engine (`$lib/jobs`), the handwritten job client
   (`$lib/api/jobs`), or the media-jobs adapter (`$lib/api/media-jobs`). JMC6A
   builds the replacement store/card but does not migrate these pages (JMC6B
   does). The inventory can only *shrink*: a new import of any legacy module
   fails the freeze (locked decision A16).
"""

from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).parents[1]
FIXTURES = ROOT / "tests/fixtures/jmc6a"
SCHEMA_PATH = ROOT / "design/api-schema.json"
FE = ROOT / "frontend/src"

_CLIENT_FREEZE = json.loads((FIXTURES / "client_contract_freeze.json").read_text())
_CONSUMER_FREEZE = json.loads((FIXTURES / "legacy_client_consumers.json").read_text())
_SCHEMA = json.loads(SCHEMA_PATH.read_text())

_IMPORT_RE = re.compile(r"""import\s[^'"]*?from\s*['"]([^'"]+)['"]""")
_LEGACY_TARGETS = {
    "lib/jobs": "lib_jobs",
    "lib/api/jobs": "api_jobs",
    "lib/api/media-jobs": "api_media_jobs",
}


def _resolve(spec: str, importer: Path) -> str | None:
    """Resolve an import specifier to a canonical `lib/...` module key."""
    if spec.startswith("$lib/"):
        target = "lib/" + spec[len("$lib/") :]
    elif spec.startswith("./") or spec.startswith("../"):
        target = (importer.parent / spec).resolve().relative_to(FE).as_posix()
    else:
        return None
    return re.sub(r"\.(ts|js|svelte)$", "", target)


def _current_consumers() -> dict[str, set[str]]:
    assert FE.is_dir(), f"frontend source tree not found at {FE}"
    groups: dict[str, set[str]] = {value: set() for value in _LEGACY_TARGETS.values()}
    for path in FE.rglob("*"):
        if path.suffix not in (".ts", ".js", ".svelte") or not path.is_file():
            continue
        if "/generated/" in path.as_posix():
            continue
        rel = path.relative_to(FE).as_posix()
        for spec in _IMPORT_RE.findall(path.read_text()):
            resolved = _resolve(spec, path)
            if resolved in _LEGACY_TARGETS:
                groups[_LEGACY_TARGETS[resolved]].add(rel)
    return groups


def test_jmc6a_openapi_version_and_path_count_are_frozen() -> None:
    assert _SCHEMA["openapi"] == _CLIENT_FREEZE["openapi"]
    # JMC6A may add narrow backend/OpenAPI fixes (A15); it may not shrink the surface.
    assert len(_SCHEMA["paths"]) >= _CLIENT_FREEZE["path_count"]


def test_jmc6a_job_wire_paths_remain_present() -> None:
    for path, methods in _CLIENT_FREEZE["job_paths"].items():
        assert path in _SCHEMA["paths"], f"job path removed: {path}"
        present = {m.upper() for m in _SCHEMA["paths"][path]}
        missing = set(methods) - present
        assert not missing, f"{path} lost methods {sorted(missing)}"


def test_jmc6a_core_schema_shapes_remain_stable() -> None:
    schemas = _SCHEMA["components"]["schemas"]
    for name, frozen in _CLIENT_FREEZE["schemas"].items():
        assert name in schemas, f"schema removed: {name}"
        current = schemas[name]
        if "enum" in frozen:
            present = set(current.get("enum") or [])
            missing = set(frozen["enum"]) - present
            assert not missing, f"{name} lost enum members {sorted(missing)}"
            continue
        props = set((current.get("properties") or {}).keys())
        missing_props = set(frozen["props"]) - props
        assert not missing_props, f"{name} lost properties {sorted(missing_props)}"
        required = set(current.get("required") or [])
        relaxed = set(frozen["required"]) - required
        assert not relaxed, f"{name} relaxed required fields {sorted(relaxed)}"


def test_jmc6a_single_multiplexed_stream_and_no_per_job_stream() -> None:
    paths = _SCHEMA["paths"]
    # One multiplexed durable stream (A06).
    assert "/api/jobs/events/stream" in paths
    # Bounded per-job event replay exists, but must not be a stream (A15: no
    # restored per-job polling SSE).
    assert "/api/jobs/{job_id}/events" in paths
    assert "/api/jobs/{job_id}/events/stream" not in paths


def test_jmc6a_legacy_consumer_inventory_can_only_shrink() -> None:
    current = _current_consumers()
    for key, frozen in _CONSUMER_FREEZE.items():
        frozen_set = set(frozen)
        added = current[key] - frozen_set
        assert not added, (
            f"new legacy import of {key!r} is forbidden (JMC6A A16); "
            f"offending files: {sorted(added)}"
        )
