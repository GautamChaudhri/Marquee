from __future__ import annotations

from collections import Counter

from sqlalchemy import select

from marquee.core.jobs.manifest import JOB_DEFINITION_REGISTRY
from marquee.core.jobs.progress import (
    ConcurrentSubject,
    MeasurementMode,
    ProgressMeasurementUpdate,
)
from marquee.core.jobs.progress_service import (
    ProgressObservation,
    ProgressWriteError,
    ProgressWriter,
)
from marquee.core.jobs.subjects import SUBJECT_SNAPSHOT_ADAPTER
from marquee.database import _get_session_factory
from marquee.models import Job

MAX_PROJECTED_CHILDREN = 500


async def project_parent_progress(
    *,
    parent_id: str,
    fence_token: int,
    writer: ProgressWriter,
) -> object:
    """Project one bounded synthetic sealed-child set through the canonical writer."""
    factory = _get_session_factory()
    async with factory() as session:
        parent = await session.scalar(select(Job).where(Job.id == parent_id))
        if parent is None or parent.current_attempt_id is None or parent.fence_token != fence_token:
            raise ProgressWriteError("parent attempt ownership is stale")
        definition = JOB_DEFINITION_REGISTRY.get(parent.type)
        if definition.parent_policy is None:
            raise ProgressWriteError("job definition is not a synthetic parent")
        children = (
            await session.scalars(
                select(Job)
                .where(Job.parent_id == parent_id)
                .order_by(Job.created_at, Job.id)
                .limit(MAX_PROJECTED_CHILDREN + 1)
            )
        ).all()
        if len(children) > MAX_PROJECTED_CHILDREN:
            raise ProgressWriteError("parent child projection exceeds its bound")
        if any(child.type not in definition.child_job_types for child in children):
            raise ProgressWriteError("parent contains a child type outside its registered policy")
        attempt_id = parent.current_attempt_id
        sealed = bool(parent.subject_snapshot.get("sealed"))
        if definition.parent_policy.require_sealed and not sealed:
            overall = ProgressMeasurementUpdate(
                scope_id=f"parent:{parent_id}:unsealed",
                mode=MeasurementMode.INDETERMINATE,
                unit="children",
                label="Child set is not sealed",
            )
        elif children:
            overall = ProgressMeasurementUpdate(
                scope_id=f"parent:{parent_id}:sealed:{len(children)}",
                mode=MeasurementMode.DETERMINATE,
                unit="children",
                completed=sum(child.phase == "terminal" for child in children),
                total=len(children),
                label="Terminal children",
            )
        else:
            overall = ProgressMeasurementUpdate(
                scope_id=f"parent:{parent_id}:sealed:empty",
                mode=MeasurementMode.NONE,
                label="Sealed child set is empty",
            )

        active = [child for child in children if child.phase != "terminal"]
        snapshots = []
        for child in active[:9]:
            snapshots.append(SUBJECT_SNAPSHOT_ADAPTER.validate_python(child.subject_snapshot))
        primary = snapshots[0] if snapshots else None
        concurrent = tuple(
            ConcurrentSubject(subject=snapshot, stage_key=active[index + 1].current_stage)
            for index, snapshot in enumerate(snapshots[1:])
        )
        outcomes = Counter(child.outcome for child in children if child.outcome is not None)
        failures = sum(
            outcomes[name]
            for name in ("failed", "partially_succeeded", "dead_letter", "unsafe")
        )
        warnings = sum(outcomes[name] for name in ("cancelled", "superseded", "no_change"))
        observation = ProgressObservation(
            stage_key="execute",
            overall=overall,
            current=ProgressMeasurementUpdate(
                scope_id=f"parent:{parent_id}:current",
                mode=MeasurementMode.NONE,
                label="Active children",
            ),
            current_subject=primary,
            concurrent_subjects=concurrent,
            warning_count=warnings,
            failure_count=failures,
        )
    return await writer.write(
        job_id=parent_id,
        attempt_id=attempt_id,
        fence_token=fence_token,
        observation=observation,
    )
