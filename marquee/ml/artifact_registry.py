"""Managed artifact registry for taste profiles and learned heads."""

from __future__ import annotations

import asyncio
import hashlib
import re
import shutil
import uuid
from collections import Counter, defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
from sqlalchemy import delete, func, select
from sqlalchemy.exc import ProgrammingError
from sqlalchemy.ext.asyncio import AsyncSession

from marquee.config import settings
from marquee.core.pipeline_config import pipeline_settings
from marquee.ml.artifact_codec import (
    GENRES_JSON_KEY,
    decode_json_string_array,
    decode_unicode_list,
    decode_unicode_scalar,
    ensure_safe_artifact,
    load_npz_safe,
)
from marquee.models import ArtifactSnapshot, ArtifactSnapshotMovie, Movie

KIND_TASTE_PROFILE = "taste_profile"
KIND_LEARNED_HEAD = "learned_head"
STATUS_ACTIVE = "active"
STATUS_ARCHIVED = "archived"

_YEAR = re.compile(r"\((\d{4})\)")
_YEAR_SUFFIX = re.compile(r"\s*\(\d{4}\)\s*$")
_DEDUP_SUFFIX = re.compile(r"\s*-\s*\d+$")
_HASH_ARRAY_KEY = "poster_sha256s"
REGISTRY_REASON_MISSING_SCHEMA = "missing_schema"


class ArtifactRegistryUnavailableError(RuntimeError):
    """Raised when artifact management is unavailable in the current runtime."""


def registry_unavailable_message() -> str:
    return "Artifact management requires database migration; run alembic upgrade head"


def _registry_status(*, available: bool, reason: str | None = None) -> dict[str, Any]:
    payload: dict[str, Any] = {"available": available}
    if reason is not None:
        payload["reason"] = reason
    return payload


def _is_missing_schema_error(exc: Exception) -> bool:
    if not isinstance(exc, ProgrammingError):
        return False
    message = str(exc)
    return (
        ("artifact_snapshots" in message or "artifact_snapshot_movies" in message)
        and "does not exist" in message
    )


async def registry_status(db: AsyncSession) -> dict[str, Any]:
    try:
        await db.execute(select(ArtifactSnapshot.id).limit(1))
        await db.execute(select(ArtifactSnapshotMovie.id).limit(1))
    except Exception as exc:  # noqa: BLE001
        if _is_missing_schema_error(exc):
            await db.rollback()
            return _registry_status(available=False, reason=REGISTRY_REASON_MISSING_SCHEMA)
        raise
    return _registry_status(available=True)


def active_artifact_path(kind: str) -> Path:
    if kind == KIND_TASTE_PROFILE:
        return Path(pipeline_settings.TASTE_PROFILE_PATH)
    if kind == KIND_LEARNED_HEAD:
        return Path(pipeline_settings.LEARNED_HEAD_PATH)
    raise ValueError(f"unsupported artifact kind {kind!r}")


def artifact_storage_dir(kind: str) -> Path:
    stem = "taste_profiles" if kind == KIND_TASTE_PROFILE else "learned_heads"
    path = settings.data_dir_path / "ml" / "artifacts" / stem
    path.mkdir(parents=True, exist_ok=True)
    return path


def _snapshot_path(kind: str, snapshot_id: str, active_path: Path) -> Path:
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S")
    suffix = "".join(active_path.suffixes) or ".npz"
    return artifact_storage_dir(kind) / f"{stamp}_{snapshot_id[:8]}{suffix}"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _copy_artifact(source: Path, dest: Path) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, dest)
    return dest


def _parse_name(name: str) -> tuple[str, int | None]:
    stem = Path(name).stem
    year_match = _YEAR.search(stem)
    year = int(year_match.group(1)) if year_match else None
    title = _YEAR_SUFFIX.sub("", stem).strip()
    title = _DEDUP_SUFFIX.sub("", title).strip()
    return title, year


def _truthy_int(value: int | None) -> int | None:
    return int(value) if value not in (None, 0) else None


