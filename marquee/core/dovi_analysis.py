"""Deep Dolby Vision inspection (ffprobe + dovi_tool).

Radarr reports only *that* a file is Dolby Vision (``Movie.has_dv``). This module
determines the *kind*: exact profile (5 / 7 / 8.x), level, base-layer signal
compatibility, and — for profile 7 — whether the enhancement layer is **FEL**
(full; playback-hostile, forces real-time transcoding) or **MEL** (minimal;
safe). The result feeds the HDR detail page and the future Profile 5→8.1 /
FEL→8.1 remediation jobs.

The heavy lifting is reused from the letterbox re-encode path:
``inspect_source`` (one ffprobe pass) already extracts every DoVi scalar except
FEL/MEL, and ``_extract_rpu_piped`` streams the HEVC RPU into ``dovi_tool`` for
the one extra probe profile 7 needs.

Everything here shells out to blocking binaries, so ``analyze_path`` offloads
the synchronous calls with ``asyncio.to_thread`` — it is only ever awaited from
the durable worker and must never block its event loop.
"""

from __future__ import annotations

import asyncio
import logging
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path

from marquee.core.letterbox_reencode import (
    _extract_rpu_piped,
    inspect_source,
)
from marquee.media import binaries

logger = logging.getLogger(__name__)


@dataclass
class DoviAnalysis:
    has_dovi: bool
    profile: int | None
    level: int | None
    el_present: bool | None
    el_type: str | None  # "FEL" | "MEL" | None
    bl_signal_compatibility_id: int | None
    source_codec: str | None
    rpu_summary: str | None  # raw `dovi_tool info --summary` stdout
    status: str  # "analyzed" | "not_dovi" | "error"
    error_reason: str | None

    def to_state_fields(self) -> dict:
        """Column subset for upserting a ``DoviState`` row."""
        data = asdict(self)
        return {
            "status": data["status"],
            "dovi_profile": data["profile"],
            "dovi_level": data["level"],
            "el_present": data["el_present"],
            "el_type": data["el_type"],
            "bl_signal_compatibility_id": data["bl_signal_compatibility_id"],
            "source_codec": data["source_codec"],
            "rpu_summary_json": data["rpu_summary"],
            "error_reason": data["error_reason"],
        }


def _error(reason: str) -> DoviAnalysis:
    return DoviAnalysis(
        has_dovi=False,
        profile=None,
        level=None,
        el_present=None,
        el_type=None,
        bl_signal_compatibility_id=None,
        source_codec=None,
        rpu_summary=None,
        status="error",
        error_reason=reason,
    )


def _classify_el_type(summary: str | None) -> str | None:
    """Map a ``dovi_tool info --summary`` blob to "FEL" / "MEL" / None.

    Deliberately forgiving: the exact token wording in dovi_tool's summary
    has not been pinned against a real profile-7 sample on this box yet (see
    the module/plan note), so we scan for the unambiguous FEL/MEL markers and
    leave the type ``None`` ("EL present, type unknown") if neither appears.
    """
    if not summary:
        return None
    text = summary.upper()
    if "FEL" in text:
        return "FEL"
    if "MEL" in text:
        return "MEL"
    return None


async def _probe_el_type(path: Path) -> tuple[str | None, str | None]:
    """Extract the RPU and read its EL type. Returns (el_type, raw_summary).

    Best-effort: any failure (no dovi_tool, extraction error, timeout) yields
    ``(None, None)`` so a profile-7 file still records its other fields rather
    than failing the whole analysis.
    """
    if binaries.resolve("dovi_tool") is None:
        return None, None
    tmpdir = await asyncio.to_thread(tempfile.mkdtemp, prefix="marquee-dovi-")
    rpu = Path(tmpdir) / "rpu.bin"
    try:
        await _extract_rpu_piped(path, rpu)
        if not rpu.exists() or rpu.stat().st_size == 0:
            return None, None
        result = await asyncio.to_thread(
            binaries.run,
            "dovi_tool",
            ["info", "-i", str(rpu), "--summary"],
            timeout=120.0,
        )
        summary = result.stdout or result.stderr or None
        return _classify_el_type(summary), summary
    except Exception as exc:  # noqa: BLE001 - EL probe is advisory, never fatal
        logger.warning("dovi EL-type probe failed for %s: %s", path, exc)
        return None, None
    finally:
        await asyncio.to_thread(_cleanup_dir, Path(tmpdir))


