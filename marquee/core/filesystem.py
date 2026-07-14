"""Confined filesystem authority for serving and mutating classified paths."""

from __future__ import annotations

import errno
import os
import shutil
import stat
import tarfile
import tempfile
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Literal
from urllib.parse import quote

from fastapi.responses import StreamingResponse


class FilesystemBoundaryError(ValueError):
    """A path, key, root, or operation violates the confinement contract."""


RootAccess = Literal["read", "read_write"]


@dataclass(frozen=True, slots=True)
class ConfinedKey:
    value: str

    @classmethod
    def parse(cls, value: str) -> ConfinedKey:
        if not isinstance(value, str) or not value or "\0" in value or "\\" in value:
            raise FilesystemBoundaryError("confined key must be non-empty unambiguous text")
        if value in {".", ".."} or value.startswith(("/", "//")):
            raise FilesystemBoundaryError("confined key must be relative POSIX text")
        if ":" in value.split("/", 1)[0]:
            raise FilesystemBoundaryError("confined key must be relative POSIX text")
        path = PurePosixPath(value)
        if str(path) != value or any(part in {"", ".", ".."} for part in path.parts):
            raise FilesystemBoundaryError("confined key contains an unsafe component")
        return cls(value)

    @property
    def parts(self) -> tuple[str, ...]:
        return PurePosixPath(self.value).parts


@dataclass(frozen=True, slots=True)
class RootSpec:
    name: str
    path: Path
    purpose: str
    access: RootAccess = "read"
    allow_symlinks: bool = False
    required: bool = True
    same_filesystem: bool = False

    def resolved(self) -> Path:
        try:
            root = self.path.resolve(strict=self.required)
        except (OSError, RuntimeError) as exc:
            raise FilesystemBoundaryError(f"root {self.name!r} is unavailable") from exc
        if self.required and not root.is_dir():
            raise FilesystemBoundaryError(f"root {self.name!r} is not a directory")
        return root


@dataclass(frozen=True, slots=True)
class ClassifiedPath:
    root: RootSpec
    key: ConfinedKey


