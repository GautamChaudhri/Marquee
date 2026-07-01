# 17 — Subtitles Management Vertical Slice

**Status:** Prompt for implementation agent — compiled 2026-06-22.

> This document packages every feature request, existing backend capability, and
> research finding into a self-contained spec for another LLM agent to implement
> the subtitles management vertical slice frontend + settings. All backend
> mutation APIs already exist; the gap is the UI.

---

## 1. Project Context

Marquee is a media management dashboard. Frontend is SvelteKit under
`frontend/`, backend is FastAPI under `marquee/`. Both run in the same
Docker container (Node serves SvelteKit, uvicorn serves `/api/*`).

**Frontend entry points:**
- Design tokens: `frontend/design/MARQUEE_HANDOFF.md`
- API contract (real, not mock): `frontend/design/MARQUEE_API.md`
- Existing subtitles stub: `frontend/src/routes/subtitles/+page.svelte`
- Settings page (pipeline only): `frontend/src/routes/settings/+page.svelte`
- API types: `frontend/src/lib/api/types.ts`
- Mock data: `frontend/src/lib/api/mock.ts`
- Sidebar already has "Subtitles" link under Toolbox group

**Auth model:** SvelteKit server-side proxy holds `X-Api-Key`, injects on every
forwarded call. Build new API client functions in `src/lib/api/` that fetch
through SvelteKit's `load()` or `+server.ts`.

**Design system:** Dark-first with `[data-theme="light"]` override. CSS custom
properties defined in `MARQUEE_HANDOFF.md §1`. Svelte 5 (runes mode: `$state`,
`$derived`, `$props`, `$effect`). No Svelte 4 stores.

---

## 2. Backend — What Already Exists

### 2.1 Data Model (PostgreSQL via SQLAlchemy)

All tables are live and Alembic-migrated:

| Table | Purpose |
|---|---|
| `subtitle_inventories` | Per-media-file cache: container, duration, audio streams, coverage |
| `subtitle_tracks` | Individual tracks: embedded or external, language, codec, kind, flags |
| `subtitle_policies` | Language-cleanup rules (allowlist/blocklist) |
| `subtitle_policy_bindings` | Scope bindings (global → series → item) |
| `managed_subtitle_assets` | Cached subtitles for re-embed on file replacement |
| `managed_subtitle_bindings` | Asset → owner (movie/episode) bindings |
| `media_jobs` | Durable job queue (plan → confirm → execute) |
| `media_backups` | Pre-mutation backups |

### 2.2 Real API Routes (all working)

**Inventory (read-only):**
```
GET  /api/media-files/{media_file_id}/subtitles
     → { inventory_id, tracks: Track[], coverage, capabilities, audio_streams, file_signature }
POST /api/media-files/{media_file_id}/subtitles/scan   (inline forced rescan)
GET  /api/media-files/{media_file_id}/subtitles/{track_id}/preview
GET  /api/media-files/{media_file_id}/subtitles/{track_id}/download
POST /api/movies/{movie_id}/subtitles/inspect          (convenience: resolve + probe)
```

**Track shape (what the API returns per track):**
```json
{
  "id": "hash-string",
  "source": "embedded" | "external",
  "stream_index": 0,
  "tool_track_id": 3,
  "external_path": "/path/to/sub.srt",
  "codec": "subrip" | "ass" | "mov_text" | "hdmv_pgs_subtitle" | ...,
  "kind": "text" | "bitmap" | "teletext" | "unknown",
  "language_raw": "eng",
  "language_tag": "en",
  "language_source": "metadata" | "filename" | "user" | "unknown",
  "title": "English (SDH)",
  "is_default": false,
  "is_forced": false,
  "is_sdh": true,
  "is_commentary": false,
  "is_generated": false,
  "size_bytes": 45678,
  "per_track_actions": {
    "remove": { "available": true, "reason": null },
    "embed": { "available": true, "reason": null },
    "extract": { "available": true, "reason": null }
  }
}
```

