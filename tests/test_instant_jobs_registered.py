"""The retired decorator registry cannot remain policy authority."""

from pathlib import Path


def test_handler_decorators_do_not_store_execution_policy() -> None:
    root = Path(__file__).resolve().parents[1]
    jobs = root / "marquee/core/jobs"

    assert not (jobs / "handlers.py").exists()
    assert not (jobs / "builtin_handlers.py").exists()
