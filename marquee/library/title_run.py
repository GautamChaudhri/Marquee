"""Run one movie, named by its title, through the real pipeline.

``marquee run`` and the ``engine_probe`` job both call :func:`run_title`. It resolves the
title through the library folders (or takes a TMDB id as given), fetches TMDB's details for
the text gate, picks a new run folder under ``data/runs/``, and runs the pipeline there. It
writes nothing to the database.

The engine is imported inside the function, not at the top of the module, so importing this
module loads no inference library.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from marquee.config import pipeline_settings, settings
from marquee.library.subjects import ResolvedSubject, normalise_title

if TYPE_CHECKING:
    from marquee.pipeline.orchestrator import PosterPipelineOutput
    from marquee.pipeline.runner import ProgressCallback, ShouldCancel


class TitleRunError(Exception):
    """The run cannot start; the message says why."""


@dataclass(frozen=True)
class TitleRun:
    subject: ResolvedSubject
    output: PosterPipelineOutput
    run_dir: Path


@dataclass(frozen=True)
class _MovieGateInputs:
    """The attributes OcrGateContext.from_movie reads, taken from TMDB's movie details."""

    director: str | None
    production_companies_json: list[str]
    tagline: str | None
    text_profile_id: str | None = None


def run_folder_name(title: str, now: datetime) -> str:
    """``<UTC timestamp>-<title slug>``, the name of a new run folder."""
    slug = re.sub(r"[^a-z0-9]+", "-", normalise_title(title)).strip("-") or "untitled"
    return f"{now.strftime('%Y%m%dT%H%M%SZ')}-{slug[:60]}"


def stack_ranks(payload: dict[str, Any]) -> dict[str, int]:
    """File name -> design stack rank, from the run archive's diagnostic ledger."""
    ledger = payload.get("diagnostic_ledger")
    candidates = ledger.get("candidates", []) if isinstance(ledger, dict) else []
    return {
        str(c["orig_filename"]): int(c["stack_rank"])
        for c in candidates
        if isinstance(c, dict) and c.get("stack_rank") is not None
    }


def ranked_row(row: dict[str, Any] | None, ranks: dict[str, int]) -> dict[str, Any] | None:
    """One ranked candidate with its full stack label (``1A``: design stack 1, variant A)."""
    if row is None:
        return None
    stack = ranks.get(str(row["orig_filename"]))
    letter = row.get("stack_label") or ""
    return {
        "rank": row["rank"],
        "stack": f"{stack}{letter}" if stack is not None else letter or None,
        "score": row["final_score"],
        "file": row["orig_filename"],
    }


def _tmdb_token() -> str:
    token = settings.TMDB_READ_ACCESS_TOKEN
    if token is None or not token.get_secret_value():
        raise TitleRunError("TMDB_READ_ACCESS_TOKEN is not set; add it to .env")
    return token.get_secret_value()


def _check_taste_profile() -> None:
    profile = Path(pipeline_settings.TASTE_PROFILE_PATH)
    if not profile.is_file():
        raise TitleRunError(f"no taste profile at {profile}; run `marquee taste seed` first")


async def run_title(
    title: str,
    *,
    year: int | None = None,
    tmdb_id: int | None = None,
    progress: ProgressCallback | None = None,
    should_cancel: ShouldCancel | None = None,
    on_resolved: Callable[[ResolvedSubject], None] | None = None,
) -> TitleRun:
    """Resolve ``title`` and run it through the pipeline in a new run folder.

    ``on_resolved`` is called once the movie is known, before the pipeline starts. Raises
    :class:`TitleRunError` when the run cannot start, and the lookup's and TMDB's own errors
    when the title does not lead to one movie.
    """
    from marquee.integrations.tmdb.client import TMDBClient
    from marquee.library.folders import scan_movie_roots
    from marquee.library.subjects import resolve_subject
    from marquee.pipeline.orchestrator import PosterSubjectInput
    from marquee.pipeline.single_run import run_single_subject
    from marquee.pipeline.text_profiles import OcrGateContext

    token = _tmdb_token()
    _check_taste_profile()
    folders = [] if tmdb_id is not None else scan_movie_roots(settings.MOVIE_ROOTS)
    async with TMDBClient(read_access_token=token) as tmdb:
        subject = await resolve_subject(
            title, client=tmdb, folders=folders, year=year, tmdb_id=tmdb_id
        )
        details = await tmdb.get_movie_details(subject.tmdb_id)
    gate = OcrGateContext.from_movie(
        _MovieGateInputs(
            director=details.director,
            production_companies_json=list(details.production_companies),
            tagline=details.tagline,
        )
    )
    run_dir = settings.runs_dir / run_folder_name(subject.title, datetime.now(UTC))
    if on_resolved is not None:
        on_resolved(subject)
    output = await run_single_subject(
        subject=PosterSubjectInput(title=subject.title, tmdb_id=subject.tmdb_id),
        run_dir=run_dir,
        ocr_gate=gate,
        progress=progress,
        should_cancel=should_cancel,
    )
    return TitleRun(subject=subject, output=output, run_dir=run_dir)
