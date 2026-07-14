"""Legacy decorators remain handler inventory only, never policy authority."""

from pathlib import Path


def test_handler_decorators_do_not_store_execution_policy() -> None:
    root = Path(__file__).resolve().parents[1]
    handlers = (root / "marquee/core/jobs/handlers.py").read_text(encoding="utf-8")
    builtins = (root / "marquee/core/jobs/builtin_handlers.py").read_text(encoding="utf-8")

    assert "_instant_types" not in handlers
    assert "_max_runtime_seconds" not in handlers
    assert "instant=" not in builtins
