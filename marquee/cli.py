"""The `marquee` command: one entry point for everything an operator runs."""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
import warnings
from collections.abc import Sequence
from typing import Any

from marquee import __version__

MODEL_ITEMS = ("clip", "dino", "zeroshot", "yolo", "ocr", "aesthetic", "face")
JOBS_SHOWN = 50
# The states in marquee.db.models.job, repeated so building the parser imports no database code.
JOB_STATES = ("queued", "running", "succeeded", "failed", "cancelled")
# Likewise from marquee.db.models.run and marquee.artwork.runs.
RUN_STATES = ("running", "completed", "failed", "cancelled")
RUN_SCOPES = ("never-run", "no-poster", "all")


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

    taste = commands.add_parser("taste", help="the taste profile and its exemplars")
    taste_commands = taste.add_subparsers(dest="taste_command", required=True, metavar="command")
    seed = taste_commands.add_parser(
        "seed",
        help="build the first taste profile from the library's poster.jpg files",
        description="Stage every movie folder's poster.jpg as a 500 px wide PNG exemplar at "
        "weight 1.0 and build the taste profile from them. Folders without a poster.jpg "
        "are ignored. Refuses to run again once a staged set exists unless told how, and in "
        "every form once exemplar records exist (`marquee taste rebuild` builds from them).",
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
    taste_rebuild = taste_commands.add_parser(
        "rebuild",
        help="rebuild the taste profile from the exemplar records, as a job",
        description="Queue a taste_rebuild of the active exemplars and follow it. It builds a "
        "new profile from their kept images and puts it in place with one rename. Without "
        "--force it does nothing when the active profile was built from the same exemplars.",
    )
    taste_rebuild.add_argument(
        "--force", action="store_true", help="build even when the profile is up to date"
    )
    taste_rebuild.add_argument(
        "--no-wait", action="store_true", help="queue it even with no worker running, and return"
    )
    taste_status = taste_commands.add_parser(
        "status",
        help="exemplars, the active profile, agreement, and what the residual would get",
    )
    taste_status.add_argument("--json", action="store_true", help="print it as JSON")
    taste_commands.add_parser(
        "import-seed",
        help="make the staged seed the first exemplar records and register its profile",
        description="Match each poster in data/ml/seed/movies.json to its movie by IMDb id, "
        "keep its staged file as an active positive exemplar, and register the taste profile "
        "file as it is. Writes nothing if anything does not match. Never builds a profile.",
    )

    run = commands.add_parser(
        "run",
        help="put one movie through the real pipeline, as a job",
        description="Find the movie in the database by its title (case ignored), queue a "
        "poster_run job for it, and follow it to the end: the worker ranks the movie's TMDB "
        "posters against the taste profile and records the run, its candidates, and the "
        "pick. Needs a running worker unless --no-wait is given. Writes nothing to the "
        "media library unless DEPLOY_POLICY says so.",
    )
    run.add_argument("title", help='the title as Radarr lists it, e.g. "Die Hard"')
    run.add_argument("--year", type=int, help="the year, when several movies share a title")
    run.add_argument("--movie-id", type=int, help="the movie's id, when several share a title")
    run.add_argument(
        "--no-wait", action="store_true", help="queue the job and exit; do not follow it"
    )
    run.add_argument("--json", action="store_true", help="print the summary as JSON")

    runs_cmd = commands.add_parser("runs", help="list and inspect runs, and queue batches of them")
    runs_commands = runs_cmd.add_subparsers(dest="runs_command", required=True, metavar="command")
    runs_list = runs_commands.add_parser("list", help=f"the newest {JOBS_SHOWN} runs")
    runs_list.add_argument("--movie-id", type=int, help="only this movie's runs")
    runs_list.add_argument("--state", choices=RUN_STATES, help="only runs in this state")
    runs_list.add_argument("--json", action="store_true", help="print the runs as JSON")
    runs_show = runs_commands.add_parser("show", help="one run and its candidates")
    runs_show.add_argument("id", type=int)
    runs_show.add_argument("--json", action="store_true", help="print the run as JSON")
    runs_batch = runs_commands.add_parser(
        "batch",
        help="queue runs of many movies as one batch",
        description="never-run: eligible movies with no run yet; no-poster: eligible movies "
        "with no poster; all: every eligible movie (present, with a file and TMDB details). "
        "A movie whose run is already queued or running is left out.",
    )
    runs_batch.add_argument("--scope", required=True, choices=RUN_SCOPES)
    runs_batch.add_argument("--limit", type=int, help="at most this many movies, by title")

    deploy_cmd = commands.add_parser(
        "deploy",
        help="write a run's pick (or another candidate) into the movie's folder, as a job",
        description="Queue the owner's deploy of a candidate of a completed run: the run's "
        "pick unless --candidate names another. The worker fetches TMDB's original, backs up "
        "the poster.jpg already there, writes and verifies the new one, and records it.",
    )
    deploy_cmd.add_argument("run_id", type=int)
    deploy_cmd.add_argument("--candidate", type=int, help="a candidate id of that run")
    deployments_cmd = commands.add_parser("deployments", help="the writes Marquee made")
    deployments_commands = deployments_cmd.add_subparsers(
        dest="deployments_command", required=True, metavar="command"
    )
    deployments_list = deployments_commands.add_parser(
        "list", help=f"the newest {JOBS_SHOWN} deployments"
    )
    deployments_list.add_argument("--movie-id", type=int, help="only this movie's")
    deployments_list.add_argument("--json", action="store_true", help="print them as JSON")
    restore_cmd = commands.add_parser(
        "restore",
        help="put back what a deployment replaced, as a job",
        description="Queue a restore that puts back what was in the folder before the "
        "deployment, from its backup, or removes the poster when there was none. Any "
        "deployment of a movie can be restored, not only the newest. What is in the folder now "
        "is backed up first.",
    )
    restore_cmd.add_argument("deployment_id", type=int)

    review = commands.add_parser(
        "review",
        help="review what the AI picked: approve, pick, rank, reject, or take one back",
        description="Each act of feedback is recorded once, keeps a copy of what was shown, "
        "and changes the exemplars at once. approve, pick, and rank also deploy the chosen "
        "poster unless given --no-deploy, and any change to the exemplars queues a taste "
        "rebuild. With a worker running, the command follows the jobs it queued.",
    )
    review_commands = review.add_subparsers(dest="review_command", required=True, metavar="cmd")
    review_list = review_commands.add_parser(
        "list", help="movies whose latest run waits for a review, policy deploys first"
    )
    review_list.add_argument("--json", action="store_true", help="print them as JSON")
    review_show = review_commands.add_parser(
        "show", help="a run, its ranked candidates with their ids, and its feedback"
    )
    review_show.add_argument("run_id", type=int)
    review_show.add_argument("--json", action="store_true", help="print it as JSON")
    approve = review_commands.add_parser("approve", help="keep the run's pick")
    approve.add_argument("run_id", type=int)
    pick = review_commands.add_parser("pick", help="choose another candidate of the run")
    pick.add_argument("run_id", type=int)
    pick.add_argument("--candidate", type=int, required=True, help="the candidate's id")
    rank = review_commands.add_parser(
        "rank", help="put candidates in order, best first, and send some to the hate pile"
    )
    rank.add_argument("run_id", type=int)
    rank.add_argument("--order", type=_id_list, required=True, help="candidate ids, e.g. 12,9,14")
    rank.add_argument("--hate", type=_id_list, default=[], help="candidate ids you dislike")
    for command in (approve, pick, rank):
        command.add_argument(
            "--no-deploy", action="store_true", help="record it without deploying the poster"
        )
    reject = review_commands.add_parser("reject", help="none of these: dislike the candidates")
    reject.add_argument("run_id", type=int)
    reject.add_argument(
        "--hate", type=_id_list, default=[], help="candidate ids (default: every one shown)"
    )
    revoke = review_commands.add_parser(
        "revoke",
        help="take an act of feedback back (its exemplars; not the library)",
        description="Revoke one event: the exemplars it made are revoked and the one its "
        "positive replaced comes back. The library is left alone: `marquee restore` puts a "
        "poster back.",
    )
    revoke.add_argument("event_id", type=int)

    sync = commands.add_parser(
        "sync",
        help="copy the movie list from Radarr and record the posters on disk",
        description="Check that Radarr's root folders map to folders here, copy every movie "
        "Radarr lists into the database, record each movie folder's poster.jpg (hashing only "
        "new or changed files), and fetch TMDB details for movies never enriched. Writes "
        "nothing to the media library. Exits 1 when a movie failed or the path check failed.",
    )
    sync.add_argument(
        "--dry-run", action="store_true", help="do everything except write to the database"
    )
    sync.add_argument("--json", action="store_true", help="print the report as JSON")

    api = commands.add_parser(
        "api",
        help="serve the HTTP API",
        description="Serve the HTTP API under uvicorn until interrupted. Every /api route "
        "needs API_KEY; /health/live and /health/ready do not.",
    )
    api.add_argument("--host", help="address to listen on (default: API_HOST)")
    api.add_argument("--port", type=int, help="port to listen on (default: API_PORT)")

    worker = commands.add_parser(
        "worker",
        help="run the job queue",
        description="Run queued jobs until stopped; SIGTERM or Ctrl-C stops it gracefully, "
        "putting unfinished jobs back in the queue. Only one worker runs at a time. GPU jobs "
        "run in a child process, stopped after GPU_IDLE_SECONDS without a job.",
    )
    worker.add_argument(
        "--drain",
        action="store_true",
        help="exit once nothing in the queue is runnable; scheduled work is not queued",
    )

    jobs = commands.add_parser("jobs", help="list, inspect, queue, cancel, and retry jobs")
    jobs_commands = jobs.add_subparsers(dest="jobs_command", required=True, metavar="command")
    jobs_list = jobs_commands.add_parser("list", help=f"the newest {JOBS_SHOWN} jobs")
    jobs_list.add_argument("--state", choices=JOB_STATES, help="only jobs in this state")
    jobs_list.add_argument("--kind", help="only jobs of this kind")
    jobs_list.add_argument("--json", action="store_true", help="print the jobs as JSON")
    jobs_show = jobs_commands.add_parser("show", help="one job and its events")
    jobs_show.add_argument("id", type=int)
    jobs_enqueue = jobs_commands.add_parser(
        "enqueue",
        help="queue a job of a registered kind",
        description="Queue a job of a registered kind: library_sync, gpu_selftest, or "
        'poster_run (whose payload needs a movie id, e.g. {"movie_id": 12}). A kind that '
        "allows one active job (per movie, for runs) returns the one already queued or "
        "running.",
    )
    jobs_enqueue.add_argument("kind")
    jobs_enqueue.add_argument("--payload", default="{}", help="a JSON object (default {})")
    jobs_enqueue.add_argument("--priority", type=int, default=0, help="higher runs first")
    jobs_cancel = jobs_commands.add_parser(
        "cancel", help="cancel a queued job, or ask a running one to stop"
    )
    jobs_cancel.add_argument("id", type=int)
    jobs_retry = jobs_commands.add_parser(
        "retry", help="queue a failed or cancelled job again, as new"
    )
    jobs_retry.add_argument("id", type=int)

    db = commands.add_parser("db", help="create and inspect the database schema")
    db_commands = db.add_subparsers(dest="db_command", required=True, metavar="command")
    db_commands.add_parser(
        "upgrade",
        help="apply every migration the database lacks",
        description="Bring the database's schema up to the newest migration in the package.",
    )
    db_commands.add_parser(
        "status",
        help="show the database's schema revision; exit 1 unless it is the newest",
    )
    revision = db_commands.add_parser(
        "revision",
        help="write a new migration from the models (for developers)",
        description="Compare the ORM models with a database at the newest revision and write "
        "the difference as a new migration under marquee/db/migrations/versions/. Read and tidy "
        "it before committing it.",
    )
    revision.add_argument("-m", "--message", required=True, help="what the migration does")
    return parser


def _id_list(value: str) -> list[int]:
    """Comma-separated ids, as `--order 12,9,14` gives them."""
    try:
        ids = [int(part) for part in value.replace(" ", ",").split(",") if part]
    except ValueError:
        raise argparse.ArgumentTypeError(f"{value!r} is not a list of ids like 12,9,14") from None
    if not ids:
        raise argparse.ArgumentTypeError("give at least one id")
    return ids


def _quiet_logging(level: str = "ERROR") -> None:
    """Keep the console to the command's own output; only real problems reach stderr."""
    from marquee.logging import setup_logging

    setup_logging(level=level)
    logging.captureWarnings(True)
    warnings.simplefilter("ignore")


def _fail(command: str, message: str) -> int:
    print(f"marquee {command}: {message}", file=sys.stderr)
    return 1


def _error_line(exc: BaseException) -> str:
    """The first line of an error, with every configured secret blanked out."""
    from marquee.config import settings

    cause = getattr(exc, "orig", None) or exc
    lines = str(cause).strip().splitlines()
    text = f"{type(cause).__name__}: {lines[0]}" if lines else type(cause).__name__
    for secret in settings.secret_values():
        text = text.replace(secret, "***")
    return text


def _database_target() -> str:
    """Where the database is, without the password."""
    from marquee.config import settings

    return (
        f"{settings.POSTGRES_USER}@{settings.POSTGRES_HOST}:{settings.POSTGRES_PORT}"
        f"/{settings.POSTGRES_DB}"
    )


def _print_sync_report(report: Any) -> None:
    print(
        f"radarr    {report.movies_listed} movies listed; {report.root_folders_checked} root folders map to folders here"
    )
    print(
        f"movies    {report.created} created, {report.updated} updated, "
        f"{report.unchanged} unchanged, {report.retired} retired, {report.failed} failed"
    )
    if report.failed_radarr_ids:
        print(f"          failed Radarr ids: {', '.join(str(i) for i in report.failed_radarr_ids)}")
    print(f"with file {report.with_file}")
    print(
        f"posters   {report.posters_found} found, {report.posters_hashed} hashed, "
        f"{report.posters_removed} removed, {report.posters_unreadable} unreadable, "
        f"{report.posters_replaced_by_hand} of Marquee's replaced by hand"
    )
    if report.tmdb_skipped:
        print("tmdb      skipped: TMDB_READ_ACCESS_TOKEN is not set")
    else:
        print(f"tmdb      {report.tmdb_enriched} enriched, {report.tmdb_failed} failed")
    print(f"folders on disk with no Radarr movie: {len(report.folders_without_movie)}")
    for name in report.folders_without_movie:
        print(f"  {name}")
    print(f"movies with a file whose folder was not found: {len(report.movies_without_folder)}")
    for name in report.movies_without_folder:
        print(f"  {name}")
    print(f"took      {report.seconds:.1f} s")
    if report.dry_run:
        print("dry run   nothing was written to the database")


def _sync(args: argparse.Namespace) -> int:
    from sqlalchemy.exc import SQLAlchemyError

    from marquee.integrations.arr import ArrClientError
    from marquee.library import sync

    _quiet_logging("WARNING")
    try:
        report = asyncio.run(sync.sync_from_settings(dry_run=args.dry_run))
    except sync.SyncError as exc:
        return _fail("sync", str(exc))
    except ArrClientError as exc:
        return _fail("sync", f"Radarr: {_error_line(exc)}")
    except SQLAlchemyError as exc:
        return _fail("sync", f"{_database_target()}: {_error_line(exc)}")
    if args.json:
        print(json.dumps(report.to_dict(), indent=2))
    else:
        _print_sync_report(report)
    return 0 if report.ok else 1


def _api(args: argparse.Namespace) -> int:
    from marquee.api.app import serve
    from marquee.config import settings
    from marquee.logging import setup_logging

    setup_logging(level=settings.LOG_LEVEL)
    if settings.API_KEY is None:
        print(
            "marquee api: API_KEY is not set; every /api request will answer 503", file=sys.stderr
        )
    serve(args.host or settings.API_HOST, args.port or settings.API_PORT)
    return 0


def _worker(args: argparse.Namespace) -> int:
    import psycopg
    from sqlalchemy.exc import SQLAlchemyError

    from marquee.config import settings
    from marquee.jobs.worker import (
        Worker,
        WorkerAlreadyRunningError,
        WorkerLostLockError,
        serve,
    )
    from marquee.logging import setup_logging

    setup_logging(level=settings.LOG_LEVEL)
    try:
        asyncio.run(serve(Worker(drain=args.drain)))
    except (WorkerAlreadyRunningError, WorkerLostLockError) as exc:
        return _fail("worker", str(exc))
    except (SQLAlchemyError, psycopg.Error, OSError) as exc:
        return _fail("worker", f"{_database_target()}: {_error_line(exc)}")
    return 0


def _when(value: Any) -> str:
    return value.strftime("%Y-%m-%d %H:%M:%S") if value is not None else "-"


def _print_jobs(page: Any) -> None:
    print(f"{'id':>6}  {'kind':<14} {'state':<10} {'try':<5} {'created (UTC)':<19}  error")
    for job in page.items:
        tries = f"{job.attempt}/{job.max_attempts}"
        error = (job.error or "")[:60]
        print(
            f"{job.id:>6}  {job.kind:<14} {job.state:<10} {tries:<5} "
            f"{_when(job.created_at):<19}  {error}"
        )
    print(f"{len(page.items)} of {page.total} job(s)")


def _print_job(job: Any, events: list[Any]) -> None:
    print(f"job       {job.id} {job.kind} ({job.job_class})")
    print(f"state     {job.state}{' (cancel requested)' if job.cancel_requested else ''}")
    print(f"attempt   {job.attempt} of {job.max_attempts}; priority {job.priority}")
    if job.parent_id is not None:
        print(f"batch     {job.parent_id}")
    print(f"payload   {json.dumps(job.payload)}")
    print(
        f"created   {_when(job.created_at)}  started {_when(job.started_at)}  "
        f"finished {_when(job.finished_at)}"
    )
    if job.state == "queued":
        print(f"runs from {_when(job.run_after)}")
    if job.progress is not None:
        print(f"progress  {json.dumps(job.progress)}")
    if job.error is not None:
        print(f"error     {job.error}")
    if job.result is not None:
        print(f"result    {json.dumps(job.result, indent=2)}")
    print("events")
    for event in events:
        detail = " ".join(
            part
            for part in (event.message or "", json.dumps(event.data) if event.data else "")
            if part
        )
        print(f"  {event.id:>6}  {_when(event.at)}  {event.type:<16} {detail}")


async def _jobs_async(args: argparse.Namespace) -> int:
    from marquee.db.engine import create_engine, create_session_factory
    from marquee.jobs import queries, queue
    from marquee.jobs.registry import default_registry

    command = f"jobs {args.jobs_command}"
    if args.jobs_command == "enqueue":
        kinds = default_registry()
        if args.kind not in kinds:
            return _fail(command, f"unknown job kind {args.kind!r}; one of {', '.join(kinds)}")
        try:
            payload = json.loads(args.payload)
        except json.JSONDecodeError as exc:
            return _fail(command, f"--payload is not JSON: {exc}")
        if not isinstance(payload, dict):
            return _fail(command, "--payload must be a JSON object")
    engine = create_engine()
    try:
        async with create_session_factory(engine)() as session:
            if args.jobs_command == "list":
                page = await queries.list_jobs(
                    session, state=args.state, kind=args.kind, page_size=JOBS_SHOWN
                )
                if args.json:
                    print(json.dumps([queries.job_dict(job) for job in page.items], indent=2))
                else:
                    _print_jobs(page)
            elif args.jobs_command == "show":
                job = await queries.get_job(session, args.id)
                if job is None:
                    return _fail(command, f"no job {args.id}")
                _print_job(job, await queries.job_events(session, args.id))
            elif args.jobs_command == "enqueue":
                job, created = await queue.enqueue(
                    session, kinds[args.kind], payload, priority=args.priority
                )
                said = "queued" if created else f"already {job.state}; not queued again"
                print(f"job {job.id} {job.kind} {said}")
            elif args.jobs_command == "cancel":
                job = await queue.request_cancel(session, args.id)
                said = "cancelled" if job.state == "cancelled" else "asked to stop"
                print(f"job {job.id} {job.kind} {said}")
            elif args.jobs_command == "retry":
                job = await queue.retry(session, args.id)
                print(f"job {job.id} {job.kind} queued again")
    except (queue.JobNotFoundError, queue.JobStateError, ValueError) as exc:
        return _fail(command, str(exc))
    finally:
        await engine.dispose()
    return 0


def _jobs(args: argparse.Namespace) -> int:
    from sqlalchemy.exc import SQLAlchemyError

    _quiet_logging()
    try:
        return asyncio.run(_jobs_async(args))
    except SQLAlchemyError as exc:
        return _fail(f"jobs {args.jobs_command}", f"{_database_target()}: {_error_line(exc)}")


def _db(args: argparse.Namespace) -> int:
    from sqlalchemy.exc import SQLAlchemyError

    from marquee.db import migrate

    _quiet_logging()
    command = f"db {args.db_command}"
    try:
        if args.db_command == "status":
            state = migrate.status()
            print(f"database  {_database_target()}")
            print(f"revision  {state.current or 'none'}")
            print(f"head      {state.head}")
            if not state.at_head:
                print("status    behind; run `marquee db upgrade`")
                return 1
            print("status    at head")
            return 0
        if args.db_command == "upgrade":
            before = migrate.status()
            after = migrate.upgrade()
            print(f"database  {_database_target()}")
            print(f"before    {before.current or 'none'}")
            print(f"after     {after.current or 'none'}")
            print("status    at head" if after.at_head else "status    behind")
            return 0 if after.at_head else 1
        if args.db_command == "revision":
            path = migrate.revision(args.message)
            print(f"wrote     {path}")
            return 0
    except migrate.MigrationError as exc:
        return _fail(command, str(exc))
    except SQLAlchemyError as exc:
        return _fail(command, f"{_database_target()}: {_error_line(exc)}")
    raise AssertionError(f"unhandled db command {args.db_command!r}")


FOLLOW_POLL_S = 0.5


def _score(row: dict[str, Any]) -> str:
    score = row["score"]
    return f"{score:.4f}" if isinstance(score, int | float) else "-     "


def _print_stages(stages: list[dict[str, Any]]) -> None:
    print("stages")
    for stage in stages:
        survivors = "-" if stage.get("survivors") is None else stage["survivors"]
        print(f"  {stage['stage']:<16} {survivors:>4} left  {stage.get('seconds') or 0:7.2f} s")


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
        print(
            f"pick      {pick['stack']} {pick['file']} (score {_score(pick)}; "
            f"candidate {pick['candidate_id']})"
        )
    else:
        print("pick      none (no candidate survived every gate)")
    print(f"run       {summary['run_id']}, in {summary['run_dir']}")
    print(f"took      {summary['seconds']:.0f} s")


def _movie_line(movie: Any) -> str:
    year = f" ({movie.year})" if movie.year else ""
    return f"{movie.title}{year}, movie {movie.id}, TMDB {movie.tmdb_id}"


class _CommandError(Exception):
    """The command cannot go on; the message is printed as it is."""


async def _find_movie(session: Any, args: argparse.Namespace) -> Any:
    from marquee.artwork import runs

    matches = await runs.find_movies_by_title(session, args.title, year=args.year)
    if args.movie_id is not None:
        matches = [movie for movie in matches if movie.id == args.movie_id]
    if not matches:
        wanted = f"{args.title!r}" + (f" ({args.year})" if args.year else "")
        if args.movie_id is not None:
            wanted += f" with id {args.movie_id}"
        raise _CommandError(
            f"no movie titled {wanted} in the database; the title is matched exactly, case "
            "ignored (run `marquee sync` if the movie is new)"
        )
    if len(matches) > 1:
        listed = "; ".join(_movie_line(movie) for movie in matches)
        raise _CommandError(
            f"{len(matches)} movies are titled {args.title!r}: {listed}. Give --year or --movie-id"
        )
    return matches[0]


async def _follow(sessions: Any, job_id: int, *, quiet: bool) -> Any:
    """Wait for a job to finish, saying when it starts; returns the finished job."""
    from marquee.jobs import queries

    said_running = False
    while True:
        async with sessions() as session:
            job = await queries.get_job(session, job_id)
        assert job is not None
        if job.state == "running" and not said_running and not quiet:
            print(f"running   attempt {job.attempt} of {job.max_attempts}", flush=True)
            said_running = True
        if job.state in ("succeeded", "failed", "cancelled"):
            return job
        await asyncio.sleep(FOLLOW_POLL_S)


async def _run_async(args: argparse.Namespace) -> int:
    from marquee.artwork import runs
    from marquee.config import settings
    from marquee.db.engine import create_engine, create_session_factory
    from marquee.jobs import queries
    from marquee.jobs.handlers.poster_run import enqueue_run

    engine = create_engine()
    sessions = create_session_factory(engine)
    try:
        async with sessions() as session:
            movie = await _find_movie(session, args)
            reason = runs.ineligible_reason(movie)
            if reason is not None:
                raise _CommandError(reason)
            worker = await queries.worker_status(session)
            if not worker.running and not args.no_wait:
                raise _CommandError(
                    "no worker is running, so nothing was queued; start one with "
                    "`marquee worker`, or give --no-wait to queue the run anyway"
                )
            job, created = await enqueue_run(session, movie.id)
        said = "queued" if created else f"already {job.state}; following it"
        if not args.json:
            print(f"movie     {_movie_line(movie)}", flush=True)
            print(f"job       {job.id} poster_run {said}", flush=True)
        if args.no_wait:
            if args.json:
                print(json.dumps({"job_id": job.id, "created": created, "movie_id": movie.id}))
            return 0
        job = await _follow(sessions, job.id, quiet=args.json)
    finally:
        await engine.dispose()
    if job.state != "succeeded":
        raise _CommandError(f"job {job.id} {job.state}: {job.error or 'no error recorded'}")
    summary = {
        **job.result,
        "job_id": job.id,
        "movie": {
            "id": movie.id,
            "title": movie.title,
            "year": movie.year,
            "tmdb_id": movie.tmdb_id,
            "imdb_id": movie.imdb_id,
        },
        "run_dir": str(settings.runs_dir / job.result["run_folder"]),
    }
    if args.json:
        print(json.dumps(summary, indent=2))
    else:
        _print_stages(summary["stages"])
        _print_run_summary(summary)
    return 0


def _run(args: argparse.Namespace) -> int:
    from sqlalchemy.exc import SQLAlchemyError

    _quiet_logging()
    try:
        return asyncio.run(_run_async(args))
    except _CommandError as exc:
        return _fail("run", str(exc))
    except SQLAlchemyError as exc:
        return _fail("run", f"{_database_target()}: {_error_line(exc)}")
    except KeyboardInterrupt:
        return _fail("run", "stopped following; the job goes on (`marquee jobs show <id>`)")


def _queued_line(job: Any, created: bool) -> str:
    if created:
        return f"job {job.id} {job.kind} queued"
    return f"job {job.id} {job.kind} already {job.state}; not queued again"


def _print_runs(page: Any) -> None:
    print(f"{'id':>6}  {'movie':>6}  {'state':<10} {'ranked':>6}  {'started (UTC)':<19}  folder")
    for run in page.items:
        ranked = "-" if run.ranked_count is None else run.ranked_count
        print(
            f"{run.id:>6}  {run.movie_id:>6}  {run.state:<10} {ranked:>6}  "
            f"{_when(run.started_at):<19}  {run.run_folder}"
        )
    print(f"{len(page.items)} of {page.total} run(s)")


def _run_dict(run: Any) -> dict[str, Any]:
    return {
        "id": run.id,
        "movie_id": run.movie_id,
        "job_id": run.job_id,
        "state": run.state,
        "run_folder": run.run_folder,
        "engine_status": run.engine_status,
        "ranked_count": run.ranked_count,
        "candidate_count": run.candidate_count,
        "error": run.error,
        "started_at": run.started_at.isoformat(),
        "finished_at": run.finished_at.isoformat() if run.finished_at else None,
    }


def _candidate_dict(row: Any) -> dict[str, Any]:
    stack = f"{row.stack_rank}{row.stack_label or ''}" if row.stack_rank is not None else None
    return {
        "id": row.id,
        "source_file": row.source_file,
        "sha256": row.sha256,
        "stage_reached": row.stage_reached,
        "rejection_reason": row.rejection_reason,
        "rank": row.rank,
        "final_score": row.final_score,
        "stack": stack,
        "is_pick": row.is_pick,
    }


def _print_run(run: Any, candidates: list[Any]) -> None:
    from marquee.config import settings

    print(f"run       {run.id} of movie {run.movie_id} (job {run.job_id})")
    print(f"state     {run.state}{f' ({run.engine_status})' if run.engine_status else ''}")
    print(f"started   {_when(run.started_at)}  finished {_when(run.finished_at)}")
    if run.error:
        print(f"error     {run.error}")
    print(f"folder    {settings.runs_dir / run.run_folder}")
    if run.counts:
        print(f"counts    {', '.join(f'{k} {v}' for k, v in run.counts.items())}")
    print("  cand  rank  stack  score   file                              fate")
    for row in candidates:
        c = _candidate_dict(row)
        score = f"{c['final_score']:.4f}" if c["final_score"] is not None else "-     "
        fate = "pick" if c["is_pick"] else (c["rejection_reason"] or c["stage_reached"] or "-")
        print(
            f"  {c['id']:>4}  {c['rank'] or '-':>4}  {c['stack'] or '-':<5}  {score}  "
            f"{c['source_file']:<33} {fate}"
        )


def _deployment_dict(row: Any) -> dict[str, Any]:
    return {
        "id": row.id,
        "movie_id": row.movie_id,
        "action": row.action,
        "initiated_by": row.initiated_by,
        "run_id": row.run_id,
        "candidate_id": row.candidate_id,
        "source_file": row.source_file,
        "job_id": row.job_id,
        "new_sha256": row.new_sha256,
        "previous_sha256": row.previous_sha256,
        "previous_origin": row.previous_origin,
        "backup_file": row.backup_file,
        "restores_deployment_id": row.restores_deployment_id,
        "restore_puts_back": row.previous_sha256,
        "created_at": row.created_at.isoformat(),
    }


def _print_deployments(page: Any) -> None:
    print(
        f"{'id':>6}  {'movie':>6}  {'action':<8} {'by':<7} {'created (UTC)':<19}  "
        f"{'wrote':<12}  a restore of it puts back"
    )
    for row in page.items:
        new = (row.new_sha256 or "no poster")[:12]
        back = (
            f"{row.previous_sha256[:12]} ({row.previous_origin})"
            if row.previous_sha256
            else "no poster"
        )
        undoes = f"  (undid {row.restores_deployment_id})" if row.restores_deployment_id else ""
        print(
            f"{row.id:>6}  {row.movie_id:>6}  {row.action:<8} {row.initiated_by:<7} "
            f"{_when(row.created_at):<19}  {new:<12}  {back}{undoes}"
        )
    print(f"{len(page.items)} of {page.total} deployment(s)")


async def _artwork_async(args: argparse.Namespace) -> int:
    from marquee.artwork import deploy, runs
    from marquee.db.engine import create_engine, create_session_factory
    from marquee.jobs.handlers import poster_run, poster_write

    engine = create_engine()
    try:
        async with create_session_factory(engine)() as session:
            if args.command == "runs" and args.runs_command == "list":
                page = await runs.list_runs(
                    session, movie_id=args.movie_id, state=args.state, page_size=JOBS_SHOWN
                )
                if args.json:
                    print(json.dumps([_run_dict(run) for run in page.items], indent=2))
                else:
                    _print_runs(page)
            elif args.command == "runs" and args.runs_command == "show":
                run = await runs.get_run(session, args.id)
                if run is None:
                    raise _CommandError(f"no run {args.id}")
                candidates = await runs.candidates_of(session, args.id)
                if args.json:
                    out = {**_run_dict(run), "counts": run.counts, "stages": run.stages}
                    out["candidates"] = [_candidate_dict(c) for c in candidates]
                    print(json.dumps(out, indent=2))
                else:
                    _print_run(run, candidates)
            elif args.command == "runs" and args.runs_command == "batch":
                parent, children, found = await poster_run.enqueue_scope(
                    session, args.scope, limit=args.limit
                )
                for child in children:
                    print(f"job {child.id} poster_run queued (movie {child.payload['movie_id']})")
                print(
                    f"batch {parent.id}: {len(children)} run(s) queued for {found} movie(s) "
                    f"in scope {args.scope}; {found - len(children)} already active"
                )
            elif args.command == "deploy":
                candidate_id = args.candidate
                if candidate_id is None:
                    pick = await runs.pick_of(session, args.run_id)
                    if pick is None:
                        raise _CommandError(f"run {args.run_id} has no pick")
                    candidate_id = pick.id
                elif await runs.get_candidate(session, args.run_id, candidate_id) is None:
                    raise _CommandError(
                        f"candidate {candidate_id} is not one of run {args.run_id}'s"
                    )
                job, created = await poster_write.enqueue_deploy(session, candidate_id)
                print(_queued_line(job, created))
            elif args.command == "deployments":
                page = await deploy.list_deployments(
                    session, movie_id=args.movie_id, page_size=JOBS_SHOWN
                )
                if args.json:
                    print(json.dumps([_deployment_dict(row) for row in page.items], indent=2))
                else:
                    _print_deployments(page)
            elif args.command == "restore":
                job, created = await poster_write.enqueue_restore(session, args.deployment_id)
                print(_queued_line(job, created))
    except poster_write.WriteRefusedError as exc:
        raise _CommandError(str(exc)) from exc
    finally:
        await engine.dispose()
    return 0


def _artwork(args: argparse.Namespace) -> int:
    from sqlalchemy.exc import SQLAlchemyError

    _quiet_logging()
    name = args.command + (
        f" {args.runs_command}"
        if args.command == "runs"
        else f" {args.deployments_command}"
        if args.command == "deployments"
        else ""
    )
    try:
        return asyncio.run(_artwork_async(args))
    except _CommandError as exc:
        return _fail(name, str(exc))
    except SQLAlchemyError as exc:
        return _fail(name, f"{_database_target()}: {_error_line(exc)}")


def _rescan_refusal() -> str | None:
    """Why ``taste seed --rescan`` must not run, or ``None``.

    Once Marquee has written a poster, the library holds the AI's picks beside the owner's,
    so scanning it again would read Marquee's picks as the owner's. If the database cannot
    be asked, that cannot be ruled out, and the rescan is refused too.
    """
    from sqlalchemy import func, select
    from sqlalchemy.exc import SQLAlchemyError

    from marquee.db.engine import create_engine, create_session_factory
    from marquee.db.models import Deployment

    async def count() -> int:
        engine = create_engine()
        try:
            async with create_session_factory(engine)() as session:
                return int(await session.scalar(select(func.count()).select_from(Deployment)) or 0)
        finally:
            await engine.dispose()

    try:
        deployed = asyncio.run(count())
    except (SQLAlchemyError, OSError) as exc:
        return (
            f"--rescan needs the database to confirm Marquee has written no poster yet; "
            f"{_database_target()}: {_error_line(exc)}"
        )
    if deployed:
        return (
            f"--rescan is refused: Marquee has written to the library ({deployed} deployment "
            "row(s)), so it now holds the AI's picks beside yours. The staged set in "
            "data/ml/seed/ stays the record of your own picks; use --reuse to rebuild from it"
        )
    return None


def _exemplar_refusal() -> str | None:
    """Why ``taste seed`` must not run in any form, or ``None``.

    Once exemplar records exist, the profile is built from them; a seed would overwrite it
    with one built from the staged set alone, losing every exemplar feedback added. A
    database whose schema predates exemplars has none. If the database cannot be asked,
    that cannot be ruled out, and the seed is refused.
    """
    from sqlalchemy import func, select
    from sqlalchemy.exc import ProgrammingError, SQLAlchemyError

    from marquee.db.engine import create_engine, create_session_factory
    from marquee.db.models import Exemplar

    async def count() -> int:
        engine = create_engine()
        try:
            async with create_session_factory(engine)() as session:
                return int(await session.scalar(select(func.count()).select_from(Exemplar)) or 0)
        finally:
            await engine.dispose()

    try:
        records = asyncio.run(count())
    except ProgrammingError:
        return None  # no exemplars table yet
    except (SQLAlchemyError, OSError) as exc:
        return (
            "the seed needs the database to confirm no exemplar record exists yet; "
            f"{_database_target()}: {_error_line(exc)}"
        )
    if records:
        return (
            f"the taste profile is built from exemplar records now ({records} of them), not "
            "from the staged seed; use `marquee taste rebuild` instead"
        )
    return None


async def _import_seed_async() -> int:
    from marquee.db.engine import create_engine, create_session_factory
    from marquee.taste import seed

    engine = create_engine()
    try:
        async with create_session_factory(engine)() as session:
            report = await seed.import_seed(session)
            if report.problems:
                await session.rollback()
            else:
                await session.commit()
    finally:
        await engine.dispose()
    if report.problems:
        print(f"marquee taste import-seed: nothing imported; {len(report.problems)} problem(s):")
        for problem in report.problems:
            print(f"  {problem}")
        return 1
    profile = f"profile {report.profile_id} ({(report.profile_sha256 or '')[:12]})"
    if report.already:
        print(
            f"already imported: {report.entries} seed exemplars and {profile} are registered; "
            "nothing changed"
        )
        return 0
    print(f"imported  {report.created} seed exemplars, active positives")
    print(f"profile   {profile} registered as it is, active; source seed")
    print(f"revision  {report.revision}")
    return 0


def _import_seed() -> int:
    from sqlalchemy.exc import SQLAlchemyError

    _quiet_logging()
    try:
        return asyncio.run(_import_seed_async())
    except SQLAlchemyError as exc:
        return _fail("taste import-seed", f"{_database_target()}: {_error_line(exc)}")


def _print_rebuild(job: Any) -> None:
    result = job.result or {}
    if result.get("skipped"):
        print(f"skipped   {result['skipped']} (revision {result['revision'][:12]})")
        if result.get("profile_id"):
            print(f"profile   {result['profile_id']} ({result['sha256'][:12]}) stays active")
        return
    how = "built" if result.get("built") else "published from an earlier attempt's build"
    print(
        f"profile   {result['profile_id']} ({result['sha256'][:12]}) {how}, active; "
        f"{result['positives']} positives, {result['negatives']} negatives, "
        f"{result['movies']} movies"
    )
    print(f"revision  {result['revision']}")
    previous = result.get("previous_profile_id")
    if previous == result["profile_id"]:
        print("same      the build is byte for byte the profile that was already active")
    elif previous:
        print(f"replaces  profile {previous}")
    print(f"took      {result['seconds']:.0f} s")


async def _taste_rebuild_async(args: argparse.Namespace) -> int:
    from marquee.db.engine import create_engine, create_session_factory
    from marquee.jobs import queries
    from marquee.jobs.handlers.taste_rebuild import enqueue_rebuild

    engine = create_engine()
    sessions = create_session_factory(engine)
    try:
        async with sessions() as session:
            worker = await queries.worker_status(session)
            if not worker.running and not args.no_wait:
                raise _CommandError(
                    "no worker is running, so nothing was queued; start one with "
                    "`marquee worker`, or give --no-wait to queue the rebuild anyway"
                )
            job, created = await enqueue_rebuild(session, force=args.force)
        said = "queued" if created else f"already {job.state}; following it"
        print(f"job       {job.id} taste_rebuild {said}", flush=True)
        if args.no_wait:
            return 0
        job = await _follow(sessions, job.id, quiet=False)
    finally:
        await engine.dispose()
    if job.state != "succeeded":
        raise _CommandError(f"job {job.id} {job.state}: {job.error or 'no error recorded'}")
    _print_rebuild(job)
    return 0


def _taste_rebuild(args: argparse.Namespace) -> int:
    from sqlalchemy.exc import SQLAlchemyError

    _quiet_logging()
    try:
        return asyncio.run(_taste_rebuild_async(args))
    except _CommandError as exc:
        return _fail("taste rebuild", str(exc))
    except SQLAlchemyError as exc:
        return _fail("taste rebuild", f"{_database_target()}: {_error_line(exc)}")
    except KeyboardInterrupt:
        return _fail("taste rebuild", "stopped following; the job goes on")


REVIEW_ACTIONS = {"approve": "approval", "pick": "override", "rank": "rank", "reject": "reject"}


def _exemplar_line(verb: str, row: Any) -> str:
    what = f"{row.polarity} {row.weight:g}"
    name = row.source_file or row.staged_name
    return f"  {verb:<11} exemplar {row.id}: {what}, movie {row.movie_id}, {name}"


def _print_review_result(result: Any, title: str) -> None:
    event = result.outcome.event
    if not result.created:
        print(f"event     {event.id} was recorded before with this key; nothing changed")
    if event.revokes_event_id:
        print(f"event     {event.id} revoke of event {event.revokes_event_id} ({title})")
    else:
        blind = {True: "blind", False: "not blind", None: "blind unknown"}[event.blind]
        print(f"event     {event.id} {event.action} on run {event.run_id} of {title} ({blind})")
    if event.chosen_source_file:
        same = " (the pick)" if event.chosen_source_file == event.pick_source_file else ""
        print(f"chosen    {event.chosen_source_file}{same}")
    changes = result.outcome.changes
    if not (changes.created or changes.ended or changes.reactivated):
        print("exemplars no change")
    else:
        print("exemplars")
    for row in sorted(changes.created, key=lambda r: r.id):
        print(_exemplar_line("made", row))
    for row in sorted(changes.ended, key=lambda r: r.id):
        print(_exemplar_line(row.state, row))
    for row in sorted(changes.reactivated, key=lambda r: r.id):
        print(_exemplar_line("active again", row))
    if result.deploy_job is not None:
        print(f"deploy    job {result.deploy_job.id} {result.deploy_job.kind}")
    elif result.deploy_note:
        print(f"deploy    {result.deploy_note}")
    if result.rebuild_job is not None:
        print(f"rebuild   job {result.rebuild_job.id} taste_rebuild")


async def _follow_review(sessions: Any, result: Any) -> None:
    """With a worker running, wait for the deploy and the rebuild and say how they ended."""
    from marquee.jobs import queries

    async with sessions() as session:
        worker = await queries.worker_status(session)
    jobs = [job for job in (result.deploy_job, result.rebuild_job) if job is not None]
    if not jobs:
        return
    if not worker.running:
        print("no worker is running; the jobs wait in the queue (`marquee worker`)")
        return
    for job in jobs:
        done = await _follow(sessions, job.id, quiet=True)
        if done.state != "succeeded":
            print(f"job {done.id} {done.kind} {done.state}: {done.error or 'no error recorded'}")
        elif done.kind == "taste_rebuild":
            _print_rebuild(done)
        else:
            outcome = (done.result or {}).get("outcome") or (done.result or {}).get("note")
            deployment = (done.result or {}).get("deployment_id")
            print(f"deployed  job {done.id}: {outcome}; deployment {deployment}")


def _pending_dict(row: Any) -> dict[str, Any]:
    return {
        "movie_id": row.movie_id,
        "title": row.title,
        "year": row.year,
        "run_id": row.run_id,
        "ranked": row.ranked,
        "finished_at": row.finished_at.isoformat() if row.finished_at else None,
        "policy_deployed": row.policy_deployed,
        "blind": row.blind,
    }


def _event_dict(event: Any, revoked_by: int | None) -> dict[str, Any]:
    return {
        "id": event.id,
        "action": event.action,
        "run_id": event.run_id,
        "chosen": event.chosen_source_file,
        "pick": event.pick_source_file,
        "blind": event.blind,
        "context": event.context,
        "shown": len(event.exposed or []),
        "revokes_event_id": event.revokes_event_id,
        "revoked_by": revoked_by,
        "created_at": event.created_at.isoformat(),
    }


async def _review_async(args: argparse.Namespace) -> int:
    import uuid

    from marquee.artwork import runs
    from marquee.db.engine import create_engine, create_session_factory
    from marquee.db.models import Movie
    from marquee.jobs import review
    from marquee.taste import agreement, feedback

    engine = create_engine()
    sessions = create_session_factory(engine)
    try:
        async with sessions() as session:
            if args.review_command == "list":
                pending = await agreement.pending_reviews(session)
                if args.json:
                    print(json.dumps([_pending_dict(row) for row in pending], indent=2))
                    return 0
                print(f"{'run':>6}  {'movie':>6}  {'ranked':>6}  {'blind':<7} title")
                for row in pending:
                    year = f" ({row.year})" if row.year else ""
                    blind = {True: "yes", False: "no", None: "unknown"}[row.blind]
                    policy = "  [deployed by the policy]" if row.policy_deployed else ""
                    print(
                        f"{row.run_id:>6}  {row.movie_id:>6}  {row.ranked or 0:>6}  {blind:<7} "
                        f"{row.title}{year}{policy}"
                    )
                print(f"{len(pending)} run(s) wait for a review")
                return 0
            if args.review_command == "show":
                run = await runs.get_run(session, args.run_id)
                if run is None:
                    raise _CommandError(f"no run {args.run_id}")
                candidates = await runs.candidates_of(session, run.id)
                events = await feedback.events_of_run(session, run.id)
                revoked = await feedback.revoked_ids(session)
                blind = await feedback.blind_for(session, run)
                latest = await feedback.latest_completed_run_id(session, run.movie_id)
                if args.json:
                    out = {
                        **_run_dict(run),
                        "blind": blind,
                        "latest": latest == run.id,
                        "candidates": [_candidate_dict(c) for c in candidates],
                        "events": [_event_dict(e, revoked.get(e.id)) for e in events],
                    }
                    print(json.dumps(out, indent=2))
                    return 0
                _print_run(run, [c for c in candidates if c.rank is not None or c.is_pick])
                state = {True: "yes", False: "no", None: "unknown (profile not registered)"}
                print(f"blind     {state[blind]}")
                if latest != run.id:
                    print(f"feedback  goes to the movie's latest completed run, {latest}")
                print(f"events    {len(events)}")
                for event in events:
                    by = revoked.get(event.id)
                    note = f" (revoked by {by})" if by else ""
                    what = event.chosen_source_file or ", ".join(event.context.get("hated", []))
                    print(f"  {event.id:>5}  {event.action:<8} {what or '-'}{note}")
                return 0
            title = ""
            if args.review_command == "revoke":
                result = await review.revoke(session, event_id=args.event_id)
            else:
                run = await runs.get_run(session, args.run_id)
                if run is None:
                    raise _CommandError(f"no run {args.run_id}")
                result = await review.submit(
                    session,
                    run_id=args.run_id,
                    action=REVIEW_ACTIONS[args.review_command],
                    idempotency_key=f"cli:{uuid.uuid4().hex}",
                    candidate_id=getattr(args, "candidate", None),
                    order=getattr(args, "order", None),
                    hated=getattr(args, "hate", None) or None,
                    deploy=not getattr(args, "no_deploy", True),
                )
            movie = await session.get(Movie, result.outcome.event.movie_id)
            title = movie.title if movie is not None else str(result.outcome.event.movie_id)
            await session.commit()
        _print_review_result(result, title)
        await _follow_review(sessions, result)
    except feedback.FeedbackError as exc:
        raise _CommandError(str(exc)) from exc
    finally:
        await engine.dispose()
    return 0


def _review(args: argparse.Namespace) -> int:
    from sqlalchemy.exc import SQLAlchemyError

    _quiet_logging()
    try:
        return asyncio.run(_review_async(args))
    except _CommandError as exc:
        return _fail(f"review {args.review_command}", str(exc))
    except SQLAlchemyError as exc:
        return _fail(f"review {args.review_command}", f"{_database_target()}: {_error_line(exc)}")
    except KeyboardInterrupt:
        return _fail(f"review {args.review_command}", "stopped following; the jobs go on")


def _print_status(status: Any) -> None:
    def counts(by: dict[str, int]) -> str:
        return ", ".join(f"{n} {polarity}" for polarity, n in sorted(by.items(), reverse=True))

    states = "; ".join(f"{state}: {counts(by)}" for state, by in sorted(status.exemplars.items()))
    print(f"exemplars {states or 'none'}")
    profile = status.profile
    if profile is None:
        print("profile   none registered (`marquee taste import-seed`)")
    else:
        print(
            f"profile   {profile.id} ({profile.sha256[:12]}), {profile.source}, "
            f"{profile.positives} positives, {profile.negatives} negatives"
        )
    due = (
        "due: the exemplars changed since the profile was built"
        if status.rebuild_due
        else ("not due")
    )
    print(f"rebuild   {due}")
    a = status.agreement
    print(f"agreement blind runs: {a.blind.text()}")
    print(f"          not blind: {a.not_blind.reviewed} reviewed; unknown: {a.unknown.reviewed}")
    for key, tally in a.by_profile.items():
        print(f"          profile {key}: {tally.text()}")
    for key, tally in sorted(a.by_month.items()):
        print(f"          {key}: {tally.text()}")
    print(f"reviews   {status.pending_reviews} run(s) wait (`marquee review list`)")
    print(
        f"residual  not trained; the events give {status.residual_pairs} pair(s) over "
        f"{status.residual_subjects} subject(s); it needs {status.residual_min_pairs} over "
        f"{status.residual_min_subjects}"
    )


async def _taste_status_async(args: argparse.Namespace) -> int:
    from marquee.db.engine import create_engine, create_session_factory
    from marquee.taste import agreement

    engine = create_engine()
    try:
        async with create_session_factory(engine)() as session:
            status = await agreement.status(session)
            await session.commit()
    finally:
        await engine.dispose()
    if args.json:
        print(json.dumps(status.to_dict(), indent=2))
    else:
        _print_status(status)
    return 0


def _taste_status(args: argparse.Namespace) -> int:
    from sqlalchemy.exc import SQLAlchemyError

    _quiet_logging()
    try:
        return asyncio.run(_taste_status_async(args))
    except SQLAlchemyError as exc:
        return _fail("taste status", f"{_database_target()}: {_error_line(exc)}")


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
        if mode == "rescan" and (refusal := _rescan_refusal()) is not None:
            print(f"marquee taste seed: {refusal}", file=sys.stderr)
            return 1
        if (refusal := _exemplar_refusal()) is not None:
            print(f"marquee taste seed: {refusal}", file=sys.stderr)
            return 1
        try:
            return seed.run_seed(mode=mode, dry_run=args.dry_run)
        except (seed.SeedError, LibraryRootError) as exc:
            print(f"marquee taste seed: {exc}", file=sys.stderr)
            return 1
    if args.command == "taste" and args.taste_command == "import-seed":
        return _import_seed()
    if args.command == "taste" and args.taste_command == "rebuild":
        return _taste_rebuild(args)
    if args.command == "taste" and args.taste_command == "status":
        return _taste_status(args)
    if args.command == "review":
        return _review(args)
    if args.command == "run":
        return _run(args)
    if args.command == "db":
        return _db(args)
    if args.command == "sync":
        return _sync(args)
    if args.command == "api":
        return _api(args)
    if args.command == "worker":
        return _worker(args)
    if args.command == "jobs":
        return _jobs(args)
    if args.command in ("runs", "deploy", "deployments", "restore"):
        return _artwork(args)
    raise AssertionError(f"unhandled command {args.command!r}")


if __name__ == "__main__":
    raise SystemExit(main())
