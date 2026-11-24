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
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from marquee.config import settings
from marquee.db.models import TasteProfile

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

    A profile already registered under this hash is made active again rather than added.
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
    return row
