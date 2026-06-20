"""Letterbox feature tests — no ffmpeg/mkvtoolnix required.

Pure logic (pre-filter, cropdetect/trim parsing, consensus, sampling, aspect
labels) is exercised directly; the engine is fed canned ``cropdetect`` stderr
and fabricated measurements. Endpoint control paths run against a seeded DB with
binaries monkeypatched, mirroring ``test_run_endpoints.py`` /
``test_poster_service.py``.
"""

from __future__ import annotations

import asyncio
import json
import shutil
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from marquee.config import settings
from marquee.core import letterbox_reencode
from marquee.core.letterbox_reencode import ReencodePlanError, _mkdir_with_retry
from marquee.core.letterbox_service import _resolve_media_file, letterbox_service
from marquee.core.media_files import ensure_media_file_for_movie, resolve_row
from marquee.core.media_jobs import media_job_manager
from marquee.core.path_utils import PathValidationError
from marquee.main import app
from marquee.media import binaries, letterbox_preview
from marquee.media import letterbox_detect as ld
from marquee.media.letterbox_manager import JobState, letterbox_manager
from marquee.media.probe import prefilter_bucket
from marquee.models import (
    Job,
    LetterboxEvent,
    LetterboxReencodeArtifact,
    LetterboxState,
    MediaFile,
    MediaJob,
    MediaJobEvent,
    Movie,
)


@pytest.fixture
async def client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


@pytest.fixture(autouse=True)
def neutralize_media_roots(monkeypatch):
    """Disable media-root enforcement so tmp paths validate (dev mode)."""
    from marquee.config import settings

    monkeypatch.setattr(settings, "RADARR_MEDIA_PATH", None)
    monkeypatch.setattr(settings, "SONARR_MEDIA_PATH", None)
    monkeypatch.setattr(settings, "RADARR_PATH_PREFIX", None)
    monkeypatch.setattr(settings, "MEDIA_ROOTS", [])
    yield


# ---------------------------------------------------------------------------
# Pre-filter (design §4)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "w,h,bucket",
    [
        (1920, 1080, "candidate"),
        (3840, 2160, "candidate"),
        (1920, 800, "skip"),     # native scope
        (1920, 1040, "skip"),    # native 1.85
        (1280, 720, "skip"),     # too small
        (1440, 1080, "skip"),    # 4:3
        (640, 480, "skip"),      # 4:3
        (0, 0, "skip"),          # unknown
    ],
)
def test_prefilter_bucket(w, h, bucket):
    assert prefilter_bucket(w, h).bucket == bucket


def test_cropdetect_limit_uses_hdr_threshold(monkeypatch):
    from marquee.config import settings

    monkeypatch.setattr(settings, "LETTERBOX_CROPDETECT_LIMIT", 24)
    monkeypatch.setattr(settings, "LETTERBOX_CROPDETECT_HDR_LIMIT", 80)
    assert ld.cropdetect_limit_for(None) == 24
    assert ld.cropdetect_limit_for("bt709") == 24
    assert ld.cropdetect_limit_for("smpte2084") == 80
    assert ld.cropdetect_limit_for("arib-std-b67") == 80


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------


def test_parse_cropdetect_takes_last_converged_crop():
    stderr = (
        "[Parsed_cropdetect_0 @ 0x] crop=1920:818:0:131\n"
        "[Parsed_cropdetect_0 @ 0x] crop=1920:800:0:140\n"
    )
    assert ld.parse_cropdetect(stderr) == (1920, 800, 0, 140)


def test_parse_cropdetect_none_when_absent():
    assert ld.parse_cropdetect("no crop here") is None


def test_parse_trim_box():
    assert ld.parse_trim("1920x800+0+140") == (1920, 800, 0, 140)
    assert ld.parse_trim("garbage") is None


def test_cropdetect_measurement_uses_limit_without_keyframe_skip(monkeypatch):
    calls = []

    def fake_run(name, args, timeout=120.0):
        calls.append((name, args, timeout))
        return binaries.CommandResult(
            0,
            "",
            "[Parsed_cropdetect_0 @ 0x] crop=3840:1600:0:280\n",
        )

    monkeypatch.setattr(binaries, "run", fake_run)
    measurement = ld.measure_window_cropdetect(
        "/movie.mkv",
        5,
        2160,
        cropdetect_limit=80,
    )
    assert measurement.ok is True
    assert measurement.top_bar == 280
    assert measurement.bottom_bar == 280
    args = calls[0][1]
    assert "-skip_frame" not in args
    assert "cropdetect=limit=80:round=2:reset=1" in args


# ---------------------------------------------------------------------------
# Consensus → status/confidence (design §12.3)
# ---------------------------------------------------------------------------


def _w(minute, top, bottom, ok=True):
    return ld.WindowMeasurement(minute=minute, ok=ok, top_bar=top, bottom_bar=bottom)


def test_consensus_case_c_consistent_high():
    r = ld.consensus([_w(5, 140, 140), _w(10, 140, 140), _w(15, 141, 140)], width=1920, height=1080)
    assert r.status == "candidate"
    assert r.confidence == "high"
    assert r.recommended_crop_top == 140
    assert r.aspect_label == "2.40:1"


def test_consensus_case_a_variable_unsafe():
    r = ld.consensus([_w(5, 140, 140), _w(10, 0, 0), _w(15, 140, 140)], width=1920, height=1080)
    assert r.status == "variable_unsafe"
    assert r.confidence == "low"
    assert r.recommended_crop_top == 0


def test_consensus_case_b_conservative_min():
    # All letterboxed but varying → recommend the median bar size (majority wins).
    # 2/3 samples within medium_spread of median (100) → medium confidence.
    r = ld.consensus([_w(5, 140, 140), _w(10, 80, 80), _w(15, 100, 100)], width=1920, height=1080)
    assert r.status == "candidate"
    assert r.recommended_crop_top == 100
    assert r.confidence == "medium"


def test_consensus_single_outlier_frame_downgrades_without_variable_ar():
    # 11/12 frames agree at 120; one anomalous frame (e.g. end-credits graphic)
    # measures 120/480 (bar=300). The outlier is too small a cluster (1 sample)
    # to count as a second aspect ratio, so it shouldn't trigger variable_ar —
    # the agreement-based logic should still call this High confidence.
    measurements = [_w(m, 120, 120) for m in range(5, 56, 5)] + [_w(60, 120, 480)]
    r = ld.consensus(measurements, width=3840, height=2160)
    assert r.status == "candidate"
    assert r.confidence == "medium"
    assert r.recommended_crop_top == 120
    assert r.variable_ar is False


def test_consensus_minor_jitter_merges_one_cluster():
    # 11/12 frames at 278; one frame at 278/304 (bar=291, only 13px from the
    # median) — within LETTERBOX_VARIABLE_GAP_PX, so it merges into the same
    # cluster rather than forming a second aspect-ratio group.
    measurements = [_w(m, 278, 278) for m in range(5, 56, 5)] + [_w(40, 278, 304)]
    r = ld.consensus(measurements, width=3840, height=2160)
    assert r.status == "candidate"
    assert r.confidence == "medium"
    assert r.recommended_crop_top == 278
    assert r.variable_ar is False


