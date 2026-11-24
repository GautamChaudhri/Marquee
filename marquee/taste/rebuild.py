"""The taste profile: which file is the registered build, and how a new one is published.

Every profile Marquee builds or registers has a ``taste_profiles`` row and a copy in
``data/ml/profiles/<sha256>.npz``; at most one row is active, and ``TASTE_PROFILE_PATH``
holds that row's file. A rebuild (the ``taste_rebuild`` job) is made whole from the active
exemplars' kept images, then published: the file goes into ``profiles/``, then over
``TASTE_PROFILE_PATH`` with one rename, then its row becomes the active one.

The file and its row are written in two steps, file first. A crash between them leaves
``TASTE_PROFILE_PATH`` holding a complete profile whose row is not yet active (or does not
exist): the doctor warns, and the job's next attempt finds the built file in ``profiles/``
and finishes the publication without building again. Nothing here imports the engine at
module level, so the worker can use it without loading a model.
"""

from __future__ import annotations

import hashlib
import logging
import os
import re
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from marquee.config import settings
from marquee.db.models import ResidualTraining, TasteProfile

logger = logging.getLogger(__name__)

PROFILE_SUFFIX = ".npz"
KEEP_NEWEST = 5
_PROFILE_NAME = re.compile(r"[0-9a-f]{64}\.npz")


class ProfileError(Exception):
    """A profile file cannot be read, kept, or published; the message says why."""


@dataclass(frozen=True)
class ProfileFacts:
    """What a profile file holds, as far as the records need to know."""

    sha256: str
    positives: int
    negatives: int
    poster_names: list[str]
    poster_sha256s: list[str]
    negative_names: list[str]


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_profile(path: Path) -> ProfileFacts:
    """Load a profile file and say what it holds. Blocking."""
    from marquee.ml.artifact_codec import decode_unicode_list

    try:
        sha = file_sha256(path)
        with np.load(path, allow_pickle=False) as data:
            positives = int(data["embeddings"].shape[0])
            negatives = (
                int(data["neg_embeddings"].shape[0]) if "neg_embeddings" in data.files else 0
            )
            names = decode_unicode_list(data["poster_names"])
            hashes = (
                decode_unicode_list(data["poster_sha256s"])
                if "poster_sha256s" in data.files
                else []
            )
            negative_names = (
                decode_unicode_list(data["neg_poster_names"])
                if "neg_poster_names" in data.files
                else []
            )
    except (OSError, KeyError, ValueError) as exc:
        raise ProfileError(f"{path.name} cannot be read as a taste profile: {exc}") from exc
    return ProfileFacts(sha, positives, negatives, names, hashes, negative_names)


def profile_copy(file_name: str) -> Path:
    if not _PROFILE_NAME.fullmatch(file_name):
        raise ProfileError(f"{file_name!r} is not a kept profile's name")
    return settings.profiles_dir / file_name


def keep_copy(path: Path, sha256: str) -> str:
    """Keep a copy of the profile at ``path`` in ``profiles/``, named by its hash. Blocking.

    Written under a temporary name, synced, renamed, and read back; one already there with
    the right hash is kept.
    """
    name = f"{sha256}{PROFILE_SUFFIX}"
    target = profile_copy(name)
    if target.is_file() and file_sha256(target) == sha256:
        return name
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.parent / f".{name}.part"
    try:
        with path.open("rb") as source, temporary.open("wb") as handle:
            while chunk := source.read(1 << 20):
                handle.write(chunk)
            handle.flush()
            os.fsync(handle.fileno())
        if file_sha256(temporary) != sha256:
            raise ProfileError(f"{path.name} changed while it was copied")
        os.replace(temporary, target)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise
    return name


def install(file_name: str, sha256: str, destination: Path) -> None:
    """Put a kept profile over ``destination`` with one rename. Blocking.

    The copy goes to a temporary name beside the destination, is synced and checked, then
    renamed over it, so ``destination`` always holds a complete profile: the old or the new.
    """
    source = profile_copy(file_name)
    if file_sha256(source) != sha256:
        raise ProfileError(f"the kept profile {file_name} does not hold its own bytes")
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.parent / f".{destination.name}.{sha256[:12]}.part"
    try:
        with source.open("rb") as handle, temporary.open("wb") as out:
            while chunk := handle.read(1 << 20):
                out.write(chunk)
            out.flush()
            os.fsync(out.fileno())
        if file_sha256(temporary) != sha256:
            raise ProfileError("the profile copy beside TASTE_PROFILE_PATH did not read back")
        os.replace(temporary, destination)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise
    directory = os.open(destination.parent, os.O_RDONLY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)


