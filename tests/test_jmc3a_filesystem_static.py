from __future__ import annotations

import re
from pathlib import Path

REPOSITORY = Path(__file__).resolve().parents[1]


def test_security_sensitive_serving_uses_filesystem_boundary() -> None:
    route_sources = list((REPOSITORY / "marquee" / "api" / "routes").glob("*.py"))
    offenders = [path for path in route_sources if "FileResponse" in path.read_text(encoding="utf-8")]
    assert offenders == []


def test_string_prefix_is_not_used_for_path_confinement() -> None:
    backend_sources = list((REPOSITORY / "marquee").rglob("*.py"))
    unsafe = re.compile(r"str\([^\n]+\)\.startswith\(str\(")
    offenders = [
        path
        for path in backend_sources
        if unsafe.search(path.read_text(encoding="utf-8"))
    ]
    assert offenders == []


def test_raw_poster_delete_and_cross_device_copy_fallback_are_absent() -> None:
    handlers = (REPOSITORY / "marquee" / "core" / "jobs" / "builtin_handlers.py").read_text(
        encoding="utf-8"
    )
    library = (REPOSITORY / "marquee" / "api" / "routes" / "library.py").read_text(
        encoding="utf-8"
    )
    mutation_handlers = "\n".join(
        path.read_text(encoding="utf-8")
        for path in (REPOSITORY / "marquee" / "core" / "jobs").glob("*mutation*.py")
    )
    assert "Path(entity.poster_path).unlink" not in handlers
    assert "Path(entity.poster_path).unlink" not in library
    assert "marquee-replace" not in mutation_handlers
