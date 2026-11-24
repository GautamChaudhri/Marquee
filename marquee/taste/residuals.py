"""The residual: train the engine's bounded correction from the owner's feedback, keep it, and
say which artifact runs use.

A training gathers every preference event (:func:`marquee.taste.feedback.residual_events`,
never the hand-picked seed posters, which stay a clean measurement), freezes them
(``freeze_residual_evidence``), builds the pairs (``build_residual_pairs``), and hands them to
``marquee.ml.residual.train_residual`` with the namespace ``movies``, the scorer weights'
signature, the active profile's hash and id, the evidence digest, and the two minimums. The
engine decides: too little evidence, no held-out improvement, a regression against the active
artifact, or an activation. Nothing here adds a gate or changes a threshold.

Every training is a ``residual_trainings`` row, whatever came of it. An activation keeps its
artifact in ``data/ml/residuals/<sha256>.npz``, file first and then its row: a pending record
(``.pending-<sha256>.json``) is written before the file is renamed into place, and the next
training finishes a record a crash left behind. At most one row is active; it is bound to the
profile it was trained for, so publishing another profile makes it inactive
(:func:`marquee.taste.rebuild.register`, in the transaction that registers the profile). The newest :data:`KEEP_NEWEST` artifact files and the active one are
kept. One training runs at a time, under a transaction lock.

Nothing here imports the engine's pipeline, so the worker can train without loading a model.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import math
import os
import re
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from marquee.config import pipeline_settings, settings
from marquee.db.models import ResidualTraining, TasteProfile
from marquee.db.models.residual import OUTCOME_ACTIVATE, OUTCOME_FAILED
from marquee.taste import feedback, rebuild

logger = logging.getLogger(__name__)

NAMESPACE = "movies"
KEEP_NEWEST = 5
TRAINING_LOCK = int.from_bytes(b"mqrs", "big")
"""The transaction lock that lets one training run at a time."""
_ARTIFACT = re.compile(r"[0-9a-f]{64}\.npz")
_PENDING = re.compile(r"\.pending-([0-9a-f]{64})\.json")


class ResidualError(Exception):
    """A training cannot start; the message says why."""


def artifact_path(file_name: str) -> Path:
    if not _ARTIFACT.fullmatch(file_name):
        raise ResidualError(f"{file_name!r} is not a residual artifact's name")
    return settings.residuals_dir / file_name


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _finite(value: Any) -> Any:
    """``value`` with every non-finite number replaced by ``None``, so it can be JSON."""
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, dict):
        return {str(k): _finite(v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [_finite(v) for v in value]
    return value


def _sync_dir(folder: Path) -> None:
    handle = os.open(folder, os.O_RDONLY)
    try:
        os.fsync(handle)
    finally:
        os.close(handle)


def _keep(artifact: Any, record: dict[str, Any]) -> str:
    """Save the artifact under its hash, with a pending record beside it. Blocking.

    The artifact is written under a temporary name and hashed; the pending record (what the
    row will say) is written next; then the artifact is renamed into place. Returns its
    SHA-256. The caller removes the pending record once the row is committed.
    """
    folder = settings.residuals_dir
    folder.mkdir(parents=True, exist_ok=True)
    temporary = folder / f".building-{uuid.uuid4().hex}.npz"
    try:
        artifact.save(temporary)
        sha = _sha256(temporary)
        pending = folder / f".pending-{sha}.json"
        part = folder / f".pending-{sha}.json.part"
        part.write_text(json.dumps(_finite(record), allow_nan=False))
        os.replace(part, pending)
        os.replace(temporary, folder / f"{sha}.npz")
        _sync_dir(folder)
    finally:
        temporary.unlink(missing_ok=True)
    return sha


def _pending_records() -> list[tuple[str, dict[str, Any]]]:
    """Pending records a training left before its row was committed. Blocking."""
    folder = settings.residuals_dir
    if not folder.is_dir():
        return []
    found = []
    for path in sorted(folder.iterdir()):
        match = _PENDING.fullmatch(path.name)
        if match is None:
            continue
        try:
            found.append((match.group(1), json.loads(path.read_text(encoding="utf-8"))))
        except (OSError, ValueError):
            logger.warning("RESIDUAL | an unreadable pending record %s is removed", path.name)
            path.unlink(missing_ok=True)
    return found


def _drop_pending(sha: str) -> None:
    (settings.residuals_dir / f".pending-{sha}.json").unlink(missing_ok=True)


async def active(session: AsyncSession) -> ResidualTraining | None:
    return await session.scalar(select(ResidualTraining).where(ResidualTraining.is_active))


async def latest(session: AsyncSession) -> ResidualTraining | None:
    return await session.scalar(
        select(ResidualTraining).order_by(ResidualTraining.id.desc()).limit(1)
    )


async def _activate(session: AsyncSession, row: ResidualTraining) -> None:
    await session.execute(
        update(ResidualTraining)
        .where(ResidualTraining.is_active, ResidualTraining.id != row.id)
        .values(is_active=False, deactivated_at=func.now())
    )
    await session.flush()
    row.is_active = True
    row.deactivated_at = None
    await session.flush()


async def _finish_pending(session: AsyncSession, profile: TasteProfile) -> list[int]:
    """Record the trainings a crash left with a kept file and no row. The record is made
    active only when it was trained for the profile that is active now."""
    finished = []
    for sha, record in await asyncio.to_thread(_pending_records):
        exists = await session.scalar(
            select(ResidualTraining.id).where(ResidualTraining.artifact_sha256 == sha)
        )
        path = settings.residuals_dir / f"{sha}.npz"
        ok = await asyncio.to_thread(lambda p=path, s=sha: p.is_file() and _sha256(p) == s)
        if exists is None and ok:
            row = ResidualTraining(
                **{
                    key: record.get(key)
                    for key in (
                        "job_id",
                        "profile_id",
                        "profile_sha256",
                        "reason",
                        "subjects",
                        "pairs",
                        "events",
                        "evidence_digest",
                        "report",
                    )
                },
                outcome=OUTCOME_ACTIVATE,
                artifact_sha256=sha,
                file_name=f"{sha}.npz",
            )
            session.add(row)
            await session.flush()
            if record.get("profile_id") == profile.id:
                await _activate(session, row)
            finished.append(row.id)
            logger.info("RESIDUAL | training %d recorded from a pending record", row.id)
        await session.commit()
        await asyncio.to_thread(_drop_pending, sha)
    return finished


async def kept_names(session: AsyncSession) -> set[str]:
    newest = await session.scalars(
        select(ResidualTraining.file_name)
        .where(ResidualTraining.file_name.is_not(None))
        .order_by(ResidualTraining.id.desc())
        .limit(KEEP_NEWEST)
    )
    special = await session.scalars(
        select(ResidualTraining.file_name).where(ResidualTraining.is_active)
    )
    return {name for name in (*newest, *special) if name}


def prune(keep: set[str]) -> list[str]:
    """Delete the artifact files not in ``keep``, unless a pending record names them.
    Blocking."""
    folder = settings.residuals_dir
    if not folder.is_dir():
        return []
    pending = {f"{sha}.npz" for sha, _ in _pending_records()}
    removed = []
    for path in sorted(folder.iterdir()):
        if (
            _ARTIFACT.fullmatch(path.name)
            and path.name not in keep
            and path.name not in pending
            and path.is_file()
        ):
            path.unlink()
            removed.append(path.name)
    return removed


def _load(row: ResidualTraining | None) -> Any:
    """The active artifact, or ``None`` when its file is gone or not its own. Blocking."""
    from marquee.ml.residual import ResidualArtifact

    if row is None or not row.file_name:
        return None
    path = artifact_path(row.file_name)
    if not path.is_file() or _sha256(path) != row.artifact_sha256:
        logger.warning("RESIDUAL | the active artifact %s is missing or changed", row.file_name)
        return None
    try:
        return ResidualArtifact.load(path)
    except (OSError, RuntimeError, ValueError, KeyError) as exc:
        logger.warning("RESIDUAL | the active artifact cannot be loaded: %s", exc)
        return None


@dataclass(frozen=True)
class Training:
    row: ResidualTraining
    activated: bool
    finished_pending: list[int]
    pruned: list[str]

    def to_dict(self) -> dict[str, Any]:
        row = self.row
        return {
            "training_id": row.id,
            "outcome": row.outcome,
            "reason": row.reason,
            "subjects": row.subjects,
            "pairs": row.pairs,
            "events": row.events,
            "profile_id": row.profile_id,
            "activated": self.activated,
            "artifact_sha256": row.artifact_sha256,
            "evaluation": (row.report or {}).get("evaluation"),
            "finished_pending": self.finished_pending,
            "pruned": self.pruned,
        }


async def train(session: AsyncSession, *, job_id: int | None = None) -> Training:
    """Train the residual from every preference event, record the training, and commit.

    An activation keeps the artifact and makes its row the active one. Anything the engine
    raises is recorded as a failed training; it is not raised again.
    """
    from marquee.ml.residual import (
        baseline_signature,
        build_residual_pairs,
        freeze_residual_evidence,
        train_residual,
    )

    await session.execute(select(func.pg_advisory_xact_lock(TRAINING_LOCK)))
    profile = await rebuild.active_profile(session)
    if profile is None:
        await session.rollback()
        raise ResidualError("there is no active taste profile to train for")
    finished = await _finish_pending(session, profile)
    # The lock went with that commit (or there was nothing to finish); take it again.
    await session.execute(select(func.pg_advisory_xact_lock(TRAINING_LOCK)))
    profile = await rebuild.active_profile(session)
    if profile is None:
        await session.rollback()
        raise ResidualError("there is no active taste profile to train for")

    events = await feedback.residual_events(session)
    evidence = freeze_residual_evidence(events)
    pairs = build_residual_pairs(events)
    current = await active(session)
    if current is not None and current.profile_id != profile.id:
        current = None
    active_artifact = await asyncio.to_thread(_load, current)
    record: dict[str, Any] = {
        "job_id": job_id,
        "profile_id": profile.id,
        "profile_sha256": profile.sha256,
        "subjects": len({pair.subject for pair in pairs}),
        "pairs": len(pairs),
        "events": len(evidence.event_ids),
        "evidence_digest": evidence.digest,
    }
    try:
        artifact, report = await asyncio.to_thread(
            train_residual,
            pairs,
            namespace=NAMESPACE,
            baseline=baseline_signature(pipeline_settings.scorer_weights),
            profile_checksum=profile.sha256,
            evidence_revision=evidence.digest,
            profile_generation=profile.id,
            min_subjects=pipeline_settings.RESIDUAL_MIN_SUBJECTS,
            min_pairs=pipeline_settings.RESIDUAL_MIN_PAIRS,
            active_residual=active_artifact,
        )
    except Exception as exc:  # a boundary: the training is recorded, never raised
        logger.exception("RESIDUAL | the training raised")
        artifact, report = (
            None,
            {"outcome": OUTCOME_FAILED, "error": f"{type(exc).__name__}: {exc}"},
        )
        report["reason"] = "error"
    report = _finite(report)
    record["report"] = report
    record["reason"] = report.get("reason")
    sha: str | None = None
    if artifact is not None:
        sha = await asyncio.to_thread(_keep, artifact, record)
    row = ResidualTraining(
        **record,
        outcome=report.get("outcome") if report.get("outcome") != "candidate" else "no_change",
        artifact_sha256=sha,
        file_name=f"{sha}.npz" if sha else None,
    )
    session.add(row)
    await session.flush()
    if sha is not None:
        await _activate(session, row)
    keep = await kept_names(session)
    await session.commit()
    if sha is not None:
        await asyncio.to_thread(_drop_pending, sha)
    pruned = await asyncio.to_thread(prune, keep)
    logger.info(
        "RESIDUAL | training %d: %s%s (%d movies, %d pairs, profile %d)",
        row.id,
        row.outcome,
        f", {row.reason}" if row.reason else "",
        row.subjects,
        row.pairs,
        profile.id,
    )
    return Training(row=row, activated=sha is not None, finished_pending=finished, pruned=pruned)


# ── What the Taste page and the status say ────────────────────────────────


def _weights(row: ResidualTraining) -> dict[str, float] | None:
    artifact = _load(row)
    if artifact is None:
        return None
    return {
        name: float(weight)
        for name, weight in zip(artifact.feature_names, artifact.weights, strict=True)
    }


async def state(session: AsyncSession) -> dict[str, Any]:
    """The residual's state: the scorer setting, the active artifact, the last training,
    whether runs use it and why, and what is still missing."""
    from marquee.ml.residual import build_residual_pairs

    profile = await rebuild.active_profile(session)
    current = await active(session)
    last = await latest(session)
    pairs = build_residual_pairs(await feedback.residual_events(session))
    subjects = len({pair.subject for pair in pairs})
    min_subjects = pipeline_settings.RESIDUAL_MIN_SUBJECTS
    min_pairs = pipeline_settings.RESIDUAL_MIN_PAIRS
    scorer = pipeline_settings.SCORER
    bound = current is not None and profile is not None and current.profile_id == profile.id
    if scorer == "weighted":
        in_use, why = False, "SCORER is weighted, which forces the baseline"
    elif current is None:
        in_use = False
        why = (
            "not trained yet"
            if last is None
            else f"the last training did not activate ({last.reason or last.outcome})"
        )
    elif not bound:
        in_use, why = False, "the active artifact was trained for another profile"
    else:
        in_use, why = True, "a compatible artifact is active"
    artifact = None
    if current is not None:
        evaluation = (current.report or {}).get("evaluation") or {}
        artifact = {
            "id": current.id,
            "trained_at": current.created_at.isoformat(),
            "profile_id": current.profile_id,
            "subjects": current.subjects,
            "pairs": current.pairs,
            "baseline_accuracy": evaluation.get("baseline_accuracy"),
            "candidate_accuracy": evaluation.get("candidate_accuracy"),
            "held_out_subjects": evaluation.get("held_out_subjects"),
            "held_out_pairs": evaluation.get("held_out_pairs"),
            "weights": await asyncio.to_thread(_weights, current),
        }
    return {
        "scorer": scorer,
        "in_use": in_use,
        "why": why,
        "active": artifact,
        "last_training": (
            {
                "id": last.id,
                "at": last.created_at.isoformat(),
                "outcome": last.outcome,
                "reason": last.reason,
                "subjects": last.subjects,
                "pairs": last.pairs,
                "profile_id": last.profile_id,
            }
            if last is not None
            else None
        ),
        "missing": {
            "subjects": max(0, min_subjects - subjects),
            "pairs": max(0, min_pairs - len(pairs)),
        },
        "subjects": subjects,
        "pairs": len(pairs),
        "min_subjects": min_subjects,
        "min_pairs": min_pairs,
    }