# ── The registry ──────────────────────────────────────────────────────────


async def active_profile(session: AsyncSession) -> TasteProfile | None:
    return await session.scalar(select(TasteProfile).where(TasteProfile.is_active))


async def profile_by_sha(session: AsyncSession, sha256: str | None) -> TasteProfile | None:
    if sha256 is None:
        return None
    return await session.scalar(select(TasteProfile).where(TasteProfile.sha256 == sha256))


async def register(
    session: AsyncSession,
    *,
    sha256: str,
    revision: str,
    source: str,
    positives: int,
    negatives: int,
    movie_ids: list[int],
    file_name: str,
    job_id: int | None = None,
) -> TasteProfile:
    """Make this profile the active one, in the caller's transaction.

    A profile already registered under this hash is made active again rather than added. An
    active residual artifact trained for another profile becomes inactive in the same
    transaction: the engine binds an artifact to the profile it was trained for.
    """
    await session.execute(
        update(TasteProfile)
        .where(TasteProfile.is_active, TasteProfile.sha256 != sha256)
        .values(is_active=False)
    )
    await session.flush()
    row = await profile_by_sha(session, sha256)
    if row is None:
        row = TasteProfile(
            sha256=sha256,
            revision=revision,
            source=source,
            positives=positives,
            negatives=negatives,
            positive_movie_ids=sorted(set(movie_ids)),
            file_name=file_name,
            job_id=job_id,
            is_active=True,
        )
        session.add(row)
    else:
        row.is_active = True
    await session.flush()
    await session.execute(
        update(ResidualTraining)
        .where(ResidualTraining.is_active, ResidualTraining.profile_id.is_distinct_from(row.id))
        .values(is_active=False, deactivated_at=func.now())
    )
    await session.flush()
    return row


# ── A rebuild ─────────────────────────────────────────────────────────────

POSITIVE = "positive"
NEGATIVE = "negative"
BUILT = "profile.npz"
MARKER = "built.json"


class RebuildRefusedError(ProfileError):
    """The active exemplars cannot make a profile; no retry changes that."""


@dataclass(frozen=True)
class Staged:
    """One exemplar as the trainer sees it: its name in the build folder and its weight."""

    name: str
    file_name: str
    sha256: str
    weight: float
    movie_id: int


@dataclass(frozen=True)
class BuildPlan:
    revision: str
    positives: list[Staged]
    negatives: list[Staged]

    @property
    def movie_ids(self) -> list[int]:
        return sorted({row.movie_id for row in self.positives})

    def weights(self) -> tuple[dict[str, float], dict[str, float]]:
        return (
            {row.name: row.weight for row in self.positives},
            {row.name: row.weight for row in self.negatives},
        )


async def plan_build(session: AsyncSession) -> BuildPlan:
    """What a profile built now is built from: the active exemplars.

    Positives are staged under their ``staged_name`` (the trainer reads titles from it, and
    orders the profile by it); negatives under their kept name, once per image. Refused when
    two active positives share a staged name, or when there are fewer positives than
    ``TASTE_SEEDING_MIN_POSTERS``.
    """
    from marquee.config import pipeline_settings
    from marquee.db.models import Movie
    from marquee.db.models.taste import POLARITY_POSITIVE
    from marquee.taste import exemplars

    rows = await exemplars.active(session)
    positives: dict[str, Staged] = {}
    negatives: dict[str, Staged] = {}
    for row in rows:
        if row.polarity == POLARITY_POSITIVE:
            assert row.staged_name is not None
            if (other := positives.get(row.staged_name)) is not None:
                titles = dict(
                    (
                        await session.execute(
                            select(Movie.id, Movie.title).where(
                                Movie.id.in_([other.movie_id, row.movie_id])
                            )
                        )
                    ).all()
                )
                raise RebuildRefusedError(
                    f"two active positives are staged as {row.staged_name!r}: "
                    f"{titles.get(other.movie_id)} (movie {other.movie_id}) and "
                    f"{titles.get(row.movie_id)} (movie {row.movie_id})"
                )
            positives[row.staged_name] = Staged(
                row.staged_name, row.file_name, row.sha256, row.weight, row.movie_id
            )
        elif row.file_name not in negatives:
            negatives[row.file_name] = Staged(
                row.file_name, row.file_name, row.sha256, row.weight, row.movie_id
            )
    minimum = pipeline_settings.TASTE_SEEDING_MIN_POSTERS
    if len(positives) < minimum:
        raise RebuildRefusedError(
            f"only {len(positives)} active positive exemplars; a profile needs at least "
            f"{minimum} (TASTE_SEEDING_MIN_POSTERS), so the current one stays"
        )
    return BuildPlan(
        revision=exemplars.revision_of(rows),
        positives=sorted(positives.values(), key=lambda s: s.name),
        negatives=sorted(negatives.values(), key=lambda s: s.name),
    )