**Mutation (plan → confirm → execute):**
```
POST /api/media-files/{media_file_id}/subtitle-plans     (201, creates a "planned" job)
     body: { operation: "subtitle_remove"|"subtitle_embed"|"subtitle_metadata",
             track_ids: ["..."], edits: [], backup: bool, allow_break: bool }
     → { job_id, status: "planned", before, after, warnings, capabilities, storage }

POST /api/media-jobs/{job_id}/confirm                    (revalidate + queue for worker)
GET  /api/media-jobs/{job_id}                            (job state/result/error)
GET  /api/media-jobs/{job_id}/events                     (SSE for live progress)
POST /api/media-jobs/{job_id}/cancel
POST /api/media-jobs/{job_id}/restore                    (restore backup)
DELETE /api/media-jobs/{job_id}/backup
GET  /api/media-jobs                                     (job history)
```

**Supported operations:**
- `subtitle_remove` — remove embedded tracks (mkvmerge remux) + delete/quarantine external sidecars
- `subtitle_embed` — embed external subtitle files into the container (mkvmerge)
- `subtitle_metadata` — edit track metadata (language tag, title, forced/default/SDH flags)

**IMPORTANT:** There is NO built-in "extract embedded to external" operation as a single mutation type. Extraction would need to be implemented as a new operation, or done client-side by:
1. Downloading the embedded track via a new extraction endpoint
2. Saving as external sidecar
3. Optionally removing the embedded track via `subtitle_remove`

**Policies:**
```
GET/POST    /api/subtitle-policies
GET/PUT/DEL /api/subtitle-policies/{policy_id}
POST        /api/subtitle-policies/{policy_id}/audit     (dry-run evaluation)
POST        /api/subtitle-policies/{policy_id}/apply     (202, queues batch removal jobs)
```

**Generation (Subgen):**
```
GET  /api/subtitle-generators                            (health + capabilities)
POST /api/media-files/{media_file_id}/subtitle-generations  (202, queue generation)
POST /api/movies/{movie_id}/subtitle-generations            (202, convenience)
```

**Library (has subtitle coverage data):**
```
GET /api/library/movies?page&page_size
    → items include: subtitle_coverage, media_file_id, subtitle_status
GET /api/library/movies/{movie_id}
    → detail with media_file_path, subtitle_coverage
```

### 2.3 SubtitleSettings (backend config)

From `marquee/core/subtitles/config.py` — all env-variable driven:
```
SUBGEN_URL, SUBGEN_PROFILE_NAME, SUBGEN_MODEL_LABEL,
SUBGEN_MODE (transcribe|translate),
SUBGEN_LOCAL_PATH_PREFIX, SUBGEN_REMOTE_PATH_PREFIX,
SUBGEN_CALLBACK_TOKEN, SUBGEN_TIMEOUT_MINUTES, SUBGEN_POLL_SECONDS,
SUBTITLE_PREFERRED_LANGUAGES (default: ["en"]),
SUBTITLE_HARDLINK_POLICY, SUBTITLE_BACKUP_MODE, SUBTITLE_EXTERNAL_DELETE_MODE
```

Currently there is NO `/api/settings` endpoint for subtitle settings. The
existing settings route only serves pipeline config (`/api/config/pipeline`).
You will need to either build a new settings endpoint or integrate with the
existing one.

---

## 3. User Feature Requirements

### 3.1 Movie List — At-a-Glance Subtitle Info

On the main subtitles page (`/subtitles`), show a list of all movies. Each row
should indicate:

- **Has embedded subtitles?** (yes/no, count of embedded tracks)
- **Has external subtitles?** (yes/no, count of external tracks)
- **Languages present** — compact display of language codes/flags for both
  embedded and external (e.g., `en, fr, ja`)
- **Subtitle status** — ok / gap (missing preferred language) / none
- **Container type** — mkv / mp4 (affects what operations are possible)
- **Media file path** (maybe truncated)

This mirrors the existing `FilmList` component pattern but focused on subtitle
data. The list endpoint already returns `subtitle_coverage` and
`subtitle_status` on each movie.

### 3.2 Movie Detail — Click to Expand

Clicking a movie should show (inline expand or navigate to detail view):

- **All tracks table** with columns:
  - Source (Embedded / External badge)
  - Language (display name + code)
  - Codec (subrip, ass, mov_text, hdmv_pgs_subtitle, etc.)
  - Kind (text / bitmap)
  - Flags (forced, SDH, commentary, default, generated)
  - Size
  - Track index / stream number