class FilesystemBoundary:
    def __init__(self, roots: Mapping[str, RootSpec]) -> None:
        self._roots = dict(roots)
        if not self._roots or set(self._roots) != {root.name for root in self._roots.values()}:
            raise FilesystemBoundaryError("filesystem roots must be non-empty and name-keyed")

    @property
    def roots(self) -> Mapping[str, RootSpec]:
        return self._roots.copy()

    def classify(
        self,
        path: str | Path,
        *,
        roots: tuple[str, ...] | None = None,
        require_exists: bool = True,
        require_file: bool = False,
        write: bool = False,
    ) -> ClassifiedPath:
        raw = os.fspath(path)
        if not raw or "\0" in raw:
            raise FilesystemBoundaryError("physical path is empty or ambiguous")
        candidate = Path(raw)
        if not candidate.is_absolute():
            raise FilesystemBoundaryError("physical path must be absolute before classification")
        try:
            resolved = candidate.resolve(strict=require_exists)
        except (OSError, RuntimeError) as exc:
            raise FilesystemBoundaryError("physical path cannot be resolved") from exc

        selected = roots or tuple(self._roots)
        for name in selected:
            root = self._roots.get(name)
            if root is None:
                raise FilesystemBoundaryError(f"unknown filesystem root {name!r}")
            root_path = root.resolved()
            if not resolved.is_relative_to(root_path) or resolved == root_path:
                continue
            if write and root.access != "read_write":
                raise FilesystemBoundaryError(f"root {name!r} is read-only")
            key = ConfinedKey.parse(resolved.relative_to(root_path).as_posix())
            if not root.allow_symlinks:
                self._reject_symlink_components(root_path, key, require_exists=require_exists)
            if require_file and not resolved.is_file():
                raise FilesystemBoundaryError("classified path is not a regular file")
            return ClassifiedPath(root=root, key=key)
        raise FilesystemBoundaryError("physical path is outside the selected roots")

    def from_key(self, root_name: str, key: str | ConfinedKey) -> ClassifiedPath:
        root = self._roots.get(root_name)
        if root is None:
            raise FilesystemBoundaryError(f"unknown filesystem root {root_name!r}")
        parsed = key if isinstance(key, ConfinedKey) else ConfinedKey.parse(key)
        return ClassifiedPath(root=root, key=parsed)

    @staticmethod
    def _reject_symlink_components(
        root: Path, key: ConfinedKey, *, require_exists: bool
    ) -> None:
        current = root
        for index, part in enumerate(key.parts):
            current = current / part
            try:
                mode = current.lstat().st_mode
            except FileNotFoundError:
                if require_exists or index < len(key.parts) - 1:
                    raise FilesystemBoundaryError("classified path component is missing") from None
                return
            if stat.S_ISLNK(mode):
                raise FilesystemBoundaryError("symlink traversal is not allowed")

    @staticmethod
    def _open_parent(root: Path, key: ConfinedKey) -> tuple[int, int, str]:
        root_fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
        current_fd = root_fd
        try:
            for part in key.parts[:-1]:
                next_fd = os.open(
                    part,
                    os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                    dir_fd=current_fd,
                )
                if current_fd != root_fd:
                    os.close(current_fd)
                current_fd = next_fd
            return root_fd, current_fd, key.parts[-1]
        except BaseException:
            if current_fd != root_fd:
                os.close(current_fd)
            os.close(root_fd)
            raise

    def open_read(self, classified: ClassifiedPath) -> int:
        current = self.classify(
            classified.root.resolved() / classified.key.value,
            roots=(classified.root.name,),
            require_file=True,
        )
        root_fd, parent_fd, leaf = self._open_parent(current.root.resolved(), current.key)
        try:
            fd = os.open(leaf, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=parent_fd)
            if not stat.S_ISREG(os.fstat(fd).st_mode):
                os.close(fd)
                raise FilesystemBoundaryError("served object is not a regular file")
            return fd
        finally:
            if parent_fd != root_fd:
                os.close(parent_fd)
            os.close(root_fd)

    def response(
        self,
        classified: ClassifiedPath,
        *,
        media_type: str | None = None,
        filename: str | None = None,
    ) -> StreamingResponse:
        fd = self.open_read(classified)

        def chunks() -> Iterator[bytes]:
            try:
                while chunk := os.read(fd, 64 * 1024):
                    yield chunk
            finally:
                os.close(fd)

        headers: dict[str, str] = {}
        if filename:
            safe_name = Path(filename).name
            headers["content-disposition"] = f"attachment; filename*=UTF-8''{quote(safe_name)}"
        return StreamingResponse(chunks(), media_type=media_type, headers=headers)

    def delete_file(self, classified: ClassifiedPath, *, missing_ok: bool = True) -> bool:
        current = self.classify(
            classified.root.resolved() / classified.key.value,
            roots=(classified.root.name,),
            require_exists=not missing_ok,
            write=True,
        )
        root_fd, parent_fd, leaf = self._open_parent(current.root.resolved(), current.key)
        try:
            try:
                os.unlink(leaf, dir_fd=parent_fd)
                return True
            except FileNotFoundError:
                if missing_ok:
                    return False
                raise
        finally:
            if parent_fd != root_fd:
                os.close(parent_fd)
            os.close(root_fd)

    def create_file(
        self,
        classified: ClassifiedPath,
        *,
        mode: int = 0o600,
        exclusive: bool = True,
    ) -> int:
        current = self.classify(
            classified.root.resolved() / classified.key.value,
            roots=(classified.root.name,),
            require_exists=False,
            write=True,
        )
        root_fd, parent_fd, leaf = self._open_parent(current.root.resolved(), current.key)
        flags = os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW
        if exclusive:
            flags |= os.O_EXCL
        else:
            flags |= os.O_TRUNC
        try:
            return os.open(leaf, flags, mode, dir_fd=parent_fd)
        finally:
            if parent_fd != root_fd:
                os.close(parent_fd)
            os.close(root_fd)

    def create_directory(
        self,
        classified: ClassifiedPath,
        *,
        mode: int = 0o700,
        parents: bool = False,
    ) -> ClassifiedPath:
        """Create a confined directory without following any path component."""
        root = classified.root
        if root.access != "read_write":
            raise FilesystemBoundaryError(f"root {root.name!r} is read-only")
        root_path = root.resolved()
        root_fd = os.open(root_path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        current_fd = root_fd
        try:
            for index, part in enumerate(classified.key.parts):
                final = index == len(classified.key.parts) - 1
                try:
                    next_fd = os.open(
                        part,
                        os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                        dir_fd=current_fd,
                    )
                    if final and not parents:
                        os.close(next_fd)
                        raise FileExistsError(classified.key.value)
                except FileNotFoundError:
                    if not parents and not final:
                        raise FilesystemBoundaryError("directory parent does not exist") from None
                    os.mkdir(part, mode=mode, dir_fd=current_fd)
                    next_fd = os.open(
                        part,
                        os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                        dir_fd=current_fd,
                    )
                if current_fd != root_fd:
                    os.close(current_fd)
                current_fd = next_fd
            os.fsync(current_fd)
        finally:
            if current_fd != root_fd:
                os.close(current_fd)
            os.close(root_fd)
        return self.classify(
            root_path / classified.key.value,
            roots=(root.name,),
            require_exists=True,
            write=True,
        )

    def temporary_file(
        self,
        directory: ClassifiedPath,
        *,
        prefix: str = ".marquee-",
    ) -> tuple[ClassifiedPath, int]:
        if "/" in prefix or "\\" in prefix or "\0" in prefix:
            raise FilesystemBoundaryError("temporary-file prefix is unsafe")
        directory_path = directory.root.resolved() / directory.key.value
        current = self.classify(
            directory_path,
            roots=(directory.root.name,),
            require_exists=True,
            write=True,
        )
        if not directory_path.is_dir():
            raise FilesystemBoundaryError("temporary-file destination is not a directory")
        fd, raw = tempfile.mkstemp(prefix=prefix, dir=directory_path)
        try:
            return self.classify(raw, roots=(current.root.name,), write=True), fd
        except BaseException:
            os.close(fd)
            os.unlink(raw)
            raise

    def copy_file(self, source: ClassifiedPath, destination: ClassifiedPath) -> None:
        source_fd = self.open_read(source)
        try:
            destination_fd = self.create_file(destination)
            try:
                while chunk := os.read(source_fd, 1024 * 1024):
                    view = memoryview(chunk)
                    while view:
                        view = view[os.write(destination_fd, view) :]
                os.fsync(destination_fd)
            finally:
                os.close(destination_fd)
        finally:
            os.close(source_fd)

    def atomic_replace(self, staged: ClassifiedPath, destination: ClassifiedPath) -> None:
        if staged.root.name != destination.root.name:
            raise FilesystemBoundaryError("atomic replacement requires one classified root")
        if staged.key.parts[:-1] != destination.key.parts[:-1]:
            raise FilesystemBoundaryError("atomic replacement requires one destination directory")
        staged_now = self.classify(
            staged.root.resolved() / staged.key.value,
            roots=(staged.root.name,),
            require_file=True,
            write=True,
        )
        destination_now = self.classify(
            destination.root.resolved() / destination.key.value,
            roots=(destination.root.name,),
            require_exists=False,
            write=True,
        )
        root_fd, parent_fd, staged_leaf = self._open_parent(
            staged_now.root.resolved(), staged_now.key
        )
        try:
            staged_stat = os.stat(staged_leaf, dir_fd=parent_fd, follow_symlinks=False)
            directory_stat = os.fstat(parent_fd)
            if staged_stat.st_dev != directory_stat.st_dev:
                raise FilesystemBoundaryError("staged output is on another filesystem")
            try:
                os.replace(
                    staged_leaf,
                    destination_now.key.parts[-1],
                    src_dir_fd=parent_fd,
                    dst_dir_fd=parent_fd,
                )
            except OSError as exc:
                if exc.errno == errno.EXDEV:
                    raise FilesystemBoundaryError("cross-device publication is prohibited") from exc
                raise
            os.fsync(parent_fd)
        finally:
            if parent_fd != root_fd:
                os.close(parent_fd)
            os.close(root_fd)

    def cleanup_directory(self, classified: ClassifiedPath) -> None:
        current = self.classify(
            classified.root.resolved() / classified.key.value,
            roots=(classified.root.name,),
            require_exists=True,
            write=True,
        )
        target = current.root.resolved() / current.key.value
        self._reject_symlink_components(current.root.resolved(), current.key, require_exists=True)
        shutil.rmtree(target)

    def extract_tar(self, archive: ClassifiedPath, destination: ClassifiedPath) -> list[ConfinedKey]:
        destination_path = destination.root.resolved() / destination.key.value
        self.classify(
            destination_path,
            roots=(destination.root.name,),
            require_exists=True,
            write=True,
        )
        extracted: list[ConfinedKey] = []
        archive_fd = self.open_read(archive)
        with os.fdopen(archive_fd, "rb", closefd=True) as stream, tarfile.open(
            fileobj=stream, mode="r:*"
        ) as bundle:
            for member in bundle.getmembers():
                member_key = ConfinedKey.parse(member.name.rstrip("/"))
                if member.issym() or member.islnk() or member.isdev():
                    raise FilesystemBoundaryError("archive links and device nodes are prohibited")
                target = destination_path.joinpath(*member_key.parts)
                if member.isdir():
                    target.mkdir(mode=0o700, parents=True, exist_ok=True)
                    continue
                if not member.isfile():
                    raise FilesystemBoundaryError("archive member type is prohibited")
                target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
                classified_target = self.classify(
                    target,
                    roots=(destination.root.name,),
                    require_exists=False,
                    write=True,
                )
                source = bundle.extractfile(member)
                if source is None:
                    raise FilesystemBoundaryError("archive member cannot be read")
                target_fd = self.create_file(classified_target)
                try:
                    with source:
                        while chunk := source.read(1024 * 1024):
                            os.write(target_fd, chunk)
                    os.fsync(target_fd)
                finally:
                    os.close(target_fd)
                extracted.append(member_key)
        return extracted


def boundary_for_roots(
    roots: Mapping[str, Path], *, access: RootAccess = "read", purpose: str = "classified"
) -> FilesystemBoundary:
    return FilesystemBoundary(
        {
            name: RootSpec(name=name, path=path, purpose=purpose, access=access)
            for name, path in roots.items()
        }
    )
