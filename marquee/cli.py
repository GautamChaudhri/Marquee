"""The `marquee` command: one entry point for everything an operator runs."""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import re
import sys
import time
import warnings
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from marquee import __version__

MODEL_ITEMS = ("clip", "dino", "zeroshot", "yolo", "ocr", "aesthetic", "face")
RANKED_SHOWN = 10


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="marquee", description="Poster artwork for Radarr movie libraries."
    )
    parser.add_argument("--version", action="version", version=f"marquee {__version__}")
    commands = parser.add_subparsers(dest="command", required=True, metavar="command")

    doctor = commands.add_parser("doctor", help="check that this machine can run Marquee")
    doctor.add_argument("--json", action="store_true", help="print the report as JSON")

    models = commands.add_parser("models", help="build and check the model files")
    model_commands = models.add_subparsers(dest="models_command", required=True, metavar="command")
    fetch = model_commands.add_parser(
        "fetch",
        help="build every missing model and write the checksum manifest",
        description="Build every model that is missing, leave alone what is present, and "
        "write data/models/manifest.json. clip, dino, zeroshot, and aesthetic need the export "
        "tools.",
    )
    fetch.add_argument(
        "--force",
        action="append",
        default=[],
        choices=MODEL_ITEMS,
        metavar="ITEM",
        help=f"rebuild this item even if present (repeatable; one of {', '.join(MODEL_ITEMS)})",
    )

    taste = commands.add_parser("taste", help="build the taste profile")
    taste_commands = taste.add_subparsers(dest="taste_command", required=True, metavar="command")
    seed = taste_commands.add_parser(
        "seed",
        help="build the first taste profile from the library's poster.jpg files",
        description="Stage every movie folder's poster.jpg as a 500 px wide PNG exemplar at "
        "weight 1.0 and build the taste profile from them. Folders without a poster.jpg "
        "are ignored. Refuses to run again once a staged set exists unless told how.",
    )
    seed.add_argument(
        "--dry-run", action="store_true", help="scan and print the report; write nothing"
    )
    again = seed.add_mutually_exclusive_group()
    again.add_argument(
        "--rescan",
        action="store_true",
        help="scan the library again and replace the staged set and the profile",
    )
    again.add_argument(
        "--reuse",
        action="store_true",
        help="rebuild the profile from the staged set without reading the library",
    )

    run = commands.add_parser(
        "run",
        help="put one movie through the real pipeline",
        description="Find the movie through its library folder (the IMDb id in the movie "
        "file's name), rank its TMDB posters against the taste profile, and leave the "
        "ranked posters and the run archive in a new folder under data/runs/.",
    )
    run.add_argument("title", help='the title as the library folder names it, e.g. "Die Hard"')
    run.add_argument("--year", type=int, help="the year, when several movies share a title")
    run.add_argument(
        "--tmdb-id", type=int, help="use this TMDB id and the title as typed; skip the library"
    )
    run.add_argument("--json", action="store_true", help="print the summary as JSON")
    return parser


def _quiet_logging(level: str = "ERROR") -> None:
    """Keep the console to the command's own output; only real problems reach stderr."""
    from marquee.logging import setup_logging

    setup_logging(level=level)
    logging.captureWarnings(True)
    warnings.simplefilter("ignore")


def _fail(command: str, message: str) -> int:
    print(f"marquee {command}: {message}", file=sys.stderr)
    return 1


@dataclass(frozen=True)
class _MovieGateInputs:
    """The attributes OcrGateContext.from_movie reads, taken from TMDB's movie details."""

    director: str | None
    production_companies_json: list[str]
    tagline: str | None
    text_profile_id: str | None = None


def _run_folder_name(title: str, now: datetime) -> str:
    from marquee.library.subjects import normalise_title

    slug = re.sub(r"[^a-z0-9]+", "-", normalise_title(title)).strip("-") or "untitled"
    return f"{now.strftime('%Y%m%dT%H%M%SZ')}-{slug[:60]}"


def _stack_ranks(payload: dict[str, Any]) -> dict[str, int]:
    """File name -> design stack rank, from the run archive's diagnostic ledger."""
    ledger = payload.get("diagnostic_ledger")
    candidates = ledger.get("candidates", []) if isinstance(ledger, dict) else []
    return {
        str(c["orig_filename"]): int(c["stack_rank"])
        for c in candidates
        if isinstance(c, dict) and c.get("stack_rank") is not None
    }


def _ranked_row(row: dict[str, Any] | None, stack_ranks: dict[str, int]) -> dict[str, Any] | None:
    """One ranked candidate with its full stack label (``1A``: design stack 1, variant A)."""
    if row is None:
        return None
    stack = stack_ranks.get(str(row["orig_filename"]))
    letter = row.get("stack_label") or ""
    return {
        "rank": row["rank"],
        "stack": f"{stack}{letter}" if stack is not None else letter or None,
        "score": row["final_score"],
        "file": row["orig_filename"],
    }


def _run_summary(
    subject: Any, output: Any, run_dir: Path, stages: list[Any], seconds: float
) -> dict[str, Any]:
    stack_ranks = _stack_ranks(output.payload)
    return {
        "subject": {
            "title": subject.title,
            "tmdb_id": subject.tmdb_id,
            "imdb_id": subject.imdb_id,
            "folder": (
                f"{subject.folder.quality}/{subject.folder.name}" if subject.folder else None
            ),
        },
        "status": output.status,
        "counts": output.counts,
        "stages": [
            {"stage": e.stage, "survivors": e.survivors, "seconds": e.elapsed_s} for e in stages
        ],
        "ranked_total": len(output.ranked),
        "ranked": [_ranked_row(row, stack_ranks) for row in output.ranked[:RANKED_SHOWN]],
        "pick": _ranked_row(output.recommendation, stack_ranks),
        "run_dir": str(run_dir),
        "seconds": round(seconds, 1),
    }


