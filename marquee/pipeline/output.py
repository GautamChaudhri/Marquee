"""Stage 7 inspectable ranked output placement."""

from __future__ import annotations

import logging
import shutil
from dataclasses import dataclass, field
from pathlib import Path

import httpx

from marquee.core.poster_sources.tmdb import PosterCandidate
from marquee.pipeline.types import CandidateScore

logger = logging.getLogger(__name__)


@dataclass
class OutputResult:
    placed_count: int = 0
    gated_count: int = 0
    original_downloads: int = 0
    download_errors: list[str] = field(default_factory=list)
    original_download_status: dict[str, bool] = field(default_factory=dict)


def _orig_suffix(orig_filename: str) -> str:
    """Preserve the source extension so PNG/WebP bytes aren't labelled .jpg."""
    return Path(orig_filename).suffix or ".jpg"


def ranked_filename(score: CandidateScore) -> str:
    if score.rank is None or score.final_score is None:
        raise ValueError("Ranked output requires rank and final score")
    return (
        f"{score.rank}__{score.final_score:.4f}__"
        f"{Path(score.orig_filename).stem}{_orig_suffix(score.orig_filename)}"
    )


def place_gated(
    candidates: list[CandidateScore],
    gated_dir: Path,
) -> int:
    gated_dir.mkdir(parents=True, exist_ok=True)
    for candidate in candidates:
        reason = candidate.gate_reason or "unknown_gate"
        destination = gated_dir / (
            f"{reason}__{Path(candidate.orig_filename).stem}"
            f"{_orig_suffix(candidate.orig_filename)}"
        )
        shutil.copy2(candidate.image_path, destination)
        candidate.image_path = destination
    return len(candidates)


async def place_ranked(
    ranked: list[CandidateScore],
    *,
    candidate_map: dict[str, PosterCandidate],
    ranked_dir: Path,
    top_n: int = 5,
) -> OutputResult:
    ranked_dir.mkdir(parents=True, exist_ok=True)
    result = OutputResult()

    for score in ranked:
        destination = ranked_dir / ranked_filename(score)
        shutil.copy2(score.image_path, destination)
        score.image_path = destination
        result.placed_count += 1

    async with httpx.AsyncClient(timeout=60.0) as client:
        for score in ranked[:top_n]:
            candidate = candidate_map.get(score.orig_filename)
            if candidate is None:
                message = f"Missing PosterCandidate for {score.orig_filename}"
                logger.warning(message)
                result.download_errors.append(message)
                score.original_download = False
                result.original_download_status[score.orig_filename] = False
                continue
            try:
                response = await client.get(candidate.url(size="original"))
                response.raise_for_status()
                score.image_path.write_bytes(response.content)
                result.original_downloads += 1
                score.original_download = True
                result.original_download_status[score.orig_filename] = True
            except Exception as exc:
                message = f"{score.orig_filename}: {exc}"
                logger.warning("Original-resolution download failed: %s", message)
                result.download_errors.append(message)
                score.original_download = False
                result.original_download_status[score.orig_filename] = False

    return result
