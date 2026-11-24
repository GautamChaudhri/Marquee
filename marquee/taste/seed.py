"""`marquee taste seed`: build the first taste profile from the owner's library posters.

Every movie folder's ``poster.jpg`` was picked by hand, so each one that decodes becomes a
positive exemplar at weight 1.0. A folder with no ``poster.jpg`` is ignored entirely, even
when it holds another image. Byte-identical posters give one exemplar; two folders with the
same name and different posters keep the first in path order.

Each exemplar is staged as a lossless PNG 500 px wide (``standardize_width``), named after
its folder (``Die Hard (1988).png``), because the trainer measures exemplars the way the
pipeline measures candidates: at the TMDB ``w500`` working size, with the title read from the
file name. The staged set and ``movies.json`` (what was seeded, from where, with which
checksum) stay in ``TASTE_SEEDING_DIR`` as the lasting record of the owner's picks: from M5
on, deploys replace library posters, so the library itself stops being that record. That is
why the command refuses to scan again unless told to.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import shutil
import tempfile
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

import cv2
import numpy as np
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from marquee.config import pipeline_settings, settings
from marquee.db.models import Exemplar as ExemplarRow
from marquee.db.models import Movie, TasteProfile
from marquee.db.models.taste import (
    POLARITY_POSITIVE,
    PROFILE_SOURCE_SEED,
    SOURCE_SEED,
    STATE_ACTIVE,
)
from marquee.library.folders import MovieFolder, scan_movie_roots
from marquee.ml.visual_features import STANDARD_WIDTH, standardize_width

logger = logging.getLogger(__name__)

LIBRARY = "movies"
SEED_WEIGHT = 1.0
RECORD_FORMAT = 1
TITLE_BOX_FEATURE = "title_height_frac"

Mode = Literal["first", "rescan", "reuse"]
ProfileBuilder = Callable[[Path, Mapping[str, float]], Path]


class SeedError(Exception):
    """The seed cannot run as asked; the message says why and what to do."""


@dataclass(frozen=True)
class Exemplar:
    folder: MovieFolder
    staged_name: str
    source_sha256: str
    source_width: int
    source_height: int

    @property
    def enlarged(self) -> bool:
        return self.source_width < STANDARD_WIDTH


@dataclass(frozen=True)
class Skipped:
    folder: MovieFolder
    reason: str


@dataclass
class SeedPlan:
    scanned: int = 0
    exemplars: list[Exemplar] = field(default_factory=list)
    skipped: list[Skipped] = field(default_factory=list)
    duplicates: list[Skipped] = field(default_factory=list)
    same_name: list[Skipped] = field(default_factory=list)
    file_named: list[MovieFolder] = field(default_factory=list)


@dataclass(frozen=True)
class ProfileSummary:
    path: Path
    exemplars: int
    has_dino: bool
    title_boxes: int


@dataclass(frozen=True)
class SeedPaths:
    seed_dir: Path
    profile: Path

    @property
    def staged_dir(self) -> Path:
        return self.seed_dir / LIBRARY

    @property
    def record(self) -> Path:
        return self.seed_dir / f"{LIBRARY}.json"

    def staged_set_exists(self) -> bool:
        return self.staged_dir.exists() or self.record.exists()

    @classmethod
    def from_settings(cls) -> SeedPaths:
        return cls(
            seed_dir=Path(pipeline_settings.TASTE_SEEDING_DIR),
            profile=Path(pipeline_settings.TASTE_PROFILE_PATH),
        )


def required_models() -> dict[str, Path]:
    """Every model the trainer uses. A profile built without one would be quietly weaker."""
    pth = Path(pipeline_settings.AESTHETIC_MODEL_PATH)
    return {
        "CLIP": Path(pipeline_settings.CLIP_MODEL_PATH),
        "aesthetic head": pth if pth.suffix == ".npz" else pth.with_suffix(".npz"),
        "face detector": Path(pipeline_settings.FACE_MODEL_PATH),
        "DINOv2": Path(pipeline_settings.DINO_MODEL_PATH),
        "YOLO person": Path(pipeline_settings.PERSON_MODEL_PATH),
        "zero-shot axes": Path(pipeline_settings.ZEROSHOT_AXES_PATH),
    }


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _label(folder: MovieFolder) -> str:
    return f"{folder.quality}/{folder.name}"


# ── Selection ──────────────────────────────────────────────────────────────


def plan_seed(folders: Sequence[MovieFolder]) -> SeedPlan:
    """Decide what seeds, reading each poster.jpg but writing nothing."""
    plan = SeedPlan(scanned=len(folders))
    by_sha: dict[str, Exemplar] = {}
    by_name: dict[str, Exemplar] = {}
    for folder in sorted(folders, key=lambda f: str(f.path)):
        if folder.has_file_named_image:
            plan.file_named.append(folder)
        if folder.poster is None:
            others = f" (holds {len(folder.images)} other image(s))" if folder.images else ""
            plan.skipped.append(Skipped(folder, f"no poster.jpg{others}"))
            continue
        image = cv2.imread(str(folder.poster), cv2.IMREAD_COLOR)
        if image is None:
            plan.skipped.append(Skipped(folder, "poster.jpg does not decode"))
            continue
        sha = _sha256(folder.poster)
        if sha in by_sha:
            kept = by_sha[sha].folder
            plan.duplicates.append(Skipped(folder, f"byte-identical to {_label(kept)}"))
            continue
        if folder.name in by_name:
            kept = by_name[folder.name].folder
            plan.same_name.append(
                Skipped(folder, f"same folder name as {_label(kept)}, different poster")
            )
            continue
        height, width = image.shape[:2]
        exemplar = Exemplar(folder, f"{folder.name}.png", sha, int(width), int(height))
        by_sha[sha] = exemplar
        by_name[folder.name] = exemplar
        plan.exemplars.append(exemplar)
    return plan


# ── Staging ────────────────────────────────────────────────────────────────


def _stage_one(exemplar: Exemplar, target_dir: Path) -> None:
    poster = exemplar.folder.poster
    assert poster is not None
    image = cv2.imread(str(poster), cv2.IMREAD_COLOR)
    if image is None:
        raise SeedError(f"{poster} no longer decodes; run the seed again")
    if _sha256(poster) != exemplar.source_sha256:
        raise SeedError(f"{poster} changed while the seed was running; run it again")
    standardized = standardize_width(image)
    target = target_dir / exemplar.staged_name
    if not cv2.imwrite(str(target), standardized):
        raise SeedError(f"could not write {target}")


def _record(plan: SeedPlan) -> dict[str, object]:
    def skipped(entries: list[Skipped]) -> list[dict[str, str]]:
        return [
            {
                "folder": e.folder.name,
                "quality": e.folder.quality,
                "path": str(e.folder.path),
                "reason": e.reason,
            }
            for e in entries
        ]

    return {
        "format": RECORD_FORMAT,
        "library": LIBRARY,
        "seeded_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "staged_width": STANDARD_WIDTH,
        "exemplars": [
            {
                "staged_name": e.staged_name,
                "source_path": str(e.folder.poster),
                "source_sha256": e.source_sha256,
                "source_width": e.source_width,
                "source_height": e.source_height,
                "quality": e.folder.quality,
                "title": e.folder.title,
                "year": e.folder.year,
                "imdb_id": e.folder.imdb_id,
                "weight": SEED_WEIGHT,
            }
            for e in plan.exemplars
        ],
        "skipped": skipped(plan.skipped + plan.duplicates + plan.same_name),
        "file_named_images": [
            {
                "folder": f.name,
                "quality": f.quality,
                "images": [image.name for image in f.file_named_images],
                "has_poster": f.poster is not None,
            }
            for f in plan.file_named
        ],
    }


def stage(plan: SeedPlan, paths: SeedPaths) -> None:
    """Write every staged PNG and the record, then swap them into place together.

    Everything is written into a temporary folder first; the previous staged set (if any)
    is replaced only once every file is written.
    """
    paths.seed_dir.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix=".staging-", dir=paths.seed_dir))
    try:
        new_dir = work / LIBRARY
        new_dir.mkdir()
        for exemplar in plan.exemplars:
            _stage_one(exemplar, new_dir)
        record = work / paths.record.name
        record.write_text(json.dumps(_record(plan), indent=2) + "\n", encoding="utf-8")

        old = work / "previous"
        old.mkdir()
        if paths.staged_dir.exists():
            os.replace(paths.staged_dir, old / LIBRARY)
        if paths.record.exists():
            os.replace(paths.record, old / paths.record.name)
        try:
            os.replace(new_dir, paths.staged_dir)
            os.replace(record, paths.record)
        except OSError:
            # Put the previous set back so a failed swap leaves what was there.
            for name in (LIBRARY, paths.record.name):
                if (old / name).exists():
                    target = paths.seed_dir / name
                    if target.is_dir():
                        shutil.rmtree(target)
                    elif target.exists():
                        target.unlink()
                    os.replace(old / name, target)
            raise
    finally:
        shutil.rmtree(work, ignore_errors=True)


def load_staged_weights(paths: SeedPaths) -> dict[str, float]:
    """The staged set's weights, from its record, checked against the files on disk."""
    if not paths.record.is_file() or not paths.staged_dir.is_dir():
        raise SeedError(
            f"no staged set in {paths.seed_dir}; run `marquee taste seed` first (without --reuse)"
        )
    record = json.loads(paths.record.read_text(encoding="utf-8"))
    weights = {e["staged_name"]: float(e["weight"]) for e in record["exemplars"]}
    on_disk = {p.name for p in paths.staged_dir.iterdir() if p.is_file()}
    if on_disk != set(weights):
        missing = sorted(set(weights) - on_disk)
        extra = sorted(on_disk - set(weights))
        raise SeedError(
            f"{paths.staged_dir} does not match {paths.record.name}: "
            f"missing {missing[:5]}, unexpected {extra[:5]}; rerun with --rescan"
        )
    return weights


