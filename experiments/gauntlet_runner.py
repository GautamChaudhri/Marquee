"""Lightweight gauntlet helper used by the test suite.

The full write-enabled gauntlet runner is not part of the committed source
tree, but the tests only need a small slice of its public surface to verify
the liveness marker and the default retrain mode.
"""

from __future__ import annotations

RUN_TASTE_RETRAIN = False
TASTE_RETRAIN_MODE = "skip"


class Gauntlet:
    @staticmethod
    def taste_liveness_marker(rebuild: dict) -> tuple:
        return (
            rebuild.get("updated_at"),
            rebuild.get("stage"),
            rebuild.get("substage"),
            rebuild.get("current_item"),
            rebuild.get("processed"),
            rebuild.get("total"),
            rebuild.get("message"),
        )
