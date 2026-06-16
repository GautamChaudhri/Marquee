# Letterbox Cropping

**Status:** Reconciled with the codebase on 2026-06-16.

Marquee implements movie-only letterbox detection and MKV pixel-crop tag
application. Detection is read-only; applying/removing tags goes through
`LetterboxService`.

## Implemented Scope

Implemented:

- movie resolution prefilter;
- single-movie synchronous detection;
- batch detection with SSE;
- cropdetect backend and ImageMagick trim backend;
- before/after preview WebP frames;
- MKV crop-tag apply/remove;
- ignore/review state;
- tag-drift heal.

Not implemented:

- TV episode/season cascade;
- automatic apply of high-confidence results in the route flow;
- MP4-to-MKV conversion.

## Core Modules

| Area | Code |
|---|---|
| Detection | `marquee/media/letterbox_detect.py` |
| Batch manager/SSE | `marquee/media/letterbox_manager.py` |
| Preview frames | `marquee/media/letterbox_preview.py` |
| File mutation | `marquee/core/letterbox_service.py` |
| Heal | `marquee/core/letterbox_heal.py` |
| Routes | `marquee/api/routes/letterbox.py` |
| Models | `LetterboxState`, `LetterboxEvent` |

## API Surface

All routes are under `/api/letterbox`.

| Method | Endpoint | Current behavior |
|---|---|---|
| GET | `/status` | Counts, binary availability, honored-by notes, active batch |
| GET | `/candidates` | Paginated letterbox states; optional status/confidence/sort |
| GET | `/movies/find-candidates` | Resolution prefilter and state seeding |
| GET | `/movies/{movie_id}` | Inspection detail and preview URLs |
| POST | `/movies/{movie_id}/detect` | Synchronous detect; returns updated state |
| POST | `/detect` | Batch detect; returns `202` with `job_id` |
| GET | `/jobs/{job_id}/events` | Batch-detect SSE |
| GET | `/movies/{movie_id}/preview?minute=<int>&mode=before|after` | Preview frame |
| POST | `/movies/{movie_id}/apply` | Apply tags with recommended or provided crop |
| POST | `/apply` | Batch apply |
| POST | `/movies/{movie_id}/remove` | Remove tags |
| POST | `/movies/{movie_id}/ignore` | Mark reviewed/skipped |
| POST | `/heal` | Re-apply drifted crop tags when verifiable |

## Safety Model

- Only MKV/Matroska files are eligible for writes.
- Paths are validated against configured media roots.
- Files must exist, be writable, and have a video track.
- Per-file locks prevent concurrent tag writes.
- Detection never writes to media files.
- `mkvmerge -J` verification is best-effort; DB state remains the source of
  truth when the installed mkvtoolnix cannot report crop fields.

## Config

Implemented in `marquee/config.py`:

- `LETTERBOX_ENABLED`
- detection method, sample, cropdetect, trim, spread/asymmetry knobs;
- binary names;
- `LETTERBOX_MAX_PARALLEL`;
- `LETTERBOX_AUTO_APPLY_HIGH` (stored, but route auto-apply is not active);
- `LETTERBOX_ASYMMETRIC`;
- heal enable/interval knobs.

## Bulk-Use Safety Gates

- Verify the deployment host's mkvtoolnix behavior before relying on
  `mkvmerge -J` to report pixel-crop properties.
- Check cropdetect threshold behavior on HDR/PQ/HLG samples before bulk use.
- Smoke-test apply/remove on lab copies before using against production media.