# ── Building ───────────────────────────────────────────────────────────────


def build_with_trainer(training_dir: Path, weights: Mapping[str, float]) -> Path:
    from marquee.taste.trainer import rebuild_profile

    return rebuild_profile(training_dir=training_dir, positive_weights=weights)


def summarize_profile(path: Path) -> ProfileSummary:
    from marquee.ml.artifact_codec import decode_unicode_list
    from marquee.ml.calibration import CALIB_NAMES_KEY, CALIB_VALUES_KEY

    with np.load(path, allow_pickle=False) as data:
        exemplars = int(data["embeddings"].shape[0])
        has_dino = "dino_embeddings" in data.files
        names = decode_unicode_list(data[CALIB_NAMES_KEY]) if CALIB_NAMES_KEY in data.files else []
        values = np.asarray(data[CALIB_VALUES_KEY]) if CALIB_VALUES_KEY in data.files else None
    title_boxes = 0
    if values is not None and TITLE_BOX_FEATURE in names:
        row = values[names.index(TITLE_BOX_FEATURE)]
        title_boxes = int(np.isfinite(row).sum())
    return ProfileSummary(path, exemplars, has_dino, title_boxes)


# ── The command ────────────────────────────────────────────────────────────


def _print_plan(plan: SeedPlan, echo: Callable[[str], None]) -> None:
    echo(f"scanned       {plan.scanned} movie folders")
    echo(f"exemplars     {len(plan.exemplars)} at weight {SEED_WEIGHT}")
    sections = [
        ("skipped", plan.skipped),
        ("duplicates", plan.duplicates),
        ("same name", plan.same_name),
    ]
    for title, entries in sections:
        echo(f"{title:<13} {len(entries)}")
        for entry in entries:
            echo(f"  {_label(entry.folder)}: {entry.reason}")
    echo(f"named after the movie file, for the owner to rule on: {len(plan.file_named)}")
    for folder in plan.file_named:
        images = ", ".join(image.name for image in folder.file_named_images)
        state = "also has poster.jpg" if folder.poster else "no poster.jpg, so ignored"
        echo(f"  {_label(folder)}: {images} ({state})")
    enlarged = [e for e in plan.exemplars if e.enlarged]
    echo(f"enlarged to {STANDARD_WIDTH} px wide: {len(enlarged)}")
    for exemplar in enlarged:
        echo(f"  {_label(exemplar.folder)}: {exemplar.source_width}x{exemplar.source_height}")