- **Coverage summary** — which languages have full, forced-only, or no coverage
- **Audio streams** present in the file
- **Container info** — format, what mutation capabilities are available
- **External sidecar files** listed by path

### 3.3 Subtitle Manipulation Operations

On the detail view, allow the user to perform these operations:

#### A. Remove Embedded Subtitles
- Select which tracks to remove
- Options: single track, multiple (checkboxes), all embedded, or auto-select
  by language whitelist/blacklist
- Creates a `subtitle_remove` plan → confirm → execute

#### B. Extract Embedded → External (keep or delete embedded)
**Requires a NEW backend operation or a multi-step flow:**
1. Download/extract the embedded subtitle track to an external `.srt` file
   next to the media file
2. Name it following Plex/Jellyfin conventions (see §4)
3. Optionally remove the embedded track afterward (via `subtitle_remove`)
- Selection: single, multiple, all, or language-based auto-select
- Default behavior: **delete the original embedded track after extraction**

#### C. Embed External → Internal (keep or delete external)
- Select external sidecar tracks
- Creates a `subtitle_embed` plan → confirm → execute
- Optionally delete the external sidecar after embedding (default: delete)
- Selection: single, multiple, all, or language-based

#### D. Track Selection UI
For every operation, provide:
- **Manual selection**: checkboxes next to each track
- **"Select All"** button (all embedded or all external depending on context)
- **Language whitelist**: text input of language codes to include
  (e.g., `en, ja, fr`)
- **Language blacklist**: text input of language codes to exclude
- **Quick-select by flags**: e.g., "select all forced", "select all SDH"

### 3.4 External Subtitle Naming Conventions

When generating or extracting subtitles, the filename MUST follow conventions
that both Plex and Jellyfin recognize:

**Format:** `{VideoBasename}.{language}.{flags...}.{ext}`

Where:
- `{VideoBasename}` = the media filename without extension
  (e.g., `Movie.Name.2024`)
- `{language}` = ISO-639-1 2-letter code (`en`, `fr`, `ja`) — BOTH Plex and
  Jellyfin accept this. ISO-639-2/B 3-letter (`eng`, `fre`, `jpn`) also works
  but 2-letter is more universal.
- `{flags}` = optional, dot-separated: `forced`, `sdh`, `default`, `hi`
  (hearing impaired, same as SDH), `commentary`
- Order of flags after language is flexible; Plex/Jellyfin parse positionally
- `{ext}` = `.srt` (preferred, universal), `.ass`, `.ssa`, `.vtt`

**Examples:**
```
Movie.Name.2024.mkv                        ← video file
Movie.Name.2024.en.srt                     ← English
Movie.Name.2024.en.forced.srt              ← English forced
Movie.Name.2024.en.sdh.srt                 ← English SDH
Movie.Name.2024.fr.srt                     ← French
Movie.Name.2024.ja.srt                     ← Japanese
Movie.Name.2024.en.forced.sdh.subgen.srt   ← English forced+SDH, AI-generated
```

**Key notes:**
- Plex: supports ISO-639-1 (2-letter) or ISO-639-2/B (3-letter). Flags like
  `.en.forced.srt`, `.en.sdh.srt`, `.en.default.srt`.
- Jellyfin: same, plus combined flags like `Film.en.sdh.srt`,
  `Film.default.en.forced.ass`. Also supports audio track suffix naming
  (not relevant here).
- Both: the subtitle file must be in the same directory as the video file
  (or in a subdirectory for Plex with individual movie folders).
- The existing backend code in `marquee/core/subtitles/external.py` already
  parses these exact token patterns (forced, sdh, hi, cc, commentary, subgen,
  ai, generated, whisper) from filenames.

### 3.5 Integrated Subgen UI

Build an integrated UI to pass movies to Subgen for subtitle generation:

#### Subgen Generation Panel
- **Movie selector** — from the movie list, select one to generate for
- **Target language** — dropdown of supported languages (see Subgen supported
  list below) or "auto-detect"
- **Mode** — transcribe (same language) or translate (to English)
- **Output format** — external (sidecar `.srt`) or embedded
- **Device** — CPU or CUDA (read from subgen status endpoint)
- **Model** — display what model subgen is configured with (from status)
- **Generate button** — submits to `/api/media-files/{id}/subtitle-generations`
- **Progress** — SSE-based progress tracking through the job lifecycle
- **Result** — show output path, validation result, cue count