def stage(plan: BuildPlan, build_dir: Path) -> None:
    """Copy every exemplar's kept image into ``positive/`` and ``negative/``. Blocking.

    Each copy is checked against its hash first. Anything an earlier attempt left is
    removed. ``rebuild_profile`` reads a ``movie/`` sub-folder specially, so the trainer is
    given ``positive/`` itself, which has no sub-folders.
    """
    import shutil

    from marquee.taste import exemplars

    if build_dir.is_symlink():
        raise ProfileError(f"the build folder {build_dir.name} is a symbolic link")
    if build_dir.exists():
        shutil.rmtree(build_dir)
    for folder, rows in ((POSITIVE, plan.positives), (NEGATIVE, plan.negatives)):
        target = build_dir / folder
        target.mkdir(parents=True)
        for row in rows:
            (target / row.name).write_bytes(exemplars.read_kept(row.file_name, row.sha256))


def verify_built(path: Path, plan: BuildPlan) -> ProfileFacts:
    """The built file must load and hold exactly what was staged. Blocking."""
    facts = read_profile(path)
    if (facts.positives, facts.negatives) != (len(plan.positives), len(plan.negatives)):
        raise ProfileError(
            f"the built profile holds {facts.positives} positives and {facts.negatives} "
            f"negatives; {len(plan.positives)} and {len(plan.negatives)} were staged"
        )
    if sorted(facts.poster_names) != [row.name for row in plan.positives]:
        raise ProfileError("the built profile's exemplars are not the ones staged")
    return facts


def mark_built(build_dir: Path, revision: str, sha256: str) -> None:
    """Say that this build folder's profile is built and kept, so a later attempt of the job
    publishes it without building again. Blocking."""
    import json

    temporary = build_dir / f".{MARKER}.part"
    temporary.write_text(json.dumps({"revision": revision, "sha256": sha256}))
    os.replace(temporary, build_dir / MARKER)


def read_marker(build_dir: Path) -> tuple[str, str] | None:
    """``(revision, sha256)`` of a profile an earlier attempt built and kept, if any."""
    import json

    try:
        value = json.loads((build_dir / MARKER).read_text(encoding="utf-8"))
        revision, sha = str(value["revision"]), str(value["sha256"])
    except (OSError, ValueError, KeyError, TypeError):
        return None
    return revision, sha


async def kept_names(session: AsyncSession) -> set[str]:
    """The profile copies to keep: the newest :data:`KEEP_NEWEST`, the seed's, and the
    active one."""
    from marquee.db.models.taste import PROFILE_SOURCE_SEED

    newest = await session.scalars(
        select(TasteProfile.file_name).order_by(TasteProfile.id.desc()).limit(KEEP_NEWEST)
    )
    special = await session.scalars(
        select(TasteProfile.file_name).where(
            TasteProfile.is_active | (TasteProfile.source == PROFILE_SOURCE_SEED)
        )
    )
    return set(newest) | set(special)


def prune(keep: set[str]) -> list[str]:
    """Delete the kept profile copies not in ``keep``; returns their names. Blocking."""
    removed = []
    folder = settings.profiles_dir
    if not folder.is_dir():
        return removed
    for path in sorted(folder.iterdir()):
        if _PROFILE_NAME.fullmatch(path.name) and path.name not in keep and path.is_file():
            path.unlink()
            removed.append(path.name)
    return removed
