"""JMC7C dead-runtime retirement contracts."""

from __future__ import annotations

from marquee.core.backup import BackupService


def test_legacy_backup_scheduler_loop_is_not_part_of_the_runtime_surface() -> None:
    assert not hasattr(BackupService, "scheduler_loop")