def _decode_optional_int_list(values: np.ndarray | None) -> list[int | None] | None:
    if values is None:
        return None
    return [_truthy_int(int(value)) for value in np.asarray(values).tolist()]


def _profile_payload(path: Path) -> dict[str, Any]:
    ensure_safe_artifact(path, KIND_TASTE_PROFILE)
    with load_npz_safe(path) as data:
        result: dict[str, Any] = {
            "model_name": decode_unicode_scalar(data["model_name"]),
            "poster_names": decode_unicode_list(data["poster_names"]),
            "neg_poster_names": (
                decode_unicode_list(data["neg_poster_names"])
                if "neg_poster_names" in data.files
                else []
            ),
            "years": _decode_optional_int_list(data["years"] if "years" in data.files else None),
            "tmdb_ids": _decode_optional_int_list(
                data["tmdb_ids"] if "tmdb_ids" in data.files else None
            ),
            "movie_ids": _decode_optional_int_list(
                data["movie_ids"] if "movie_ids" in data.files else None
            ),
            "movie_titles": (
                decode_unicode_list(data["movie_titles"]) if "movie_titles" in data.files else None
            ),
            "genres": (
                decode_json_string_array(data[GENRES_JSON_KEY])
                if GENRES_JSON_KEY in data.files
                else None
            ),
            "source_mode": (
                decode_unicode_scalar(data["source_mode"]) if "source_mode" in data.files else None
            ),
            "sha256s": (
                decode_unicode_list(data[_HASH_ARRAY_KEY]) if _HASH_ARRAY_KEY in data.files else None
            ),
        }
    return result


def _head_payload(path: Path) -> dict[str, Any]:
    ensure_safe_artifact(path, KIND_LEARNED_HEAD)
    with load_npz_safe(path) as data:
        feature_names = decode_unicode_list(data["feature_names"])
        weights = np.asarray(data["weights"], dtype=np.float64)
        order = np.argsort(np.abs(weights))[::-1][:10]
        return {
            "model_name": decode_unicode_scalar(data["model_name"]),
            "trained_at": decode_unicode_scalar(data["trained_at"]),
            "n_samples": int(np.asarray(data["n_samples"]).item()),
            "train_accuracy": float(np.asarray(data["train_accuracy"]).item()),
            "top_features": [
                {
                    "name": feature_names[index],
                    "weight": float(weights[index]),
                }
                for index in order
            ],
        }