def test_consensus_variable_ar_two_wide_clusters():
    # 8 frames at 276/276 (2.40:1) and 4 frames at 68/68 (1.90:1) — two
    # well-supported, mutually disagreeing clusters, neither near 16:9.
    # Should be flagged variable_ar and recommend the smaller (safer) crop.
    measurements = [_w(m, 276, 276) for m in (5, 15, 25, 35, 40, 45, 50, 60)] + [
        _w(m, 68, 68) for m in (10, 20, 30, 55)
    ]
    r = ld.consensus(measurements, width=3840, height=2160)
    assert r.status == "candidate"
    assert r.confidence == "variable"
    assert r.recommended_crop_top == 68
    assert r.recommended_crop_bottom == 68
    assert r.variable_ar is True
    assert "1.90:1" in r.variable_ar_note
    assert "2.39:1" in r.variable_ar_note
    assert "68px" in r.variable_ar_note


def test_consensus_case_a_variable_unsafe_has_note():
    r = ld.consensus([_w(5, 140, 140), _w(10, 0, 0), _w(15, 140, 140)], width=1920, height=1080)
    assert r.status == "variable_unsafe"
    assert r.variable_ar is True
    assert "16:9" in r.variable_ar_note


def test_consensus_not_letterboxed():
    r = ld.consensus([_w(5, 1, 1), _w(10, 0, 0), _w(15, 2, 2)], width=1920, height=1080)
    assert r.status == "not_letterboxed"
    assert r.confidence == "none"


def test_consensus_errored_when_no_ok_frames():
    r = ld.consensus([_w(5, 0, 0, ok=False)], width=1920, height=1080)
    assert r.status == "errored"
    assert r.error


def test_consensus_asymmetry_downgrades_when_symmetric_forced(monkeypatch):
    from marquee.config import settings

    monkeypatch.setattr(settings, "LETTERBOX_ASYMMETRIC", False)
    # 140 top vs 120 bottom, consistent → would be High, but asymmetry > tol.
    r = ld.consensus([_w(5, 140, 120), _w(10, 140, 120)], width=1920, height=1080)
    assert r.status == "candidate"
    assert r.confidence == "low"


def test_consensus_asymmetric_mode_honors_uneven(monkeypatch):
    from marquee.config import settings

    monkeypatch.setattr(settings, "LETTERBOX_ASYMMETRIC", True)
    r = ld.consensus([_w(5, 140, 120), _w(10, 140, 120)], width=1920, height=1080)
    assert r.recommended_crop_top == 140
    assert r.recommended_crop_bottom == 120


def test_consensus_rejects_repeated_one_sided_false_crop():
    measurements = [_w(m, 0, 560) for m in range(5, 65, 5)]
    r = ld.consensus(measurements, width=3840, height=2160)
    assert r.status == "not_letterboxed"
    assert r.confidence == "none"
    assert r.recommended_crop_top == 0


def test_consensus_many_asymmetric_samples_forces_low_confidence():
    measurements = [_w(m, 280, 280) for m in range(5, 45, 5)] + [
        _w(45, 0, 560),
        _w(50, 0, 560),
        _w(55, 0, 560),
        _w(60, 0, 560),
    ]
    r = ld.consensus(measurements, width=3840, height=2160)
    assert r.status == "candidate"
    assert r.confidence == "low"
    assert r.recommended_crop_top == 280
    assert r.variable_ar is False


def test_consensus_asymmetric_outliers_do_not_create_variable_ar():
    measurements = [_w(m, 278, 278) for m in range(15, 65, 5)] + [
        _w(5, 388, 512),
        _w(10, 598, 278),
    ]
    r = ld.consensus(measurements, width=3840, height=2160)
    assert r.status == "candidate"
    assert r.confidence == "medium"
    assert r.recommended_crop_top == 278
    assert r.variable_ar is False


# ---------------------------------------------------------------------------
# Sampling + labels
# ---------------------------------------------------------------------------


def test_sample_minutes_movie_and_tv():
    movie = ld.sample_minutes(7200, is_tv=False)
    assert movie[0] == 5 and movie[-1] == 60
    assert ld.sample_minutes(1800, is_tv=True) == [5, 10, 15]


def test_sample_minutes_clamped_to_short_duration():
    # 12-minute file: only samples before minute 12 survive.
    minutes = ld.sample_minutes(12 * 60, is_tv=False)
    assert max(minutes) < 12


def test_aspect_label():
    assert ld.aspect_label(1920, 800) == "2.40:1"
    assert ld.aspect_label(1920, 0) is None


# ---------------------------------------------------------------------------
# Eligibility (design §20)
# ---------------------------------------------------------------------------


def _movie_with_file(tmp_path, name="Movie (2020).mkv"):
    folder = tmp_path / "Movie (2020)"
    folder.mkdir(parents=True, exist_ok=True)
    media = folder / name
    media.write_bytes(b"\x00")
    return Movie(
        title="Movie", year=2020, folder_path=str(folder),
        movie_file_path=name, tmdb_id=111,
    ), media


def _preview_root(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "DATA_DIR", str(tmp_path / "data"))
    return settings.letterbox_preview_path


def _reencode_plan(job_id: str = "job1") -> dict:
    return {
        "job_id": job_id,
        "status": "planned",
        "expires_at": "2026-06-19T00:00:00+00:00",
        "method": "permanent",
        "crop": {"top": 140, "bottom": 140, "output_height": 800},
        "source": {
            "path": "/tmp/Movie (2020)/Movie (2020).mkv",
            "size_bytes": 1,
            "codec": "hevc",
            "width": 1920,
            "height": 1080,
            "pix_fmt": "yuv420p",
            "color_transfer": None,
            "color_primaries": None,
            "color_space": None,
            "has_hdr": False,
            "has_dovi": False,
            "dovi_profile": None,
        },
        "encoder": {
            "codec": "hevc",
            "encoder": "hevc_nvenc",
            "family": "nvidia",
            "quality": 16,
            "preset": "p7",
            "available_encoders": ["hevc_nvenc"],
            "used_cpu_fallback": False,
        },
        "hdr": {"status": "sdr"},
        "dovi": {
            "status": "not_present",
            "supported": False,
            "reason": None,
            "profile": None,
            "level": None,
            "el_present": None,
        },
        "storage": {
            "estimated_temp_bytes": 1,
            "free_bytes": 2,
            "original_preserved_by_default": True,
            "replace_original_after_review": True,
        },
        "warnings": [],
        "confirmation_required": False,
        "input_signature": "sig",
    }


def test_eligibility_mkv_ok_without_mkvmerge(tmp_path, monkeypatch):
    # No mkvmerge → container/track check skipped; a writable .mkv is eligible.
    monkeypatch.setattr(binaries, "resolve", lambda name: None)
    movie, _ = _movie_with_file(tmp_path)
    elig = letterbox_service.check_eligibility(movie)
    assert elig.eligible is True
    assert elig.reason is None


def test_eligibility_rejects_mp4(tmp_path, monkeypatch):
    monkeypatch.setattr(binaries, "resolve", lambda name: None)
    movie, _ = _movie_with_file(tmp_path, name="Movie (2020).mp4")
    elig = letterbox_service.check_eligibility(movie)
    assert elig.eligible is False
    assert elig.reason == "not_mkv"


def test_eligibility_missing_file(tmp_path, monkeypatch):
    monkeypatch.setattr(binaries, "resolve", lambda name: None)
    movie = Movie(
        title="Gone", year=2020, folder_path=str(tmp_path),
        movie_file_path="nope.mkv", tmdb_id=222,
    )
    elig = letterbox_service.check_eligibility(movie)
    assert elig.eligible is False
    assert elig.reason == "missing"


