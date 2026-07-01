# Letterbox

## Overview

The letterbox subsystem detects movie files with black bars, records review
state, generates before-and-after previews, applies or removes MKV crop tags,
and can escalate to permanent re-encoding when tags are not enough. The public
surface is in `marquee/api/routes/letterbox.py`; detection and batching live in
`marquee/media/`, and persistent mutation logic lives in `marquee/core/`.

## Implemented Scope

Implemented:

- Movie-only letterbox detection
- Synchronous single-movie detect and queued batch detect
- Preview frame generation
- Crop-tag apply and remove flows
- Review, ignore, and reset state transitions
- Tag-drift heal scans
- Re-encode plan, artifact management, and original replacement / restore

Not implemented:

- TV-first workflows or season / episode cascade behavior
- Automatic application as the default behavior
- Automatic MP4-to-MKV conversion as a general policy

## Core Modules

- `marquee/media/letterbox_detect.py` performs crop detection using ffmpeg
  `cropdetect` and the ImageMagick `trim` fallback path.
- `marquee/media/letterbox_manager.py` coordinates single and batch detection,
  progress publishing, and state storage.
- `marquee/media/letterbox_preview.py` generates preview frames and caches them
  under `settings.letterbox_preview_path`.
- `marquee/core/letterbox_service.py` resolves media files, checks
  eligibility, and applies or removes crop tags safely.
- `marquee/core/letterbox_heal.py` scans for tag drift.
- `marquee/core/letterbox_reencode.py` builds and runs permanent re-encode
  plans, including Dolby Vision preservation logic.

## Detection Model

Detection samples frames over the movie and aggregates crop measurements into a
consensus result. The service distinguishes between clean scope bars, no-bars
results, asymmetric bars, and variable-aspect-ratio cases.

Important behavior:

- `cropdetect` is the default backend and `trim` is available as a fallback.
- Resolution and bar-size heuristics keep already-cropped or obviously
  ineligible files out of the workflow.
- Batch work is bounded by request-path and worker-path concurrency controls so
  ffmpeg does not overwhelm the media host.
- Detection results are written to `LetterboxState` and audit history is stored
  in `LetterboxEvent`.

## API Surface

Representative routes in `marquee/api/routes/letterbox.py`:

- `GET /api/letterbox/status`
- `GET /api/letterbox/candidates`
- `GET /api/letterbox/movies/{movie_id}`
- `POST /api/letterbox/movies/{movie_id}/detect`
- `POST /api/letterbox/detect`
- `GET /api/letterbox/movies/{movie_id}/preview`
- `POST /api/letterbox/movies/{movie_id}/apply`
- `POST /api/letterbox/movies/{movie_id}/remove`
- `POST /api/letterbox/movies/{movie_id}/ignore`
- `POST /api/letterbox/heal`
- `POST /api/letterbox/movies/{movie_id}/reencode-plan`

Single-movie detect is synchronous. Batch detection and some heavier operations
run as queued jobs and expose progress through the job APIs described in
`job-platform.md`.

## Apply, Remove, And Heal

Tag application is handled by `LetterboxService.apply()` and removal by
`LetterboxService.remove()`. The service uses per-file locking, validates the
resolved media path, and writes audit events before returning success.

`marquee/core/letterbox_heal.py` periodically checks for drift between stored
state and the actual file metadata when `LETTERBOX_HEAL_ENABLED=true`.

## Permanent Re-encode

Some users want a physical re-encode rather than MKV crop tags alone.
`marquee/core/letterbox_reencode.py` supports:

- source inspection with ffprobe
- encoder selection and hardware-aware planning
- Dolby Vision preservation and fallback warnings
- artifact tracking, original replacement, original restore, and cleanup

This path is more invasive than crop-tag application, so it is surfaced as a
planned and reviewable workflow rather than the default.

## Important Configuration

Representative knobs in `marquee/config.py`:

- Feature switches: `LETTERBOX_ENABLED`, `LETTERBOX_HEAL_ENABLED`,
  `LETTERBOX_AUTO_APPLY_HIGH=false`
- Detection: `LETTERBOX_DETECT_METHOD=cropdetect`,
  `LETTERBOX_CROPDETECT_LIMIT=24`,
  `LETTERBOX_CROPDETECT_HDR_LIMIT=80`,
  `LETTERBOX_TRIM_FUZZ=[5,15,25]`
- Sampling: `LETTERBOX_MOVIE_SAMPLES_MIN=5`,
  `LETTERBOX_MOVIE_SAMPLES_MAX=60`,
  `LETTERBOX_MOVIE_SAMPLE_STEP=5`
- Concurrency: `LETTERBOX_MAX_PARALLEL=0`,
  `LETTERBOX_FFMPEG_CONCURRENCY=2`
- Re-encode: `LETTERBOX_REENCODE_ALLOW_CPU_FALLBACK=true`,
  `LETTERBOX_REENCODE_NVIDIA_ACCELERATION=auto`,
  `LETTERBOX_REENCODE_STRICT_DOVI=false`

## Cross References

- `library.md` for movie/media-file persistence
- `job-platform.md` for queued batch detect, apply, remove, and re-encode work
- `timeline.md` for deferred TV support and other follow-up work
