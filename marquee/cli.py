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
        f"{report.posters_removed} removed, {report.posters_unreadable} unreadable"
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
        try:
            return seed.run_seed(mode=mode, dry_run=args.dry_run)
        except (seed.SeedError, LibraryRootError) as exc:
            print(f"marquee taste seed: {exc}", file=sys.stderr)
            return 1
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
    raise AssertionError(f"unhandled command {args.command!r}")


if __name__ == "__main__":
    raise SystemExit(main())