def test_resolve_media_file_rejects_escape(tmp_path):
    movie = Movie(
        title="Evil", year=2020, folder_path=str(tmp_path),
        movie_file_path="../../etc/passwd", tmdb_id=333,
    )
    with pytest.raises(PathValidationError):
        _resolve_media_file(movie)


# ---------------------------------------------------------------------------
# _mkdir_with_retry — cross-account ownership + transient mount errors
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_mkdir_with_retry_succeeds_after_transient_permission_error(tmp_path, monkeypatch):
    monkeypatch.setattr(letterbox_reencode.asyncio, "sleep", lambda _delay: _noop())
    real_mkdir = Path.mkdir
    target = tmp_path / "candidates" / "radarr_movie-file_2010"
    target.parent.mkdir()  # pre-exists, so the real mkdir call is a single non-recursive op
    calls = {"count": 0}

    def flaky_mkdir(self, *args, **kwargs):
        if self == target:
            calls["count"] += 1
            if calls["count"] < 3:
                raise PermissionError("transient mergerfs branch error")
        return real_mkdir(self, *args, **kwargs)

    monkeypatch.setattr(Path, "mkdir", flaky_mkdir)
    await _mkdir_with_retry(target, boundary=tmp_path)
    assert calls["count"] == 3
    assert target.is_dir()


@pytest.mark.asyncio
async def test_mkdir_with_retry_raises_reencode_plan_error_when_exhausted(tmp_path, monkeypatch):
    monkeypatch.setattr(letterbox_reencode.asyncio, "sleep", lambda _delay: _noop())

    def always_denied(self, *args, **kwargs):
        raise PermissionError("permission denied")

    monkeypatch.setattr(Path, "mkdir", always_denied)
    target = tmp_path / "candidates" / "radarr_movie-file_2010"
    with pytest.raises(ReencodePlanError) as exc_info:
        await _mkdir_with_retry(target, boundary=tmp_path)
    assert exc_info.value.code == "candidate_dir_unavailable"


@pytest.mark.asyncio
async def test_mkdir_with_retry_sets_setgid_group_write_up_to_boundary(tmp_path):
    boundary = tmp_path / "movies" / ".marquee"
    target = boundary / "letterbox" / "candidates" / "radarr_movie-file_2010" / "job1"
    await _mkdir_with_retry(target, boundary=boundary)
    for directory in (target, target.parent, target.parent.parent, boundary):
        assert directory.stat().st_mode & 0o7777 == 0o2775
    # Nothing above the boundary should have been touched.
    assert boundary.parent.stat().st_mode & 0o7777 != 0o2775


@pytest.mark.asyncio
async def test_mkdir_with_retry_ignores_chmod_denied_on_foreign_ancestor(tmp_path, monkeypatch):
    boundary = tmp_path / "movies" / ".marquee"
    foreign = boundary / "letterbox" / "candidates"  # simulates a dir owned by the other account
    target = foreign / "radarr_movie-file_2010" / "job1"
    target.mkdir(parents=True)
    shutil.rmtree(target)  # leave `foreign` in place, owned by "someone else"

    real_chmod = Path.chmod

    def chmod_denying_foreign(self, *args, **kwargs):
        if self == foreign:
            raise PermissionError("not the owner")
        return real_chmod(self, *args, **kwargs)

    monkeypatch.setattr(Path, "chmod", chmod_denying_foreign)
    await _mkdir_with_retry(target, boundary=boundary)

    assert target.is_dir()
    assert target.stat().st_mode & 0o7777 == 0o2775


async def _noop():
    return None


# ---------------------------------------------------------------------------
# Pipeline resilience: non-blocking probes + DB lock avoidance
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_resolve_row_does_not_write_on_read_path(db, tmp_path):
    # resolve_row is on the hot GET path; it must not dirty the session (which
    # would trigger an autoflush UPDATE that contends with the encode worker).
    movie, media = _movie_with_file(tmp_path)
    db.add(movie)
    await db.flush()
    row = MediaFile(
        source="radarr", source_key="radarr:movie:1", movie_id=movie.id,
        path=str(media), relative_path=media.name, container="mkv",
        is_active=True, last_resolved_path="/stale/path",
    )
    db.add(row)
    await db.commit()

    await resolve_row(db, row)

    assert not db.dirty
    assert row.last_resolved_path == "/stale/path"


@pytest.mark.asyncio
async def test_emit_persist_false_skips_event_row(db):
    # Live progress ticks (persist=False) publish to SSE subscribers but write
    # no MediaJobEvent row — only throttled/transition events persist.
    job = MediaJob(job_id="emit-job", operation="letterbox_reencode", media_file_id=None, status="running")
    db.add(job)
    await db.commit()

    await media_job_manager.emit(db, "emit-job", "encode", "running", progress={"percent": 5}, persist=False)
    rows = (await db.execute(select(MediaJobEvent).where(MediaJobEvent.job_id == "emit-job"))).scalars().all()
    assert rows == []

    await media_job_manager.emit(db, "emit-job", "encode", "running", progress={"percent": 6}, persist=True)
    rows = (await db.execute(select(MediaJobEvent).where(MediaJobEvent.job_id == "emit-job"))).scalars().all()
    assert len(rows) == 1


@pytest.mark.asyncio
async def test_movie_detail_does_not_probe(client, db, tmp_path, monkeypatch):
    # The detail fetch must be subprocess-free so it returns instantly even
    # while an encode saturates disk I/O — DoVi presence comes from the cached
    # has_dv column, not a live ffprobe.
    movie, _ = _movie_with_file(tmp_path)
    db.add(movie)
    await db.commit()
    await db.refresh(movie)
    db.add(LetterboxState(movie_id=movie.id, status="candidate", confidence="high"))
    await db.commit()

    probed = False

    def boom(_path):
        nonlocal probed
        probed = True
        raise binaries.BinaryError("inspect_source must not run on the detail path")

    monkeypatch.setattr(letterbox_reencode, "inspect_source", boom)

    resp = await client.get(f"/api/letterbox/movies/{movie.id}")
    assert resp.status_code == 200
    assert probed is False
    assert resp.json()["dolby_vision"]["present"] is False


# ---------------------------------------------------------------------------
# detect_and_store state mapping (engine mocked — no ffmpeg)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_detect_and_store_persists_and_auto_reviews(db, monkeypatch):
    movie = Movie(title="Var", year=2014, folder_path="/m/Var", tmdb_id=157336)
    db.add(movie)
    await db.commit()
    await db.refresh(movie)

    monkeypatch.setattr(
        letterbox_manager, "detect_movie_blocking",
        lambda m: {
            "status": "variable_unsafe", "confidence": "low", "eligible": True,
            "ineligible_reason": None, "source_width": 1920, "source_height": 1080,
            "recommended_crop_top": 0, "recommended_crop_bottom": 0,
            "aspect_label": None, "detect_method": "cropdetect",
            "samples_json": "[]", "error": None, "_container": "mkv",
        },
    )
    state = await letterbox_manager.detect_and_store(db, movie)
    assert state.status == "variable_unsafe"
    assert state.reviewed is True  # auto-reviewed → leaves the candidate queue