#### Subgen Capabilities (read from `/api/subtitle-generators`):
- Provider health (online/offline + version)
- Supported modes (transcribe/translate)
- Whether language hint is supported
- Transport type (shared_path)

#### Subgen Supported Languages (from OpenAI Whisper):
Afrikaans, Arabic, Armenian, Azerbaijani, Belarusian, Bosnian, Bulgarian,
Catalan, Chinese, Croatian, Czech, Danish, Dutch, English, Estonian, Finnish,
French, Galician, German, Greek, Hebrew, Hindi, Hungarian, Icelandic,
Indonesian, Italian, Japanese, Kannada, Kazakh, Korean, Latvian, Lithuanian,
Macedonian, Malay, Marathi, Maori, Nepali, Norwegian, Persian, Polish,
Portuguese, Romanian, Russian, Serbian, Slovak, Slovenian, Spanish, Swahili,
Swedish, Tagalog, Tamil, Thai, Turkish, Ukrainian, Urdu, Vietnamese, Welsh.

### 3.6 Settings Page — Subgen Connection

Add a Subgen section to the settings page (or a new tab in `/settings`):

#### Connection Settings
- **Subgen URL** — text input (e.g., `http://localhost:9000`)
- **Test Connection button** — calls `GET {url}/status`, shows result
  (version, healthy, model info)
- **Connection status indicator** — green/red dot with detail

#### Path Mapping
- **Local path prefix** — what Marquee sees (e.g., `/mnt/PLUNDER/Media/Movies`)
- **Remote path prefix** — what Subgen sees the same path as
  (e.g., `/movies`)
- **Example translation** — show live preview: `/mnt/PLUNDER/Media/Movies/Film.mkv`
  → `/movies/Film.mkv`
- **Enable path mapping** — checkbox toggle

#### External File Naming
- **Language code format** — dropdown: ISO-639-1 (en) / ISO-639-2/B (eng) /
  ISO-639-2/T (eng) / Native name (English)
- **Include subgen tag** — checkbox: append `.subgen` to filename
- **Include model tag** — checkbox: append model name to filename
- **SDH flag** — use `sdh` or `hi` (hearing impaired)
- **Preview** — live example filename based on current settings

#### Generation Defaults
- **Default mode** — transcribe / translate
- **Default output** — external / embedded
- **Default language** — for auto-selection when none specified
- **Preferred languages** — list for coverage gap detection (already in config
  as `SUBTITLE_PREFERRED_LANGUAGES`)

#### Mutation Safety Settings (existing backend config, expose in UI)
- **Hardlink policy** — block / allow_break
- **Backup mode** — none / keep_original
- **External delete mode** — quarantine / delete
- **Plan TTL** — minutes before plan expires
- **Preview max cues** — for text preview endpoint

---

## 4. Subgen Integration Details

### 4.1 Architecture
Subgen is a separate Docker container (`mccloud/subgen:latest`) running at
`localhost:9000` in the current deployment. Marquee communicates with it over
HTTP. No shared filesystem needed for Bazarr mode, but for path-based batch
mode (which Marquee uses), the media paths must be accessible to both.

### 4.2 Endpoints Marquee Uses
- `GET /status` — health check, returns version, model, device
- `POST /batch` — submit a file for transcription
  - Body: `{"path": "/absolute/path/to/video.mkv", "forceLanguage": "en"}`
  - Subgen generates an `.srt` next to the video, then optionally sends a
    webhook completion callback
- `POST /v1/audio/transcriptions` — OpenAI-compatible API (not used by Marquee,
  but available)

### 4.3 Path Translation
Subgen's container may mount media at a different path than Marquee.
The backend already has `translate_local_to_remote()` in
`marquee/core/subtitles/generators/subgen.py` that maps:
- `SUBGEN_LOCAL_PATH_PREFIX` → `SUBGEN_REMOTE_PATH_PREFIX`
- Example: if Marquee sees `/mnt/PLUNDER/Media/Movies` and Subgen sees
  `/movies`, configure:
  - Local prefix: `/mnt/PLUNDER/Media`
  - Remote prefix: `` (empty)
  - Result: `/mnt/PLUNDER/Media/Movies/Film.mkv` → `/Movies/Film.mkv`

