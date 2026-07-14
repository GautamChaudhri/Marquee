"""Dolby Vision Profile 8.1 remediation jobs.

The conversion path is intentionally non-destructive: jobs write a candidate
MKV under the same managed ``.marquee`` tree used by letterbox re-encodes and
return that path in the durable job result. Replacing originals can be layered
on top of this artifact later without making the first conversion pass risky.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import shutil
import time
from pathlib import Path
from typing import Any, Literal

from sqlalchemy.ext.asyncio import AsyncSession

from marquee.core.jobs import job_manager
from marquee.core.jobs.child_tracking import clear_child_pid, record_child_pid
from marquee.core.letterbox_reencode import (
    _extract_rpu_piped,
    _managed_root,
    _parse_progress,
    _run_checked,
    _source_key,
    build_dovi_remux_args,
    inspect_source,
)
from marquee.core.media_files import ResolvedMediaFile, compute_signature
from marquee.media import binaries
from marquee.media.concurrency import gated
from marquee.models import Job, MediaFile

logger = logging.getLogger(__name__)

ConversionKind = Literal["p5_to_p81", "p7_strip_el"]


class DoviConversionError(Exception):
    """A Dolby Vision conversion cannot be planned or completed."""

    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(message)


def candidate_output_path(source: Path, source_key: str, job_id: str) -> Path:
    return _managed_root(source) / "dovi" / "candidates" / source_key / job_id / source.name


async def _emit(
    db: AsyncSession,
    job: Job,
    stage: str,
    state: str = "running",
    *,
    message: str | None = None,
    progress: dict[str, Any] | None = None,
    persist: bool = True,
) -> None:
    job.current_stage = stage
    if progress is not None:
        job.progress = {"stage": stage, **progress}
    elif message:
        job.progress = {"stage": stage, "message": message}
    await job_manager.emit(
        db,
        job,
        state=state,
        stage=stage,
        message=message,
        detail=job.progress,
        persist=persist,
    )
    if persist:
        await db.commit()


def _require_binaries(*names: str) -> None:
    missing = [name for name in names if binaries.resolve(name) is None]
    if missing:
        raise DoviConversionError(
            "missing_binary",
            f"Required binary not found on PATH: {', '.join(missing)}",
        )


def _p5_base_encode_args(source: Path, output: Path) -> list[str]:
    """Build the Profile 5 base-layer re-encode command.

    Profile 5's IPT-PQ-C2 base layer is what produces the familiar green/purple
    playback on non-DV clients. The encode writes a BT.2020/PQ HEVC base layer;
    the converted RPU is injected in a later step.
    """
    return [
        "-y",
        "-hide_banner",
        "-nostdin",
        "-i",
        str(source),
        "-map",
        "0",
        "-map_metadata",
        "0",
        "-map_chapters",
        "0",
        "-copy_unknown",
        "-max_muxing_queue_size",
        "4096",
        "-c",
        "copy",
        "-c:v:0",
        "libx265",
        "-preset",
        "slow",
        "-crf",
        "18",
        "-pix_fmt",
        "yuv420p10le",
        "-vf",
        "format=p010le,zscale=primaries=bt2020:transfer=smpte2084:matrix=bt2020nc,format=yuv420p10le",
        "-color_primaries",
        "bt2020",
        "-color_trc",
        "smpte2084",
        "-colorspace",
        "bt2020nc",
        "-progress",
        "pipe:1",
        "-nostats",
        str(output),
    ]


async def _run_p5_base_encode(
    db: AsyncSession,
    job: Job,
    source: Path,
    output: Path,
    duration_s: float | None,
) -> None:
    proc = await asyncio.create_subprocess_exec(
        binaries.resolve("ffmpeg") or "ffmpeg",
        *_p5_base_encode_args(source, output),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    await record_child_pid(proc.pid)
    try:
        assert proc.stdout is not None
        last_persist = 0.0
        progress_values: dict[str, str] = {}
        while True:
            line = await proc.stdout.readline()
            if not line:
                break
            progress = _parse_progress(line.decode(errors="replace"), duration_s, progress_values)
            if progress is None:
                continue
            now = time.monotonic()
            persist = now - last_persist >= 1.0
            if persist:
                last_persist = now
            await _emit(
                db,
                job,
                "encode",
                message="Re-encoding Profile 5 base layer",
                progress={**progress, "message": "Re-encoding Profile 5 base layer"},
                persist=persist,
            )
            if persist:
                await db.refresh(job, ["cancel_requested"])
                if job.desired_state == "cancel":
                    proc.terminate()
                    await proc.wait()
                    output.unlink(missing_ok=True)
                    raise DoviConversionError("cancelled", "Dolby Vision conversion cancelled")
        stderr = await proc.stderr.read() if proc.stderr is not None else b""
        await proc.wait()
        if proc.returncode != 0:
            output.unlink(missing_ok=True)
            diagnostic = stderr.decode(errors="replace")[:500]
            raise DoviConversionError("ffmpeg_failed", diagnostic or "ffmpeg encode failed")
    finally:
        await clear_child_pid(proc.pid)


async def _pipe_hevc_to_dovi(
    source_mkv: Path,
    dovi_args: list[str],
    *,
    timeout: float = 3600,
) -> None:
    ffmpeg_bin = binaries.resolve("ffmpeg") or "ffmpeg"
    dovi_bin = binaries.resolve("dovi_tool") or "dovi_tool"
    ffmpeg_args = [
        "-hide_banner",
        "-nostdin",
        "-i",
        str(source_mkv),
        "-map",
        "0:v:0",
        "-c:v",
        "copy",
        "-bsf:v",
        "hevc_mp4toannexb",
        "-f",
        "hevc",
        "pipe:1",
    ]
    ffmpeg_proc = await asyncio.create_subprocess_exec(
        ffmpeg_bin,
        *ffmpeg_args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
    )
    await record_child_pid(ffmpeg_proc.pid)
    assert ffmpeg_proc.stdout is not None
    dovi_proc = await asyncio.create_subprocess_exec(
        dovi_bin,
        *dovi_args,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.PIPE,
    )
    await record_child_pid(dovi_proc.pid)
    assert dovi_proc.stdin is not None

    try:

        async def _pump() -> None:
            try:
                while True:
                    chunk = await ffmpeg_proc.stdout.read(1 << 20)
                    if not chunk:
                        break
                    try:
                        dovi_proc.stdin.write(chunk)
                        await dovi_proc.stdin.drain()
                    except (BrokenPipeError, ConnectionResetError):
                        break
            finally:
                with contextlib.suppress(Exception):
                    dovi_proc.stdin.close()

        _, (_, dovi_stderr) = await asyncio.wait_for(
            asyncio.gather(_pump(), dovi_proc.communicate()), timeout=timeout
        )
        await ffmpeg_proc.wait()
    finally:
        await clear_child_pid(ffmpeg_proc.pid)
        await clear_child_pid(dovi_proc.pid)

    if ffmpeg_proc.returncode != 0:
        raise DoviConversionError(
            "ffmpeg_failed",
            f"FFmpeg HEVC extraction exited {ffmpeg_proc.returncode} during DoVi conversion",
        )
    if dovi_proc.returncode != 0:
        message = (dovi_stderr or b"").decode(errors="replace")[:500]
        raise DoviConversionError("dovi_tool_failed", message or "dovi_tool conversion failed")


async def _convert_hevc_stream(
    source_mkv: Path,
    output_hevc: Path,
    *,
    mode: int,
    discard_el: bool = False,
) -> None:
    args = ["--mode", str(mode), "convert"]
    if discard_el:
        args.append("--discard")
    args.extend(["-", "-o", str(output_hevc)])
    await _pipe_hevc_to_dovi(source_mkv, args)


async def _inject_converted_rpu(
    encoded_mkv: Path,
    rpu: Path,
    output_hevc: Path,
    *,
    mode: int,
) -> None:
    args = [
        "--mode",
        str(mode),
        "inject-rpu",
        "-i",
        "-",
        "--rpu-in",
        str(rpu),
        "-o",
        str(output_hevc),
    ]
    await _pipe_hevc_to_dovi(encoded_mkv, args)


def _validate_output(
    source_path: Path, output_path: Path, source_info, kind: ConversionKind
) -> list[str]:
    problems: list[str] = []
    out = inspect_source(output_path)
    if out is None:
        return ["output not probeable"]
    if out.width != source_info.width or out.height != source_info.height:
        problems.append(
            f"unexpected dimensions: {out.width}x{out.height}, expected {source_info.width}x{source_info.height}"
        )
    if out.audio_streams != source_info.audio_streams:
        problems.append(
            f"audio stream count changed: {source_info.audio_streams} -> {out.audio_streams}"
        )
    if out.subtitle_streams != source_info.subtitle_streams:
        problems.append(
            f"subtitle stream count changed: {source_info.subtitle_streams} -> {out.subtitle_streams}"
        )
    if out.attachment_streams != source_info.attachment_streams:
        problems.append(
            f"attachment stream count changed: {source_info.attachment_streams} -> {out.attachment_streams}"
        )
    if (
        source_info.duration_s
        and out.duration_s
        and abs(source_info.duration_s - out.duration_s) > 2.0
    ):
        problems.append(f"duration drifted: {source_info.duration_s:.1f}s -> {out.duration_s:.1f}s")
    if not out.has_dovi:
        problems.append("Dolby Vision metadata was not detected in the output")
    if out.dovi_profile not in {8, None}:
        problems.append(f"output Dolby Vision profile is {out.dovi_profile}, expected profile 8")
    if kind == "p7_strip_el" and out.dovi_el_present:
        problems.append("enhancement layer still detected after Profile 7 strip")
    if not output_path.is_file() or output_path.samefile(source_path):
        problems.append("candidate path is invalid")
    return problems


async def _run_p7_strip(
    db: AsyncSession,
    job: Job,
    source: Path,
    out: Path,
    work: Path,
) -> None:
    converted_hevc = work / "converted-p81.hevc"
    await _emit(
        db,
        job,
        "convert",
        message="Stripping enhancement layer and converting RPU to Profile 8.1",
        progress={"percent": 35, "message": "Converting RPU to Profile 8.1"},
    )
    await _convert_hevc_stream(source, converted_hevc, mode=2, discard_el=True)
    await _emit(
        db,
        job,
        "remux",
        message="Remuxing Profile 8.1 candidate",
        progress={"percent": 75, "message": "Remuxing Profile 8.1 candidate"},
    )
    await _run_checked("ffmpeg", build_dovi_remux_args(source, converted_hevc, out), timeout=3600)


async def _run_p5_to_p81(
    db: AsyncSession,
    job: Job,
    source: Path,
    out: Path,
    work: Path,
    duration_s: float | None,
) -> None:
    rpu = work / "RPU.bin"
    encoded_mkv = work / "base-bt2020.mkv"
    injected_hevc = work / "injected-p81.hevc"

    await _emit(
        db,
        job,
        "rpu",
        message="Extracting Profile 5 RPU",
        progress={"percent": 5, "message": "Extracting Profile 5 RPU"},
    )
    await _extract_rpu_piped(source, rpu)

    await _emit(
        db,
        job,
        "encode",
        message="Re-encoding Profile 5 base layer",
        progress={"percent": 10, "message": "Re-encoding Profile 5 base layer"},
    )
    await _run_p5_base_encode(db, job, source, encoded_mkv, duration_s)

    await _emit(
        db,
        job,
        "inject",
        message="Injecting converted Profile 8.1 RPU",
        progress={"percent": 82, "message": "Injecting converted Profile 8.1 RPU"},
    )
    await _inject_converted_rpu(encoded_mkv, rpu, injected_hevc, mode=3)

    await _emit(
        db,
        job,
        "remux",
        message="Remuxing Profile 8.1 candidate",
        progress={"percent": 92, "message": "Remuxing Profile 8.1 candidate"},
    )
    await _run_checked(
        "ffmpeg", build_dovi_remux_args(encoded_mkv, injected_hevc, out), timeout=3600
    )


async def execute_conversion(
    db: AsyncSession,
    job: Job,
    *,
    resolved: ResolvedMediaFile,
    media_file: MediaFile | None,
    kind: ConversionKind,
) -> dict[str, Any]:
    _require_binaries("ffmpeg", "ffprobe", "dovi_tool")
    if resolved.path.suffix.lower() != ".mkv":
        raise DoviConversionError("not_mkv", "Dolby Vision conversion supports MKV files only.")

    source_info = await gated(inspect_source, resolved.path)
    if source_info is None:
        raise DoviConversionError("probe_failed", "Could not inspect the source video.")
    if source_info.codec != "hevc":
        raise DoviConversionError(
            "unsupported_codec", "Dolby Vision conversion requires HEVC video."
        )
    if not source_info.has_dovi:
        raise DoviConversionError("not_dovi", "No Dolby Vision stream was detected.")
    if kind == "p5_to_p81" and source_info.dovi_profile != 5:
        raise DoviConversionError(
            "wrong_profile", "Profile 5 conversion requires a Profile 5 source."
        )
    if kind == "p7_strip_el" and source_info.dovi_profile != 7:
        raise DoviConversionError("wrong_profile", "Profile 7 strip requires a Profile 7 source.")

    source_key = _source_key(media_file, resolved)
    out = candidate_output_path(resolved.path, source_key, job.id)
    work = out.parent / ".work"
    if work.exists():
        shutil.rmtree(work)
    work.mkdir(parents=True, exist_ok=True)
    out.unlink(missing_ok=True)

    free = shutil.disk_usage(resolved.path.parent).free
    if free < resolved.size_bytes:
        logger.warning("low disk space before DoVi conversion job %s", job.id)

    try:
        await _emit(
            db,
            job,
            "start",
            message="Starting Dolby Vision Profile 8.1 conversion",
            progress={"percent": 0, "message": "Starting conversion"},
        )
        if kind == "p5_to_p81":
            await _run_p5_to_p81(db, job, resolved.path, out, work, source_info.duration_s)
        else:
            await _run_p7_strip(db, job, resolved.path, out, work)

        await _emit(
            db,
            job,
            "validate",
            message="Validating converted candidate",
            progress={"percent": 96, "message": "Validating converted candidate"},
        )
        validation = await gated(_validate_output, resolved.path, out, source_info, kind)
        if validation:
            out.unlink(missing_ok=True)
            raise DoviConversionError("validation_failed", "; ".join(validation))

        stat = out.stat()
        result = {
            "status": "candidate_ready",
            "kind": kind,
            "target_profile": "8.1",
            "source_profile": source_info.dovi_profile,
            "original_path": str(resolved.path),
            "candidate_path": str(out),
            "candidate_size_bytes": stat.st_size,
            "candidate_signature": compute_signature(
                out, size=stat.st_size, mtime_ns=stat.st_mtime_ns
            ),
            "input_signature": resolved.signature,
            "original_untouched": True,
        }
        await _emit(
            db,
            job,
            "done",
            message="Dolby Vision candidate ready",
            progress={"percent": 100, "message": "Candidate ready", **result},
        )
        return result
    finally:
        with contextlib.suppress(OSError):
            shutil.rmtree(work)
