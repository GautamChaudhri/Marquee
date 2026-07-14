"""Durable Linux process identity and containment capability inspection."""

from __future__ import annotations

import contextlib
import errno
import os
import platform
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path


class ProcessIdentityError(RuntimeError):
    pass


class IdentityStatus(StrEnum):
    MATCH = "match"
    DEAD = "dead"
    MISMATCH = "mismatch"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class ProcessIdentity:
    worker_node: str
    host_boot_id: str
    pid: int
    process_group_id: int
    process_start_ticks: int
    cgroup_path: str | None = None


@dataclass(frozen=True, slots=True)
class ContainmentCapabilities:
    platform: str
    process_groups: bool
    proc_identity: bool
    cgroup_v2_mounted: bool
    cgroup_v2_delegated: bool

    @property
    def tier(self) -> str:
        if self.cgroup_v2_delegated:
            return "cgroup_v2"
        if self.process_groups and self.proc_identity:
            return "process_group"
        return "unsupported"

    def public(self) -> dict[str, str | bool]:
        return {
            "tier": self.tier,
            "platform": self.platform,
            "process_groups": self.process_groups,
            "proc_identity": self.proc_identity,
            "cgroup_v2_mounted": self.cgroup_v2_mounted,
            "cgroup_v2_delegated": self.cgroup_v2_delegated,
        }


@dataclass(frozen=True, slots=True)
class CgroupV2Handle:
    path: Path

    def kill(self) -> None:
        try:
            (self.path / "cgroup.kill").write_text("1", encoding="ascii")
        except OSError as exc:
            raise ProcessIdentityError("cgroup kill failed") from exc

    def populated(self) -> bool:
        try:
            events = (self.path / "cgroup.events").read_text(encoding="ascii")
        except OSError as exc:
            raise ProcessIdentityError("cgroup population is unavailable") from exc
        values = dict(line.split(maxsplit=1) for line in events.splitlines() if " " in line)
        if values.get("populated") not in {"0", "1"}:
            raise ProcessIdentityError("cgroup population is malformed")
        return values["populated"] == "1"

    def cleanup(self) -> None:
        if self.populated():
            raise ProcessIdentityError("cannot remove a populated attempt cgroup")
        try:
            self.path.rmdir()
        except OSError as exc:
            raise ProcessIdentityError("attempt cgroup cleanup failed") from exc


def create_attempt_cgroup(
    pid: int,
    *,
    process_start_ticks: int,
    cgroup_root: Path = Path("/sys/fs/cgroup"),
) -> CgroupV2Handle | None:
    capabilities = containment_capabilities(cgroup_root=cgroup_root)
    if not capabilities.cgroup_v2_delegated:
        return None
    path = cgroup_root / f"marquee-{pid}-{process_start_ticks}"
    try:
        path.mkdir(mode=0o700)
        (path / "cgroup.procs").write_text(str(pid), encoding="ascii")
        members = {
            int(value)
            for value in (path / "cgroup.procs").read_text(encoding="ascii").splitlines()
        }
    except (OSError, ValueError) as exc:
        with contextlib.suppress(OSError):
            path.rmdir()
        raise ProcessIdentityError("delegated cgroup attachment failed") from exc
    if pid not in members:
        raise ProcessIdentityError("delegated cgroup did not contain the runner")
    return CgroupV2Handle(path)


def read_boot_id(path: Path = Path("/proc/sys/kernel/random/boot_id")) -> str:
    try:
        value = path.read_text(encoding="ascii").strip()
    except OSError as exc:
        raise ProcessIdentityError("host boot identity is unavailable") from exc
    if not value or len(value) > 64:
        raise ProcessIdentityError("host boot identity is invalid")
    return value


def read_process_start_ticks(pid: int, *, proc_root: Path = Path("/proc")) -> int:
    if isinstance(pid, bool) or pid <= 0:
        raise ProcessIdentityError("process id is invalid")
    try:
        raw = (proc_root / str(pid) / "stat").read_text(encoding="ascii")
    except FileNotFoundError:
        raise ProcessLookupError(pid) from None
    except OSError as exc:
        raise ProcessIdentityError("kernel process identity is unavailable") from exc
    close = raw.rfind(")")
    fields = raw[close + 2 :].split() if close >= 0 else []
    if len(fields) <= 19:
        raise ProcessIdentityError("kernel process identity is malformed")
    try:
        return int(fields[19])  # /proc stat field 22; suffix begins at field 3.
    except ValueError as exc:
        raise ProcessIdentityError("kernel process identity is malformed") from exc


def capture_process_identity(
    pid: int,
    *,
    worker_node: str,
    cgroup_path: str | None = None,
) -> ProcessIdentity:
    try:
        process_group_id = os.getpgid(pid)
    except OSError as exc:
        raise ProcessIdentityError("process group identity is unavailable") from exc
    return ProcessIdentity(
        worker_node=worker_node,
        host_boot_id=read_boot_id(),
        pid=pid,
        process_group_id=process_group_id,
        process_start_ticks=read_process_start_ticks(pid),
        cgroup_path=cgroup_path,
    )


def verify_process_identity(
    identity: ProcessIdentity,
    *,
    current_boot_id: str | None = None,
    proc_root: Path = Path("/proc"),
) -> IdentityStatus:
    try:
        boot_id = current_boot_id or read_boot_id()
    except ProcessIdentityError:
        return IdentityStatus.UNKNOWN
    if boot_id != identity.host_boot_id:
        return IdentityStatus.MISMATCH
    try:
        start_ticks = read_process_start_ticks(identity.pid, proc_root=proc_root)
    except ProcessLookupError:
        return IdentityStatus.DEAD
    except ProcessIdentityError:
        return IdentityStatus.UNKNOWN
    if start_ticks != identity.process_start_ticks:
        return IdentityStatus.MISMATCH
    try:
        if os.getpgid(identity.pid) != identity.process_group_id:
            return IdentityStatus.MISMATCH
    except ProcessLookupError:
        return IdentityStatus.DEAD
    except OSError:
        return IdentityStatus.UNKNOWN
    return IdentityStatus.MATCH


def containment_capabilities(
    *, cgroup_root: Path = Path("/sys/fs/cgroup")
) -> ContainmentCapabilities:
    linux = platform.system() == "Linux"
    mounted = linux and (cgroup_root / "cgroup.controllers").is_file()
    delegated = bool(
        mounted
        and os.access(cgroup_root, os.W_OK)
        and (cgroup_root / "cgroup.procs").is_file()
        and (cgroup_root / "cgroup.events").is_file()
    )
    return ContainmentCapabilities(
        platform=platform.system().lower(),
        process_groups=bool(os.name == "posix" and hasattr(os, "killpg")),
        proc_identity=linux and Path("/proc/self/stat").is_file(),
        cgroup_v2_mounted=mounted,
        cgroup_v2_delegated=delegated,
    )


def process_group_exists(process_group_id: int) -> bool:
    try:
        os.killpg(process_group_id, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError as exc:
        if exc.errno == errno.ESRCH:
            return False
        raise
    return True