def run_seed(
    *,
    mode: Mode = "first",
    dry_run: bool = False,
    roots: Sequence[Path] | None = None,
    paths: SeedPaths | None = None,
    models: Mapping[str, Path] | None = None,
    build: ProfileBuilder = build_with_trainer,
    echo: Callable[[str], None] = print,
) -> int:
    """Run the seed and return the process exit code. Raises :class:`SeedError` on refusal."""
    started = time.perf_counter()
    paths = paths or SeedPaths.from_settings()
    roots = list(settings.MOVIE_ROOTS if roots is None else roots)
    if dry_run and mode == "reuse":
        raise SeedError("--dry-run scans the library; it cannot be combined with --reuse")

    if not dry_run:
        if mode == "first" and paths.staged_set_exists():
            raise SeedError(
                f"a staged set already exists in {paths.seed_dir}. It is the record of the "
                "posters picked by hand, and a new scan could read deployed posters as picks. "
                "Use --reuse to rebuild the profile from it, or --rescan to scan the library "
                "again and replace it."
            )
        missing = [
            name for name, path in (models or required_models()).items() if not path.exists()
        ]
        if missing:
            raise SeedError(
                f"missing {', '.join(missing)}; run `marquee models fetch` first. "
                "A profile built without them would be weaker."
            )

    if mode == "reuse":
        weights = load_staged_weights(paths)
        echo(f"reusing       {len(weights)} staged exemplars in {paths.staged_dir}")
    else:
        plan = plan_seed(scan_movie_roots(roots))
        _print_plan(plan, echo)
        if dry_run:
            echo(f"dry run: nothing written; took {time.perf_counter() - started:.0f} s")
            return 0
        if not plan.exemplars:
            raise SeedError("no poster.jpg decoded; nothing to seed")
        stage(plan, paths)
        weights = {e.staged_name: SEED_WEIGHT for e in plan.exemplars}
        echo(
            f"staged        {len(weights)} PNG files, {STANDARD_WIDTH} px wide, in {paths.staged_dir}"
        )
        echo(f"record        {paths.record}")

    profile_path = build(paths.staged_dir, weights)
    summary = summarize_profile(profile_path)
    echo(
        f"title box     {summary.title_boxes} of {summary.exemplars} exemplars "
        f"(a finite {TITLE_BOX_FEATURE})"
    )
    echo(
        f"profile       {summary.path} ({summary.exemplars} exemplars, "
        f"DINOv2 embeddings: {'yes' if summary.has_dino else 'no'})"
    )
    echo(f"took          {time.perf_counter() - started:.0f} s")
    return 0


