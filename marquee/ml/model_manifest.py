"""The checksum manifest of every model file the engine loads.

``data/models/manifest.json`` lists one entry per model file or model folder: its path
relative to the data directory, its size, its SHA-256, where it came from, the tool versions
that built it, and when. A model folder (the OCR weights) gets one digest computed over its
files' relative paths and their SHA-256s, so adding, removing, renaming, or changing any file
inside it changes the digest. Hidden files and folders are left out: downloaders keep their
bookkeeping there (lock files, ``.cache/``), and it is not part of the model.

:func:`verify` re-hashes what the manifest lists and returns every entry that is missing or
no longer matches. The manifest is written atomically so a crash never leaves half a file.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

MANIFEST_FORMAT = 1
_CHUNK = 1024 * 1024

EntryKind = Literal["file", "folder"]


class ManifestError(Exception):
    """The manifest file exists but cannot be read as a manifest."""


@dataclass(frozen=True)
class ManifestEntry:
    """One model file or model folder."""

    item: str
    """The ``marquee models fetch`` item this belongs to (``clip``, ``ocr``, ...)."""
    path: str
    """Relative to the data directory, with ``/`` separators."""
    kind: EntryKind
    size: int
    """Bytes; for a folder, the total over its files."""
    sha256: str
    source: str
    built_at: str
    """When the file was built or first recorded (ISO 8601, UTC)."""
    tools: dict[str, str] = field(default_factory=dict)
    details: dict[str, str] = field(default_factory=dict)
    """Anything else worth keeping, such as the checksum of a file it was built from."""


@dataclass(frozen=True)
class Manifest:
    entries: tuple[ManifestEntry, ...]
    written_at: str

    def by_path(self) -> dict[str, ManifestEntry]:
        return {entry.path: entry for entry in self.entries}

    def for_item(self, item: str) -> list[ManifestEntry]:
        return [entry for entry in self.entries if entry.item == item]


@dataclass(frozen=True)
class ManifestProblem:
    path: str
    reason: Literal["missing", "size mismatch", "checksum mismatch", "wrong kind"]


def utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(_CHUNK):
            digest.update(chunk)
    return digest.hexdigest()


def _folder_files(folder: Path) -> list[Path]:
    """Every visible file under ``folder``; nothing below a hidden file or folder."""
    return sorted(
        path
        for path in folder.rglob("*")
        if path.is_file()
        and not any(part.startswith(".") for part in path.relative_to(folder).parts)
    )


def folder_digest(folder: Path) -> tuple[str, int]:
    """(digest, total size) over every visible file under ``folder``.

    The digest is the SHA-256 of one ``<relative path>\\0<file sha256>\\n`` line per file,
    in sorted path order.
    """
    digest = hashlib.sha256()
    total = 0
    for file in _folder_files(folder):
        relative = file.relative_to(folder).as_posix()
        digest.update(f"{relative}\0{sha256_file(file)}\n".encode())
        total += file.stat().st_size
    return digest.hexdigest(), total


def relative_to_data(data_dir: Path, path: Path) -> str:
    return Path(path).resolve().relative_to(Path(data_dir).resolve()).as_posix()


def describe(
    data_dir: Path,
    path: Path,
    *,
    item: str,
    source: str,
    tools: dict[str, str] | None = None,
    built_at: str | None = None,
    details: dict[str, str] | None = None,
) -> ManifestEntry:
    """Hash a model file or folder under ``data_dir`` into a manifest entry."""
    path = Path(path)
    if path.is_dir():
        sha, size = folder_digest(path)
        kind: EntryKind = "folder"
    elif path.is_file():
        sha, size = sha256_file(path), path.stat().st_size
        kind = "file"
    else:
        raise FileNotFoundError(f"model path not found: {path}")
    return ManifestEntry(
        item=item,
        path=relative_to_data(data_dir, path),
        kind=kind,
        size=size,
        sha256=sha,
        source=source,
        built_at=built_at or utc_now(),
        tools=dict(tools or {}),
        details=dict(details or {}),
    )


def load_manifest(path: Path) -> Manifest | None:
    """The manifest at ``path``, or ``None`` when there is none."""
    path = Path(path)
    if not path.exists():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        if raw.get("format") != MANIFEST_FORMAT:
            raise ManifestError(f"{path}: unsupported manifest format {raw.get('format')!r}")
        entries = tuple(ManifestEntry(**entry) for entry in raw["entries"])
        return Manifest(entries=entries, written_at=str(raw["written_at"]))
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise ManifestError(f"{path}: cannot read manifest: {exc}") from exc


def write_manifest(
    path: Path, entries: list[ManifestEntry] | tuple[ManifestEntry, ...]
) -> Manifest:
    """Write the manifest atomically: a temporary file in the same folder, then a rename."""
    path = Path(path)
    paths = [entry.path for entry in entries]
    if len(paths) != len(set(paths)):
        raise ValueError("manifest entries must have unique paths")
    manifest = Manifest(
        entries=tuple(sorted(entries, key=lambda entry: (entry.item, entry.path))),
        written_at=utc_now(),
    )
    payload = {
        "format": MANIFEST_FORMAT,
        "written_at": manifest.written_at,
        "entries": [asdict(entry) for entry in manifest.entries],
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, sort_keys=False)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        # mkstemp creates the file private to this user; the manifest is not a secret.
        os.chmod(tmp_name, 0o644)
        os.replace(tmp_name, path)
    except BaseException:
        Path(tmp_name).unlink(missing_ok=True)
        raise
    return manifest


def verify_entry(data_dir: Path, entry: ManifestEntry) -> ManifestProblem | None:
    target = Path(data_dir) / entry.path
    if not target.exists():
        return ManifestProblem(entry.path, "missing")
    if (entry.kind == "folder") != target.is_dir():
        return ManifestProblem(entry.path, "wrong kind")
    if entry.kind == "file":
        if target.stat().st_size != entry.size:
            return ManifestProblem(entry.path, "size mismatch")
        if sha256_file(target) != entry.sha256:
            return ManifestProblem(entry.path, "checksum mismatch")
        return None
    sha, size = folder_digest(target)
    if size != entry.size:
        return ManifestProblem(entry.path, "size mismatch")
    if sha != entry.sha256:
        return ManifestProblem(entry.path, "checksum mismatch")
    return None


def verify(manifest: Manifest, data_dir: Path) -> list[ManifestProblem]:
    """Every listed file or folder that is missing or does not match. Empty means clean."""
    problems = []
    for entry in manifest.entries:
        problem = verify_entry(data_dir, entry)
        if problem is not None:
            problems.append(problem)
    return problems