@pytest.mark.asyncio
async def test_detect_and_store_v1_records_v1_source(db, monkeypatch):
    movie = Movie(title="Scope", year=2015, folder_path="/m/Scope", tmdb_id=9001)
    db.add(movie)
    await db.commit()
    await db.refresh(movie)

    monkeypatch.setattr(
        letterbox_manager,
        "detect_movie_blocking_v1",
        lambda m: {
            "status": "candidate", "confidence": "high", "eligible": True,
            "ineligible_reason": None, "source_width": 3840, "source_height": 2160,
            "recommended_crop_top": 280, "recommended_crop_bottom": 280,
            "aspect_label": "2.40:1", "detect_method": "v1_script",
            "samples_json": "{}", "error": None, "_container": "mkv",
        },
    )

    state = await letterbox_manager.detect_and_store(db, movie, detector="v1")
    assert state.status == "candidate"
    event = (await db.execute(select(LetterboxEvent).where(LetterboxEvent.movie_id == movie.id))).scalar_one()
    assert event.source == "detect_v1"
    detail = json.loads(event.detail)
    assert detail["detector"] == "v1"


@pytest.mark.asyncio
async def test_detect_and_store_schedules_warm_off_request_path(db, monkeypatch):
    """Warming ~44 frames inline blocked the detect response (502s); it must be
    scheduled as a background task, not awaited before the function returns."""
    movie = Movie(title="Warm", year=2022, folder_path="/m/Warm", tmdb_id=4242)
    db.add(movie)
    await db.commit()
    await db.refresh(movie)

    monkeypatch.setattr(
        letterbox_manager, "detect_movie_blocking",
        lambda m: {
            "status": "candidate", "confidence": "high", "eligible": True,
            "ineligible_reason": None, "source_width": 3840, "source_height": 2160,
            "recommended_crop_top": 280, "recommended_crop_bottom": 280,
            "aspect_label": "2.40:1", "detect_method": "cropdetect",
            "samples_json": json.dumps([{"minute": 5, "ok": True}]),
            "error": None, "_container": "mkv", "_source_path": "/m.mkv",
        },
    )
    warmed = asyncio.Event()
    monkeypatch.setattr(
        letterbox_preview, "warm_movie_previews",
        lambda *a, **k: warmed.set() or [],
    )

    state = await letterbox_manager.detect_and_store(db, movie)
    assert state.status == "candidate"
    # Returned without awaiting the warm — the background task hasn't run yet.
    assert warmed.is_set() is False
    # ...but it is scheduled and runs once the loop yields.
    await asyncio.wait_for(warmed.wait(), timeout=2.0)


def test_warm_movie_previews_renders_every_ok_sample(tmp_path, monkeypatch):
    preview_root = _preview_root(tmp_path, monkeypatch)
    stale = preview_root / "7_before_99_legacy.webp"
    stale.parent.mkdir(parents=True, exist_ok=True)
    stale.write_bytes(b"stale")

    monkeypatch.setattr(binaries, "resolve", lambda name: "/usr/bin/ffmpeg")
    calls = []

    def fake_generate_preview(
        source,
        *,
        movie_id,
        minute,
        mode,
        crop_top,
        crop_bottom,
        height=None,
        candidate_minutes=None,
        exact=False,
        force=False,
    ):
        calls.append(
            {
                "source": source,
                "movie_id": movie_id,
                "minute": minute,
                "mode": mode,
                "crop_top": crop_top,
                "crop_bottom": crop_bottom,
                "height": height,
                "candidate_minutes": list(candidate_minutes or []),
                "exact": exact,
                "force": force,
            }
        )
        out = letterbox_preview.preview_path(movie_id, mode, minute, exact=exact)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(f"{minute}:{mode}".encode())
        return out

    monkeypatch.setattr(letterbox_preview, "generate_preview", fake_generate_preview)
    samples = [
        {"minute": 5, "ok": True},
        {"minute": 10, "ok": True},
        {"minute": 15, "ok": False},
    ]

    outputs = letterbox_preview.warm_movie_previews(
        "/movie.mkv",
        movie_id=7,
        samples=samples,
        crop_top=140,
        crop_bottom=140,
        height=2160,
    )

    assert stale.exists() is False
    assert len(calls) == 8
    assert {call["minute"] for call in calls} == {5, 10}
    assert {call["mode"] for call in calls} == {"before", "after"}
    assert all(call["candidate_minutes"] == [5, 10] for call in calls)
    assert {call["exact"] for call in calls} == {True, False}
    assert all(call["force"] is True for call in calls)
    assert len(outputs) == 8
    assert all(path.exists() for path in outputs)


def test_preview_cache_key_separates_exact_and_bright(tmp_path, monkeypatch):
    _preview_root(tmp_path, monkeypatch)
    bright = letterbox_preview.preview_path(7, "before", 10)
    exact = letterbox_preview.preview_path(7, "before", 10, exact=True)

    assert bright != exact
    assert "_bright_" in bright.name
    assert "_exact_" in exact.name


def test_exact_preview_uses_requested_minute_without_substitution(tmp_path, monkeypatch):
    """Regression: a too-dark minute=10 frame must not silently render minute=5's
    content into the minute=10 cache slot — that's what made Lincoln/Borderlands
    sample clicks keep showing the wrong frame."""
    _preview_root(tmp_path, monkeypatch)
    monkeypatch.setattr(binaries, "resolve", lambda name: "/usr/bin/ffmpeg")
    monkeypatch.setattr(letterbox_preview, "_pick_bright_minute", lambda *a, **k: 5)
    timestamps = []

    def fake_run(name, args, timeout=None):
        timestamps.append(args[args.index("-ss") + 1])
        out = Path(args[-1])
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(b"webp")
        return type("Result", (), {"ok": True, "stderr": ""})()

    monkeypatch.setattr(binaries, "run", fake_run)

    out = letterbox_preview.generate_preview(
        "/movie.mkv",
        movie_id=7,
        minute=10,
        mode="before",
        crop_top=140,
        crop_bottom=140,
        height=1080,
        candidate_minutes=[5, 10],
        exact=True,
    )

    assert out is not None
    assert timestamps == ["00:10:00"]


def test_measure_luma_returns_none_on_binary_error(monkeypatch):
    """A slow/timed-out probe must degrade to None, not raise — otherwise it
    bubbles out of generate_preview and 500s the whole preview request."""
    monkeypatch.setattr(binaries, "resolve", lambda name: "/usr/bin/ffmpeg")

    def boom(name, args, timeout=None):
        raise binaries.BinaryError(f"{name} timed out after {timeout}s")

    monkeypatch.setattr(binaries, "run", boom)
    assert letterbox_preview._measure_luma("/movie.mkv", 5) is None


def test_pick_bright_minute_falls_back_when_probes_fail(monkeypatch):
    """When every luma probe fails, fall back to the requested minute."""
    letterbox_preview._bright_minute_cache.clear()
    monkeypatch.setattr(letterbox_preview, "_measure_luma", lambda *a, **k: None)
    chosen = letterbox_preview._pick_bright_minute("/m.mkv", 7, [3, 11, 19], movie_id=42)
    assert chosen == 7


def test_pick_bright_minute_caps_probe_count(monkeypatch):
    """At most _MAX_BRIGHT_PROBES decodes run for one pick, no matter how many
    candidate minutes are offered (each decode is expensive on 4K)."""
    letterbox_preview._bright_minute_cache.clear()
    probed: list[int] = []

    def fake_measure(source, minute):
        probed.append(minute)
        return 0.0  # always too dark, forcing the candidate sweep

    monkeypatch.setattr(letterbox_preview, "_measure_luma", fake_measure)
    letterbox_preview._pick_bright_minute(
        "/m.mkv", 5, [10, 15, 20, 25, 30, 35, 40], movie_id=1
    )
    assert len(probed) <= letterbox_preview._MAX_BRIGHT_PROBES


