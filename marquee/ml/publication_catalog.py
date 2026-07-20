"""Read-only catalog over the canonical ML publication authority."""

from __future__ import annotations

import asyncio
import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from marquee.core.jobs.artifact_service import physical_artifact_file, verify_physical_artifact
from marquee.ml.learned_head import LogisticHead
from marquee.ml.taste_store import NumpyTasteStore
from marquee.models import Job, JobArtifact, MlActivePublication

CATALOG_STATUS = {"available": True, "authority": "ml_active_publications"}
_NAME = re.compile(r"^(?P<title>.*?)(?: \((?P<year>\d{4})\))?(?:\.[^.]+)?$")


class PublicationCatalogError(RuntimeError):
    pass


@dataclass(frozen=True)
class CatalogEntry:
    artifact: JobArtifact
    job: Job
    library: str
    active: MlActivePublication | None

    @property
    def path(self) -> Path:
        _boundary, stored = physical_artifact_file(self.artifact)
        return stored.root.resolved() / stored.key.value


def _metadata(row: JobArtifact) -> dict[str, Any]:
    return row.artifact_metadata if isinstance(row.artifact_metadata, dict) else {}


async def list_entries(
    db: AsyncSession,
    *,
    kind: str,
    library: str,
) -> list[CatalogEntry]:
    rows = (
        await db.execute(
            select(JobArtifact, Job)
            .join(Job, Job.id == JobArtifact.job_id)
            .where(JobArtifact.kind == kind, JobArtifact.status == "available")
            .order_by(JobArtifact.created_at.desc())
            .limit(100)
        )
    ).all()
    pointer = await db.scalar(
        select(MlActivePublication).where(MlActivePublication.family == f"{kind}:{library}")
    )
    entries = []
    for artifact, job in rows:
        if _metadata(artifact).get("library") != library:
            continue
        entries.append(
            CatalogEntry(
                artifact=artifact,
                job=job,
                library=library,
                active=pointer if pointer and pointer.artifact_id == artifact.id else None,
            )
        )
    return entries


async def get_entry(
    db: AsyncSession,
    *,
    kind: str,
    library: str,
    artifact_id: str,
) -> CatalogEntry:
    try:
        parsed_id = int(artifact_id)
    except ValueError as exc:
        raise FileNotFoundError(f"ML artifact {artifact_id!r} was not found") from exc
    artifact = await db.get(JobArtifact, parsed_id)
    if (
        artifact is None
        or artifact.kind != kind
        or artifact.status != "available"
        or _metadata(artifact).get("library") != library
    ):
        raise FileNotFoundError(f"ML artifact {artifact_id!r} was not found")
    job = await db.get(Job, artifact.job_id)
    if job is None:
        raise PublicationCatalogError("ML artifact has no canonical producer")
    pointer = await db.scalar(
        select(MlActivePublication).where(MlActivePublication.family == f"{kind}:{library}")
    )
    await verify_physical_artifact(artifact)
    return CatalogEntry(
        artifact=artifact,
        job=job,
        library=library,
        active=pointer if pointer and pointer.artifact_id == artifact.id else None,
    )


def _iso(value: object) -> str | None:
    return value.isoformat() if hasattr(value, "isoformat") else None


def _base_summary(entry: CatalogEntry, *, summary: dict[str, Any]) -> dict[str, Any]:
    artifact = entry.artifact
    job = entry.job
    request = job.request if isinstance(job.request, dict) else {}
    active = entry.active
    return {
        "id": str(artifact.id),
        "kind": artifact.kind,
        "status": "active" if active else "archived",
        "label": (f"Generation {active.generation}" if active else f"Job {job.id[:8]}"),
        "model_name": summary.get("model_name"),
        "source_mode": request.get("source") or request.get("mutation"),
        "imported_from_active": False,
        "created_at": _iso(artifact.created_at),
        "updated_at": _iso(job.updated_at),
        "trained_at": summary.get("trained_at") or _iso(job.terminal_at),
        "activated_at": _iso(active.activated_at) if active else None,
        "storage_path": artifact.storage_key or "",
        "checksum": artifact.checksum,
        "generation": active.generation if active else None,
        "producer_job_id": job.id,
        "summary": summary,
    }


