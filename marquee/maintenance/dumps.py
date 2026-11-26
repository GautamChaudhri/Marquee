"""Database dumps: make one, list them, keep a set number, and restore one.

A dump is ``pg_dump``'s custom format of the whole database, in ``<data>/backups/db/``:

- ``marquee-<UTC time>-<schema revision>.dump``: made by the ``db_backup`` job on its
  schedule; after each one the newest ``DB_BACKUP_KEEP`` of these are kept.
- ``manual-<UTC time>-<schema revision>.dump``: made by ``marquee db backup``; never
  deleted by Marquee.
- ``pre-restore-<UTC time>.dump``: the database as it was, made by a restore before it
  drops anything; the newest three are kept.

A dump is written under a temporary name in that folder (mode 600) and renamed only after
``pg_restore --list`` has read it back. The client tools must be the server's major
version or newer, which is checked before every dump and restore. The connection reaches
each child process through libpq's environment variables (``PGPASSWORD`` among them), never
its arguments, and whatever a tool prints is scrubbed of secrets and connection strings
before it is shown or logged.

Every function here is blocking: call it off the event loop.
"""

from __future__ import annotations

import hashlib
import logging
import os
import re
import shutil
import subprocess
import threading
import time
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

import psycopg

from marquee.cancellation import CancellationSignal, raise_if_cancelled
from marquee.config import settings
from marquee.fs.boundary import FilesystemBoundary, boundary_for_roots

logger = logging.getLogger(__name__)

Kind = Literal["scheduled", "manual", "pre-restore"]
PREFIXES: dict[str, str] = {
    "scheduled": "marquee",
    "manual": "manual",
    "pre-restore": "pre-restore",
}
PRE_RESTORE_KEEP = 3
SUFFIX = ".dump"
TIME_FORMAT = "%Y%m%dT%H%M%SZ"
_NAME = re.compile(
    r"(?P<prefix>marquee|manual|pre-restore)-(?P<time>\d{8}T\d{6}Z)"
    r"(?:-(?P<revision>[0-9A-Za-z_]+))?\.dump"
)
_TEMPORARY_PREFIX = ".marquee-dump-"
_ROOT = "dumps"
APPLICATION_NAME = "marquee-backup"
CONNECT_TIMEOUT_S = 10
DUMP_TIMEOUT_S = 60 * 60
CHECK_TIMEOUT_S = 10 * 60
TOOLS = ("pg_dump", "pg_restore", "psql")
_VERSION = re.compile(r"\(PostgreSQL\)\s+(\d+)")
_URL_CREDENTIALS = re.compile(r"(postgres(?:ql)?(?:\+\w+)?://)[^@\s/]*@", re.IGNORECASE)
_PASSWORD_WORDS = re.compile(r"(password\s*=\s*)\S+", re.IGNORECASE)


class DumpError(Exception):
    """A dump or a restore cannot be done; the message says why and holds no secret."""


class DumpToolsError(DumpError):
    """The PostgreSQL client tools are missing, or older than the server."""


def scrub(text: str) -> str:
    """``text`` with every configured secret and any connection string's credentials blanked."""
    for secret in settings.secret_values():
        text = text.replace(secret, "***")
    text = _URL_CREDENTIALS.sub(r"\1***@", text)
    return _PASSWORD_WORDS.sub(r"\1***", text)


def _target() -> str:
    """Where the database is, without the password."""
    return (
        f"{settings.POSTGRES_USER}@{settings.POSTGRES_HOST}:{settings.POSTGRES_PORT}"
        f"/{settings.POSTGRES_DB}"
    )


def _tail(text: str, lines: int = 6) -> str:
    kept = [line.strip() for line in text.strip().splitlines() if line.strip()][-lines:]
    return scrub(" | ".join(kept)) or "no message"


# ── The tools and the server ──────────────────────────────────────────────


@dataclass(frozen=True)
class Tools:
    paths: dict[str, Path]
    major: int
    """The oldest major version among the three tools."""

    def __getitem__(self, name: str) -> str:
        return str(self.paths[name])


