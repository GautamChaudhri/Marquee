# Subtitle Management

**Status:** Reconciled with the codebase on 2026-06-16.

Subtitle management is implemented as a backend feature around physical
`MediaFile` rows, subtitle inventories, durable media jobs, policies, and an
optional external Subgen provider.

## Implemented Modules

| Area | Code |
|---|---|
| Inventory/probe | `marquee/core/subtitles/probe.py`, `service.py`, `external.py`, `coverage.py` |
| Mutation planning/execution | `marquee/core/subtitles/mutation.py`, `adapters/` |
| Durable jobs | `marquee/core/media_jobs/manager.py`, `handlers.py` |
| Policies | `marquee/core/subtitles/policy.py`, `api/routes/subtitle_policies.py` |
| Generation | `marquee/core/subtitles/generation.py`, `generators/subgen.py` |
| Routes | `api/routes/subtitles.py`, `media_jobs.py`, `subtitle_generators.py` |
| Models | `SubtitleInventory`, `SubtitleTrack`, `ManagedSubtitleAsset`, `SubtitlePolicy`, `MediaJob`, `MediaBackup` |

## Implemented API Surface

### Library and Inventory

| Method | Endpoint | Current behavior |
|---|---|---|
| GET | `/api/library/movies` | Paginated movies with active `media_file_id` and cached subtitle coverage |
| GET | `/api/library/movies/{movie_id}` | Movie detail with media-file linkage |
| GET | `/api/library/series` | Paginated series |
| GET | `/api/library/series/{series_id}` | Series detail |
| GET | `/api/library/series/{series_id}/seasons` | Seasons for a series |
| GET | `/api/library/episodes/{episode_id}` | Episode detail |
| GET | `/api/media-files/{media_file_id}/subtitles` | Inventory, coverage, capabilities |
| POST | `/api/media-files/{media_file_id}/subtitles/scan` | Inline forced rescan; returns inventory directly |
| GET | `/api/media-files/{media_file_id}/subtitles/{track_id}/preview` | Text preview where possible |
| GET | `/api/media-files/{media_file_id}/subtitles/{track_id}/download` | Safe file response for downloadable tracks |
| POST | `/api/movies/{movie_id}/subtitles/inspect` | Movie convenience inspection |

Server-side library filters beyond pagination are `[PLANNED]`.

### Plans and Jobs

| Method | Endpoint | Current behavior |
|---|---|---|
| POST | `/api/media-files/{media_file_id}/subtitle-plans` | Create planned job for supported operations |
| POST | `/api/media-jobs/{job_id}/confirm` | Revalidate and queue a planned job |
| GET | `/api/media-jobs/{job_id}` | Job state/result/error |
| GET | `/api/media-jobs/{job_id}/events` | Persisted plus live SSE |
| POST | `/api/media-jobs/{job_id}/cancel` | Request cancellation |
| GET | `/api/media-jobs` | Job history |
| POST | `/api/media-jobs/{job_id}/restore` | Restore tracked backup when available |
| DELETE | `/api/media-jobs/{job_id}/backup` | Delete tracked backup |

Dedicated `/api/subtitle-batches/...` routes are not implemented yet and are
deferred for a later pass. Policy apply
creates a `MediaBatch` and child `MediaJob` rows instead.

### Policies and Generation

| Method | Endpoint | Current behavior |
|---|---|---|
| GET/POST | `/api/subtitle-policies` | List/create policies |
| GET/PUT/DELETE | `/api/subtitle-policies/{policy_id}` | Manage one policy |
| POST | `/api/subtitle-policies/{policy_id}/audit` | Dry-run policy selection |
| POST | `/api/subtitle-policies/{policy_id}/apply` | Queue policy jobs |
| GET | `/api/subtitle-generators` | Provider health/capabilities |
| POST | `/api/media-files/{media_file_id}/subtitle-generations` | Queue generation for a media file |
| POST | `/api/movies/{movie_id}/subtitle-generations` | Movie convenience generation |
| POST | `/api/webhooks/subgen` | Optional Subgen callback |

Subgen is external and disabled unless `SUBGEN_URL` is configured.

Current gauntlet target note, verified on 2026-06-16: Subgen is reachable at
`http://localhost:9000/status` and reports `Subgen 2026.06.3`, stable-ts
`2.19.1`, and faster-whisper `1.2.1` from Docker. Configure Marquee with
`SUBGEN_URL=http://localhost:9000` for generation tests on that host.

## Safety Model

Implemented safeguards:

- Path resolution through `resolve_media_file()`.
- File signatures for stale-plan detection.
- Hardlink protection by default.
- Full-file temp output and atomic replace for remux operations.
- Optional backups.
- Validation after remux before replace.
- Per-media-file job locks.
- Restart recovery marks crashed `running` jobs as `interrupted`.

## Deferred or Partial

- `[DEFERRED]` Dedicated subtitle batch CRUD/pause/resume/cancel routes.
- `[PLANNED]` Server-side library filter query parameters.
- `[PLANNED]` Radarr/Sonarr rescan commands after every mutation are described
  in older designs but not implemented in current clients.
- `[PLANNED]` Container conversion workflows.

## Safety Gates

- Real binary behavior (`ffmpeg`, `mkvmerge`, `mkvpropedit`) must be tested on
  lab copies for each target container before production mutation claims.
- Backup/restore semantics must be verified for hardlinked media before use on
  production libraries.
