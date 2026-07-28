"""Typed, bounded internal-runner progress frames and the host-side bridge (JMC6I §6).

The fixed runner child streams small ``type: "progress"`` frames on the control
channel. This module owns their validation (allowlisted fields, finite bounded
values, monotonic frame cursor) and the mapping of runner-native measurements
onto the registered definition's progress vocabulary through the
:class:`~marquee.core.jobs.execution_progress.ExecutionProgress` observation API.

Runner strings never become primary UI copy: the semantic stage key is mapped
through a definition-owned vocabulary, labels come from the presenter, and the
server remains the sole percentage authority. Unknown stages, unknown fields,
decreasing same-scope values, and out-of-order cursors are recorded as
operational degradation — they can never fail the underlying product operation.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from marquee.core.jobs.execution_progress import ExecutionProgress, ScopeObservation

logger = logging.getLogger(__name__)

RUNNER_PROGRESS_STATES = frozenset({"start", "progress", "end"})

# Complete closed field vocabulary of one runner progress frame. ``v``/``type``
# are the transport envelope; everything else is bounded measurement data.
_ALLOWED_FIELDS = frozenset(
    {
        "v",
        "type",
        "stage",
        "state",
        "scope",
        "subject",
        "done",
        "total",
        "unit",
        "survivors",
        "message",
        "cursor",
    }
)
_MAX_TEXT = 200
# One frame may not claim more work than this; a larger figure is not a credible
# per-stage measurement for any current operation.
_MAX_COUNT = 10_000_000


class RunnerFrameError(ValueError):
    """A runner progress frame violated the bounded typed contract."""


@dataclass(frozen=True, slots=True)
class RunnerProgressFrame:
    """One validated runner measurement, still in runner-native vocabulary."""

    stage: str
    state: str
    scope: str | None
    subject: str | None
    done: float | None
    total: float | None
    unit: str | None
    survivors: int | None
    message: str | None
    cursor: int | None


def _bounded_text(value: Any, name: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value or len(value) > _MAX_TEXT:
        raise RunnerFrameError(f"frame field {name!r} is not bounded text")
    return value


def _bounded_number(value: Any, name: str) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise RunnerFrameError(f"frame field {name!r} is not a number")
    number = float(value)
    if not (0 <= number <= _MAX_COUNT) or number != number:
        raise RunnerFrameError(f"frame field {name!r} is out of bounds")
    return number


def _bounded_int(value: Any, name: str) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise RunnerFrameError(f"frame field {name!r} is not an integer")
    if not 0 <= value <= _MAX_COUNT:
        raise RunnerFrameError(f"frame field {name!r} is out of bounds")
    return value


def parse_runner_progress_frame(frame: dict[str, Any]) -> RunnerProgressFrame:
    """Validate one raw runner frame into the bounded typed model."""
    if not isinstance(frame, dict) or frame.get("type") != "progress":
        raise RunnerFrameError("frame is not a progress frame")
    unknown = set(frame) - _ALLOWED_FIELDS
    if unknown:
        raise RunnerFrameError(f"frame carries non-allowlisted fields: {sorted(unknown)}")
    stage = _bounded_text(frame.get("stage"), "stage")
    if stage is None:
        raise RunnerFrameError("frame names no stage")
    state = frame.get("state", "progress")
    if state not in RUNNER_PROGRESS_STATES:
        raise RunnerFrameError(f"frame state {state!r} is not in the closed vocabulary")
    done = _bounded_number(frame.get("done"), "done")
    total = _bounded_number(frame.get("total"), "total")
    if done is not None and total is not None and done > total:
        raise RunnerFrameError("frame claims more completed work than its total")
    return RunnerProgressFrame(
        stage=stage,
        state=state,
        scope=_bounded_text(frame.get("scope"), "scope"),
        subject=_bounded_text(frame.get("subject"), "subject"),
        done=done,
        total=total,
        unit=_bounded_text(frame.get("unit"), "unit"),
        survivors=_bounded_int(frame.get("survivors"), "survivors"),
        message=_bounded_text(frame.get("message"), "message"),
        cursor=_bounded_int(frame.get("cursor"), "cursor"),
    )


class RunnerProgressBridge:
    """Map validated runner frames onto the attempt's registered progress policy.

    - ``stage_map`` translates runner-native stage keys into the definition's
      registered stage vocabulary; unmapped stages degrade, never render.
    - The overall scope is the furthest registered stage reached over the stable
      declared vocabulary (a defensible stable denominator), and is monotonic.
    - The current scope is the mapped stage (optionally refined by the frame's
      explicit ``scope``); real ``done``/``total`` become a determinate current
      measurement, otherwise the stage stays honestly indeterminate.
    - Survivor counts ride as bounded metrics. Nothing here invents an ETA or a
      percentage; the durable writer computes and clamps server-side.
    """

    def __init__(
        self,
        progress: ExecutionProgress,
        *,
        stage_map: dict[str, str],
        overall_from_stages: bool = True,
    ) -> None:
        self._progress = progress
        self._stage_map = dict(stage_map)
        self._overall_from_stages = overall_from_stages
        stage_order = [key for key, _label in progress.definition.progress_policy.stages]
        self._stage_ordinal = {key: index + 1 for index, key in enumerate(stage_order)}
        self._stage_total = len(stage_order)
        self._furthest_ordinal = 0
        self._last_cursor: int | None = None
        self.degraded_frames = 0

    def _degrade(self, reason: str) -> None:
        self.degraded_frames += 1
        logger.warning("runner progress frame degraded for %s: %s", self._progress.job_id, reason)

    async def on_frame(self, frame: dict[str, Any]) -> None:
        """The ``on_progress`` callable handed to ``run_internal_operation``."""
        try:
            parsed = parse_runner_progress_frame(frame)
        except RunnerFrameError as exc:
            self._degrade(str(exc))
            return
        if parsed.cursor is not None:
            if self._last_cursor is not None and parsed.cursor <= self._last_cursor:
                self._degrade("runner frame cursor is stale or out of order")
                return
            self._last_cursor = parsed.cursor
        mapped_stage = self._stage_map.get(parsed.stage)
        if mapped_stage is None or mapped_stage not in self._stage_ordinal:
            self._degrade(f"runner stage {parsed.stage!r} has no registered mapping")
            return

        overall: ScopeObservation | None = None
        if self._overall_from_stages:
            ordinal = self._stage_ordinal[mapped_stage]
            if ordinal > self._furthest_ordinal:
                self._furthest_ordinal = ordinal
            overall = ScopeObservation.determinate(
                completed=float(self._furthest_ordinal),
                total=float(self._stage_total),
                unit=self._progress.definition.progress_policy.overall_unit,
            )

        scope_key = mapped_stage if parsed.scope is None else f"{mapped_stage}:{parsed.scope}"
        if parsed.done is not None and parsed.total is not None and parsed.total > 0:
            current = ScopeObservation.determinate(
                completed=parsed.done,
                total=parsed.total,
                unit=parsed.unit,
                scope_key=scope_key,
            )
        else:
            current = ScopeObservation.indeterminate(unit=parsed.unit, scope_key=scope_key)

        # Stage boundaries are durable; intra-stage samples coalesce on cadence.
        durable = parsed.state in {"start", "end"}
        await self._progress.observe(
            mapped_stage,
            overall=overall,
            current=current,
            survivors=parsed.survivors,
            durable=durable,
        )

    async def stage(self, stage_key: str) -> None:
        """Advance to a registered handler-side stage (validate/register/publish).

        The handler owns the post-runner phases; this keeps the same monotonic
        stage-position overall measurement the runner frames advanced.
        """
        if stage_key not in self._stage_ordinal:
            self._degrade(f"handler stage {stage_key!r} is not in the registered vocabulary")
            return
        ordinal = self._stage_ordinal[stage_key]
        if ordinal > self._furthest_ordinal:
            self._furthest_ordinal = ordinal
        overall = None
        if self._overall_from_stages:
            overall = ScopeObservation.determinate(
                completed=float(self._furthest_ordinal),
                total=float(self._stage_total),
                unit=self._progress.definition.progress_policy.overall_unit,
            )
        await self._progress.observe(
            stage_key,
            overall=overall,
            current=ScopeObservation.indeterminate(scope_key=stage_key),
            durable=True,
        )

    async def close(self) -> None:
        """Flush any coalesced tail sample before the handler returns."""
        await self._progress.flush_pending()
