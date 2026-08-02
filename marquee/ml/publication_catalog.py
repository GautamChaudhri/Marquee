"""Read-only catalog over the canonical ML publication authority."""

from __future__ import annotations

import asyncio
import re
from collections import Counter, defaultdict
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from marquee.core.jobs.artifact_service import physical_artifact_file, verify_physical_artifact
from marquee.ml.residual import ResidualArtifact
from marquee.ml.taste_store import NumpyTasteStore
from marquee.models import Job, JobArtifact, MlActivePublication, TasteExemplar

CATALOG_STATUS = {"available": True, "authority": "ml_active_publications"}
# "Title (Year) - 2.jpg". The trailing " - N" is a copy counter, not part of the title:
# the retired profile updater numbered filename collisions rather than overwriting, so
# the movies training set accumulated "Title (Year).jpg", " - 2.jpg", " - 3.jpg" for the
# same film. Without stripping it every copy reads as a separate movie. Titles that
# genuinely contain " - " keep it, because the counter only ever follows the year.
_NAME = re.compile(r"^(?P<title>.*?)(?: \((?P<year>\d{4})\))?(?: - (?P<copy>\d+))?(?:\.[^.]+)?$")


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


def _subject_identity(
    item: dict[str, Any],
    resolver: Mapping[str, dict[str, Any]] | None,
) -> tuple[str, int | None]:
    """The subject (series or movie) one exemplar row stands for.

    Filenames alone are not the subject. TV profiles stage posters as ``show-12.jpg``
    and ``season-12-03.jpg`` — the series id, not a title — while movie profiles built
    from frozen evidence stage them as ``{exemplar_id}.jpg``. Both carry the real
    identity elsewhere, so the filename is the last resort, not the first.
    """
    series_title = str(item.get("series_title") or "").strip()
    if series_title:
        return _parsed_name(series_title)
    name = str(item.get("filename") or "unknown")
    if resolver:
        resolved = resolver.get(Path(name).stem)
        if resolved and resolved.get("title"):
            year = resolved.get("year")
            return str(resolved["title"]), int(year) if year else None
    return _parsed_name(name)


def _asset_facets(item: dict[str, Any]) -> tuple[str, int | None]:
    """``(asset_kind, season_number)`` for one exemplar row."""
    kind = str(item.get("asset_kind") or "unknown")
    season = item.get("season_number")
    return kind, int(season) if isinstance(season, int | float) else None


def _titled(title: str, year: int | None) -> str:
    return f"{title} ({year})" if year else title


def _asset_label(title: str, year: int | None, asset_kind: str, season: int | None) -> str:
    """One poster's display name — the subject, plus the season it belongs to."""
    if asset_kind == "season" and season is not None:
        return f"{_titled(title, year)} · Season {season}"
    return _titled(title, year)


# Shows before their seasons, movies after both; anything unrecognised sorts last.
_KIND_ORDER = {"show": 0, "season": 1, "movie": 2}
_KIND_PLURALS = {"show": "shows", "season": "seasons", "movie": "movies", "unknown": "assets"}


def _asset_breakdown(counts: Mapping[str, int]) -> str:
    """``"1 show · 4 seasons"`` — what a subject actually contributed."""
    ordered = sorted(counts.items(), key=lambda pair: (_KIND_ORDER.get(pair[0], 9), pair[0]))
    parts = [
        f"{count} {kind if count == 1 else _KIND_PLURALS.get(kind, f'{kind}s')}"
        for kind, count in ordered
        if count
    ]
    return " · ".join(parts)


