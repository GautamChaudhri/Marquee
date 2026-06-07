"""Test pipeline endpoint — end-to-end stage-by-stage testing.

Downloads all TMDB posters for a movie, then runs each pipeline stage
sequentially, copying survivors into nested subdirectories for inspection.

  POST /api/test/pipeline/movie/{movie_id}

After completion, the output directory structure is::

    experiments/data/<movie_title>/
    ├── <all_downloaded_posters>.jpg
    └── sha256/
        ├── <sha256_survivors>.jpg
        └── phash/
            ├── <phash_survivors>.jpg
            └── ocr/
                ├── <ocr_survivors>.jpg
                └── clip/
                    ├── 1.jpg  (highest taste score)
                    ├── 2.jpg
                    ├── 3.jpg
                    ├── 4.jpg
                    └── 5.jpg
"""

from __future__ import annotations

import asyncio
import logging
import re
import shutil
import time
from pathlib import Path

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from marquee.api.deps import get_tmdb
from marquee.config import settings
from marquee.core.poster_sources.tmdb import PosterCandidate, TMDBClient
from marquee.database import get_db
from marquee.models import Movie
from marquee.ml.scorer import TasteScorer
from marquee.pipeline.deduper import PosterDeduper
from marquee.pipeline.ocr_filter import PosterTextFilter

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/test", tags=["test"])

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
_EXPERIMENTS_DATA = Path(__file__).parent.parent.parent / "experiments" / "ocr-first"

# Concurrency limit for TMDB poster downloads
_DOWNLOAD_SEMAPHORE = asyncio.Semaphore(5)
_DOWNLOAD_SIZE = "w500"  # fast processing — top 5 re-downloaded at original


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _sanitise_filename(name: str) -> str:
    """Replace filesystem-unsafe characters in a directory name."""
    return re.sub(r'[<>:"/\\|?*]', "_", name).strip()


def _log_stage(name: str, start: float) -> None:
    """Log stage completion with elapsed time."""
    elapsed = time.monotonic() - start
    logger.info("=" * 60)
    logger.info("  STAGE: %s — completed in %.1fs", name, elapsed)
    logger.info("=" * 60)


async def _download_poster(
    poster: PosterCandidate,
    dest_dir: Path,
    client: httpx.AsyncClient,
) -> str:
    """Download one poster. Returns 'downloaded', 'skipped', or error string."""
    filename = poster.file_path.lstrip("/").split("/")[-1]
    dest = dest_dir / filename

    if dest.exists():
        return "skipped"

    url = poster.url(size=_DOWNLOAD_SIZE)
    try:
        async with _DOWNLOAD_SEMAPHORE:
            resp = await client.get(url)
            resp.raise_for_status()
            dest.write_bytes(resp.content)
        return "downloaded"
    except Exception as exc:
        return f"{filename}: {exc}"


# ---------------------------------------------------------------------------
# Endpoint
# ---------------------------------------------------------------------------