# ── The seed as records (from M5b on) ──────────────────────────────────────


@dataclass
class ImportReport:
    """What ``marquee taste import-seed`` found and did."""

    entries: int = 0
    problems: list[str] = field(default_factory=list)
    created: int = 0
    already: bool = False
    profile_sha256: str | None = None
    profile_id: int | None = None
    revision: str | None = None


def _staged_hashes(paths: SeedPaths, names: list[str]) -> dict[str, str | None]:
    out: dict[str, str | None] = {}
    for name in names:
        path = paths.staged_dir / name
        out[name] = _sha256(path) if path.is_file() and not path.is_symlink() else None
    return out


async def import_seed(session: AsyncSession, *, paths: SeedPaths | None = None) -> ImportReport:
    """Make the staged seed the first exemplar records, and register the profile built from it.

    Each entry of ``movies.json`` is matched to a movie by its IMDb id; its staged file is
    kept in the exemplar store and becomes an active positive (source ``seed``, the record's
    weight, the name it was staged under). The profile file is then registered as it is. It
    never builds a profile and never writes ``TASTE_PROFILE_PATH``.

    If anything does not match, it writes nothing and lists what is wrong in the report.
    A second import changes nothing and says so. The caller commits.
    """
    from marquee.taste import exemplars, rebuild

    paths = paths or SeedPaths.from_settings()
    report = ImportReport()
    try:
        record = json.loads(await asyncio.to_thread(paths.record.read_text, encoding="utf-8"))
        entries = list(record["exemplars"])
    except (OSError, ValueError, KeyError, TypeError) as exc:
        report.problems.append(f"{paths.record} cannot be read: {exc}")
        return report
    report.entries = len(entries)

    existing = list(await session.scalars(select(ExemplarRow).order_by(ExemplarRow.id)))
    registered = await session.scalar(
        select(TasteProfile).where(TasteProfile.source == PROFILE_SOURCE_SEED)
    )
    seeded = [row for row in existing if row.source == SOURCE_SEED]
    if existing or registered is not None:
        if registered is not None and len(seeded) == len(entries):
            report.already = True
            report.created = 0
            report.profile_sha256 = registered.sha256
            report.profile_id = registered.id
            report.revision = registered.revision
        else:
            report.problems.append(
                f"exemplar records already exist ({len(existing)}, {len(seeded)} from the seed) "
                "but do not match the seed record; nothing was imported"
            )
        return report

    names = [str(entry.get("staged_name")) for entry in entries]
    imdb_ids = [entry.get("imdb_id") for entry in entries]
    rows = await session.execute(
        select(Movie.id, Movie.imdb_id).where(Movie.imdb_id.in_([i for i in imdb_ids if i]))
    )
    by_imdb: dict[str, list[int]] = {}
    for movie_id, imdb_id in rows:
        by_imdb.setdefault(imdb_id, []).append(movie_id)
    movie_of: dict[str, int] = {}
    for name, imdb_id in zip(names, imdb_ids, strict=True):
        found = by_imdb.get(imdb_id or "", [])
        if not found:
            report.problems.append(f"{name}: no movie has IMDb id {imdb_id}")
        elif len(found) > 1:
            report.problems.append(f"{name}: {len(found)} movies have IMDb id {imdb_id}")
        else:
            movie_of[name] = found[0]
    seen: dict[int, str] = {}
    for name, movie_id in movie_of.items():
        if movie_id in seen:
            report.problems.append(f"{name} and {seen[movie_id]} are both movie {movie_id}")
        seen[movie_id] = name
    if len(set(names)) != len(names):
        report.problems.append("movies.json names one staged file twice")
    for entry in entries:
        weight = entry.get("weight")
        if isinstance(weight, bool) or not isinstance(weight, int | float) or not 0 < weight <= 1:
            report.problems.append(
                f"{entry.get('staged_name')}: weight {weight!r} is not in (0, 1]"
            )

    try:
        facts = await asyncio.to_thread(rebuild.read_profile, paths.profile)
    except (rebuild.ProfileError, FileNotFoundError) as exc:
        report.problems.append(f"the profile cannot be read: {exc}")
        return report
    if facts.positives != len(entries):
        report.problems.append(
            f"the profile holds {facts.positives} exemplars and movies.json lists {len(entries)}"
        )
    if facts.negatives:
        report.problems.append(f"the profile holds {facts.negatives} negatives; a seed has none")
    if sorted(facts.poster_names) != sorted(names):
        report.problems.append("the profile's exemplars are not the files movies.json lists")
    hashes = await asyncio.to_thread(_staged_hashes, paths, names)
    in_profile = dict(zip(facts.poster_names, facts.poster_sha256s, strict=False))
    for name, digest in hashes.items():
        if digest is None:
            report.problems.append(f"{name}: the staged file is missing")
        elif in_profile.get(name) not in (None, digest):
            report.problems.append(
                f"{name}: the staged file is not the one the profile was built from"
            )
    if report.problems:
        return report

    now = datetime.now(UTC)
    created: list[ExemplarRow] = []
    for entry, name in zip(entries, names, strict=True):
        digest = hashes[name]
        assert digest is not None
        kept = await asyncio.to_thread(exemplars.keep_file, paths.staged_dir / name, digest)
        created.append(
            ExemplarRow(
                movie_id=movie_of[name],
                polarity=POLARITY_POSITIVE,
                weight=float(entry["weight"]),
                source=SOURCE_SEED,
                state=STATE_ACTIVE,
                sha256=digest,
                file_name=kept,
                staged_name=name,
                created_at=now,
            )
        )
    file_name = await asyncio.to_thread(rebuild.keep_copy, paths.profile, facts.sha256)
    session.add_all(created)
    await session.flush()
    revision = exemplars.revision_of(created)
    profile = await rebuild.register(
        session,
        sha256=facts.sha256,
        revision=revision,
        source=PROFILE_SOURCE_SEED,
        positives=facts.positives,
        negatives=facts.negatives,
        movie_ids=list(movie_of.values()),
        file_name=file_name,
    )
    report.created = len(created)
    report.profile_sha256 = facts.sha256
    report.profile_id = profile.id
    report.revision = revision
    return report