def _asset_rows(
    metadata: list[dict[str, Any]],
    resolver: Mapping[str, dict[str, Any]] | None,
) -> list[dict[str, Any]]:
    """Flatten the store metadata into one resolved row per embedding.

    A duplicate is the same subject *and* the same asset appearing twice — a show
    poster plus its four season posters is five distinct assets, not a five-way
    duplicate, which is what grouping on the filename alone used to imply.
    """
    rows = []
    for item in metadata:
        name = str(item.get("filename") or "unknown")
        title, year = _subject_identity(item, resolver)
        asset_kind, season = _asset_facets(item)
        rows.append(
            {
                "name": name,
                "title": title,
                "year": year,
                "subject_key": f"{title.casefold()}|{year if year else ''}",
                "asset_kind": asset_kind,
                "season_number": season,
                "label": _asset_label(title, year, asset_kind, season),
            }
        )
    asset_counts = Counter((row["subject_key"], row["asset_kind"], row["season_number"]) for row in rows)
    for row in rows:
        count = asset_counts[(row["subject_key"], row["asset_kind"], row["season_number"])]
        row["is_duplicate"] = count > 1
        row["duplicate_count"] = count
    return rows


def _profile_payload(
    path: Path,
    resolver: Mapping[str, dict[str, Any]] | None = None,
) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
    store = NumpyTasteStore(path)
    metadata = [item or {} for item in store.metadata]
    rows = _asset_rows(metadata, resolver)

    subjects: dict[str, dict[str, Any]] = {}
    duplicates: dict[tuple[str, str, int | None], list[str]] = defaultdict(list)
    kind_counts: Counter[str] = Counter()
    for row in rows:
        kind_counts[row["asset_kind"]] += 1
        duplicates[(row["subject_key"], row["asset_kind"], row["season_number"])].append(row["name"])
        subject = subjects.setdefault(
            row["subject_key"],
            {
                "movie_id": None,
                "title": row["title"],
                "year": row["year"],
                "tmdb_id": None,
                "contribution_count": 0,
                "subject_key": row["subject_key"],
                "asset_counts": Counter(),
                "assets": [],
            },
        )
        subject["contribution_count"] += 1
        subject["asset_counts"][row["asset_kind"]] += 1
        subject["assets"].append(
            {
                "name": row["name"],
                "label": row["label"],
                "asset_kind": row["asset_kind"],
                "season_number": row["season_number"],
                "is_duplicate": row["is_duplicate"],
                "duplicate_count": row["duplicate_count"],
            }
        )

    for subject in subjects.values():
        subject["assets"].sort(
            key=lambda asset: (
                _KIND_ORDER.get(asset["asset_kind"], 9),
                asset["season_number"] if asset["season_number"] is not None else -1,
                asset["name"],
            )
        )
        subject["asset_summary"] = _asset_breakdown(subject["asset_counts"])
        subject["asset_counts"] = dict(subject["asset_counts"])

    duplicate_groups = []
    by_key = {subject["subject_key"]: subject for subject in subjects.values()}
    for (subject_key, asset_kind, season), names in duplicates.items():
        if len(names) < 2:
            continue
        subject = by_key[subject_key]
        duplicate_groups.append(
            {
                "title": subject["title"],
                "year": subject["year"],
                "asset_kind": asset_kind,
                "season_number": season,
                "label": _asset_label(subject["title"], subject["year"], asset_kind, season),
                "count": len(names),
                "exemplars": names,
            }
        )

    summary: dict[str, Any] = {
        "exemplars": store.size,
        "unique_movies": len(subjects),
        "unique_subjects": len(subjects),
        "total_assets": len(rows),
        "negative_exemplars": store.negative_size,
        "duplicate_groups": len(duplicate_groups),
        "duplicate_exemplars": sum(group["count"] - 1 for group in duplicate_groups),
        "by_kind": dict(kind_counts),
    }
    return summary, list(subjects.values()), duplicate_groups