### 4.4 Filename Reconciliation Gap
The backend `expected_output_srt()` generates a simple filename like
`Movie.en.srt`. However, Subgen's own output filename is controlled by its
environment variables (`SUBTITLE_LANGUAGE_NAME`, `SUBTITLE_LANGUAGE_NAMING_TYPE`,
`SHOW_IN_SUBNAME_SUBGEN`, `SHOW_IN_SUBNAME_MODEL`) and may produce names like
`Movie.eng.subgen.medium.srt`. The `reconcile()` method has a fallback glob
(`{media.stem}*.srt`) that catches these, but the naming mismatch means Marquee
can't precisely predict the output path. When implementing the settings page,
make sure the naming settings in Marquee's UI correspond to Subgen's actual
configuration so the two stay in sync.

### 4.5 Completion Detection
The backend polls for the output `.srt` file on the filesystem
(`SUBGEN_POLL_SECONDS` interval, up to `SUBGEN_TIMEOUT_MINUTES`).
Subgen also supports an optional completion webhook (`WEBHOOK_URL_COMPLETED`)
which posts JSON to Marquee's `/api/webhooks/subgen` — this is an optimization
that short-circuits polling.

### 4.6 Subgen Environment Variables (for reference)
The full list is in the GitHub README (https://github.com/McCloudS/subgen).
Key ones the Marquee settings UI should expose or at least document:
- `TRANSCRIBE_DEVICE` (cpu/cuda)
- `WHISPER_MODEL` (tiny/base/small/medium/large-v3/large-v3-turbo)
- `TRANSCRIBE_OR_TRANSLATE` (transcribe/translate)
- `SUBTITLE_LANGUAGE_NAME` (default `aa` — language code for output filename)
- `SUBTITLE_LANGUAGE_NAMING_TYPE` (ISO_639_1/ISO_639_2_B/ISO_639_2_T/NAME/NATIVE)
- `SHOW_IN_SUBNAME_SUBGEN` (True/False — append `.subgen` to filename)
- `SHOW_IN_SUBNAME_MODEL` (True/False — append model name)
- `CONCURRENT_TRANSCRIPTIONS` (default 2)
- `USE_PATH_MAPPING`, `PATH_MAPPING_FROM`, `PATH_MAPPING_TO`
- `SKIP_IF_TARGET_SUBTITLES_EXIST`, `SKIP_IF_INTERNAL_SUBTITLES_LANGUAGE`
- `PREFERRED_AUDIO_LANGUAGES`

---

## 5. Frontend Implementation Plan

### 5.1 Page Architecture

Transform the current stub at `frontend/src/routes/subtitles/+page.svelte`
into a full feature page with tabs:

```
/subtitles
├── Inventory tab   — movie list with at-a-glance subtitle info (default)
├── Policies tab    — language-cleanup policy CRUD
├── Generation tab  — Subgen-integrated generation UI
└── Jobs tab        — job queue/history
```

### 5.2 New Components Needed

| Component | Purpose |
|---|---|
| `SubtitleMovieList` | Table of movies with subtitle status columns |
| `SubtitleMovieRow` | Single movie row with expand/collapse |
| `SubtitleMovieDetail` | Expanded view: track table, coverage, actions |
| `TrackTable` | Table of subtitle tracks with checkboxes |
| `TrackRow` | Single track row with all metadata |
| `TrackSelector` | Selection UI: whitelist/blacklist, select all, flag filters |
| `OperationPanel` | Choose operation type, configure options, submit |
| `SubgenPanel` | Generation form: language, mode, target |
| `SubgenStatus` | Provider health indicator |
| `PolicyEditor` | CRUD form for subtitle policies |
| `PolicyList` | Table of existing policies |
| `JobList` | Live job queue from `/api/media-jobs` |
| `JobRow` | Job progress bar + actions (cancel, restore) |
| `SubtitleSettings` | Settings section for subgen connection + naming |

### 5.3 New API Client Modules

Create under `frontend/src/lib/api/`:
- `subtitles.ts` — inventory, scan, preview, download, plans, inspect
- `subtitle-policies.ts` — CRUD, audit, apply
- `subtitle-generators.ts` — list generators, submit generation
- `media-jobs.ts` — list, get, cancel, restore, SSE events
- `settings.ts` — extend with subtitle/subgen settings (or new module)

### 5.4 TypeScript Types

Extend `frontend/src/lib/api/types.ts` with:
```typescript
interface SubtitleTrack {
  id: string;
  source: 'embedded' | 'external';
  stream_index: number | null;
  tool_track_id: number | null;
  codec: string | null;
  kind: 'text' | 'bitmap' | 'teletext' | 'unknown';
  language_tag: string;
  display_name: string;       // computed on frontend
  title: string | null;
  is_default: boolean;
  is_forced: boolean;
  is_sdh: boolean;
  is_commentary: boolean;
  is_generated: boolean;
  size_bytes: number | null;
  per_track_actions: {
    remove: { available: boolean; reason: string | null };
    embed: { available: boolean; reason: string | null };
    extract: { available: boolean; reason: string | null };
  };
}

interface SubtitleInventory {
  inventory_id: number;
  tracks: SubtitleTrack[];
  coverage: Record<string, unknown>;
  capabilities: Record<string, unknown>;
  audio_streams: Record<string, unknown>[];
  file_signature: string | null;
}

interface SubtitleGenerator {
  id: string;
  name: string;
  provider: string;
  mode: 'transcribe' | 'translate';
  model_label: string;
  healthy: boolean;
  version: string | null;
  detail: string | null;
  supports_language_hint: boolean;
  supports_per_request_model: boolean;
  supports_percent_progress: boolean;
  transport: string;
}

// ... etc for policies, jobs, plans
```

### 5.5 Data Flow

1. **Movie list**: Client calls `GET /api/library/movies` (already exists) →
   `MovieListItem.subtitle_coverage` + `subtitle_status` drive the at-a-glance
   display
2. **Movie detail**: Client calls `GET /api/media-files/{media_file_id}/subtitles`
   → full `SubtitleInventory` with all tracks
3. **Operations**: Client calls `POST /api/media-files/{media_file_id}/subtitle-plans`
   → receives `job_id` → confirm → watch SSE for progress
4. **Generation**: Client calls `GET /api/subtitle-generators` for health,
   then `POST /api/media-files/{id}/subtitle-generations` to queue
5. **Settings**: Need to build or extend a settings endpoint to read/write
   the env-driven `SubtitleSettings`

---

## 6. Implementation Order

### Phase 1 — Read-Only UI (no mutations)
1. Build `SubtitleMovieList` + `SubtitleMovieRow` — list movies with subtitle data
2. Build `SubtitleMovieDetail` + `TrackTable` — expand movie to see tracks
3. Build API client (`subtitles.ts`) for inventory reads
4. Test with real backend data

### Phase 2 — Subgen Integration
1. Build `SubgenPanel` + `SubgenStatus`
2. Build `subtitle-generators.ts` API client
3. Wire up generation submission + SSE progress
4. Build `JobList` + `JobRow` for tracking generation jobs

### Phase 3 — Mutation Operations
1. Build `TrackSelector` — selection UI
2. Build `OperationPanel` — operation type + options
3. Wire up plan creation → confirmation → SSE progress
4. Handle the "extract embedded → external" flow (determine if new backend
   endpoint needed or multi-step client-side)

### Phase 4 — Policies
1. Build `PolicyList` + `PolicyEditor`
2. Build `subtitle-policies.ts` API client
3. Wire up audit (dry-run) and apply flows

### Phase 5 — Settings
1. Design Subgen settings UI section
2. Build or extend settings endpoint on backend
3. Wire up connection test, path mapping, naming options

---

## 7. Key Technical Notes

### 7.1 Media File ID Resolution
The subtitle API routes use `media_file_id`, not `movie_id`. Movies in the
library list already include `media_file_id`. The convenience endpoint
`POST /api/movies/{movie_id}/subtitles/inspect` resolves the movie to its
media file automatically.

### 7.2 Plan → Confirm → Execute Flow
Subtitle mutations follow a strict lifecycle:
1. **Plan**: `POST /api/media-files/{id}/subtitle-plans` creates a `planned` job
   with a before/after diff, warnings, and capabilities. Plans expire after
   `SUBTITLE_PLAN_TTL_MINUTES` (default 15 min).
2. **Confirm**: `POST /api/media-jobs/{job_id}/confirm` re-validates (checks
   file signature hasn't changed, disk space, hardlinks) and queues for worker.
3. **Execute**: The durable worker picks up the queued job, runs ffmpeg/mkvmerge,
   validates output, replaces original atomically, rescans inventory.
4. **Monitor**: SSE events at `GET /api/media-jobs/{job_id}/events` show progress.

### 7.3 Safety Features (inform the UI)
- Hardlinks are protected by default (`SUBTITLE_HARDLINK_POLICY=block`)
- Backups are opt-in (`SUBTITLE_BACKUP_MODE=none` by default)
- External subtitle deletions go to quarantine by default, not permanent delete
- File signatures detect changes between plan and execution
- Per-media-file job locks prevent concurrent mutations

### 7.4 Missing Backend Pieces
The following do NOT exist yet and would need backend work:
- **Extract embedded → external endpoint.** The backend has `subtitle_extract`
  as a capability flag on tracks but no mutation operation for it. Options:
  (a) Add a new `subtitle_extract` operation, or (b) Client-side: download
  track + write to sidecar + optionally call `subtitle_remove`.
- **Subtitle settings CRUD endpoint.** Currently settings are env-var driven
  with no read/write API. The existing `/api/config/pipeline` pattern (GET/PUT
  with knob groups) can be replicated.
- **Bulk operations.** No batch endpoint for subtitle mutations (deferred in
  design docs). Policy `apply` creates batch jobs internally though.

### 7.5 Container Capabilities (for UI logic)
From `marquee/core/subtitles/capabilities.py`:
- **MKV (Matroska)**: Full support — can remove embedded, embed text+bitmap,
  edit metadata, extract. Uses mkvmerge/mkvextract.
- **MP4**: Limited — can only embed text as mov_text (no ASS styling, no
  bitmap), can extract. Remove embedded from MP4 is limited.
- **Other containers**: Read-only inventory.

The UI should show available operations based on container type.

---

## 8. Design & UX Guidelines

### 8.1 Visual Style
Follow `MARQUEE_HANDOFF.md §1` design tokens exactly:
- Dark background: `--ink` (#0c0d11), panels: `--panel` (#15171e)
- Text: `--text` (#e8e9ef), muted: `--muted` (#8a909f)
- Accent: `--gold` (#ffc24b) for actions/selections
- Semantic: `--good` (green), `--warn` (amber), `--bad` (red), `--info` (blue)

### 8.2 Component Patterns
- Use existing `TabBar`, `SectionHeader`, `ConfirmDialog`, `Toast` components
- Use `PosterThumb` for movie thumbnails in list
- Match existing table styles from `FilmList.svelte`
- SSE pattern: use native `EventSource` with connection management

### 8.3 UX Behaviors
- **At-a-glance**: Language codes shown as compact pills/badges
- **Expand**: Click movie row to expand inline detail (not navigate away)
- **Selection**: Checkbox selection with shift-click range select
- **Progress**: Inline progress bar during mutations with SSE updates
- **Confirmation**: Always show before/after diff before confirming mutations
- **Errors**: Show friendly error messages from backend (the backend already
  returns structured error codes)

---

## 9. Testing

- Use `frontend/src/lib/api/mock.ts` pattern to build mock subtitle data
  for development without a running backend
- The existing `tests/test_subtitles.py` tests the backend pure logic —
  frontend tests should mock the API layer
- Test the naming convention formatter with various language/flag combinations

---

## 10. References

- Backend subtitle design doc: `design/more-features/03-subtitle-management.md`
- Backend durable jobs: `design/15-durable-job-platform.md`
- Frontend handoff: `frontend/design/MARQUEE_HANDOFF.md`
- Frontend API reconciliation: `frontend/design/MARQUEE_API.md`
- Subgen repo: https://github.com/McCloudS/subgen
- Plex subtitle naming: https://support.plex.tv/articles/200471133-adding-local-subtitles-to-your-media/
- Jellyfin movie naming: https://jellyfin.org/docs/general/server/media/movies/
- Plex movie naming: https://support.plex.tv/articles/naming-and-organizing-your-movie-media-files/