def _parsed_name(name: str) -> tuple[str, int | None]:
    match = _NAME.match(name)
    if match is None:
        return name, None
    year = match.group("year")
    return match.group("title") or name, int(year) if year else None


def _profile_payload(
    path: Path,
) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
    store = NumpyTasteStore(path)
    metadata = [item or {} for item in store.metadata]
    movies: dict[tuple[str, int | None], dict[str, Any]] = {}
    grouped: dict[tuple[str, int | None], list[str]] = defaultdict(list)
    kind_counts: Counter[str] = Counter()
    for item in metadata:
        name = str(item.get("filename") or "unknown")
        title, year = _parsed_name(name)
        key = (title, year)
        grouped[key].append(name)
        kind_counts[str(item.get("asset_kind") or "unknown")] += 1
        movies.setdefault(
            key,
            {
                "movie_id": None,
                "title": title,
                "year": year,
                "tmdb_id": None,
                "contribution_count": 0,
            },
        )["contribution_count"] += 1
    duplicate_groups = [
        {"title": title, "year": year, "count": len(names), "exemplars": names}
        for (title, year), names in grouped.items()
        if len(names) > 1
    ]
    summary: dict[str, Any] = {
        "exemplars": store.size,
        "unique_movies": len(movies),
        "negative_exemplars": store.negative_size,
        "duplicate_groups": len(duplicate_groups),
        "duplicate_exemplars": sum(group["count"] - 1 for group in duplicate_groups),
        "by_kind": dict(kind_counts),
    }
    return summary, list(movies.values()), duplicate_groups


def _head_payload(path: Path) -> dict[str, Any]:
    head = LogisticHead.load(path)
    top = sorted(
        zip(head.feature_names, head.weights, strict=True),
        key=lambda item: abs(float(item[1])),
        reverse=True,
    )[:10]
    return {
        "model_name": head.model_name,
        "mode": "pairwise" if head.bias == 0.0 else "pointwise",
        "sample_count": head.n_samples,
        "train_accuracy": head.train_accuracy,
        "trained_at": head.trained_at,
        "top_features": [{"name": name, "weight": float(weight)} for name, weight in top],
    }


async def artifact_summary(entry: CatalogEntry) -> dict[str, Any]:
    await verify_physical_artifact(entry.artifact)
    if entry.artifact.kind == "taste_profile":
        summary, _movies, _duplicates = await asyncio.to_thread(_profile_payload, entry.path)
    elif entry.artifact.kind == "learned_head":
        summary = await asyncio.to_thread(_head_payload, entry.path)
    else:
        raise PublicationCatalogError(f"unsupported ML artifact kind: {entry.artifact.kind}")
    return _base_summary(entry, summary=summary)


async def artifact_detail(entry: CatalogEntry) -> dict[str, Any]:
    await verify_physical_artifact(entry.artifact)
    if entry.artifact.kind == "taste_profile":
        summary, movies, duplicates = await asyncio.to_thread(_profile_payload, entry.path)
        return {
            **_base_summary(entry, summary=summary),
            "movies": movies,
            "duplicate_groups": duplicates,
            "negative_exemplars": [],
        }
    summary = await asyncio.to_thread(_head_payload, entry.path)
    return {**_base_summary(entry, summary=summary), "movies": []}


async def profile_exemplars(entry: CatalogEntry) -> list[dict[str, Any]]:
    if entry.artifact.kind != "taste_profile":
        raise PublicationCatalogError("artifact is not a taste profile")
    await verify_physical_artifact(entry.artifact)
    store = await asyncio.to_thread(NumpyTasteStore, entry.path)
    metadata = await asyncio.to_thread(lambda: store.metadata)
    counts = Counter(str((item or {}).get("filename") or "unknown") for item in metadata)
    rows = []
    for item in metadata:
        name = str((item or {}).get("filename") or "unknown")
        title, year = _parsed_name(name)
        rows.append(
            {
                "name": name,
                "title": title,
                "year": year,
                "movie_id": None,
                "movie_title": title,
                "tmdb_id": None,
                "is_duplicate": counts[name] > 1,
                "duplicate_count": counts[name],
                "exists_in_training_dir": False,
                "thumb_url": None,
            }
        )
    return rows