def test_pick_bright_minute_caches_decision(monkeypatch):
    """The before/after pair (same movie + minute) must not each re-probe."""
    letterbox_preview._bright_minute_cache.clear()
    calls = {"n": 0}

    def fake_measure(source, minute):
        calls["n"] += 1
        return 200.0  # bright enough, keeps the requested minute

    monkeypatch.setattr(letterbox_preview, "_measure_luma", fake_measure)
    first = letterbox_preview._pick_bright_minute("/m.mkv", 5, [10], movie_id=9)
    second = letterbox_preview._pick_bright_minute("/m.mkv", 5, [10], movie_id=9)
    assert first == second == 5
    assert calls["n"] == 1  # second call served from cache


def test_purge_clears_bright_minute_cache(tmp_path, monkeypatch):
    _preview_root(tmp_path, monkeypatch)
    letterbox_preview._bright_minute_cache[(3, 5)] = 5
    letterbox_preview._bright_minute_cache[(4, 5)] = 5
    letterbox_preview.purge_movie_previews(3)
    assert (3, 5) not in letterbox_preview._bright_minute_cache
    assert (4, 5) in letterbox_preview._bright_minute_cache  # other movie untouched


@pytest.mark.asyncio
async def test_ffmpeg_gate_serializes_to_limit(monkeypatch):
    """The shared gate caps concurrent request-path ffmpeg work at the
    configured limit so probes/previews can't dogpile the disk."""
    from marquee.media import concurrency

    monkeypatch.setattr(settings, "LETTERBOX_FFMPEG_CONCURRENCY", 2)
    monkeypatch.setattr(concurrency, "_semaphore", None)  # rebuild at new limit

    active = 0
    peak = 0

    def work():
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        time.sleep(0.02)
        active -= 1
        return True

    await asyncio.gather(*(concurrency.gated(work) for _ in range(8)))
    assert peak <= 2


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_status_reports_binaries(client, db):
    resp = await client.get("/api/letterbox/status")
    assert resp.status_code == 200
    body = resp.json()
    assert "binaries" in body and "ffmpeg" in body["binaries"]
    assert "plex-desktop" in body["honored_by"]


@pytest.mark.asyncio
async def test_status_counts_full_frame_present_prefilter_skips(client, db):
    native = Movie(
        title="Native Wide",
        year=2000,
        folder_path="/m/native",
        movie_file_path="Native.mkv",
        tmdb_id=71,
    )
    unavailable = Movie(title="Unavailable", year=2001, folder_path="/m/missing", tmdb_id=72)
    db.add_all([native, unavailable])
    await db.commit()
    await db.refresh(native)
    await db.refresh(unavailable)
    db.add_all([
        LetterboxState(
            movie_id=native.id,
            status="prefilter_skipped",
            prefilter_reason="native_wide",
        ),
        LetterboxState(
            movie_id=unavailable.id,
            status="prefilter_skipped",
            prefilter_reason="missing_movie_file_path",
        ),
    ])
    await db.commit()

    resp = await client.get("/api/letterbox/status")
    assert resp.status_code == 200
    assert resp.json()["full_frame"] == 1


@pytest.mark.asyncio
async def test_status_ignores_terminal_letterbox_batches(client, db):
    db.add_all([
        Job(
            id="batch-interrupted",
            type="letterbox_detect_batch",
            status="interrupted",
            finished_at=datetime.now(UTC),
        ),
        Job(
            id="batch-cancelled",
            type="letterbox_detect_batch",
            status="cancelled",
            finished_at=datetime.now(UTC),
        ),
    ])
    await db.commit()

    resp = await client.get("/api/letterbox/status")

    assert resp.status_code == 200
    assert resp.json()["batch_active"] is None


@pytest.mark.asyncio
async def test_cancel_job_route_cascades_letterbox_batch(client, db):
    parent = Job(id="batch-active", type="letterbox_detect_batch", status="waiting_external")
    db.add(parent)
    await db.flush()
    db.add_all([
        Job(id="child-done", type="letterbox_detect", parent_id=parent.id, status="succeeded", result={"status": "candidate"}),
        Job(id="child-queued", type="letterbox_detect", parent_id=parent.id, status="queued"),
    ])
    await db.commit()

    resp = await client.post(f"/api/jobs/{parent.id}/cancel")

    assert resp.status_code == 202
    body = resp.json()
    assert body["job_id"] == parent.id
    assert body["status"] == "cancelled"
    cancelled_child = await db.get(Job, "child-queued")
    assert cancelled_child is not None and cancelled_child.status == "cancelled"


@pytest.mark.asyncio
async def test_candidates_filter_and_paginate(client, db):
    m1 = Movie(title="Scope", year=2000, folder_path="/m/s", movie_file_path="s.mkv", tmdb_id=1)
    m2 = Movie(title="Flat", year=2001, folder_path="/m/f", movie_file_path="f.mkv", tmdb_id=2)
    db.add_all([m1, m2])
    await db.commit()
    await db.refresh(m1)
    await db.refresh(m2)
    db.add_all([
        LetterboxState(movie_id=m1.id, status="candidate", confidence="high",
                       recommended_crop_top=140, recommended_crop_bottom=140),
        LetterboxState(movie_id=m2.id, status="tagged", confidence="high",
                       applied_crop_top=140, applied_crop_bottom=140),
    ])
    await db.commit()

    all_resp = await client.get("/api/letterbox/candidates")
    assert all_resp.json()["total"] == 2

    cand = await client.get("/api/letterbox/candidates?status=candidate")
    items = cand.json()["items"]
    assert len(items) == 1
    assert items[0]["title"] == "Scope"
    assert items[0]["recommended_crop_top"] == 140


@pytest.mark.asyncio
async def test_cleared_candidates_query_excludes_prefilter_skipped(client, db):
    cleared = Movie(
        title="Cleared",
        year=2000,
        folder_path="/m/cleared",
        movie_file_path="Cleared.mkv",
        tmdb_id=81,
    )
    full_frame = Movie(
        title="Full Frame",
        year=2001,
        folder_path="/m/full",
        movie_file_path="Full.mkv",
        tmdb_id=82,
    )
    db.add_all([cleared, full_frame])
    await db.commit()
    await db.refresh(cleared)
    await db.refresh(full_frame)
    db.add_all([
        LetterboxState(
            movie_id=cleared.id,
            status="not_letterboxed",
            confidence="none",
            reviewed=True,
        ),
        LetterboxState(
            movie_id=full_frame.id,
            status="prefilter_skipped",
            prefilter_reason="native_wide",
        ),
    ])
    await db.commit()

    resp = await client.get("/api/letterbox/candidates?status=not_letterboxed&sort=recent")
    assert resp.status_code == 200
    assert [item["title"] for item in resp.json()["items"]] == ["Cleared"]


@pytest.mark.asyncio
async def test_candidates_confidence_sort_places_variable_after_medium(client, db):
    movies = [
        Movie(title="High", year=2000, folder_path="/m/h", movie_file_path="h.mkv", tmdb_id=21),
        Movie(title="Medium", year=2000, folder_path="/m/m", movie_file_path="m.mkv", tmdb_id=22),
        Movie(title="Variable", year=2000, folder_path="/m/v", movie_file_path="v.mkv", tmdb_id=23),
        Movie(title="Low", year=2000, folder_path="/m/l", movie_file_path="l.mkv", tmdb_id=24),
    ]
    db.add_all(movies)
    await db.commit()
    for movie in movies:
        await db.refresh(movie)
    db.add_all([
        LetterboxState(movie_id=movies[0].id, status="candidate", confidence="high"),
        LetterboxState(movie_id=movies[1].id, status="candidate", confidence="medium"),
        LetterboxState(
            movie_id=movies[2].id,
            status="candidate",
            confidence="variable",
            variable_ar=True,
        ),
        LetterboxState(movie_id=movies[3].id, status="candidate", confidence="low"),
    ])
    await db.commit()

    resp = await client.get("/api/letterbox/candidates?status=candidate&sort=confidence")
    assert resp.status_code == 200
    assert [item["title"] for item in resp.json()["items"]] == [
        "High",
        "Medium",
        "Variable",
        "Low",
    ]