@router.post("/pipeline/movie/{movie_id}")
async def test_pipeline_movie(
    movie_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
    tmdb: TMDBClient = Depends(get_tmdb),
):
    """Run the full poster pipeline on one movie for testing and tuning.

    1. Look up movie by Marquee ID → get title + TMDB ID
    2. Download every poster TMDB returns
    3. SHA-256 dedup → survivors copied to ``sha256/``
    4. pHash dedup → survivors copied to ``phash/``
    5. OCR text filter → survivors copied to ``ocr/``
    6. CLIP taste scoring → top-5 copied to ``clip/``, renamed 1–5

    All intermediate results are preserved in nested subdirectories for
    manual inspection and threshold tuning.
    """
    t_total = time.monotonic()

    # ── 1. Look up movie ──────────────────────────────────────────────
    movie = (
        await db.execute(select(Movie).where(Movie.id == movie_id))
    ).scalar_one_or_none()

    if movie is None:
        # Check if ANY movies exist — suggest sync if DB is empty
        count = (await db.execute(select(Movie))).scalars().all()
        total = len(count)
        if total == 0:
            raise HTTPException(
                status_code=400,
                detail=f"No movies in database — run POST /api/sync/all first",
            )
        raise HTTPException(
            status_code=404,
            detail=f"Movie id={movie_id} not found. Database has {total} movies "
            f"(id range: {count[0].id}–{count[-1].id}).",
        )
    if movie.tmdb_id is None:
        raise HTTPException(
            status_code=400,
            detail=f"Movie '{movie.title}' (id={movie_id}) has no tmdb_id — run sync first",
        )

    logger.info("")
    logger.info("╔══════════════════════════════════════════════════════════════╗")
    logger.info("║  TEST PIPELINE — %s (id=%d, tmdb=%d)", movie.title, movie_id, movie.tmdb_id)
    logger.info("╚══════════════════════════════════════════════════════════════╝")
    logger.info("")

    # ── 2. Create output directory ──────────────────────────────────
    safe_title = _sanitise_filename(movie.title)
    out_dir = _EXPERIMENTS_DATA / safe_title
    out_dir.mkdir(parents=True, exist_ok=True)
    logger.info("Output directory: %s", out_dir)

    # ── 3. Fetch TMDB posters ────────────────────────────────────────
    t_fetch = time.monotonic()
    logger.info("Fetching posters from TMDB for tmdb_id=%d ...", movie.tmdb_id)

    candidates = await tmdb.get_movie_images(movie.tmdb_id)
    logger.info("TMDB returned %d poster candidates", len(candidates))

    # ── 4. Download all posters ──────────────────────────────────────
    logger.info("Downloading %d posters (size=%s, concurrency=5) ...",
                len(candidates), _DOWNLOAD_SIZE)

    downloaded = 0
    skipped = 0
    errors: list[str] = []
    # Map filename → PosterCandidate for re-download at original size later
    candidate_map: dict[str, PosterCandidate] = {}

    async with httpx.AsyncClient(timeout=60.0) as client:
        tasks = [_download_poster(p, out_dir, client) for p in candidates]
        results = await asyncio.gather(*tasks)

    for i, r in enumerate(results):
        filename = candidates[i].file_path.lstrip("/").split("/")[-1]
        candidate_map[filename] = candidates[i]
        if r == "downloaded":
            downloaded += 1
        elif r == "skipped":
            skipped += 1
        else:
            errors.append(r)

    for e in errors:
        logger.error("  Download error: %s", e)

    fetch_duration = time.monotonic() - t_fetch
    logger.info(
        "Download complete: %d new, %d cached, %d errors (%.1fs)",
        downloaded, skipped, len(errors), fetch_duration,
    )

    # Get all downloaded files (including previously cached)
    all_files = sorted(
        p for p in out_dir.iterdir()
        if p.is_file() and p.suffix.lower() in
        {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tiff"}
    )

    if not all_files:
        raise HTTPException(
            status_code=500,
            detail="No posters were downloaded or found in output directory",
        )

    logger.info("Total posters available: %d\n", len(all_files))

    # ── 5. Stage 2a: SHA-256 dedup ────────────────────────────────────
    t_sha = time.monotonic()
    sha_dir = out_dir / "sha256"
    sha_dir.mkdir(exist_ok=True)
    sha_rej_dir = out_dir / "sha256_rejected"
    sha_rej_dir.mkdir(exist_ok=True)

    logger.info("--- SHA-256 dedup: %d input ---", len(all_files))
    deduper_sha = PosterDeduper(sha256_only=True)
    sha_result = deduper_sha.deduplicate(all_files)

    for p in sha_result.survivors:
        shutil.copy2(p, sha_dir / p.name)

    # Copy rejected
    survivor_names = {p.name for p in sha_result.survivors}
    sha_rejected = [p for p in all_files if p.name not in survivor_names]
    for p in sha_rejected:
        shutil.copy2(p, sha_rej_dir / p.name)

    _log_stage("SHA-256 dedup", t_sha)
    logger.info(
        "  Input:      %d\n"
        "  Removed:    %d (size: %d)\n"
        "  SHA groups: %d\n"
        "  Survivors:  %d → %s/\n"
        "  Rejected:   %d → %s/",
        len(all_files),
        sha_result.sha256_removed + sha_result.size_filter_removed,
        sha_result.size_filter_removed,
        sha_result.sha256_groups_found,
        sha_result.final,
        sha_dir.name,
        len(sha_rejected),
        sha_rej_dir.name,
    )

    sha_files = sorted(p for p in sha_dir.iterdir() if p.is_file())

    # ── 6. Stage 3: OCR filter ────────────────────────────────────────
    t_ocr = time.monotonic()
    ocr_dir = sha_dir / "ocr"
    ocr_dir.mkdir(exist_ok=True)
    ocr_rej_dir = sha_dir / "ocr_rejected"
    ocr_rej_dir.mkdir(exist_ok=True)

    # Silence PaddleOCR model-loading noise
    import os as _os
    import sys as _sys
    _stashed_stderr = _sys.stderr
    _stashed_stdout = _sys.stdout
    _sys.stderr = open(_os.devnull, "w")
    _sys.stdout = open(_os.devnull, "w")
    import warnings
    warnings.filterwarnings("ignore", message="No ccache found")
    try:
        logger.info("--- OCR filter: %d input (title=%r) ---", len(sha_files), movie.title)
        ocr_filter = PosterTextFilter(movie.title, director=None)
        ocr_accepted, ocr_rejected, accepted_texts = ocr_filter.filter_batch(
            sha_files, include_texts=True,
        )
        # Track text-free posters for CLIP penalty (require ≥ 3 chars to count)
        text_free_posters: set[str] = {
            p.name for p, t in accepted_texts.items() if len(t.strip()) < 3
        }
        logger.info(
            "  Text-free accepted: %d (of %d total)",
            len(text_free_posters), len(ocr_accepted),
        )
    finally:
        _sys.stderr.close()
        _sys.stdout.close()
        _sys.stderr = _stashed_stderr
        _sys.stdout = _stashed_stdout

    for p in ocr_accepted:
        shutil.copy2(p, ocr_dir / p.name)
    for p, _ in ocr_rejected:
        shutil.copy2(p, ocr_rej_dir / p.name)

    _log_stage("OCR filter", t_ocr)
    logger.info(
        "  Input:    %d\n"
        "  Accepted: %d\n"
        "  Rejected: %d\n"
        "  → %s/%s/\n"
        "  Rejected → %s/%s/",
        len(sha_files),
        len(ocr_accepted),
        len(ocr_rejected),
        sha_dir.name, ocr_dir.name,
        sha_dir.name, ocr_rej_dir.name,
    )

    # Sample rejection reasons
    rejection_samples: list[dict] = []
    for path, text in ocr_rejected[:5]:
        logger.info("  REJECTED: %s — \"%s\"", path.name, text[:100])
        rejection_samples.append({"file": path.name, "text": text[:120]})

    ocr_files = sorted(p for p in ocr_dir.iterdir() if p.is_file())

    # ── 7. Stage 2b: pHash dedup (now after OCR) ─────────────────────
    t_phash = time.monotonic()
    phash_dir = ocr_dir / "phash"
    phash_dir.mkdir(exist_ok=True)
    phash_rej_dir = ocr_dir / "phash_rejected"
    phash_rej_dir.mkdir(exist_ok=True)

    logger.info("--- pHash dedup: %d input ---", len(ocr_files))
    deduper_phash = PosterDeduper()
    phash_result = deduper_phash.deduplicate(ocr_files)

    for p in phash_result.survivors:
        shutil.copy2(p, phash_dir / p.name)

    # Copy rejected
    phash_survivor_names = {p.name for p in phash_result.survivors}
    phash_rejected = [p for p in ocr_files if p.name not in phash_survivor_names]
    for p in phash_rejected:
        shutil.copy2(p, phash_rej_dir / p.name)

    _log_stage("pHash dedup", t_phash)
    logger.info(
        "  Input:      %d\n"
        "  pH removed: %d\n"
        "  pH groups:  %d\n"
        "  Survivors:  %d → %s/%s/%s/\n"
        "  Rejected:   %d → %s/%s/",
        len(ocr_files),
        phash_result.phash_removed,
        phash_result.phash_groups_found,
        phash_result.final,
        sha_dir.name, ocr_dir.name, phash_dir.name,
        len(phash_rejected),
        sha_dir.name, phash_rej_dir.name,
    )

    phash_files = sorted(p for p in phash_dir.iterdir() if p.is_file())

    # ── 8. Stage 4: CLIP scoring ──────────────────────────────────────
    t_clip = time.monotonic()
    clip_dir = phash_dir / "clip"
    clip_dir.mkdir(exist_ok=True)
    clip_rej_dir = phash_dir / "clip_rejected"
    clip_rej_dir.mkdir(exist_ok=True)

    logger.info("--- CLIP scoring: %d input (negative filter DISABLED) ---", len(phash_files))

    scorer = TasteScorer(neg_threshold=2.0)
    clip_result = scorer.rank(phash_files)

    # Apply text-free penalty: push text-free posters to the end of the
    # ranked list.  They are only selected if fewer than 5 posters with
    # text survive.  Keep their relative CLIP ordering among themselves.
    if text_free_posters:
        with_text = [s for s in clip_result.accepted if s.image_path.name not in text_free_posters]
        without_text = [s for s in clip_result.accepted if s.image_path.name in text_free_posters]

        # Log which posters are classified as text-free vs with-text
        logger.info("  --- CLIP classification ---")
        for s in clip_result.accepted:
            is_free = s.image_path.name in text_free_posters
            label = "TEXT-FREE → end" if is_free else "with-text  ✓"
            logger.info(
                "    %s  score=%.4f  emb=%.4f  color=%.4f  %s",
                label, s.final_score, s.emb_similarity,
                s.color_similarity, s.image_path.name,
            )

        clip_result.accepted = with_text + without_text
        logger.info(
            "  --- Repositioned: %d kept, %d text-free moved to end ---",
            len(with_text), len(without_text),
        )

    scorer.print_ranked_table(clip_result, top_n=5)

    # Copy top-5, rename, re-download at original
    for rank, s in enumerate(clip_result.ranked[:5], 1):
        dest = clip_dir / f"{rank}.jpg"
        shutil.copy2(s.image_path, dest)

    # Copy rank 6+ to clip_rejected
    for s in clip_result.ranked[5:]:
        shutil.copy2(s.image_path, clip_rej_dir / s.image_path.name)

    # Re-download top 5 at original resolution
    logger.info("  Re-downloading top-5 at original size ...")
    re_dl = 0
    async with httpx.AsyncClient(timeout=60.0) as client:
        for rank, s in enumerate(clip_result.ranked[:5], 1):
            orig_filename = s.image_path.name
            if orig_filename in candidate_map:
                url = candidate_map[orig_filename].url(size="original")
                dest = clip_dir / f"{rank}.jpg"
                try:
                    resp = await client.get(url)
                    resp.raise_for_status()
                    dest.write_bytes(resp.content)
                    re_dl += 1
                except Exception as exc:
                    logger.warning("  Failed to re-download #%d at original: %s", rank, exc)
    logger.info("  Re-downloaded %d/5 at original size", re_dl)

    _log_stage("CLIP scoring", t_clip)
    logger.info(
        "  Input:           %d\n"
        "  Top-5 → %s/%s/%s/%s/ (renamed 1.jpg–5.jpg)\n"
        "  Rank 6+ → %s/%s/%s/",
        len(phash_files),
        sha_dir.name, ocr_dir.name, phash_dir.name, clip_dir.name,
        sha_dir.name, ocr_dir.name, clip_rej_dir.name,
    )

    # Log top-5 scores
    for rank, s in enumerate(clip_result.ranked[:5], 1):
        logger.info(
            "  #%d: %s → score=%.4f  emb=%.4f  color=%.4f  neg=%.4f",
            rank, s.image_path.name,
            s.final_score, s.emb_similarity,
            s.color_similarity, s.neg_sim_max,
        )

    # ── 9. Build response ─────────────────────────────────────────────
    total_duration = time.monotonic() - t_total

    response = {
        "movie_id": movie.id,
        "title": movie.title,
        "tmdb_id": movie.tmdb_id,
        "output_dir": str(out_dir),
        "total_duration_s": round(total_duration, 1),
        "stages": {
            "fetch": {
                "posters_found": len(candidates),
                "downloaded": downloaded,
                "skipped": skipped,
                "errors": len(errors),
                "total_available": len(all_files),
                "duration_s": round(fetch_duration, 1),
            },
            "sha256": {
                "input": len(all_files),
                "survivors": sha_result.final,
                "removed": sha_result.sha256_removed,
                "size_filtered": sha_result.size_filter_removed,
                "groups_found": sha_result.sha256_groups_found,
                "duration_s": round(time.monotonic() - t_sha, 1),
            },
            "phash": {
                "input": len(sha_files),
                "survivors": phash_result.final,
                "removed": phash_result.phash_removed,
                "groups_found": phash_result.phash_groups_found,
                "duration_s": round(time.monotonic() - t_phash, 1),
            },
            "ocr": {
                "input": len(phash_files),
                "survivors": len(ocr_accepted),
                "removed": len(ocr_rejected),
                "duration_s": round(time.monotonic() - t_ocr, 1),
                "sample_rejections": rejection_samples,
            },
            "clip": {
                "input": len(ocr_files),
                "accepted": len(clip_result.accepted),
                "neg_filter_disabled": True,
                "duration_s": round(time.monotonic() - t_clip, 1),
                "top5": [
                    {
                        "rank": i + 1,
                        "score": round(s.final_score, 4),
                        "emb_similarity": round(s.emb_similarity, 4),
                        "color_similarity": round(s.color_similarity, 4),
                        "neg_sim_max": round(s.neg_sim_max, 4),
                        "original_file": s.image_path.name,
                    }
                    for i, s in enumerate(clip_result.ranked[:5])
                ],
            },
        },
    }

    logger.info("")
    logger.info("╔══════════════════════════════════════════════════════════════╗")
    logger.info("║  PIPELINE COMPLETE — total %.1fs", total_duration)
    logger.info("║  Output: %s", out_dir)
    logger.info("╚══════════════════════════════════════════════════════════════╝")
    logger.info("")

    return response
