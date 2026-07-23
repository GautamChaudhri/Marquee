"""Letterbox feature tests — no ffmpeg/mkvtoolnix required.

Pure logic (pre-filter, cropdetect/trim parsing, consensus, sampling, aspect
labels) is exercised directly; the engine is fed canned ``cropdetect`` stderr
and fabricated measurements. Endpoint control paths run against a seeded DB with
binaries monkeypatched, mirroring ``test_run_endpoints.py``.
"""

from __future__ import annotations

import asyncio
import json
import time
from datetime import UTC, datetime

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from marquee.config import settings
from marquee.core.letterbox_eligibility import (
    check_movie_eligibility,
    resolve_movie_media_path,
)
from marquee.core.media_files import ensure_media_file_for_movie, resolve_row
from marquee.core.path_utils import PathValidationError
from marquee.main import app
from marquee.media import binaries, letterbox_preview
from marquee.media import letterbox_detect as ld
from marquee.media.probe import prefilter_bucket
from marquee.models import (
    Episode,
    Job,
    JobArtifact,
    LetterboxEvent,
    LetterboxState,
    MediaFile,
    Movie,
    Season,
    Series,
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
        (1920, 800, "skip"),  # native scope
        (1920, 1040, "skip"),  # native 1.85
        (1280, 720, "skip"),  # too small
        (1440, 1080, "skip"),  # 4:3
        (640, 480, "skip"),  # 4:3
        (0, 0, "skip"),  # unknown
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


def test_cropdetect_nvdec_downloads_frames_before_cpu_filter(monkeypatch):
    calls = []

    def fake_run(name, args, timeout=120.0):
        calls.append((name, args, timeout))
        return binaries.CommandResult(0, "", "[Parsed_cropdetect_0 @ 0x] crop=3840:1600:0:280\n")

    monkeypatch.setattr(binaries, "run", fake_run)
    measurement = ld.measure_window_cropdetect(
        "/movie.mkv", 5, 2160, nvdec_decoder="hevc_cuvid", pix_fmt="yuv420p10le"
    )

    assert measurement.ok is True
    assert measurement.backend == "nvdec"
    args = calls[0][1]
    assert args[args.index("-hwaccel") + 1] == "cuda"
    assert args[args.index("-c:v:0") + 1] == "hevc_cuvid"
    assert "hwdownload,format=p010le,cropdetect=limit=24:round=2:reset=1" in args


def test_detect_uses_nvdec_only_after_faster_matching_benchmark(monkeypatch):
    ld._NVDEC_BENCHMARKS.clear()
    monkeypatch.setattr(ld, "sample_minutes", lambda *_args, **_kwargs: [5, 10])
    monkeypatch.setattr(ld, "_nvdec_decoder_for", lambda *_args, **_kwargs: "hevc_cuvid")

    def fake_measure(_path, minute, _height, *, nvdec_decoder=None, **_kwargs):
        return ld.WindowMeasurement(
            minute=minute,
            ok=True,
            top_bar=140,
            bottom_bar=140,
            width=1920,
            height=800,
            backend="nvdec" if nvdec_decoder else "cpu",
            elapsed_ms=40 if nvdec_decoder else 100,
        )

    monkeypatch.setattr(ld, "measure_window_cropdetect", fake_measure)
    result = ld.detect("/movie.mkv", width=1920, height=1080, codec="hevc", pix_fmt="yuv420p")

    assert result.method == "cropdetect_nvdec"
    assert [sample["backend"] for sample in result.samples] == ["nvdec", "nvdec"]


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
    assert ld.sample_minutes(1800, is_tv=True) == [4, 15, 26]


def test_sample_offsets_proportional_respects_runtime_span():
    assert ld.sample_offsets_proportional(20 * 60, 3, 12, 12) == [144, 600, 1056]
    assert ld.sample_offsets_proportional(40 * 60, 3, 12, 12) == [288, 1200, 2112]
    assert ld.sample_offsets_proportional(65 * 60, 8, 12, 12) == [
        468,
        891,
        1315,
        1738,
        2162,
        2585,
        3009,
        3432,
    ]


def test_sample_offsets_proportional_short_clip_clamps_monotonic():
    assert ld.sample_offsets_proportional(15, 8, 12, 12) == [2, 3, 5, 7, 8, 10, 12, 13]


def test_sample_minutes_thorough_uses_denser_movie_schedule():
    regular = ld.sample_minutes(7200, is_tv=False)
    thorough = ld.sample_minutes(7200, is_tv=False, thorough=True)
    assert thorough[:4] == [5, 6, 7, 8]
    assert len(thorough) > len(regular)


def test_sample_minutes_clamped_to_short_duration():
    # 12-minute file: only samples before minute 12 survive.
    minutes = ld.sample_minutes(12 * 60, is_tv=False)
    assert max(minutes) < 12


def test_detect_tv_escalates_quick_pass_to_thorough(monkeypatch):
    calls: list[int] = []

    def fake_measure(_path, minute, _height, **_kwargs):
        calls.append(minute)
        bar = (20 if len(calls) == 2 else 0) if len(calls) <= 3 else 12
        return ld.WindowMeasurement(
            minute=minute,
            ok=True,
            top_bar=bar,
            bottom_bar=bar,
            width=1920,
            height=1080 - (bar * 2),
        )

    monkeypatch.setattr(ld, "_nvdec_decoder_for", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(ld, "measure_window_cropdetect", fake_measure)

    result = ld.detect("/episode.mkv", width=1920, height=1080, duration_s=1800, is_tv=True)

    assert result.status == "candidate"
    assert len(result.samples) == 8
    assert len(calls) == 11


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
        title="Movie",
        year=2020,
        folder_path=str(folder),
        movie_file_path=name,
        tmdb_id=111,
    ), media


def _preview_root(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "DATA_DIR", str(tmp_path / "data"))
    return settings.letterbox_preview_path

def test_retired_preview_cache_cleanup_is_subject_scoped(tmp_path, monkeypatch):
    preview_root = _preview_root(tmp_path, monkeypatch)
    preview_root.mkdir(parents=True, exist_ok=True)
    movie_cache = preview_root / "movie-7_before_5_bright_v3.webp"
    legacy_cache = preview_root / "7_before_5_bright_v3.webp"
    episode_cache = preview_root / "episode-7_before_5_bright_v3.webp"
    for path in (movie_cache, legacy_cache, episode_cache):
        path.write_bytes(b"retired")

    assert letterbox_preview.purge_movie_previews(7) == 2
    assert not movie_cache.exists()
    assert not legacy_cache.exists()
    assert episode_cache.exists()


def test_retired_episode_cache_cleanup_keeps_movie_entries(tmp_path, monkeypatch):
    preview_root = _preview_root(tmp_path, monkeypatch)
    preview_root.mkdir(parents=True, exist_ok=True)
    episode_cache = preview_root / "episode-7_before_5_bright_v3.webp"
    movie_cache = preview_root / "movie-7_before_5_bright_v3.webp"
    episode_cache.write_bytes(b"retired")
    movie_cache.write_bytes(b"retired")

    assert letterbox_preview.purge_all_episode_previews() == 1
    assert not episode_cache.exists()
    assert movie_cache.exists()


def test_retired_preview_module_exposes_no_execution_helpers():
    assert not hasattr(letterbox_preview, "generate_preview")
    assert not hasattr(letterbox_preview, "warm_previews")
    assert not hasattr(letterbox_preview, "preview_path")


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
    elig = check_movie_eligibility(movie)
    assert elig.eligible is True
    assert elig.reason is None


def test_eligibility_rejects_mp4(tmp_path, monkeypatch):
    monkeypatch.setattr(binaries, "resolve", lambda name: None)
    movie, _ = _movie_with_file(tmp_path, name="Movie (2020).mp4")
    elig = check_movie_eligibility(movie)
    assert elig.eligible is False
    assert elig.reason == "not_mkv"


def test_eligibility_missing_file(tmp_path, monkeypatch):
    monkeypatch.setattr(binaries, "resolve", lambda name: None)
    movie = Movie(
        title="Gone",
        year=2020,
        folder_path=str(tmp_path),
        movie_file_path="nope.mkv",
        tmdb_id=222,
    )
    elig = check_movie_eligibility(movie)
    assert elig.eligible is False
    assert elig.reason == "missing"


def test_resolve_media_file_rejects_escape(tmp_path):
    movie = Movie(
        title="Evil",
        year=2020,
        folder_path=str(tmp_path),
        movie_file_path="../../etc/passwd",
        tmdb_id=333,
    )
    with pytest.raises(PathValidationError):
        resolve_movie_media_path(movie)


# ---------------------------------------------------------------------------
# _mkdir_with_retry — cross-account ownership + transient mount errors
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_resolve_row_does_not_write_on_read_path(db, tmp_path):
    # resolve_row is on the hot GET path; it must not dirty the session (which
    # would trigger an autoflush UPDATE that contends with the encode worker).
    movie, media = _movie_with_file(tmp_path)
    db.add(movie)
    await db.flush()
    row = MediaFile(
        source="radarr",
        source_key="radarr:movie:1",
        movie_id=movie.id,
        path=str(media),
        relative_path=media.name,
        container="mkv",
        is_active=True,
        last_resolved_path="/stale/path",
    )
    db.add(row)
    await db.commit()

    await resolve_row(db, row)

    assert not db.dirty
    assert row.last_resolved_path == "/stale/path"


@pytest.mark.asyncio
async def test_movie_detail_uses_cached_dolby_vision_state(client, db, tmp_path):
    # The detail fetch must be subprocess-free so it returns instantly even
    # while an encode saturates disk I/O — DoVi presence comes from the cached
    # has_dv column, not a live ffprobe.
    movie, _ = _movie_with_file(tmp_path)
    db.add(movie)
    await db.commit()
    await db.refresh(movie)
    db.add(LetterboxState(movie_id=movie.id, status="candidate", confidence="high"))
    await db.commit()

    resp = await client.get(f"/api/letterbox/movies/{movie.id}")
    assert resp.status_code == 200
    assert resp.json()["dolby_vision"]["present"] is False


# ---------------------------------------------------------------------------
# detect_and_store state mapping (engine mocked — no ffmpeg)
# ---------------------------------------------------------------------------























  # second call served from cache


  # other movie untouched





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
async def test_status_snapshot_preserves_movie_payload_shape(client, db, monkeypatch):
    monkeypatch.setattr(binaries, "reset_cache", lambda: None)
    monkeypatch.setattr(
        binaries,
        "availability",
        lambda: {"ffmpeg": True, "ffprobe": True, "mkvmerge": False, "mkvpropedit": False},
    )

    candidate = Movie(
        title="Candidate",
        year=2000,
        folder_path="/m/candidate",
        movie_file_path="Candidate.mkv",
        tmdb_id=201,
    )
    tagged = Movie(
        title="Tagged",
        year=2001,
        folder_path="/m/tagged",
        movie_file_path="Tagged.mkv",
        tmdb_id=202,
    )
    full_frame = Movie(
        title="Full Frame",
        year=2002,
        folder_path="/m/full-frame",
        movie_file_path="FullFrame.mkv",
        tmdb_id=203,
    )
    db.add_all([candidate, tagged, full_frame])
    await db.commit()
    for movie in (candidate, tagged, full_frame):
        await db.refresh(movie)

    db.add_all(
        [
            LetterboxState(
                movie_id=candidate.id,
                status="candidate",
                confidence="high",
                last_detected_at=datetime(2024, 1, 2, 3, 4, 5, tzinfo=UTC),
            ),
            LetterboxState(
                movie_id=tagged.id,
                status="tagged",
                confidence="medium",
            ),
            LetterboxState(
                movie_id=full_frame.id,
                status="prefilter_skipped",
                prefilter_reason="native_wide",
            ),
        ]
    )
    db.add(
        Job(
            id="lb-batch",
            type="letterbox_detect_batch",
            root_id="lb-batch",
            phase="running",
            request={},
            subject_snapshot={},
        )
    )
    db.add(
        Job(
            id="lb-child",
            type="letterbox_detect",
            parent_id="lb-batch",
            root_id="lb-batch",
            phase="queued",
            request={},
            subject_snapshot={},
        )
    )
    await db.commit()

    resp = await client.get("/api/letterbox/status")

    assert resp.status_code == 200
    assert resp.json() == {
        "enabled": settings.LETTERBOX_ENABLED,
        "method": settings.LETTERBOX_DETECT_METHOD,
        "counts": {
            "candidate": 1,
            "prefilter_skipped": 1,
            "tagged": 1,
        },
        "full_frame": 1,
        "binaries": {
            "ffmpeg": True,
            "ffprobe": True,
            "mkvmerge": False,
            "mkvpropedit": False,
        },
        "honored_by": [
            "plex-desktop",
            "vlc",
            "mpv",
        ],
        "not_honored_by": [
            "plex-web",
            "plex-mobile",
        ],
        "last_scan": "2024-01-02T03:04:05+00:00",
        "batch_active": "lb-batch",
    }


@pytest.mark.asyncio
async def test_movie_endpoints_ignore_episode_letterbox_rows(client, db, monkeypatch):
    monkeypatch.setattr(binaries, "reset_cache", lambda: None)
    monkeypatch.setattr(
        binaries,
        "availability",
        lambda: {"ffmpeg": True, "ffprobe": True, "mkvmerge": False, "mkvpropedit": False},
    )

    movie = Movie(
        title="Movie Only",
        year=2000,
        folder_path="/m/movie-only",
        movie_file_path="MovieOnly.mkv",
        tmdb_id=250,
    )
    series = Series(title="Show", year=2020, series_path="/tv/show", tvdb_id=1250)
    db.add_all([movie, series])
    await db.commit()
    await db.refresh(movie)
    await db.refresh(series)

    season = Season(series_id=series.id, season_number=1, episode_file_count=1)
    db.add(season)
    await db.commit()
    episode = Episode(series_id=series.id, season_number=1, episode_number=1)
    db.add(episode)
    await db.commit()
    await db.refresh(episode)

    db.add_all(
        [
            LetterboxState(
                media_type="movie",
                movie_id=movie.id,
                status="candidate",
                confidence="high",
            ),
            LetterboxState(
                media_type="episode",
                episode_id=episode.id,
                status="tagged",
                confidence="low",
            ),
        ]
    )
    await db.commit()

    status_resp = await client.get("/api/letterbox/status")
    candidates_resp = await client.get("/api/letterbox/candidates")

    assert status_resp.status_code == 200
    assert status_resp.json()["counts"] == {"candidate": 1}
    assert candidates_resp.status_code == 200
    assert candidates_resp.json()["total"] == 1
    assert [item["movie_id"] for item in candidates_resp.json()["items"]] == [movie.id]


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
    db.add_all(
        [
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
        ]
    )
    await db.commit()

    resp = await client.get("/api/letterbox/status")
    assert resp.status_code == 200
    assert resp.json()["full_frame"] == 1


@pytest.mark.asyncio
async def test_status_ignores_terminal_letterbox_batches(client, db):
    now = datetime.now(UTC)
    db.add_all(
        [
            Job(
                id="batch-interrupted",
                type="letterbox_detect_batch",
                root_id="batch-interrupted",
                phase="terminal",
                outcome="failed",
                terminal_at=now,
                request={},
                subject_snapshot={},
            ),
            Job(
                id="batch-cancelled",
                type="letterbox_detect_batch",
                root_id="batch-cancelled",
                phase="terminal",
                outcome="cancelled",
                terminal_at=now,
                request={},
                subject_snapshot={},
            ),
        ]
    )
    await db.commit()

    resp = await client.get("/api/letterbox/status")

    assert resp.status_code == 200
    assert resp.json()["batch_active"] is None


@pytest.mark.asyncio
async def test_candidates_filter_and_paginate(client, db):
    m1 = Movie(title="Scope", year=2000, folder_path="/m/s", movie_file_path="s.mkv", tmdb_id=1)
    m2 = Movie(title="Flat", year=2001, folder_path="/m/f", movie_file_path="f.mkv", tmdb_id=2)
    db.add_all([m1, m2])
    await db.commit()
    await db.refresh(m1)
    await db.refresh(m2)
    db.add_all(
        [
            LetterboxState(
                movie_id=m1.id,
                status="candidate",
                confidence="high",
                recommended_crop_top=140,
                recommended_crop_bottom=140,
            ),
            LetterboxState(
                movie_id=m2.id,
                status="tagged",
                confidence="high",
                applied_crop_top=140,
                applied_crop_bottom=140,
            ),
        ]
    )
    await db.commit()

    all_resp = await client.get("/api/letterbox/candidates")
    assert all_resp.json()["total"] == 2

    cand = await client.get("/api/letterbox/candidates?status=candidate")
    items = cand.json()["items"]
    assert len(items) == 1
    assert items[0]["title"] == "Scope"
    assert items[0]["recommended_crop_top"] == 140


@pytest.mark.asyncio
async def test_candidates_snapshot_preserves_movie_payload_shape(client, db):
    alpha = Movie(
        title="Alpha Scope",
        year=2000,
        folder_path="/m/alpha",
        movie_file_path="Alpha.mkv",
        tmdb_id=301,
    )
    beta = Movie(
        title="Beta Tagged",
        year=2001,
        folder_path="/m/beta",
        movie_file_path="Beta.mkv",
        tmdb_id=302,
    )
    db.add_all([alpha, beta])
    await db.commit()
    for movie in (alpha, beta):
        await db.refresh(movie)

    db.add_all(
        [
            LetterboxState(
                movie_id=alpha.id,
                status="candidate",
                confidence="high",
                eligible=True,
                source_width=1920,
                source_height=1080,
                recommended_crop_top=140,
                recommended_crop_bottom=140,
                aspect_label="2.40:1",
                detect_method="cropdetect",
                reviewed=False,
                last_detected_at=datetime(2024, 1, 2, 3, 4, 5, tzinfo=UTC),
                error=None,
                prefilter_bucket="candidate",
                prefilter_reason="ratio_16_9",
                prefilter_aspect_ratio=1.7777777778,
                last_prefiltered_at=datetime(2024, 1, 1, 0, 0, 0, tzinfo=UTC),
                variable_ar=False,
                variable_ar_note=None,
            ),
            LetterboxState(
                movie_id=beta.id,
                status="tagged",
                confidence="low",
                eligible=False,
                ineligible_reason="read_only",
                source_width=3840,
                source_height=2160,
                recommended_crop_top=280,
                recommended_crop_bottom=280,
                aspect_label="2.40:1",
                applied_crop_top=280,
                applied_crop_bottom=280,
                detect_method="cropdetect_nvdec",
                reviewed=True,
                last_detected_at=datetime(2024, 1, 3, 4, 5, 6, tzinfo=UTC),
                last_applied_at=datetime(2024, 1, 3, 5, 6, 7, tzinfo=UTC),
                error="kept for regression snapshot",
                prefilter_bucket="candidate",
                prefilter_reason="ratio_16_9",
                prefilter_aspect_ratio=1.7777777778,
                last_prefiltered_at=datetime(2024, 1, 2, 0, 0, 0, tzinfo=UTC),
                variable_ar=True,
                variable_ar_note="mixed bars",
            ),
        ]
    )
    await db.commit()

    resp = await client.get("/api/letterbox/candidates?sort=confidence")

    assert resp.status_code == 200
    assert resp.json() == {
        "total": 2,
        "page": 1,
        "page_size": 50,
        "items": [
            {
                "movie_id": alpha.id,
                "status": "candidate",
                "confidence": "high",
                "eligible": True,
                "ineligible_reason": None,
                "source_width": 1920,
                "source_height": 1080,
                "recommended_crop_top": 140,
                "recommended_crop_bottom": 140,
                "aspect_label": "2.40:1",
                "applied_crop_top": None,
                "applied_crop_bottom": None,
                "detect_method": "cropdetect",
                "reviewed": False,
                "last_detected_at": "2024-01-02T03:04:05+00:00",
                "last_applied_at": None,
                "error": None,
                "prefilter_bucket": "candidate",
                "prefilter_reason": "ratio_16_9",
                "prefilter_aspect_ratio": 1.7777777778,
                "last_prefiltered_at": "2024-01-01T00:00:00+00:00",
                "variable_ar": False,
                "variable_ar_note": None,
                "title": "Alpha Scope",
                "year": 2000,
            },
            {
                "movie_id": beta.id,
                "status": "tagged",
                "confidence": "low",
                "eligible": False,
                "ineligible_reason": "read_only",
                "source_width": 3840,
                "source_height": 2160,
                "recommended_crop_top": 280,
                "recommended_crop_bottom": 280,
                "aspect_label": "2.40:1",
                "applied_crop_top": 280,
                "applied_crop_bottom": 280,
                "detect_method": "cropdetect_nvdec",
                "reviewed": True,
                "last_detected_at": "2024-01-03T04:05:06+00:00",
                "last_applied_at": "2024-01-03T05:06:07+00:00",
                "error": "kept for regression snapshot",
                "prefilter_bucket": "candidate",
                "prefilter_reason": "ratio_16_9",
                "prefilter_aspect_ratio": 1.7777777778,
                "last_prefiltered_at": "2024-01-02T00:00:00+00:00",
                "variable_ar": True,
                "variable_ar_note": "mixed bars",
                "title": "Beta Tagged",
                "year": 2001,
            },
        ],
    }


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
    db.add_all(
        [
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
        ]
    )
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
    db.add_all(
        [
            LetterboxState(movie_id=movies[0].id, status="candidate", confidence="high"),
            LetterboxState(movie_id=movies[1].id, status="candidate", confidence="medium"),
            LetterboxState(
                movie_id=movies[2].id,
                status="candidate",
                confidence="variable",
                variable_ar=True,
            ),
            LetterboxState(movie_id=movies[3].id, status="candidate", confidence="low"),
        ]
    )
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
    db.add_all(
        [
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
        ]
    )
    await db.commit()

    resp = await client.get("/api/letterbox/candidates?status=tagged&reviewed=false&sort=recent")
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
    db.add_all(
        [
            candidate,
            native_scope,
            unknown,
            low_res,
            pathless_candidate,
            analyzed_false_positive,
        ]
    )
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

    skipped_resp = await client.get("/api/letterbox/movies/find-candidates?include_skipped=true")
    skipped_items = {item["movie_id"]: item for item in skipped_resp.json()["items"]}
    assert len(skipped_items) == 4
    assert pathless_candidate.id not in skipped_items
    assert analyzed_false_positive.id not in skipped_items
    assert skipped_items[native_scope.id]["prefilter"]["reason"] == "native_wide"
    assert skipped_items[low_res.id]["prefilter"]["reason"] == "low_resolution"

    analyzed_resp = await client.get("/api/letterbox/movies/find-candidates?include_analyzed=true")
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
        analyzed_items[analyzed_false_positive.id]["letterbox_state"]["status"] == "not_letterboxed"
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
    db.add_all(
        [
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
        ]
    )
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
async def test_movie_detail_includes_sample_previews_even_when_reviewed(client, db):
    movie = Movie(title="Reviewed", year=2000, folder_path="/m/rev", tmdb_id=10)
    db.add(movie)
    await db.commit()
    await db.refresh(movie)
    db.add(
        LetterboxState(
            movie_id=movie.id,
            status="tagged",
            confidence="high",
            reviewed=True,
            samples_json=json.dumps(
                [
                    {"minute": 5, "ok": True},
                    {"minute": 10, "ok": False, "error": "dark frame"},
                ]
            ),
        )
    )
    await db.commit()

    resp = await client.get(f"/api/letterbox/movies/{movie.id}")
    assert resp.status_code == 200
    body = resp.json()
    assert "preview_urls" not in body
    assert body["sample_previews"] == [
        {"minute": 5, "ok": True},
        {"minute": 10, "ok": False},
    ]


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
        Job(
            id="job1",
            type="letterbox_reencode",
            root_id="job1",
            phase="terminal",
            outcome="succeeded",
            terminal_at=datetime.now(UTC),
            request={},
            subject_kind="movie",
            subject_reference=str(movie.id),
            subject_snapshot={"title": movie.title},
        )
    )
    await db.commit()
    db.add(
        JobArtifact(
            job_id="job1",
            kind="media_candidate",
            name="letterbox-candidate.mkv",
            status="available",
            storage_key="jmc3/evidence/artifacts/job1/1/artifact-1.mkv",
            content_type="video/x-matroska",
            size_bytes=2,
            artifact_metadata={"media_file_id": media_file.id, "crop_top": 140, "crop_bottom": 140},
        )
    )
    await db.commit()

    resp = await client.get(f"/api/letterbox/movies/{movie.id}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["reencode"]["job"] is None
    assert body["reencode"]["artifact"]["job_id"] == "job1"
    assert body["reencode"]["artifact"]["status"] == "available"


@pytest.mark.asyncio
async def test_detect_defers_tool_availability_to_the_canonical_worker(client, db, monkeypatch):
    monkeypatch.setattr(binaries, "resolve", lambda name: None)
    movie = Movie(title="X", year=2000, folder_path="/m/x", tmdb_id=8)
    db.add(movie)
    await db.commit()
    await db.refresh(movie)
    resp = await client.post(f"/api/letterbox/movies/{movie.id}/detect")
    assert resp.status_code == 409
    assert resp.json()["detail"] == "movie has no active media file"


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
        await db.execute(select(LetterboxEvent).where(LetterboxEvent.movie_id == movie.id))
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

    before = preview_root / f"movie-{movie.id}_before_5_bright_v3.webp"
    after = preview_root / f"{movie.id}_after_5_bright_v3.webp"
    before.parent.mkdir(parents=True, exist_ok=True)
    before.write_bytes(b"before")
    after.write_bytes(b"after")

    resp = await client.post(f"/api/letterbox/movies/{movie.id}/confirm")
    assert resp.status_code == 200
    assert resp.json()["reviewed"] is True
    assert not before.exists()
    assert not after.exists()


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

    before = preview_root / f"movie-{movie.id}_before_10_bright_v3.webp"
    after = preview_root / f"{movie.id}_after_10_bright_v3.webp"
    before.parent.mkdir(parents=True, exist_ok=True)
    before.write_bytes(b"before")
    after.write_bytes(b"after")

    resp = await client.post(f"/api/letterbox/movies/{movie.id}/ignore")
    assert resp.status_code == 200
    assert resp.json()["status"] == "skipped"
    assert resp.json()["reviewed"] is True
    assert not before.exists()
    assert not after.exists()


@pytest.mark.asyncio
async def test_preview_submission_blocks_reviewed_movie(client, db):
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
        )
    )
    await db.commit()

    resp = await client.post(f"/api/letterbox/movies/{movie.id}/preview?mode=before&minute=5")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_legacy_job_events_route_is_removed(client):
    resp = await client.get("/api/letterbox/jobs/nope/events", follow_redirects=False)
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_apply_submits_canonical_verified_mutation(client, db, tmp_path, monkeypatch):
    from marquee.core.jobs.pgqueuer_gateway import pgqueuer_gateway

    async def enqueue_stub(*_args, **_kwargs):
        return 1

    monkeypatch.setattr(pgqueuer_gateway, "enqueue", enqueue_stub)
    movie, _media = _movie_with_file(tmp_path)
    db.add(movie)
    await db.commit()
    await db.refresh(movie)
    db.add(
        LetterboxState(
            movie_id=movie.id,
            status="candidate",
            confidence="high",
            source_width=1920,
            source_height=1080,
            recommended_crop_top=140,
            recommended_crop_bottom=140,
        )
    )
    await db.commit()

    resp = await client.post(f"/api/letterbox/movies/{movie.id}/apply", json={})
    assert resp.status_code == 202, resp.text
    payload = resp.json()
    assert payload["phase"] == "queued"
    job = await db.get(Job, payload["job_id"])
    assert job.type == "letterbox_apply"
    assert job.request["media_file_id"] > 0
    assert job.request["before"]["source_width"] == 1920


@pytest.mark.asyncio
async def test_apply_ineligible_mp4_returns_422(client, db, tmp_path, monkeypatch):
    movie, _ = _movie_with_file(tmp_path, name="Movie (2020).mp4")
    db.add(movie)
    await db.commit()
    await db.refresh(movie)
    db.add(
        LetterboxState(
            movie_id=movie.id,
            status="candidate",
            confidence="high",
            recommended_crop_top=140,
            recommended_crop_bottom=140,
        )
    )
    await db.commit()

    monkeypatch.setattr(binaries, "resolve", lambda name: None)
    resp = await client.post(f"/api/letterbox/movies/{movie.id}/apply", json={})
    assert resp.status_code == 422