@pytest.mark.asyncio
async def test_candidates_reviewed_filter_and_recent_sort(client, db):
    old = Movie(
        title="Old Preview", year=2000, folder_path="/m/old", movie_file_path="old.mkv", tmdb_id=31
    )
    new = Movie(
        title="New Preview", year=2001, folder_path="/m/new", movie_file_path="new.mkv", tmdb_id=32
    )
    done = Movie(
        title="Reviewed", year=2002, folder_path="/m/done", movie_file_path="done.mkv", tmdb_id=33
    )
    db.add_all([old, new, done])
    await db.commit()
    for movie in [old, new, done]:
        await db.refresh(movie)
    db.add_all([
        LetterboxState(
            movie_id=old.id,
            status="tagged",
            confidence="high",
            reviewed=False,
            updated_at=datetime(2024, 1, 1, tzinfo=UTC),
        ),
        LetterboxState(
            movie_id=new.id,
            status="tagged",
            confidence="high",
            reviewed=False,
            updated_at=datetime(2024, 1, 2, tzinfo=UTC),
        ),
        LetterboxState(
            movie_id=done.id,
            status="tagged",
            confidence="high",
            reviewed=True,
            updated_at=datetime(2024, 1, 3, tzinfo=UTC),
        ),
    ])
    await db.commit()

    resp = await client.get(
        "/api/letterbox/candidates?status=tagged&reviewed=false&sort=recent"
    )
    assert resp.status_code == 200
    assert [item["title"] for item in resp.json()["items"]] == ["New Preview", "Old Preview"]


@pytest.mark.asyncio
async def test_find_candidate_movies_prefilters_radarr_resolutions(client, db):
    candidate = Movie(
        title="Candidate",
        year=2000,
        folder_path="/m/c",
        movie_file_path="Candidate.mkv",
        tmdb_id=11,
        radarr_id=101,
        video_width=1920,
        video_height=1080,
        container="mkv",
    )
    native_scope = Movie(
        title="Native Scope",
        year=2001,
        folder_path="/m/s",
        movie_file_path="Scope.mkv",
        tmdb_id=12,
        video_width=1920,
        video_height=800,
        container="mkv",
    )
    unknown = Movie(
        title="Unknown",
        year=2002,
        folder_path="/m/u",
        movie_file_path="Unknown.mkv",
        tmdb_id=13,
    )
    low_res = Movie(
        title="Low Res",
        year=2003,
        folder_path="/m/l",
        movie_file_path="Low.mkv",
        tmdb_id=14,
        video_width=1280,
        video_height=720,
        container="mkv",
    )
    pathless_candidate = Movie(
        title="Pathless",
        year=2004,
        folder_path="/m/p",
        tmdb_id=15,
        video_width=3840,
        video_height=2160,
        container="mkv",
    )
    analyzed_false_positive = Movie(
        title="Analyzed False Positive",
        year=2005,
        folder_path="/m/a",
        movie_file_path="Analyzed.mkv",
        tmdb_id=16,
        video_width=3840,
        video_height=2160,
        container="mkv",
    )
    db.add_all([
        candidate,
        native_scope,
        unknown,
        low_res,
        pathless_candidate,
        analyzed_false_positive,
    ])
    await db.commit()
    for movie in [
        candidate,
        native_scope,
        unknown,
        low_res,
        pathless_candidate,
        analyzed_false_positive,
    ]:
        await db.refresh(movie)

    db.add(
        LetterboxState(
            movie_id=analyzed_false_positive.id,
            status="not_letterboxed",
            confidence="none",
            source_width=3840,
            source_height=2160,
            recommended_crop_top=0,
            recommended_crop_bottom=0,
            reviewed=True,
            last_detected_at=datetime.now(UTC),
        )
    )
    await db.commit()

    resp = await client.get("/api/letterbox/movies/find-candidates")
    assert resp.status_code == 200
    body = resp.json()
    assert body["total_movies"] == 6
    assert body["returned"] == 2
    assert body["counts"] == {
        "candidate": 1,
        "unknown_resolution": 1,
        "skipped_by_resolution": 2,
        "missing_file_path": 1,
        "already_analyzed": 1,
        "will_detect_in_all_candidates_batch": 2,
    }
    assert set(body["movie_ids"]) == {candidate.id, unknown.id}
    assert body["candidate_movie_ids"] == [candidate.id]
    assert body["unknown_resolution_movie_ids"] == [unknown.id]
    assert set(body["detectable_movie_ids"]) == {candidate.id, unknown.id}

    items = {item["movie_id"]: item for item in body["items"]}
    assert items[candidate.id]["resolution"] == "1920x1080"
    assert items[candidate.id]["prefilter"]["reason"] == "sixteen_nine_container"
    assert items[candidate.id]["letterbox_state"]["status"] == "prefilter_candidate"
    assert items[candidate.id]["letterbox_state"]["prefilter_reason"] == "sixteen_nine_container"
    assert items[unknown.id]["needs_probe"] is True
    assert pathless_candidate.id not in items
    assert analyzed_false_positive.id not in items

    skipped_resp = await client.get(
        "/api/letterbox/movies/find-candidates?include_skipped=true"
    )
    skipped_items = {item["movie_id"]: item for item in skipped_resp.json()["items"]}
    assert len(skipped_items) == 4
    assert pathless_candidate.id not in skipped_items
    assert analyzed_false_positive.id not in skipped_items
    assert skipped_items[native_scope.id]["prefilter"]["reason"] == "native_wide"
    assert skipped_items[low_res.id]["prefilter"]["reason"] == "low_resolution"

    analyzed_resp = await client.get(
        "/api/letterbox/movies/find-candidates?include_analyzed=true"
    )
    analyzed_body = analyzed_resp.json()
    analyzed_items = {item["movie_id"]: item for item in analyzed_body["items"]}
    assert set(analyzed_body["movie_ids"]) == {
        candidate.id,
        unknown.id,
        analyzed_false_positive.id,
    }
    assert analyzed_false_positive.id in analyzed_items
    assert analyzed_items[analyzed_false_positive.id]["already_analyzed"] is True
    assert (
        analyzed_items[analyzed_false_positive.id]["letterbox_state"]["status"]
        == "not_letterboxed"
    )