async def _resolve_profile_entries(
    db: AsyncSession, payload: dict[str, Any]
) -> list[dict[str, Any]]:
    names: list[str] = payload["poster_names"]
    years = payload.get("years") or [None] * len(names)
    tmdb_ids = payload.get("tmdb_ids") or [None] * len(names)
    movie_ids = payload.get("movie_ids") or [None] * len(names)
    movie_titles = payload.get("movie_titles") or [None] * len(names)
    entries = []
    for index, name in enumerate(names):
        parsed_title, parsed_year = _parse_name(name)
        entries.append(
            {
                "name": name,
                "title": movie_titles[index] or parsed_title,
                "year": years[index] or parsed_year,
                "tmdb_id": tmdb_ids[index],
                "movie_id": movie_ids[index],
                "movie_title": movie_titles[index] or parsed_title,
            }
        )

    explicit_movie_ids = {entry["movie_id"] for entry in entries if entry["movie_id"] is not None}
    explicit_tmdb_ids = {entry["tmdb_id"] for entry in entries if entry["tmdb_id"] is not None}
    titles = {str(entry["title"]).lower() for entry in entries if entry["title"]}

    by_id: dict[int, tuple[int, str, int | None, int | None]] = {}
    if explicit_movie_ids:
        rows = (
            await db.execute(
                select(Movie.id, Movie.title, Movie.year, Movie.tmdb_id).where(
                    Movie.id.in_(sorted(explicit_movie_ids))
                )
            )
        ).all()
        by_id.update({row.id: (row.id, row.title, row.year, row.tmdb_id) for row in rows})

    by_tmdb: dict[int, tuple[int, str, int | None, int | None]] = {}
    if explicit_tmdb_ids:
        rows = (
            await db.execute(
                select(Movie.id, Movie.title, Movie.year, Movie.tmdb_id).where(
                    Movie.tmdb_id.in_(sorted(explicit_tmdb_ids))
                )
            )
        ).all()
        by_tmdb.update({row.tmdb_id: (row.id, row.title, row.year, row.tmdb_id) for row in rows})

    by_title: dict[str, list[tuple[int, str, int | None, int | None]]] = defaultdict(list)
    if titles:
        rows = (
            await db.execute(
                select(Movie.id, Movie.title, Movie.year, Movie.tmdb_id).where(
                    func.lower(Movie.title).in_(sorted(titles))
                )
            )
        ).all()
        for row in rows:
            by_title[str(row.title).lower()].append((row.id, row.title, row.year, row.tmdb_id))

    for entry in entries:
        resolved = None
        if entry["movie_id"] is not None:
            resolved = by_id.get(entry["movie_id"])
        if resolved is None and entry["tmdb_id"] is not None:
            resolved = by_tmdb.get(entry["tmdb_id"])
        if resolved is None:
            candidates = by_title.get(str(entry["title"]).lower(), [])
            if entry["year"] is not None:
                resolved = next((row for row in candidates if row[2] == entry["year"]), None)
            if resolved is None and len(candidates) == 1:
                resolved = candidates[0]
        if resolved is not None:
            entry["movie_id"] = resolved[0]
            entry["movie_title"] = resolved[1]
            entry["title"] = resolved[1]
            entry["year"] = entry["year"] or resolved[2]
            entry["tmdb_id"] = entry["tmdb_id"] or resolved[3]

    return entries


def _duplicate_groups(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, int | None], list[str]] = defaultdict(list)
    for entry in entries:
        grouped[(str(entry["title"]).lower(), entry["year"])].append(entry["name"])
    result = []
    for (_lower_title, year), exemplars in grouped.items():
        if len(exemplars) < 2:
            continue
        title, _ = _parse_name(exemplars[0])
        result.append(
            {
                "title": title,
                "year": year,
                "count": len(exemplars),
                "exemplars": sorted(exemplars),
            }
        )
    result.sort(key=lambda item: (-item["count"], item["title"], item["year"] or 0))
    return result


