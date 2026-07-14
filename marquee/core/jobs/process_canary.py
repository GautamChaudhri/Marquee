"""Closed, test/development-only subprocess canary behaviors."""

from __future__ import annotations

import os
import signal
import sys
import time
from enum import StrEnum


class CanaryBehavior(StrEnum):
    CLEAN_EXIT = "clean_exit"
    BOUNDED_OUTPUT = "bounded_output"
    COOPERATIVE_WAIT = "cooperative_wait"
    IGNORE_UNTIL_KILL = "ignore_until_kill"
    FIXED_FAILURE = "fixed_failure"
    FIXED_STAGED_FILE = "fixed_staged_file"


def _await_start() -> None:
    sys.stdout.buffer.write(b"MARQUEE_CANARY_READY\n")
    sys.stdout.buffer.flush()
    if sys.stdin.buffer.read(1) != b"1":
        raise SystemExit(78)


def _wait_forever() -> None:
    while True:
        time.sleep(0.05)


def run(behavior: CanaryBehavior) -> int:
    stopping = False

    def stop(*_: object) -> None:
        nonlocal stopping
        stopping = True

    if behavior == CanaryBehavior.COOPERATIVE_WAIT:
        signal.signal(signal.SIGINT, stop)
    elif behavior == CanaryBehavior.IGNORE_UNTIL_KILL:
        signal.signal(signal.SIGINT, signal.SIG_IGN)
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
    _await_start()
    if behavior == CanaryBehavior.CLEAN_EXIT:
        return 0
    if behavior == CanaryBehavior.BOUNDED_OUTPUT:
        for _ in range(1024):
            sys.stdout.buffer.write(b"o" * 1024)
            sys.stderr.buffer.write(b"e" * 1024)
        sys.stdout.buffer.flush()
        sys.stderr.buffer.flush()
        return 0
    if behavior == CanaryBehavior.FIXED_FAILURE:
        return 23
    if behavior == CanaryBehavior.FIXED_STAGED_FILE:
        with open("canary-output.bin", "xb") as output:
            output.write(b"marquee-jmc3a-fixed-canary\n")
            output.flush()
            os.fsync(output.fileno())
        return 0
    if behavior == CanaryBehavior.COOPERATIVE_WAIT:
        while not stopping:
            time.sleep(0.02)
        return 0
    if behavior == CanaryBehavior.IGNORE_UNTIL_KILL:
        child = os.fork()
        if child == 0:
            _wait_forever()
        _wait_forever()
    raise SystemExit(64)


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit(64)
    try:
        behavior = CanaryBehavior(sys.argv[1])
    except ValueError:
        raise SystemExit(64) from None
    raise SystemExit(run(behavior))


if __name__ == "__main__":
    main()