def _cleanup_dir(path: Path) -> None:
    import shutil  # noqa: PLC0415

    shutil.rmtree(path, ignore_errors=True)


async def analyze_path(path: Path) -> DoviAnalysis:
    """Inspect *path* and return its full Dolby Vision profile.

    Awaited from the durable worker. ``inspect_source`` (blocking ffprobe) is
    offloaded; the profile-7 RPU probe runs only when an enhancement layer is
    actually present.
    """
    source = await asyncio.to_thread(inspect_source, path)
    if source is None:
        return _error("probe_failed")
    if not source.has_dovi:
        return DoviAnalysis(
            has_dovi=False,
            profile=source.dovi_profile,
            level=source.dovi_level,
            el_present=source.dovi_el_present,
            el_type=None,
            bl_signal_compatibility_id=source.dovi_bl_signal_compatibility_id,
            source_codec=source.codec,
            rpu_summary=None,
            status="not_dovi",
            error_reason=None,
        )

    el_present = source.dovi_el_present
    el_type: str | None = None
    rpu_summary: str | None = None
    # Only profile 7 carries an FEL/MEL distinction; profiles 5/8 are
    # single-layer. Probe the RPU when profile 7 (or ffprobe flagged an EL) and
    # the stream is HEVC (the only thing dovi_tool can extract from here).
    if source.codec == "hevc" and (source.dovi_profile == 7 or source.dovi_el_present):
        el_type, rpu_summary = await _probe_el_type(path)
        if source.dovi_profile == 7:
            el_present = True

    return DoviAnalysis(
        has_dovi=True,
        profile=source.dovi_profile,
        level=source.dovi_level,
        el_present=el_present,
        el_type=el_type,
        bl_signal_compatibility_id=source.dovi_bl_signal_compatibility_id,
        source_codec=source.codec,
        rpu_summary=rpu_summary,
        status="analyzed",
        error_reason=None,
    )


def conversion_eligibility(profile: int | None, el_type: str | None) -> dict:
    """Advisory (display-only) verdict on Profile→8.1 remediation.

    Phase 1 surfaces this on the detail page; Phase 2 wires the actual
    ``dovi_convert`` jobs. No file is touched here.
    """
    if profile is None:
        return {"eligible": False, "target": None, "kind": None, "reason": "Not analyzed yet."}
    if profile == 8:
        return {
            "eligible": False,
            "target": None,
            "kind": None,
            "reason": "Already a single-layer profile 8 stream — broadly compatible.",
        }
    if profile == 5:
        return {
            "eligible": True,
            "target": "8.1",
            "kind": "p5_to_p81",
            "reason": (
                "Profile 5 shows green/purple tints on non-DV hardware. Re-encode the "
                "base layer to BT.2020 and convert the RPU to profile 8.1 for compatibility."
            ),
        }
    if profile == 7:
        if el_type == "MEL":
            return {
                "eligible": True,
                "target": "8.1",
                "kind": "p7_strip_el",
                "reason": (
                    "Profile 7 MEL — the minimal enhancement layer carries no picture data, "
                    "so it can be dropped losslessly and the RPU converted to profile 8.1."
                ),
            }
        if el_type == "FEL":
            return {
                "eligible": "lossy",
                "target": "8.1",
                "kind": "p7_strip_el",
                "reason": (
                    "Profile 7 FEL — the full enhancement layer can force real-time "
                    "transcoding. Stripping it to profile 8.1 fixes playback but discards "
                    "the FEL residual (a lossy step)."
                ),
            }
        return {
            "eligible": "lossy",
            "target": "8.1",
            "kind": "p7_strip_el",
            "reason": (
                "Profile 7 with an undetermined enhancement-layer type. Converting to "
                "profile 8.1 drops the EL; run analysis with dovi_tool to confirm FEL vs MEL."
            ),
        }
    return {
        "eligible": False,
        "target": None,
        "kind": None,
        "reason": f"Profile {profile} has no supported single-layer conversion.",
    }