def _compact_movie_rows(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[tuple[int | None, str, int | None, int | None], int] = Counter()
    for entry in entries:
        key = (
            entry["movie_id"],
            entry["movie_title"],
            entry["year"],
            entry["tmdb_id"],
        )
        grouped[key] += 1
    rows = [
        {
            "movie_id": movie_id,
            "title": title,
            "year": year,
            "tmdb_id": tmdb_id,
            "contribution_count": count,
        }
        for (movie_id, title, year, tmdb_id), count in grouped.items()
    ]
    rows.sort(key=lambda item: (-item["contribution_count"], item["title"], item["year"] or 0))
    return rows


async def _profile_summary(
    db: AsyncSession, path: Path, *, source_mode: str | None = None
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    payload = _profile_payload(path)
    entries = await _resolve_profile_entries(db, payload)
    movies = _compact_movie_rows(entries)
    duplicates = _duplicate_groups(entries)
    summary = {
        "exemplars": len(entries),
        "unique_movies": len(movies),
        "negative_exemplars": len(payload["neg_poster_names"]),
        "duplicate_groups": len(duplicates),
        "duplicate_exemplars": sum(group["count"] for group in duplicates),
        "source_mode": source_mode or payload.get("source_mode") or "unknown",
        "movies": movies[:12],
        "duplicate_preview": duplicates[:12],
    }
    return summary, movies


async def _head_movies(db: AsyncSession) -> list[dict[str, Any]]:
    from marquee.ml import feedback_store  # noqa: PLC0415

    rows = feedback_store.read_all()
    movie_ids = {
        int(movie_id)
        for row in rows
        if (movie_id := row.get("movie_id")) is not None
    }
    resolved: dict[int, tuple[str, int | None, int | None]] = {}
    if movie_ids:
        db_rows = (
            await db.execute(
                select(Movie.id, Movie.title, Movie.year, Movie.tmdb_id).where(
                    Movie.id.in_(sorted(movie_ids))
                )
            )
        ).all()
        resolved = {
            row.id: (row.title, row.year, row.tmdb_id)
            for row in db_rows
        }

    grouped: Counter[tuple[int | None, str, int | None, int | None]] = Counter()
    for row in rows:
        movie_id = int(raw_movie_id) if (raw_movie_id := row.get("movie_id")) is not None else None
        if movie_id in resolved:
            title, year, tmdb_id = resolved[movie_id]
        else:
            title = row.get("title") or f"Movie {movie_id}" if movie_id is not None else "Unknown"
            year = row.get("year")
            tmdb_id = None
        grouped[(movie_id, str(title), year, tmdb_id)] += 1

    items = [
        {
            "movie_id": movie_id,
            "title": title,
            "year": year,
            "tmdb_id": tmdb_id,
            "contribution_count": count,
        }
        for (movie_id, title, year, tmdb_id), count in grouped.items()
    ]
    items.sort(key=lambda item: (-item["contribution_count"], item["title"], item["year"] or 0))
    return items


async def _head_summary(
    db: AsyncSession, path: Path, info: dict[str, Any] | None = None
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    payload = _head_payload(path)
    movies = await _head_movies(db)
    summary = {
        "mode": (info or {}).get("mode") or ("pairwise" if "n_pairs" in (info or {}) else None),
        "sample_count": (
            (info or {}).get("n_pairs")
            or (info or {}).get("n_samples")
            or payload["n_samples"]
        ),
        "train_accuracy": payload["train_accuracy"],
        "trained_at": payload["trained_at"],
        "unique_movies": len(movies),
        "top_features": payload["top_features"],
    }
    return summary, movies


async def _replace_movie_rows(
    db: AsyncSession, snapshot_id: str, movies: list[dict[str, Any]]
) -> None:
    await db.execute(
        delete(ArtifactSnapshotMovie).where(ArtifactSnapshotMovie.artifact_id == snapshot_id)
    )
    for movie in movies:
        db.add(
            ArtifactSnapshotMovie(
                artifact_id=snapshot_id,
                movie_id=movie["movie_id"],
                title=movie["title"],
                year=movie["year"],
                tmdb_id=movie["tmdb_id"],
                contribution_count=movie["contribution_count"],
            )
        )


async def _build_summary(
    db: AsyncSession,
    kind: str,
    path: Path,
    *,
    source_mode: str | None = None,
    info: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], list[dict[str, Any]], str, datetime | None]:
    if kind == KIND_TASTE_PROFILE:
        payload = _profile_payload(path)
        summary, movies = await _profile_summary(db, path, source_mode=source_mode)
        return summary, movies, payload["model_name"], None
    payload = _head_payload(path)
    summary, movies = await _head_summary(db, path, info)
    trained_at = datetime.fromisoformat(payload["trained_at"])
    return summary, movies, payload["model_name"], trained_at


def _snapshot_label(kind: str, *, imported: bool, active_path: Path) -> str:
    prefix = "Imported active" if imported else "Snapshot"
    nice = "Taste profile" if kind == KIND_TASTE_PROFILE else "Learned head"
    return f"{prefix} {nice} ({active_path.name})"


async def _snapshot_row(
    db: AsyncSession,
    kind: str,
    *,
    source_mode: str | None = None,
    info: dict[str, Any] | None = None,
    imported: bool = False,
) -> ArtifactSnapshot:
    active_path = active_artifact_path(kind)
    if not active_path.exists():
        raise FileNotFoundError(f"{kind} artifact not found: {active_path}")

    snapshot_id = str(uuid.uuid4())
    storage_path = _snapshot_path(kind, snapshot_id, active_path)
    _copy_artifact(active_path, storage_path)
    sha256 = _sha256(active_path)
    summary, movies, model_name, trained_at = await _build_summary(
        db,
        kind,
        active_path,
        source_mode=source_mode,
        info=info,
    )
    for row in (
        await db.execute(
            select(ArtifactSnapshot).where(
                ArtifactSnapshot.kind == kind,
                ArtifactSnapshot.status == STATUS_ACTIVE,
            )
        )
    ).scalars():
        row.status = STATUS_ARCHIVED

    now = datetime.now(UTC)
    row = ArtifactSnapshot(
        id=snapshot_id,
        kind=kind,
        status=STATUS_ACTIVE,
        label=_snapshot_label(kind, imported=imported, active_path=active_path),
        storage_path=str(storage_path),
        active_path=str(active_path),
        model_name=model_name,
        sha256=sha256,
        source_mode=source_mode,
        imported_from_active=imported,
        summary=summary,
        trained_at=trained_at,
        activated_at=now,
    )
    db.add(row)
    await db.flush()
    await _replace_movie_rows(db, row.id, movies)
    await db.commit()
    await db.refresh(row)
    return row


async def register_active_artifact(
    db: AsyncSession,
    kind: str,
    *,
    source_mode: str | None = None,
    info: dict[str, Any] | None = None,
    imported: bool = False,
) -> ArtifactSnapshot:
    return await _snapshot_row(
        db,
        kind,
        source_mode=source_mode,
        info=info,
        imported=imported,
    )


async def _sync_active_row(
    db: AsyncSession,
    row: ArtifactSnapshot,
    *,
    source_mode: str | None = None,
    info: dict[str, Any] | None = None,
) -> ArtifactSnapshot:
    active_path = active_artifact_path(row.kind)
    if not active_path.exists():
        row.status = STATUS_ARCHIVED
        row.activated_at = None
        await db.commit()
        await db.refresh(row)
        return row

    current_hash = _sha256(active_path)
    if row.sha256 == current_hash and Path(row.storage_path).exists():
        return row

    summary, movies, model_name, trained_at = await _build_summary(
        db,
        row.kind,
        active_path,
        source_mode=source_mode or row.source_mode,
        info=info,
    )
    storage_path = Path(row.storage_path)
    if not storage_path.exists():
        storage_path = _snapshot_path(row.kind, row.id, active_path)
        row.storage_path = str(storage_path)
    _copy_artifact(active_path, storage_path)
    row.sha256 = current_hash
    row.model_name = model_name
    row.source_mode = source_mode or row.source_mode
    row.summary = summary
    row.trained_at = trained_at
    row.activated_at = row.activated_at or datetime.now(UTC)
    await _replace_movie_rows(db, row.id, movies)
    await db.commit()
    await db.refresh(row)
    return row


async def _active_rows(db: AsyncSession, kind: str) -> list[ArtifactSnapshot]:
    rows = (
        await db.execute(
            select(ArtifactSnapshot)
            .where(ArtifactSnapshot.kind == kind, ArtifactSnapshot.status == STATUS_ACTIVE)
            .order_by(
                ArtifactSnapshot.activated_at.desc().nullslast(),
                ArtifactSnapshot.updated_at.desc().nullslast(),
                ArtifactSnapshot.created_at.desc(),
            )
        )
    ).scalars().all()
    return list(rows)


async def _normalize_active_rows(
    db: AsyncSession,
    kind: str,
) -> ArtifactSnapshot | None:
    rows = await _active_rows(db, kind)
    if not rows:
        return None
    keep = rows[0]
    changed = False
    for row in rows[1:]:
        if row.status != STATUS_ARCHIVED or row.activated_at is not None:
            row.status = STATUS_ARCHIVED
            row.activated_at = None
            changed = True
    if changed:
        await db.commit()
        await db.refresh(keep)
    return keep


async def ensure_registry(db: AsyncSession) -> dict[str, Any]:
    status = await registry_status(db)
    if not status["available"]:
        return status
    for kind in (KIND_TASTE_PROFILE, KIND_LEARNED_HEAD):
        active_path = active_artifact_path(kind)
        active = await _normalize_active_rows(db, kind)
        if active is None:
            if active_path.exists():
                await register_active_artifact(db, kind, imported=True)
            continue
        await _sync_active_row(db, active)
    return status


async def list_artifacts(db: AsyncSession, kind: str) -> list[ArtifactSnapshot]:
    status = await ensure_registry(db)
    if not status["available"]:
        return []
    rows = (
        await db.execute(
            select(ArtifactSnapshot)
            .where(ArtifactSnapshot.kind == kind)
            .order_by(
                ArtifactSnapshot.status.asc(),
                ArtifactSnapshot.activated_at.desc().nullslast(),
                ArtifactSnapshot.created_at.desc(),
            )
        )
    ).scalars().all()
    return list(rows)


async def get_artifact(db: AsyncSession, kind: str, artifact_id: str) -> ArtifactSnapshot:
    status = await ensure_registry(db)
    if not status["available"]:
        raise ArtifactRegistryUnavailableError(registry_unavailable_message())
    row = (
        await db.execute(
            select(ArtifactSnapshot).where(
                ArtifactSnapshot.kind == kind,
                ArtifactSnapshot.id == artifact_id,
            )
        )
    ).scalar_one_or_none()
    if row is None:
        raise FileNotFoundError(f"{kind} artifact {artifact_id} not found")
    if row.status == STATUS_ACTIVE:
        row = await _sync_active_row(db, row)
    return row


async def artifact_movies(db: AsyncSession, artifact_id: str) -> list[ArtifactSnapshotMovie]:
    rows = (
        await db.execute(
            select(ArtifactSnapshotMovie)
            .where(ArtifactSnapshotMovie.artifact_id == artifact_id)
            .order_by(
                ArtifactSnapshotMovie.contribution_count.desc(),
                ArtifactSnapshotMovie.title.asc(),
            )
        )
    ).scalars().all()
    return list(rows)


async def activate_artifact(db: AsyncSession, kind: str, artifact_id: str) -> ArtifactSnapshot:
    row = await get_artifact(db, kind, artifact_id)
    storage_path = Path(row.storage_path)
    if not storage_path.exists():
        raise FileNotFoundError(f"snapshot file missing for {artifact_id}")

    active_path = active_artifact_path(kind)
    _copy_artifact(storage_path, active_path)
    for active in (
        await db.execute(
            select(ArtifactSnapshot).where(
                ArtifactSnapshot.kind == kind,
                ArtifactSnapshot.status == STATUS_ACTIVE,
            )
        )
    ).scalars():
        if active.id != row.id:
            active.status = STATUS_ARCHIVED
    row.status = STATUS_ACTIVE
    row.activated_at = datetime.now(UTC)
    row.sha256 = _sha256(active_path)
    await db.commit()
    await db.refresh(row)
    return row


async def archive_artifact(db: AsyncSession, kind: str, artifact_id: str) -> ArtifactSnapshot:
    row = await get_artifact(db, kind, artifact_id)
    if row.status != STATUS_ACTIVE:
        return row
    if kind == KIND_TASTE_PROFILE:
        raise RuntimeError("activate another taste profile before archiving the active one")

    active_path = active_artifact_path(kind)
    if active_path.exists():
        active_path.unlink()
    row.status = STATUS_ARCHIVED
    row.activated_at = None
    await db.commit()
    await db.refresh(row)
    return row


async def delete_artifact(db: AsyncSession, kind: str, artifact_id: str) -> None:
    row = await get_artifact(db, kind, artifact_id)
    if row.status == STATUS_ACTIVE and kind == KIND_TASTE_PROFILE:
        raise RuntimeError("activate another taste profile before deleting the active one")
    if row.status == STATUS_ACTIVE and kind == KIND_LEARNED_HEAD:
        active_path = active_artifact_path(kind)
        if active_path.exists():
            active_path.unlink()
    elif row.status != STATUS_ARCHIVED:
        raise RuntimeError("archive the artifact before deleting it")

    storage_path = Path(row.storage_path)
    if storage_path.exists():
        storage_path.unlink()
    await db.delete(row)
    await db.commit()


async def active_artifact_summary(
    db: AsyncSession, kind: str
) -> dict[str, Any] | None:
    status = await ensure_registry(db)
    if not status["available"]:
        return None
    row = await _normalize_active_rows(db, kind)
    if row is None:
        return None
    return artifact_to_summary(row)


def artifact_to_summary(row: ArtifactSnapshot) -> dict[str, Any]:
    summary = dict(row.summary or {})
    return {
        "id": row.id,
        "kind": row.kind,
        "status": row.status,
        "label": row.label,
        "model_name": row.model_name,
        "source_mode": row.source_mode,
        "imported_from_active": row.imported_from_active,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
        "trained_at": row.trained_at.isoformat() if row.trained_at else None,
        "activated_at": row.activated_at.isoformat() if row.activated_at else None,
        "storage_path": row.storage_path,
        "summary": summary,
    }


async def artifact_detail(db: AsyncSession, kind: str, artifact_id: str) -> dict[str, Any]:
    row = await get_artifact(db, kind, artifact_id)
    detail = artifact_to_summary(row)
    movies = await artifact_movies(db, row.id)
    detail["movies"] = [
        {
            "movie_id": movie.movie_id,
            "title": movie.title,
            "year": movie.year,
            "tmdb_id": movie.tmdb_id,
            "contribution_count": movie.contribution_count,
        }
        for movie in movies
    ]
    if kind == KIND_TASTE_PROFILE:
        payload = _profile_payload(Path(row.storage_path))
        entries = await _resolve_profile_entries(db, payload)
        detail["duplicate_groups"] = _duplicate_groups(entries)
        detail["negative_exemplars"] = payload["neg_poster_names"]
    return detail


async def profile_exemplars(db: AsyncSession, artifact_id: str) -> list[dict[str, Any]]:
    row = await get_artifact(db, KIND_TASTE_PROFILE, artifact_id)
    payload = _profile_payload(Path(row.storage_path))
    entries = await _resolve_profile_entries(db, payload)
    duplicate_counts = Counter((str(entry["title"]).lower(), entry["year"]) for entry in entries)
    training_dir = Path(pipeline_settings.TRAINING_DATA_DIR)
    exemplars = []
    for entry in entries:
        key = (str(entry["title"]).lower(), entry["year"])
        exemplars.append(
            {
                "name": entry["name"],
                "title": entry["title"],
                "year": entry["year"],
                "movie_id": entry["movie_id"],
                "movie_title": entry["movie_title"],
                "tmdb_id": entry["tmdb_id"],
                "is_duplicate": duplicate_counts[key] > 1,
                "duplicate_count": duplicate_counts[key],
                "exists_in_training_dir": (training_dir / entry["name"]).exists(),
                "thumb_url": f"/api/taste/exemplars/{entry['name']}/image?size=thumb",
            }
        )
    exemplars.sort(key=lambda item: (-item["duplicate_count"], item["title"], item["name"]))
    return exemplars


async def delete_profile_exemplar(db: AsyncSession, artifact_id: str, exemplar_name: str) -> ArtifactSnapshot:
    from marquee.ml import profile_updater  # noqa: PLC0415

    row = await get_artifact(db, KIND_TASTE_PROFILE, artifact_id)
    if row.status != STATUS_ACTIVE:
        raise RuntimeError("only the active taste profile can be edited")
    removed = await asyncio.to_thread(profile_updater.remove_exemplar, exemplar_name)
    if not removed:
        raise FileNotFoundError(f"exemplar {exemplar_name!r} not found")
    return await _sync_active_row(db, row, source_mode="incremental")