def _score(row: dict[str, Any]) -> str:
    score = row["score"]
    return f"{score:.4f}" if isinstance(score, int | float) else "-     "


def _print_run_summary(summary: dict[str, Any]) -> None:
    counts = ", ".join(f"{name} {value}" for name, value in summary["counts"].items())
    print(f"status    {summary['status']}")
    print(f"counts    {counts}")
    ranked = summary["ranked"]
    if ranked:
        print(f"ranked    top {len(ranked)} of {summary['ranked_total']}")
        print("  rank  stack  score   file")
        for row in ranked:
            print(f"  {row['rank']:>4}  {row['stack'] or '-':<5}  {_score(row)}  {row['file']}")
    pick = summary["pick"]
    if pick:
        print(f"pick      {pick['stack']} {pick['file']} (score {_score(pick)})")
    else:
        print("pick      none (no candidate survived every gate)")
    print(f"run       {summary['run_dir']}")
    print(f"took      {summary['seconds']:.0f} s")


async def _run_async(args: argparse.Namespace, token: str) -> int:
    from marquee.config import settings
    from marquee.integrations.tmdb.client import TMDBClient
    from marquee.library.folders import scan_movie_roots
    from marquee.library.subjects import resolve_subject
    from marquee.pipeline.orchestrator import PosterSubjectInput
    from marquee.pipeline.single_run import run_single_subject
    from marquee.pipeline.text_profiles import OcrGateContext

    started = time.perf_counter()
    folders = [] if args.tmdb_id is not None else scan_movie_roots(settings.MOVIE_ROOTS)
    async with TMDBClient(read_access_token=token) as tmdb:
        subject = await resolve_subject(
            args.title, client=tmdb, folders=folders, year=args.year, tmdb_id=args.tmdb_id
        )
        details = await tmdb.get_movie_details(subject.tmdb_id)
    gate = OcrGateContext.from_movie(
        _MovieGateInputs(
            director=details.director,
            production_companies_json=list(details.production_companies),
            tagline=details.tagline,
        )
    )
    run_dir = settings.runs_dir / _run_folder_name(subject.title, datetime.now(UTC))

    if not args.json:
        where = (
            f", {subject.imdb_id}, {subject.folder.quality}/{subject.folder.name}"
            if subject.folder
            else ""
        )
        print(f"subject   {subject.title} (TMDB {subject.tmdb_id}{where})", flush=True)
        print("stages", flush=True)
    stages: list[Any] = []

    def on_progress(event: Any) -> None:
        if event.state != "end":
            return
        stages.append(event)
        if not args.json:
            survivors = "-" if event.survivors is None else event.survivors
            print(
                f"  {event.stage:<16} {survivors:>4} left  {event.elapsed_s or 0:7.2f} s",
                flush=True,
            )

    output = await run_single_subject(
        subject=PosterSubjectInput(title=subject.title, tmdb_id=subject.tmdb_id),
        run_dir=run_dir,
        ocr_gate=gate,
        progress=on_progress,
    )
    summary = _run_summary(subject, output, run_dir, stages, time.perf_counter() - started)
    if args.json:
        print(json.dumps(summary, indent=2, default=str))
    else:
        _print_run_summary(summary)
    return 0


def _run(args: argparse.Namespace) -> int:
    from marquee.config import pipeline_settings, settings
    from marquee.integrations.tmdb.exceptions import TMDBError
    from marquee.library.folders import LibraryRootError
    from marquee.library.subjects import SubjectLookupError

    # The console shows only the command's own lines and real errors; the run's full
    # log still reaches pipeline.log, which hangs off the "marquee" logger.
    _quiet_logging()
    logging.getLogger("marquee").setLevel(logging.INFO)
    token = settings.TMDB_READ_ACCESS_TOKEN
    if token is None or not token.get_secret_value():
        return _fail("run", "TMDB_READ_ACCESS_TOKEN is not set; add it to .env")
    profile = Path(pipeline_settings.TASTE_PROFILE_PATH)
    if not profile.is_file():
        return _fail("run", f"no taste profile at {profile}; run `marquee taste seed` first")
    try:
        return asyncio.run(_run_async(args, token.get_secret_value()))
    except (SubjectLookupError, LibraryRootError, TMDBError) as exc:
        return _fail("run", str(exc))


def main(argv: Sequence[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    if args.command == "doctor":
        from marquee import doctor

        # The checks load ONNX Runtime and PaddleOCR, which are chatty.
        _quiet_logging()
        return doctor.main(as_json=args.json)
    if args.command == "models" and args.models_command == "fetch":
        from marquee import model_fetch

        _quiet_logging()
        return model_fetch.fetch(force=args.force)
    if args.command == "taste" and args.taste_command == "seed":
        from marquee.library.folders import LibraryRootError
        from marquee.taste import seed

        _quiet_logging()
        mode: seed.Mode = "rescan" if args.rescan else "reuse" if args.reuse else "first"
        try:
            return seed.run_seed(mode=mode, dry_run=args.dry_run)
        except (seed.SeedError, LibraryRootError) as exc:
            print(f"marquee taste seed: {exc}", file=sys.stderr)
            return 1
    if args.command == "run":
        return _run(args)
    raise AssertionError(f"unhandled command {args.command!r}")


if __name__ == "__main__":
    raise SystemExit(main())