@pytest.mark.asyncio
async def test_find_candidate_movies_is_idempotent_after_resets(client, db):
    detected = Movie(
        title="Detected Reset",
        year=2000,
        folder_path="/m/d",
        movie_file_path="Detected.mkv",
        tmdb_id=41,
        video_width=3840,
        video_height=2160,
    )
    not_letterboxed = Movie(
        title="Not Letterboxed Reset",
        year=2001,
        folder_path="/m/n",
        movie_file_path="NotLetterboxed.mkv",
        tmdb_id=42,
        video_width=1920,
        video_height=1080,
    )
    db.add_all([detected, not_letterboxed])
    await db.commit()
    await db.refresh(detected)
    await db.refresh(not_letterboxed)
    db.add_all([
        LetterboxState(
            movie_id=detected.id,
            status="candidate",
            confidence="medium",
            recommended_crop_top=140,
            recommended_crop_bottom=140,
            prefilter_bucket="candidate",
            prefilter_reason="sixteen_nine_container",
        ),
        LetterboxState(
            movie_id=not_letterboxed.id,
            status="not_letterboxed",
            confidence="none",
            reviewed=True,
            recommended_crop_top=0,
            recommended_crop_bottom=0,
            prefilter_bucket="candidate",
            prefilter_reason="sixteen_nine_container",
        ),
    ])
    await db.commit()

    assert (await client.post("/api/letterbox/dev/reset-detected")).status_code == 200
    assert (await client.post("/api/letterbox/dev/reset-not-letterboxed")).status_code == 200

    first = (await client.get("/api/letterbox/movies/find-candidates")).json()
    second = (await client.get("/api/letterbox/movies/find-candidates")).json()
    assert second["counts"] == first["counts"]
    assert second["movie_ids"] == first["movie_ids"]
    assert second["candidate_movie_ids"] == first["candidate_movie_ids"]
    assert second["detectable_movie_ids"] == first["detectable_movie_ids"]