def _residual_payload(path: Path) -> dict[str, Any]:
    residual = ResidualArtifact.load(path)
    top = sorted(
        zip(residual.feature_names, residual.weights, strict=True),
        key=lambda item: abs(float(item[1])),
        reverse=True,
    )[:10]
    return {
        "mode": "bounded_residual",
        "namespace": residual.namespace,
        "evidence_revision": residual.evidence_revision,
        "profile_checksum": residual.profile_checksum,
        "profile_generation": residual.profile_generation,
        "baseline_signature": residual.baseline_signature,
        "alpha": residual.alpha,
        "delta_max": residual.delta_max,
        "evaluation": asdict(residual.evaluation),
        "trained_at": residual.trained_at,
        "top_features": [{"name": name, "weight": float(weight)} for name, weight in top],
    }


async def subject_name_index(
    db: AsyncSession,
    *,
    library: str,
) -> dict[str, dict[str, Any]]:
    """Map exemplar id → subject identity, for profiles staged as ``{exemplar_id}.jpg``.

    Frozen-evidence movie builds name every staged poster after the exemplar row that
    froze it, so the artifact alone cannot say which film it came from. The snapshot
    recorded alongside the exemplar can. Purely a display nicety: any failure here
    degrades to filename parsing rather than failing the request.
    """
    try:
        rows = (
            await db.execute(
                select(TasteExemplar.id, TasteExemplar.subject_snapshot)
                .where(TasteExemplar.namespace.in_(("global", library)))
                .limit(5000)
            )
        ).all()
    except Exception:  # noqa: BLE001 - never fail a read because a name is missing
        return {}
    index: dict[str, dict[str, Any]] = {}
    for exemplar_id, snapshot in rows:
        if not isinstance(snapshot, dict) or not snapshot.get("title"):
            continue
        index[str(exemplar_id)] = {
            "title": snapshot.get("title"),
            "year": snapshot.get("year"),
            "tmdb_id": snapshot.get("tmdb_id"),
            "movie_id": snapshot.get("id"),
        }
    return index


async def artifact_summary(
    entry: CatalogEntry,
    *,
    resolver: Mapping[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    await verify_physical_artifact(entry.artifact)
    if entry.artifact.kind == "taste_profile":
        summary, _movies, _duplicates = await asyncio.to_thread(
            _profile_payload, entry.path, resolver
        )
    elif entry.artifact.kind == "ranking_residual":
        summary = await asyncio.to_thread(_residual_payload, entry.path)
    else:
        raise PublicationCatalogError(f"unsupported ML artifact kind: {entry.artifact.kind}")
    return _base_summary(entry, summary=summary)


async def artifact_detail(
    entry: CatalogEntry,
    *,
    resolver: Mapping[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    await verify_physical_artifact(entry.artifact)
    if entry.artifact.kind == "taste_profile":
        summary, movies, duplicates = await asyncio.to_thread(
            _profile_payload, entry.path, resolver
        )
        return {
            **_base_summary(entry, summary=summary),
            "movies": movies,
            "duplicate_groups": duplicates,
            "negative_exemplars": [],
        }
    summary = await asyncio.to_thread(_residual_payload, entry.path)
    return {**_base_summary(entry, summary=summary), "movies": []}


async def profile_exemplars(
    entry: CatalogEntry,
    *,
    resolver: Mapping[str, dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    if entry.artifact.kind != "taste_profile":
        raise PublicationCatalogError("artifact is not a taste profile")
    await verify_physical_artifact(entry.artifact)
    store = await asyncio.to_thread(NumpyTasteStore, entry.path)
    metadata = await asyncio.to_thread(lambda: store.metadata)
    rows = _asset_rows([item or {} for item in metadata], resolver)
    return [
        {
            "name": row["name"],
            "title": row["title"],
            "year": row["year"],
            "movie_id": None,
            "movie_title": row["title"],
            "tmdb_id": None,
            "asset_kind": row["asset_kind"],
            "season_number": row["season_number"],
            "label": row["label"],
            "is_duplicate": row["is_duplicate"],
            "duplicate_count": row["duplicate_count"],
            "exists_in_training_dir": False,
            "thumb_url": None,
        }
        for row in rows
    ]