def tool_major(path: Path) -> int | None:
    """The major version a PostgreSQL client tool reports, or ``None`` if it cannot say."""
    try:
        done = subprocess.run(
            [str(path), "--version"], capture_output=True, text=True, timeout=30, env={}
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    found = _VERSION.search(done.stdout)
    return int(found.group(1)) if done.returncode == 0 and found else None


def find_tools(server_major: int | None = None) -> Tools:
    """``pg_dump``, ``pg_restore``, and ``psql``, each the server's major version or newer."""
    paths: dict[str, Path] = {}
    majors: dict[str, int] = {}
    needed = f"version {server_major} or newer" if server_major else "the server's version"
    for name in TOOLS:
        found = shutil.which(name)
        if found is None:
            raise DumpToolsError(
                f"{name} is not installed; database backups need the PostgreSQL client tools, "
                f"{needed} (the Marquee image carries them)"
            )
        major = tool_major(Path(found))
        if major is None:
            raise DumpToolsError(f"{name} does not say which version it is")
        paths[name], majors[name] = Path(found), major
    oldest = min(majors.values())
    if server_major is not None and oldest < server_major:
        name = min(majors, key=majors.__getitem__)
        raise DumpToolsError(
            f"{name} is version {oldest} and the database server is version {server_major}; "
            "the client tools must be the server's version or newer"
        )
    return Tools(paths=paths, major=oldest)


@dataclass(frozen=True)
class DatabaseState:
    server_major: int
    revision: str | None
    has_tables: bool
    other_connections: int


def _connect() -> psycopg.Connection:
    try:
        return psycopg.connect(
            settings.database_url_sync,
            connect_timeout=CONNECT_TIMEOUT_S,
            application_name=APPLICATION_NAME,
            autocommit=True,
        )
    except psycopg.Error as exc:
        raise DumpError(
            f"the database {_target()} cannot be reached: {_tail(str(exc), 2)}"
        ) from None


def database_state() -> DatabaseState:
    """The server's major version, the schema revision, whether there is anything to dump,
    and how many other sessions are connected to the database."""
    with _connect() as conn:
        version = int(conn.execute("SHOW server_version_num").fetchone()[0])  # type: ignore[index]
        has_tables = bool(
            conn.execute(
                "SELECT EXISTS (SELECT 1 FROM pg_tables WHERE schemaname = 'public')"
            ).fetchone()[0]  # type: ignore[index]
        )
        revision = None
        if conn.execute("SELECT to_regclass('public.alembic_version')").fetchone()[0]:  # type: ignore[index]
            row = conn.execute("SELECT version_num FROM alembic_version").fetchone()
            revision = row[0] if row else None
        others = conn.execute(
            "SELECT count(*) FROM pg_stat_activity WHERE datname = current_database() "
            "AND pid <> pg_backend_pid() AND backend_type = 'client backend'"
        ).fetchone()[0]  # type: ignore[index]
    return DatabaseState(
        server_major=version // 10000,
        revision=revision,
        has_tables=has_tables,
        other_connections=int(others),
    )


# ── Running a tool ────────────────────────────────────────────────────────


def _environment() -> dict[str, str]:
    return {
        **settings.libpq_environment(),
        "PGAPPNAME": APPLICATION_NAME,
        "PGCONNECT_TIMEOUT": str(CONNECT_TIMEOUT_S),
    }


class _Readers:
    """Threads that drain a process's open output pipes, so a full pipe never stalls it."""

    def __init__(
        self, process: subprocess.Popen[bytes], streams: tuple[str, ...] = ("stdout", "stderr")
    ) -> None:
        self.outputs: dict[str, bytes] = {}
        self._threads: list[threading.Thread] = []
        for name in streams:
            stream = getattr(process, name)
            if stream is not None and not stream.closed:
                thread = threading.Thread(target=self._read, args=(name, stream), daemon=True)
                thread.start()
                self._threads.append(thread)

    def _read(self, name: str, stream: Any) -> None:
        self.outputs[name] = stream.read()

    def join(self) -> tuple[bytes, str]:
        for thread in self._threads:
            thread.join(10)
        errors = self.outputs.get("stderr", b"").decode("utf-8", "replace")
        return self.outputs.get("stdout", b""), scrub(errors)


def _start(
    args: list[str], *, stdin: Any = subprocess.DEVNULL, stdout: Any
) -> subprocess.Popen[bytes]:
    """Start a client tool with the connection in its environment, never its arguments."""
    try:
        return subprocess.Popen(
            args, stdin=stdin, stdout=stdout, stderr=subprocess.PIPE, env=_environment()
        )
    except OSError as exc:
        raise DumpToolsError(f"{Path(args[0]).name} cannot be started: {exc.strerror}") from None


def _wait(
    process: subprocess.Popen[bytes],
    readers: _Readers,
    timeout_s: float,
    cancel: CancellationSignal | None = None,
) -> tuple[int, bytes, str]:
    """Wait for a tool; returns its exit code, its output, and its scrubbed error output."""
    name = Path(str(process.args[0])).name  # type: ignore[index]
    deadline = time.monotonic() + timeout_s
    while process.poll() is None:
        if cancel is not None and cancel.is_set():
            process.terminate()
            process.wait(10)
            raise_if_cancelled(cancel, f"{name} stopped: the job was cancelled")
        if time.monotonic() > deadline:
            process.kill()
            process.wait(10)
            raise DumpError(f"{name} did not finish within {timeout_s:.0f} s")
        time.sleep(0.05)
    output, errors = readers.join()
    return process.returncode, output, errors


def _run(
    args: list[str],
    *,
    timeout_s: float,
    stdout: Any = subprocess.PIPE,
    cancel: CancellationSignal | None = None,
) -> tuple[int, bytes, str]:
    process = _start(args, stdout=stdout)
    return _wait(process, _Readers(process), timeout_s, cancel)


# ── Dump files ────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class DumpFile:
    name: str
    kind: Kind
    made_at: datetime
    revision: str | None
    size_bytes: int

    def to_dict(self) -> dict[str, Any]:
        return {**asdict(self), "made_at": self.made_at.isoformat()}


def _kind(prefix: str) -> Kind:
    return next(kind for kind, value in PREFIXES.items() if value == prefix)  # type: ignore[return-value]


def parse_name(name: str) -> tuple[Kind, datetime, str | None] | None:
    """What a dump's file name says: its kind, when it was made, and its schema revision."""
    found = _NAME.fullmatch(name)
    if found is None:
        return None
    made = datetime.strptime(found.group("time"), TIME_FORMAT).replace(tzinfo=UTC)
    return _kind(found.group("prefix")), made, found.group("revision")


def dump_name(kind: Kind, made_at: datetime, revision: str | None) -> str:
    stamp = made_at.astimezone(UTC).strftime(TIME_FORMAT)
    if kind == "pre-restore":
        return f"{PREFIXES[kind]}-{stamp}{SUFFIX}"
    return f"{PREFIXES[kind]}-{stamp}-{revision or 'none'}{SUFFIX}"


def list_dumps(folder: Path | None = None) -> list[DumpFile]:
    """Every dump in the folder, newest first. Temporary files and other names are skipped."""
    base = folder or settings.db_backups_dir
    if not base.is_dir():
        return []
    found: list[DumpFile] = []
    for entry in os.scandir(base):
        parsed = parse_name(entry.name)
        if parsed is None or not entry.is_file(follow_symlinks=False):
            continue
        kind, made, revision = parsed
        found.append(DumpFile(entry.name, kind, made, revision, entry.stat().st_size))
    return sorted(found, key=lambda dump: (dump.made_at, dump.name), reverse=True)


def _boundary(folder: Path) -> FilesystemBoundary:
    folder.mkdir(mode=0o700, parents=True, exist_ok=True)
    return boundary_for_roots({_ROOT: folder}, access="read_write", purpose="database dumps")


def prune(keep: int, kind: Kind = "scheduled", folder: Path | None = None) -> list[str]:
    """Delete the dumps of ``kind`` beyond the newest ``keep``; returns the names deleted."""
    if kind == "manual":
        raise ValueError("dumps made with `marquee db backup` are never deleted by Marquee")
    base = folder or settings.db_backups_dir
    stale = [dump for dump in list_dumps(base) if dump.kind == kind][keep:]
    if not stale:
        return []
    boundary = _boundary(base)
    for dump in stale:
        boundary.delete_file(boundary.from_key(_ROOT, dump.name))
        logger.info("BACKUP | deleted %s (keeping the newest %d)", dump.name, keep)
    return [dump.name for dump in stale]


def sha256_of_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def check_dump(path: Path, tools: Tools) -> None:
    """``pg_restore --list`` must read the file; raises :class:`DumpError` otherwise."""
    code, listing, errors = _run(
        [tools["pg_restore"], "--list", str(path)], timeout_s=CHECK_TIMEOUT_S
    )
    if code != 0 or not listing.strip():
        raise DumpError(f"{path.name} is not a dump pg_restore can read: {errors or 'no listing'}")


def dump_revision(path: Path, tools: Tools) -> str | None:
    """The schema revision a dump holds (its ``alembic_version`` row), or ``None``."""
    code, script, errors = _run(
        [tools["pg_restore"], "--data-only", "--table=alembic_version", "--file=-", str(path)],
        timeout_s=CHECK_TIMEOUT_S,
    )
    if code != 0:
        raise DumpError(f"{path.name}: the schema revision cannot be read: {errors}")
    lines = script.decode("utf-8", "replace").splitlines()
    for index, line in enumerate(lines):
        if line.startswith("COPY public.alembic_version") and index + 1 < len(lines):
            value = lines[index + 1].strip()
            return None if value in ("", "\\.") else value.split("\t")[0]
    return None


# ── Making a dump ─────────────────────────────────────────────────────────


@dataclass(frozen=True)
class DumpResult:
    file: str
    kind: Kind
    size_bytes: int
    sha256: str
    revision: str | None
    seconds: float
    server_major: int
    client_major: int
    deleted: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def make_dump(
    kind: Kind = "scheduled",
    *,
    folder: Path | None = None,
    now: datetime | None = None,
    cancel: CancellationSignal | None = None,
) -> DumpResult:
    """Dump the whole database to a new file in ``folder`` and check it reads back."""
    started = time.monotonic()
    state = database_state()
    tools = find_tools(state.server_major)
    base = folder or settings.db_backups_dir
    boundary = _boundary(base)
    name = dump_name(kind, now or datetime.now(UTC), state.revision)
    if (base / name).exists():
        raise DumpError(f"a dump named {name} already exists; try again in a second")
    staged, fd = boundary.temporary_root_file(_ROOT, prefix=_TEMPORARY_PREFIX)
    staged_path = base / staged.key.value
    try:
        try:
            code, _, errors = _run(
                [tools["pg_dump"], "--format=custom", "--no-password"],
                timeout_s=DUMP_TIMEOUT_S,
                stdout=fd,
                cancel=cancel,
            )
            os.fsync(fd)
        finally:
            os.close(fd)
        if code != 0:
            raise DumpError(f"pg_dump failed (exit {code}) on {_target()}: {_tail(errors)}")
        check_dump(staged_path, tools)
        os.chmod(staged_path, 0o600)
        boundary.atomic_replace(staged, boundary.from_key(_ROOT, name))
    except BaseException:
        boundary.delete_file(staged, missing_ok=True)
        raise
    final = base / name
    result = DumpResult(
        file=name,
        kind=kind,
        size_bytes=final.stat().st_size,
        sha256=sha256_of_file(final),
        revision=state.revision,
        seconds=round(time.monotonic() - started, 2),
        server_major=state.server_major,
        client_major=tools.major,
    )
    logger.info(
        "BACKUP | %s: %d bytes, revision %s, %.1f s",
        name,
        result.size_bytes,
        result.revision,
        result.seconds,
    )
    return result
