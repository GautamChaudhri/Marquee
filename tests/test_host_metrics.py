"""The per-process memory reader. No GPU, no network."""

from __future__ import annotations

import os
from pathlib import Path

from marquee import host_metrics


def test_the_resident_memory_of_this_process() -> None:
    rss = host_metrics.process_rss_bytes(os.getpid())
    assert rss is not None and rss > 1024 * 1024


def _status(root: Path, pid: int, text: str) -> None:
    (root / str(pid)).mkdir(parents=True)
    (root / str(pid) / "status").write_text(text)


def test_vmrss_is_read_in_kib(tmp_path: Path) -> None:
    _status(tmp_path, 7, "Name:\tmarquee\nVmHWM:\t  9000 kB\nVmRSS:\t  2048 kB\nThreads:\t3\n")
    assert host_metrics.process_rss_bytes(7, tmp_path) == 2048 * 1024


def test_a_gone_process_or_an_odd_line_is_none(tmp_path: Path) -> None:
    assert host_metrics.process_rss_bytes(8, tmp_path) is None
    _status(tmp_path, 9, "Name:\tkthreadd\nThreads:\t1\n")  # kernel threads have no VmRSS
    assert host_metrics.process_rss_bytes(9, tmp_path) is None
    _status(tmp_path, 10, "VmRSS:\t  2 MB\n")
    assert host_metrics.process_rss_bytes(10, tmp_path) is None
    _status(tmp_path, 11, "VmRSS:\t  lots kB\n")
    assert host_metrics.process_rss_bytes(11, tmp_path) is None
