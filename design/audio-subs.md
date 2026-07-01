# Audio And Subtitles

## Overview

The audio and subtitle subsystem manages physical media-file inspection,
subtitle inventory snapshots, policy-driven cleanup, durable mutations, and
optional AI generation through an external Subgen service. The feature is built
around `MediaFile` and `SubtitleInventory` rows rather than around movie rows
alone, which lets Marquee track real container state and restore managed
subtitle assets later.

## Implemented Scope

Implemented:

- ffprobe-backed inventory of audio streams and embedded/external subtitle
  tracks
- persisted subtitle inventory and coverage summaries
- text previews and downloads for subtitle tracks
- policy CRUD and audit / apply flows
- durable mutation planning and execution for audio and subtitle operations
- Subgen-backed generation jobs
- managed subtitle assets and pre-mutation backups
- frontend pages for inventory, policies, generation, and jobs

Not implemented:

- The dedicated subtitle batch endpoint family described in older planning docs
- A fully generalized TV-first documentation surface in these core docs

## Architecture

Key modules under `marquee/core/subtitles/`:

- `probe.py` inspects the container with ffprobe and normalizes embedded audio
  and subtitle streams.
- `external.py` discovers subtitle sidecars next to the media file.
- `service.py` turns probe results into persisted inventory rows and API-facing
  dictionaries.
- `coverage.py` computes preferred-language coverage and summary flags.
- `policy.py` evaluates cleanup policies against current tracks.
- `mutation.py` builds plans, performs preflight, executes queued container
  mutations, and records backups.
- `generation.py` and `generators/subgen.py` integrate with the external
  Subgen service.
- `validation.py` validates output artifacts before the mutation workflow
  commits them.

The persistent model lives in `marquee/models/subtitle_inventory.py` and the
related media/job tables in `marquee/models/`.

## Data Model

Important tables:

- `MediaFile` for one physical movie file
- `SubtitleInventory` for the latest cached snapshot of that file’s inventory
- `SubtitleTrack` for the individual embedded and external subtitle tracks
- `ManagedSubtitleAsset` and `ManagedSubtitleBinding` for restorable assets
- `MediaBackup` for pre-mutation file backups
- `MediaJob`, `MediaBatch`, and `MediaJobEvent` for durable job execution

This model is intentionally file-centric because subtitle operations mutate
containers and sidecars, not abstract movie records.

## API Surface

Representative routes:

- `GET /api/media-files/{media_file_id}/subtitles`
- `POST /api/media-files/{media_file_id}/subtitles/scan`
- `GET /api/media-files/{media_file_id}/subtitles/{track_id}/preview`
- `GET /api/media-files/{media_file_id}/subtitles/{track_id}/download`
- `POST /api/media-files/{media_file_id}/subtitle-plans`
- `POST /api/media-files/{media_file_id}/subtitles/{track_id}/extract`
- `POST /api/subtitles/scan-library`
- `GET|POST|PUT|DELETE /api/subtitle-policies/...`
- `POST /api/media-files/{media_file_id}/subtitle-generations`
- `POST /api/movies/{movie_id}/subtitle-generations`
- `GET /api/media-jobs`, `GET /api/media-jobs/{job_id}`, and related event and
  restore routes

`POST /api/media-files/{media_file_id}/subtitles/scan` is an inline forced
rescan that returns updated inventory rather than queuing a separate batch job.

## Policy, Mutation, And Safety Model

Policy evaluation is read-only until a user applies the result. When a mutation
is requested, `mutation.py` builds a plan, runs preflight checks, and executes
the actual container operations through the durable job platform.

Safety controls come from `subtitle_settings`:

- `SUBTITLE_HARDLINK_POLICY=block`
- `SUBTITLE_BACKUP_MODE=none`
- `SUBTITLE_EXTERNAL_DELETE_MODE=quarantine`
- `SUBTITLE_PROTECT_FORCED=true`
- `SUBTITLE_PROTECT_LAST_FULL_DIALOGUE=true`
- `SUBTITLE_NORMALIZE_TEXT_UTF8=true`

These defaults are conservative: they avoid destructive cleanup unless the file
and track state is clearly understood.

## Generation

AI subtitle generation is optional and depends on `SUBGEN_URL` being configured.
`marquee/core/subtitles/generators/subgen.py` translates local and remote paths,
submits work to the provider, polls for completion, and predicts the expected
output `.srt` file.

Representative generation knobs:

- `SUBGEN_URL`
- `SUBGEN_MODE=transcribe`
- `SUBGEN_TIMEOUT_MINUTES=120`
- `SUBGEN_POLL_SECONDS=30`

## Frontend

The main UI lives under `frontend/src/routes/audio-subs/` and
`frontend/src/lib/components/subtitles/`. It is organized around inventory,
policies, AI generation, and jobs rather than around raw container commands.

## Cross References

- `library.md` for media-file persistence and movie browse context
- `job-platform.md` for queueing, progress, and resource reservation behavior
- `timeline.md` for deferred batch-route work and broader future scope