@pytest.mark.asyncio
async def test_movie_detail_404_without_state(client, db):
    movie = Movie(title="NoState", year=2000, folder_path="/m/n", tmdb_id=9)
    db.add(movie)
    await db.commit()
    await db.refresh(movie)
    resp = await client.get(f"/api/letterbox/movies/{movie.id}")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_movie_detail_without_reencode_snapshot(client, db, tmp_path):
    movie, _ = _movie_with_file(tmp_path)
    db.add(movie)
    await db.commit()
    await db.refresh(movie)
    db.add(LetterboxState(movie_id=movie.id, status="candidate", confidence="high"))
    await db.commit()

    resp = await client.get(f"/api/letterbox/movies/{movie.id}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "candidate"
    assert body.get("reencode") is None


@pytest.mark.asyncio
async def test_movie_detail_includes_active_reencode_snapshot(client, db, tmp_path):
    movie, _ = _movie_with_file(tmp_path)
    db.add(movie)
    await db.commit()
    await db.refresh(movie)
    media_file = await ensure_media_file_for_movie(db, movie)
    assert media_file is not None
    db.add(LetterboxState(movie_id=movie.id, status="candidate", confidence="high"))
    db.add(
        MediaJob(
            job_id="job1",
            operation="letterbox_reencode",
            media_file_id=media_file.id,
            status="running",
            stage="encode",
            progress_done=25,
            progress_total=100,
            plan_json=json.dumps(_reencode_plan("job1")),
        )
    )
    await db.commit()

    resp = await client.get(f"/api/letterbox/movies/{movie.id}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["reencode"]["job"]["job_id"] == "job1"
    assert body["reencode"]["job"]["status"] == "running"
    assert body["reencode"]["job"]["stage"] == "encode"
    assert body["reencode"]["job"]["progress_done"] == 25
    assert body["reencode"]["job"]["progress_total"] == 100
    assert body["reencode"]["job"]["plan"]["encoder"]["encoder"] == "hevc_nvenc"
    assert body["reencode"]["artifact"] is None


@pytest.mark.asyncio
async def test_movie_detail_uses_latest_active_reencode_job(client, db, tmp_path):
    movie, _ = _movie_with_file(tmp_path)
    db.add(movie)
    await db.commit()
    await db.refresh(movie)
    media_file = await ensure_media_file_for_movie(db, movie)
    assert media_file is not None
    db.add(LetterboxState(movie_id=movie.id, status="candidate", confidence="high"))
    db.add(
        MediaJob(
            job_id="job-old",
            operation="letterbox_reencode",
            media_file_id=media_file.id,
            status="planned",
            stage="plan",
            progress_done=0,
            progress_total=100,
            plan_json=json.dumps(_reencode_plan("job-old")),
            created_at=datetime.now(UTC) - timedelta(minutes=5),
        )
    )
    db.add(
        MediaJob(
            job_id="job-new",
            operation="letterbox_reencode",
            media_file_id=media_file.id,
            status="running",
            stage="encode",
            progress_done=50,
            progress_total=100,
            plan_json=json.dumps(_reencode_plan("job-new")),
            created_at=datetime.now(UTC),
        )
    )
    await db.commit()

    resp = await client.get(f"/api/letterbox/movies/{movie.id}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["reencode"]["job"]["job_id"] == "job-new"
    assert body["reencode"]["job"]["status"] == "running"
    assert body["reencode"]["job"]["progress_done"] == 50
    assert body["reencode"]["job"]["plan"]["operation"] == "letterbox_reencode"


@pytest.mark.asyncio
async def test_movie_detail_includes_finished_reencode_artifact(client, db, tmp_path):
    movie, media = _movie_with_file(tmp_path)
    db.add(movie)
    await db.commit()
    await db.refresh(movie)
    media_file = await ensure_media_file_for_movie(db, movie)
    assert media_file is not None
    db.add(LetterboxState(movie_id=movie.id, status="candidate", confidence="high"))
    db.add(
        MediaJob(
            job_id="job1",
            operation="letterbox_reencode",
            media_file_id=media_file.id,
            status="succeeded",
        )
    )
    db.add(
        LetterboxReencodeArtifact(
            job_id="job1",
            movie_id=movie.id,
            media_file_id=media_file.id,
            original_path=str(media),
            candidate_path=str(media.with_name("Movie (2020).letterbox.job1.mkv")),
            original_size_bytes=1,
            candidate_size_bytes=2,
            encoder="hevc_nvenc",
            encoder_family="nvidia",
            codec="hevc",
            crop_top=140,
            crop_bottom=140,
            status="candidate_ready",
        )
    )
    await db.commit()

    resp = await client.get(f"/api/letterbox/movies/{movie.id}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["reencode"]["job"] is None
    assert body["reencode"]["artifact"]["job_id"] == "job1"
    assert body["reencode"]["artifact"]["status"] == "candidate_ready"


@pytest.mark.asyncio
async def test_detect_returns_503_without_ffmpeg(client, db, monkeypatch):
    monkeypatch.setattr(binaries, "resolve", lambda name: None)
    movie = Movie(title="X", year=2000, folder_path="/m/x", tmdb_id=8)
    db.add(movie)
    await db.commit()
    await db.refresh(movie)
    resp = await client.post(f"/api/letterbox/movies/{movie.id}/detect")
    assert resp.status_code == 503


@pytest.mark.asyncio
async def test_detect_batch_requires_target(client, db, monkeypatch):
    monkeypatch.setattr(binaries, "resolve", lambda name: "/usr/bin/ffmpeg")
    resp = await client.post("/api/letterbox/detect", json={})
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_apply_refuses_variable_unsafe(client, db):
    movie = Movie(title="Inter", year=2014, folder_path="/m/i", tmdb_id=157)
    db.add(movie)
    await db.commit()
    await db.refresh(movie)
    db.add(LetterboxState(movie_id=movie.id, status="variable_unsafe", confidence="low"))
    await db.commit()
    resp = await client.post(f"/api/letterbox/movies/{movie.id}/apply", json={})
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_ignore_moves_to_skipped(client, db):
    movie = Movie(title="Skip", year=2000, folder_path="/m/sk", tmdb_id=7)
    db.add(movie)
    await db.commit()
    await db.refresh(movie)
    db.add(LetterboxState(movie_id=movie.id, status="candidate", confidence="high"))
    await db.commit()
    resp = await client.post(f"/api/letterbox/movies/{movie.id}/ignore")
    assert resp.status_code == 200
    assert resp.json()["status"] == "skipped"
    assert resp.json()["reviewed"] is True


@pytest.mark.asyncio
async def test_mark_not_letterboxed_moves_detected_candidate(client, db):
    movie = Movie(title="Bad Detect", year=2000, folder_path="/m/bd", tmdb_id=17)
    db.add(movie)
    await db.commit()
    await db.refresh(movie)
    db.add(
        LetterboxState(
            movie_id=movie.id,
            status="candidate",
            confidence="medium",
            recommended_crop_top=280,
            recommended_crop_bottom=280,
            aspect_label="2.40:1",
            variable_ar=True,
            variable_ar_note="bad variable note",
            error="old error",
        )
    )
    await db.commit()

    resp = await client.post(f"/api/letterbox/movies/{movie.id}/mark-not-letterboxed")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "not_letterboxed"
    assert body["confidence"] == "none"
    assert body["reviewed"] is True
    assert body["recommended_crop_top"] == 0
    assert body["recommended_crop_bottom"] == 0
    assert body["aspect_label"] is None
    assert body["variable_ar"] is False
    assert body["variable_ar_note"] is None
    assert body["error"] is None

    event = (
        await db.execute(
            select(LetterboxEvent).where(LetterboxEvent.movie_id == movie.id)
        )
    ).scalar_one()
    assert event.action == "mark_not_letterboxed"


@pytest.mark.asyncio
async def test_confirm_purges_previews_and_marks_reviewed(client, db):
    preview_root = settings.letterbox_preview_path
    movie = Movie(title="Confirm", year=2000, folder_path="/m/cf", tmdb_id=8)
    db.add(movie)
    await db.commit()
    await db.refresh(movie)
    db.add(
        LetterboxState(
            movie_id=movie.id,
            status="tagged",
            confidence="high",
            recommended_crop_top=140,
            recommended_crop_bottom=140,
            applied_crop_top=140,
            applied_crop_bottom=140,
            reviewed=False,
        )
    )
    await db.commit()

    before = letterbox_preview.preview_path(movie.id, "before", 5)
    after = letterbox_preview.preview_path(movie.id, "after", 5)
    before.parent.mkdir(parents=True, exist_ok=True)
    before.write_bytes(b"before")
    after.write_bytes(b"after")

    resp = await client.post(f"/api/letterbox/movies/{movie.id}/confirm")
    assert resp.status_code == 200
    assert resp.json()["reviewed"] is True
    assert before.exists() is False
    assert after.exists() is False
    assert list(preview_root.glob(f"{movie.id}_*.webp")) == []


@pytest.mark.asyncio
async def test_ignore_purges_previews(client, db):
    preview_root = settings.letterbox_preview_path
    movie = Movie(title="Ignore", year=2000, folder_path="/m/ig", tmdb_id=9)
    db.add(movie)
    await db.commit()
    await db.refresh(movie)
    db.add(
        LetterboxState(
            movie_id=movie.id,
            status="candidate",
            confidence="high",
            reviewed=False,
        )
    )
    await db.commit()

    before = letterbox_preview.preview_path(movie.id, "before", 10)
    after = letterbox_preview.preview_path(movie.id, "after", 10)
    before.parent.mkdir(parents=True, exist_ok=True)
    before.write_bytes(b"before")
    after.write_bytes(b"after")

    resp = await client.post(f"/api/letterbox/movies/{movie.id}/ignore")
    assert resp.status_code == 200
    assert resp.json()["status"] == "skipped"
    assert resp.json()["reviewed"] is True
    assert before.exists() is False
    assert after.exists() is False
    assert list(preview_root.glob(f"{movie.id}_*.webp")) == []


@pytest.mark.asyncio
async def test_preview_route_does_not_regenerate_for_reviewed_movie(
    client, db, monkeypatch
):
    preview_root = settings.letterbox_preview_path
    movie = Movie(title="Reviewed", year=2000, folder_path="/m/rev", tmdb_id=10)
    db.add(movie)
    await db.commit()
    await db.refresh(movie)
    db.add(
        LetterboxState(
            movie_id=movie.id,
            status="tagged",
            confidence="high",
            recommended_crop_top=140,
            recommended_crop_bottom=140,
            applied_crop_top=140,
            applied_crop_bottom=140,
            reviewed=True,
            samples_json=json.dumps([
                {"minute": 5, "ok": True},
                {"minute": 10, "ok": True},
            ]),
        )
    )
    await db.commit()

    letterbox_preview.purge_movie_previews(movie.id)
    assert list(preview_root.glob(f"{movie.id}_*.webp")) == []

    called = False

    def fail_generate(*args, **kwargs):
        nonlocal called
        called = True
        raise AssertionError("preview generation should not run for reviewed movies")

    monkeypatch.setattr(letterbox_preview, "generate_preview", fail_generate)

    resp = await client.get(f"/api/letterbox/movies/{movie.id}/preview?mode=before&minute=5")
    assert resp.status_code == 404
    assert called is False


@pytest.mark.asyncio
async def test_job_events_404_unknown(client):
    resp = await client.get("/api/letterbox/jobs/nope/events")
    assert resp.status_code == 404


def test_job_state_summary_tracks_requested_totals():
    state = JobState(job_id="job", total=5, detector="v1")
    for status in ["candidate", "candidate", "not_letterboxed", "variable_unsafe", "missing"]:
        state.record_status(status)
    assert state.summary(completed=5) == {
        "candidate": 2,
        "not_letterboxed": 1,
        "variable": 1,
        "total": 5,
        "completed": 5,
    }


@pytest.mark.asyncio
async def test_apply_success_with_mocked_binaries(client, db, tmp_path, monkeypatch):
    movie, media = _movie_with_file(tmp_path)
    db.add(movie)
    await db.commit()
    await db.refresh(movie)
    db.add(LetterboxState(movie_id=movie.id, status="candidate", confidence="high",
                          recommended_crop_top=140, recommended_crop_bottom=140))
    await db.commit()

    mkv_json = json.dumps(
        {"container": {"type": "Matroska"}, "tracks": [{"type": "video", "properties": {}}]}
    )

    def fake_run(name, args, timeout=120.0):
        if name == "mkvmerge":
            return binaries.CommandResult(0, mkv_json, "")
        return binaries.CommandResult(0, "", "")  # mkvpropedit

    monkeypatch.setattr(binaries, "resolve", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(binaries, "run", fake_run)
    resp = await client.post(f"/api/letterbox/movies/{movie.id}/apply", json={})
    assert resp.status_code == 200
    body = resp.json()
    assert body["applied"] is True and body["top"] == 140


@pytest.mark.asyncio
async def test_apply_ineligible_mp4_returns_422(client, db, tmp_path, monkeypatch):
    movie, _ = _movie_with_file(tmp_path, name="Movie (2020).mp4")
    db.add(movie)
    await db.commit()
    await db.refresh(movie)
    db.add(LetterboxState(movie_id=movie.id, status="candidate", confidence="high",
                          recommended_crop_top=140, recommended_crop_bottom=140))
    await db.commit()

    monkeypatch.setattr(binaries, "resolve", lambda name: None)
    resp = await client.post(f"/api/letterbox/movies/{movie.id}/apply", json={})
    assert resp.status_code == 422
