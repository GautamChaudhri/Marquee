"""Poster deduplication — two-stage pipeline.

Stage 2 of the poster pipeline.  Removes duplicate posters before the
expensive OCR and AI scoring stages.

  **Stage 2a — SHA-256 exact dedup:**
  Hashes every file's raw bytes.  Identical files (same poster downloaded
  from different URLs, or redownloaded) are caught here.  Among duplicates,
  the highest-resolution version is kept.

  **Stage 2b — pHash perceptual dedup:**
  Computes a perceptual hash for each survivor.  Posters with Hamming
  distance ≤ ``DEDUP_PHASH_THRESHOLD`` (default 6) are considered
  near-duplicates — same visual composition but different compression,
  colour-space, or minor edits.

Group representative selection: an optional ``preference_by_name`` sort key
(title found, fewest residual text boxes, taste similarity) decides which
member of a duplicate group survives; resolution breaks remaining ties.
Without preference data the largest width×height wins (SHA-256 stage, where
duplicates are byte-identical anyway).

Salvaged from ``experiments/filtering/deduplicate_posters.py`` (SHA-256)
and extended with pHash.

Usage::

    from marquee.pipeline.deduper import PosterDeduper

    deduper = PosterDeduper()
    result = deduper.deduplicate(poster_paths)
    print(f"{result.initial} → sha256 removed {result.sha256_removed} "
          f"→ phash removed {result.phash_removed} → {result.final} survivors")
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass, field
from pathlib import Path

import imagehash
from PIL import Image, ImageFile

from marquee.core.pipeline_config import pipeline_settings

logger = logging.getLogger(__name__)

ImageFile.LOAD_TRUNCATED_IMAGES = True


# ---------------------------------------------------------------------------
# Result type
# ---------------------------------------------------------------------------


@dataclass
class DedupRemoval:
    """One candidate removed by an exact, perceptual, or size decision."""

    removed: Path
    kept: Path | None
    reason: str
    removed_hash: str | None = None
    kept_hash: str | None = None
    distance: int | None = None


@dataclass
class DedupResult:
    """Stats and survivors from a dedup run."""

    initial: int = 0                # input count
    survivors: list[Path] = field(default_factory=list)

    # Stage 2a
    sha256_removed: int = 0
    sha256_groups_found: int = 0

    # Stage 2b
    phash_removed: int = 0
    phash_groups_found: int = 0

    # Pre-filter
    size_filter_removed: int = 0    # removed by DEDUP_MIN_POSTER_WIDTH
    removals: list[DedupRemoval] = field(default_factory=list)

    @property
    def final(self) -> int:
        return len(self.survivors)


# ---------------------------------------------------------------------------
# Deduper
# ---------------------------------------------------------------------------


class PosterDeduper:
    """Two-stage poster deduplication: SHA-256 exact → pHash perceptual.

    Constructor arguments are optional — defaults come from config.

    Args:
        sha256_only: If True, skip pHash stage (debug / dry-run).
        min_width: Minimum poster width in pixels (default from config).
        phash_threshold: Hamming distance ≤ this → near-duplicate.
        preference_by_name: Optional sort key per filename (higher tuple wins)
            used to pick the cluster representative BEFORE the resolution
            tiebreak.  The pipeline passes (title_found, -residual_boxes,
            knn_sim) so a near-dupe group keeps its cleanest, most on-taste
            member instead of just the largest file.
    """

    def __init__(
        self,
        *,
        sha256_only: bool = False,
        min_width: int | None = None,
        phash_threshold: int | None = None,
        resolution_by_name: dict[str, tuple[int, int]] | None = None,
        preference_by_name: dict[str, tuple] | None = None,
    ):
        self._sha256_only = sha256_only
        self._min_width = (
            min_width if min_width is not None
            else pipeline_settings.DEDUP_MIN_POSTER_WIDTH
        )
        self._phash_threshold = (
            phash_threshold if phash_threshold is not None
            else pipeline_settings.DEDUP_PHASH_THRESHOLD
        )
        self._resolution_by_name = resolution_by_name or {}
        self._preference_by_name = preference_by_name or {}

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def deduplicate(self, poster_paths: list[Path]) -> DedupResult:
        """Run both dedup stages and return survivors with stats.

        Args:
            poster_paths: Paths to poster candidate images.

        Returns:
            ``DedupResult`` with survivors and per-stage counts.
        """
        result = DedupResult(initial=len(poster_paths))

        # --- Size pre-filter ---
        paths = self._filter_by_size(poster_paths, result)

        # --- Stage 2a: SHA-256 exact ---
        paths = self._sha256_dedup(paths, result)

        # --- Stage 2b: pHash perceptual ---
        if not self._sha256_only:
            paths = self._phash_dedup(paths, result)
        else:
            logger.info("pHash stage skipped (sha256_only=True)")

        result.survivors = paths

        logger.info(
            "Dedup complete: %d → sha256 -%d → phash -%d → %d survivors",
            result.initial,
            result.sha256_removed,
            result.phash_removed,
            result.final,
        )

        return result

    # ------------------------------------------------------------------
    # Size filter
    # ------------------------------------------------------------------

    def _filter_by_size(
        self, paths: list[Path], result: DedupResult
    ) -> list[Path]:
        """Remove candidates narrower than min_width pixels."""
        if self._min_width <= 0:
            return list(paths)

        kept: list[Path] = []
        for p in paths:
            original = self._resolution_by_name.get(p.name)
            if original is not None:
                # TMDB metadata carries the original dimensions — no need to
                # decode the file just to read its width.
                w = original[0]
            else:
                try:
                    with Image.open(p) as img:
                        w = img.width
                except Exception:
                    logger.warning("Could not read dimensions for %s — keeping", p.name)
                    kept.append(p)
                    continue

            if w >= self._min_width:
                kept.append(p)
            else:
                result.size_filter_removed += 1
                result.removals.append(
                    DedupRemoval(
                        removed=p,
                        kept=None,
                        reason="min_width",
                    )
                )
                logger.debug(
                    "Size-filtered %s: width=%d < min=%d",
                    p.name, w, self._min_width,
                )

        if result.size_filter_removed:
            logger.info(
                "Size filter removed %d poster(s) below %dpx width",
                result.size_filter_removed, self._min_width,
            )

        return kept

    # ------------------------------------------------------------------
    # Stage 2a: SHA-256 exact dedup
    # ------------------------------------------------------------------

    def _sha256_dedup(
        self, paths: list[Path], result: DedupResult
    ) -> list[Path]:
        """Hash every file. Group by hash. Keep highest res per group."""
        hashes: dict[str, list[Path]] = {}

        for p in paths:
            file_hash = self._hash_file(p)
            hashes.setdefault(file_hash, []).append(p)

        survivors: list[Path] = []
        for file_hash, group in hashes.items():
            kept = self._pick_best_resolution(group)
            if len(group) > 1:
                result.sha256_groups_found += 1
                result.sha256_removed += len(group) - 1
                for path in group:
                    if path != kept:
                        result.removals.append(
                            DedupRemoval(
                                removed=path,
                                kept=kept,
                                reason="sha256",
                                removed_hash=file_hash,
                                kept_hash=file_hash,
                                distance=0,
                            )
                        )
                logger.debug(
                    "SHA-256 dupe group (x%d): %s",
                    len(group), {g.name for g in group},
                )
            survivors.append(kept)

        return survivors

    @staticmethod
    def _hash_file(filepath: Path) -> str:
        """Compute SHA-256 hash of a file (64 KB chunked reading)."""
        hasher = hashlib.sha256()
        with open(filepath, "rb") as f:
            for chunk in iter(lambda: f.read(65536), b""):
                hasher.update(chunk)
        return hasher.hexdigest()

    # ------------------------------------------------------------------
    # Stage 2b: pHash perceptual dedup
    # ------------------------------------------------------------------

    def _phash_dedup(
        self, paths: list[Path], result: DedupResult
    ) -> list[Path]:
        """Compute pHash for each survivor. Group near-duplicates. Keep best res."""
        # Compute hash objects once upfront (parsing them per-comparison was
        # the old O(n^2) hot spot).
        scored: list[tuple[imagehash.ImageHash | None, Path]] = []
        for p in paths:
            try:
                with Image.open(p) as img:
                    ph = imagehash.phash(img)
            except Exception as exc:
                logger.warning("pHash failed for %s: %s — keeping", p.name, exc)
                ph = None
            scored.append((ph, p))

        # Greedy clustering: for each image, find all near-duplicates
        # not already assigned to a group
        assigned: set[int] = set()
        groups: list[list[tuple[imagehash.ImageHash, Path, int]]] = []

        for i, (ph_i, path_i) in enumerate(scored):
            if i in assigned or ph_i is None:
                continue

            group = [(ph_i, path_i, 0)]
            assigned.add(i)

            for j, (ph_j, path_j) in enumerate(scored):
                if j in assigned or ph_j is None:
                    continue
                dist = ph_i - ph_j
                if dist <= self._phash_threshold:
                    group.append((ph_j, path_j, dist))
                    assigned.add(j)

            groups.append(group)

        # Add any unassigned (pHash-failed) as singletons
        for i in range(len(scored)):
            if i not in assigned:
                groups.append([(scored[i][0], scored[i][1], 0)])

        # Keep best resolution per group
        survivors: list[Path] = []
        for group in groups:
            paths = [item[1] for item in group]
            kept = self._pick_best_resolution(paths)
            kept_hash = next(item[0] for item in group if item[1] == kept)
            if len(group) > 1:
                result.phash_groups_found += 1
                result.phash_removed += len(group) - 1
                for phash, path, _distance in group:
                    if path != kept:
                        result.removals.append(
                            DedupRemoval(
                                removed=path,
                                kept=kept,
                                reason="phash",
                                removed_hash=str(phash) if phash is not None else None,
                                kept_hash=str(kept_hash) if kept_hash is not None else None,
                                distance=(
                                    phash - kept_hash
                                    if phash is not None and kept_hash is not None
                                    else None
                                ),
                            )
                        )
                logger.debug(
                    "pHash near-dupe group (x%d): %s",
                    len(group), {path.name for _, path, _ in group},
                )
            survivors.append(kept)

        return survivors

    # ------------------------------------------------------------------
    # Resolution tiebreaker
    # ------------------------------------------------------------------

    def _pick_best_resolution(self, paths: list[Path]) -> Path:
        """Return the preferred group representative.

        Sort key is (preference tuple, width×height): the caller-supplied
        preference (title found, fewest residual boxes, taste similarity)
        decides first; resolution only breaks remaining ties.  Without
        preference data this degrades to the original pure-resolution pick.
        If dimensions can't be read, the path is still included (sorted last).
        """
        if len(paths) == 1:
            return paths[0]

        scored: list[tuple[tuple, int, Path]] = []
        for p in paths:
            preference = self._preference_by_name.get(p.name, ())
            original = self._resolution_by_name.get(p.name)
            if original is not None:
                scored.append((preference, original[0] * original[1], p))
                continue
            try:
                with Image.open(p) as img:
                    scored.append((preference, img.width * img.height, p))
            except Exception:
                scored.append((preference, 0, p))

        scored.sort(key=lambda x: (x[0], x[1]), reverse=True)
        best = scored[0][2]
        log = logger.info if any(x[0] for x in scored) else logger.debug
        log(
            "DEDUP KEEP | file=%s | preference=%s | resolution=%dpx2 | "
            "group_size=%d",
            best.name,
            scored[0][0] or None,
            scored[0][1],
            len(paths),
        )
        return best
