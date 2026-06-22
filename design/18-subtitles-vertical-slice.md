# 18 — Subtitles Management Vertical Slice

**Status:** Build prompt for implementing agent — compiled 2026-06-22.

> This is a self-contained spec for building the subtitles management frontend
> vertical slice. All backend mutation APIs already exist; the gap is the UI.
> Hand this entire document to an LLM agent and say "build this."

---

## 1. Project Context

Marquee is a self-hosted media management dashboard. Frontend is SvelteKit
under `frontend/`, backend is FastAPI under `marquee/`. Both run in the same
Docker container (Node serves SvelteKit, uvicorn serves `/api/*`).

**Key files you'll work with:**
- Design tokens & component spec: `frontend/design/MARQUEE_HANDOFF.md`
- Real API contract (not mock): `frontend/design/MARQUEE_API.md`
- Existing subtitles stub: `frontend/src/routes/subtitles/+page.svelte`
- Existing settings page: `frontend/src/routes/settings/+page.svelte`
- Sidebar nav: `frontend/src/lib/components/Sidebar.svelte`
- API types: `frontend/src/lib/api/types.ts`
- Mock data: `frontend/src/lib/api/mock.ts`

**Auth model:** SvelteKit server-side proxy holds `X-Api-Key`, injects on
every forwarded call via `load()` or `+server.ts`. Never put the key in
browser code.

**Design system:** Dark-first with `[data-theme="light"]` override. CSS
custom properties defined in `MARQUEE_HANDOFF.md §1`. Svelte 5 (runes:
`$state`, `$derived`, `$props`, `$effect`). No Svelte 4 stores.

**User prefs from project memory:**
- Minimal UI: no floating badges, rank only in captions
- Colored accent lines by stack_id, 8-color palette
- Toggle views with localStorage persistence
- Prefers parallel downloads, root-cause fixes

---

## 2. Backend — What Already Exists

### 2.1 Data Model (PostgreSQL via SQLAlchemy, all live)

| Table | Purpose |
|---|---|
| `subtitle_inventories` | Per-media-file cache: container, duration, audio streams, coverage summary |
| `subtitle_tracks` | Individual tracks: embedded or external, language, codec, kind, flags |
| `subtitle_policies` | Language-cleanup rules (allowlist/blocklist) |
| `subtitle_policy_bindings` | Scope bindings (global → series → item) |
| `managed_subtitle_assets` | Cached subtitles for re-embed on file replacement |
| `managed_subtitle_bindings` | Asset → owner (movie/episode) bindings |
| `media_jobs` | Durable job queue (plan → confirm → execute) |
| `media_backups` | Pre-mutation backups |

### 2.2 Real API Routes

**Inventory (read-only):**
```
GET  /api/media-files/{media_file_id}/subtitles
     → { inventory_id, tracks: Track[], coverage, capabilities, audio_streams, file_signature }
POST /api/media-files/{media_file_id}/subtitles/scan          (inline forced rescan)
GET  /api/media-files/{media_file_id}/subtitles/{track_id}/preview
GET  /api/media-files/{media_file_id}/subtitles/{track_id}/download
POST /api/movies/{movie_id}/subtitles/inspect                 (convenience: resolve movie to media file + probe)
```

**Track shape (what `/subtitles` returns per track):**
```
{
  id: "hash-string",
  source: "embedded" | "external",
  stream_index: 0,
  tool_track_id: 3,
  external_path: "/path/to/sub.srt" | null,
  codec: "subrip" | "ass" | "mov_text" | "hdmv_pgs_subtitle" | ...,
  kind: "text" | "bitmap" | "teletext" | "unknown",
  language_raw: "eng",
  language_tag: "en",
  language_source: "metadata" | "filename" | "user" | "unknown",
  title: "English (SDH)" | null,
  is_default: false,
  is_forced: false,
  is_sdh: true,
  is_commentary: false,
  is_generated: false,
  size_bytes: 45678 | null,
  per_track_actions: {
    remove: { available: true, reason: null },
    embed: { available: true, reason: null },
    extract: { available: true, reason: null }
  }
}
```

**Mutation (plan → confirm → execute lifecycle):**
```
POST /api/media-files/{media_file_id}/subtitle-plans     → 201
     body: { operation: "subtitle_remove"|"subtitle_embed"|"subtitle_metadata",
             track_ids: ["id1","id2"], edits: [], backup: bool, allow_break: bool }
     → { job_id, status: "planned", before, after, warnings, capabilities, storage }

POST /api/media-jobs/{job_id}/confirm                    (revalidate + queue)
GET  /api/media-jobs/{job_id}                            (job state + result)
GET  /api/media-jobs/{job_id}/events                     (SSE for live progress)
POST /api/media-jobs/{job_id}/cancel
POST /api/media-jobs/{job_id}/restore                    (restore backup)
DELETE /api/media-jobs/{job_id}/backup
GET  /api/media-jobs                                     (history)
```

**Supported operations and what they do:**
- `subtitle_remove` — remove embedded tracks (mkvmerge remux) + delete/quarantine external sidecars
- `subtitle_embed` — embed external `.srt`/`.ass` files into the container (mkvmerge)
- `subtitle_metadata` — edit track metadata flags (language tag, title, forced/default/SDH)

**IMPORTANT GAP:** There is NO "extract embedded to external" as a single
operation. The UI must either (a) implement a multi-step client flow: download
embedded content → write sidecar → optionally `subtitle_remove` the source, or
(b) coordinate with a backend change to add a `subtitle_extract` operation.

**Policies:**
```
GET/POST    /api/subtitle-policies
GET/PUT/DEL /api/subtitle-policies/{policy_id}
POST        /api/subtitle-policies/{policy_id}/audit     (dry-run: evaluate against selection)
POST        /api/subtitle-policies/{policy_id}/apply     (202, queues batch removal jobs)
```

**Generation (Subgen):**
```
GET  /api/subtitle-generators                            (health + capabilities)
POST /api/media-files/{media_file_id}/subtitle-generations  (202, body: { language_hint, output })
POST /api/movies/{movie_id}/subtitle-generations            (202, convenience)
```

**Library (has subtitle data per movie):**
```
GET /api/library/movies?page&page_size
    → items[] each have: subtitle_coverage, media_file_id, subtitle_status
GET /api/library/movies/{movie_id}
    → also has media_file_path
```

### 2.3 Backend Config (env-driven, no API yet)
```
SUBGEN_URL, SUBGEN_PROFILE_NAME, SUBGEN_MODEL_LABEL,
SUBGEN_MODE (transcribe|translate),
SUBGEN_LOCAL_PATH_PREFIX, SUBGEN_REMOTE_PATH_PREFIX,
SUBGEN_CALLBACK_TOKEN, SUBGEN_TIMEOUT_MINUTES, SUBGEN_POLL_SECONDS,
SUBTITLE_PREFERRED_LANGUAGES (default: ["en"]),
SUBTITLE_HARDLINK_POLICY, SUBTITLE_BACKUP_MODE, SUBTITLE_EXTERNAL_DELETE_MODE
```
**GAP:** There is NO `/api/settings` endpoint for subtitle settings. You need
to either build one or integrate with the existing pipeline config endpoint
pattern.

---

## 3. What To Build

### 3.1 Movie List Page — At-a-Glance Subtitle Info

The `/subtitles` page should show a table of all movies. Each row displays:

- **Thumbnail** (use existing `PosterThumb`)
- **Title + year**
- **Embedded subtitle count** — badge with count, "None" if zero
- **External subtitle count** — badge with count
- **Languages present** — compact pills showing language codes for both
  embedded and external (e.g., `en fr ja`)
- **Subtitle status** — ok / gap / none indicator
- **Container** — mkv / mp4 (affects available operations)
- **Expand chevron** — click to reveal detail

Data source: `GET /api/library/movies` already returns `subtitle_coverage`,
`subtitle_status`, and `media_file_id` on each item.

### 3.2 Movie Detail — Expand Inline

When clicking a movie row, expand inline (don't navigate away) to show:

- **Track table** with columns:
  - Source (Embedded badge or External badge)
  - Language (display name like "English" + code like "en")
  - Codec (subrip, ass, mov_text, hdmv_pgs_subtitle)
  - Kind (text or bitmap)
  - Flags (forced, SDH, commentary, default, generated — as colored pills)
  - Size (human-readable)
  - Checkbox (for selection)
- **Coverage summary** — which languages have full/forced-only/no coverage
- **Audio streams** — language + channel count for each
- **Container info** — format + mutation capabilities
- **External sidecar files** listed by path

Data source: `GET /api/media-files/{media_file_id}/subtitles`

### 3.3 Subtitle Manipulation Operations

On the expanded detail view, provide these operations:

#### A. Remove Embedded Subtitles
- Select tracks to remove → create plan → confirm → execute
- Backend operation: `subtitle_remove`

#### B. Extract Embedded → External
- Select embedded tracks → extract content to `.srt` sidecar next to media
- Name the output file following Plex/Jellyfin conventions (see §4)
- Option: keep or delete the original embedded track after extraction
- **Default: delete original** (clean extraction)
- Since no single "extract" backend operation exists, use a multi-step flow:
  1. Get embedded track content (may need new backend endpoint)
  2. Write `.srt` file to media directory
  3. Optionally call `subtitle_remove` on the source track

#### C. Embed External → Internal
- Select external sidecar tracks
- Create `subtitle_embed` plan → confirm → execute
- Option: keep or delete the external sidecar after embedding
- **Default: delete the external original**

#### D. Track Selection UI (for all operations)
Provide these selection methods:
- **Manual**: checkboxes next to each track
- **Select All**: all embedded or all external (context-dependent)
- **Select None**: clear selection
- **Language whitelist**: text input — comma-separated language codes to include
  (e.g., `en, ja, fr`)
- **Language blacklist**: text input — comma-separated codes to exclude
- **Quick flag selects**: "Select all Forced", "Select all SDH", "Select all
  Commentary"

### 3.4 Integrated Subgen UI

#### Generation Panel (on the subtitles page or per-movie)
- **Movie selector** — pick from list or use context of currently expanded movie
- **Target language** — dropdown of supported languages with "Auto-detect" option
- **Mode** — transcribe (same language) or translate (to English)
- **Output** — external sidecar `.srt` or embed in container
- **Generate button** → calls `POST /api/movies/{id}/subtitle-generations`
- **Progress** — watch SSE at `GET /api/media-jobs/{job_id}/events`
- **Result display** — output path, validation status, cue count

#### Subgen Status Panel
- Show provider health from `GET /api/subtitle-generators`
- Online/offline indicator, version, model info
- What capabilities are available (language hint support, etc.)

#### Subgen Supported Languages (full list from OpenAI Whisper):
Afrikaans, Arabic, Armenian, Azerbaijani, Belarusian, Bosnian, Bulgarian,
Catalan, Chinese, Croatian, Czech, Danish, Dutch, English, Estonian, Finnish,
French, Galician, German, Greek, Hebrew, Hindi, Hungarian, Icelandic,
Indonesian, Italian, Japanese, Kannada, Kazakh, Korean, Latvian, Lithuanian,
Macedonian, Malay, Marathi, Maori, Nepali, Norwegian, Persian, Polish,
Portuguese, Romanian, Russian, Serbian, Slovak, Slovenian, Spanish, Swahili,
Swedish, Tagalog, Tamil, Thai, Turkish, Ukrainian, Urdu, Vietnamese, Welsh.

### 3.5 Settings Page — Subgen Connection

Add these settings sections (either as a new tab in `/settings` or its own
section):

#### Connection
- **Subgen URL** — text input (e.g., `http://localhost:9000`)
- **Test Connection** button — calls `GET {url}/status`, shows:
  - ✅ Online / ❌ Offline
  - Version string
  - Model name
  - Device (CPU/CUDA)

#### Path Mapping
- **Local path prefix** — what Marquee sees (e.g., `/mnt/PLUNDER/Media/Movies`)
- **Remote path prefix** — what Subgen sees (e.g., `/movies`)
- **Live example** — show translation:
  Input: `/mnt/PLUNDER/Media/Movies/Film (2024).mkv`
  Output: `/movies/Film (2024).mkv`
- **Enable path mapping** — toggle

#### External Subtitle Filename Style
- **Language code format** — dropdown: `ISO-639-1 (en)` / `ISO-639-2/B (eng)` /
  `ISO-639-2/T (eng)` / `Native name (English)`
- **Include "subgen" tag** — checkbox (appends `.subgen` to filename)
- **Include model name** — checkbox (appends model to filename)
- **SDH flag style** — `sdh` or `hi` (hearing impaired)
- **Live preview** — show an example filename:
  `Movie.Name.2024.en.sdh.subgen.srt`

#### Generation Defaults
- **Default mode** — transcribe / translate
- **Default output** — external / embedded
- **Default target language** — for auto-selection
- **Preferred languages** — comma-separated list for coverage gap detection
  (already backed by `SUBTITLE_PREFERRED_LANGUAGES`)

#### Mutation Safety
- **Hardlink policy** — block / allow_break (radio)
- **Backup mode** — none / keep_original (radio)
- **External delete mode** — quarantine / delete (radio)
- **Plan TTL** — minutes (number input)

---

## 4. External Subtitle Naming Conventions

When generating or extracting subtitles, filenames MUST follow conventions that
both Plex and Jellyfin recognize. This is verified against official docs.

### Format
```
{VideoBasename}.{language}.{flags...}.{ext}
```

Where:
- **VideoBasename** = media filename without extension (e.g., `Movie.Name.2024`)
- **language** = ISO-639-1 2-letter code (`en`, `fr`, `ja`) — universally
  compatible. ISO-639-2/B 3-letter (`eng`, `fre`, `jpn`) also works.
- **flags** = optional, dot-separated, order is flexible:
  - `forced` — forced subtitles (foreign language segments only)
  - `sdh` or `hi` — hearing impaired / SDH
  - `default` — default track
  - `commentary` — director's commentary
  - `subgen` — AI-generated marker (recognized by existing parser)
- **ext** = `.srt` (preferred), `.ass`, `.ssa`, `.vtt`

### Examples
```
Movie.Name.2024.mkv                    ← video file
Movie.Name.2024.en.srt                 ← English
Movie.Name.2024.en.forced.srt          ← English forced only
Movie.Name.2024.en.sdh.srt             ← English SDH
Movie.Name.2024.fr.srt                 ← French
Movie.Name.2024.ja.srt                 ← Japanese
Movie.Name.2024.en.forced.sdh.subgen.srt ← English forced+SDH, AI-generated
```

### References
- Plex: https://support.plex.tv/articles/200471133-adding-local-subtitles-to-your-media/
  - Uses ISO-639-1 (2-letter) or ISO-639-2/B (3-letter)
  - Flags: `.en.forced.srt`, `.en.sdh.srt`, `.en.default.srt`
- Jellyfin: https://jellyfin.org/docs/general/server/media/movies/
  - Same format, plus combined flags like `Film.en.sdh.srt`, `Film.default.en.forced.ass`
  - Both 2-letter and 3-letter codes work

### Existing Parser
The backend in `marquee/core/subtitles/external.py` already parses these token
patterns: `forced`, `sdh`, `hi`, `cc`, `commentary`, `subgen`, `ai`,
`generated`, `whisper`. Any filename following the convention above will be
correctly classified.

---

## 5. Subgen Integration Details

### 5.1 How Marquee Talks to Subgen

Subgen is a separate Docker container (`mccloud/subgen:latest`) running at
`localhost:9000` in the current deployment.

Marquee uses these Subgen endpoints:
- `GET /status` — health check, returns `{ version: "2026.06.3", ... }`
- `POST /batch` — submit file for transcription
  - Body: `{"path": "/absolute/path/to/video.mkv", "forceLanguage": "en"}`
  - Subgen generates `.srt` next to the video
  - Can optionally POST a completion webhook to `/api/webhooks/subgen`

### 5.2 Path Translation
Because Subgen may mount media at different paths than Marquee, the backend
translates:
- `SUBGEN_LOCAL_PATH_PREFIX` → `SUBGEN_REMOTE_PATH_PREFIX`
- Example: local `/mnt/PLUNDER/Media/Movies/Film.mkv` → remote
  `/movies/Film.mkv`

### 5.3 Completion Flow
After submitting, Marquee polls for the output `.srt` file every
`SUBGEN_POLL_SECONDS` (default 30s) up to `SUBGEN_TIMEOUT_MINUTES` (default
120 min). Subgen's optional completion webhook can short-circuit this but
isn't required.

### 5.4 Filename Reconciliation Gap
⚠️ **Important:** The backend `expected_output_srt()` produces a simple name
like `Movie.en.srt`. But Subgen's own output filename is controlled by ITS
environment variables (`SUBTITLE_LANGUAGE_NAME`, `SUBTITLE_LANGUAGE_NAMING_TYPE`,
`SHOW_IN_SUBNAME_SUBGEN`, `SHOW_IN_SUBNAME_MODEL`) and may produce names like
`Movie.eng.subgen.medium.srt`. The `reconcile()` method has a fallback glob
(`{media.stem}*.srt`) that catches these, but naming may diverge. When building
the settings page, make sure the naming UI corresponds to Subgen's actual
configuration so both sides match.

### 5.5 Key Subgen Environment Variables
Full list at https://github.com/McCloudS/subgen. Key ones relevant to settings:
- `TRANSCRIBE_DEVICE` (cpu/cuda)
- `WHISPER_MODEL` (tiny/base/small/medium/large-v3/large-v3-turbo)
- `TRANSCRIBE_OR_TRANSLATE` (transcribe/translate)
- `SUBTITLE_LANGUAGE_NAME` (language code for output filename, default `aa`)
- `SUBTITLE_LANGUAGE_NAMING_TYPE` (ISO_639_1/ISO_639_2_B/ISO_639_2_T/NAME/NATIVE)
- `SHOW_IN_SUBNAME_SUBGEN` (True/False)
- `SHOW_IN_SUBNAME_MODEL` (True/False)
- `CONCURRENT_TRANSCRIPTIONS` (default 2)
- `USE_PATH_MAPPING`, `PATH_MAPPING_FROM`, `PATH_MAPPING_TO`
- `SKIP_IF_TARGET_SUBTITLES_EXIST`, `SKIP_IF_INTERNAL_SUBTITLES_LANGUAGE`
- `PREFERRED_AUDIO_LANGUAGES`
- `WEBHOOK_URL_COMPLETED` — Subgen can POST completion to Marquee
- Subgen also exposes OpenAI-compatible API at `/v1/audio/transcriptions` and
  `/v1/audio/translations` (not used by Marquee but available)

---

## 6. Page Architecture

Transform the current stub at `frontend/src/routes/subtitles/+page.svelte` into
a full tabbed page:

```
/subtitles
├── Inventory tab     — movie list with at-a-glance subtitle info (default tab)
├── Generation tab    — subgen integration panel
├── Policies tab      — language-cleanup policy CRUD
└── Jobs tab          — job queue with progress, cancel, restore
```

The settings page (`/settings`) should gain a new tab or section for subtitle
configuration.

### New Components to Build

| Component | Purpose |
|---|---|
| `SubtitleMovieList` | Table of movies with subtitle status columns |
| `SubtitleMovieRow` | Single movie row, click to expand |
| `SubtitleMovieDetail` | Expanded inline panel: tracks, coverage, actions |
| `TrackTable` | Table of subtitle tracks with checkboxes |
| `TrackRow` | Single track row with all metadata |
| `TrackSelector` | Selection UI (whitelist/blacklist, select all, flag filters) |
| `OperationPanel` | Choose operation type, configure options, submit plan |
| `PlanReview` | Before/after diff + warnings before confirming mutation |
| `SubgenPanel` | Generation form: language, mode, output target |
| `SubgenStatus` | Provider health indicator (online/offline + detail) |
| `PolicyEditor` | CRUD form for subtitle policies |
| `PolicyList` | Table of existing policies |
| `JobList` | Live/recent job queue with progress bars |
| `JobRow` | Single job with progress, status, cancel/restore buttons |
| `SubtitleSettings` | Settings form for subgen connection, path mapping, naming |

### New API Client Modules

Create under `frontend/src/lib/api/`:
- `subtitles.ts` — inventory, scan, preview, download, plans, inspect
- `subtitle-policies.ts` — CRUD, audit, apply
- `subtitle-generators.ts` — list generators, submit generation
- `media-jobs.ts` — list, get, cancel, restore, SSE events
- `subtitle-settings.ts` — settings read/write (may need backend endpoint)

### TypeScript Types to Add

Extend `frontend/src/lib/api/types.ts` with full type definitions for:
`SubtitleTrack`, `SubtitleInventory`, `SubtitleCoverage`, `SubtitlePlan`,
`SubtitlePlanRequest`, `SubtitleGenerator`, `MediaJob`, `SubtitlePolicy`,
`GenerationRequest`, `TrackAction`.

Field names must match the backend exactly — map to display models in
components.

---

## 7. Implementation Phases

Build in this order. Each phase is independently testable:

### Phase 1 — Read-Only UI (no mutations, no subgen)
1. Build `SubtitleMovieList` + `SubtitleMovieRow` — movie table with subtitle
   summary columns
2. Build `SubtitleMovieDetail` + `TrackTable` + `TrackRow` — expand to see
   all tracks, coverage, audio streams
3. Build `subtitles.ts` API client for inventory reads
4. Add mock data to `mock.ts` for development without backend
5. Each row shows: embedded count badge, external count badge, language pills,
   status icon, container type

### Phase 2 — Subgen Integration
1. Build `SubgenPanel` — form for language, mode, output selection
2. Build `SubgenStatus` — health indicator from `/api/subtitle-generators`
3. Build `subtitle-generators.ts` API client
4. Build `JobList` + `JobRow` — show generation jobs with SSE progress
5. Wire up generation submission → "Submitted" → progress bar → result

### Phase 3 — Mutation Operations
1. Build `TrackSelector` — selection UI with all modes
2. Build `OperationPanel` — operation type picker + options + submit
3. Build `PlanReview` — before/after diff display with warnings
4. Wire up plan creation → plan review → confirm → SSE progress → result
5. Build `media-jobs.ts` API client

### Phase 4 — Policies
1. Build `PolicyList` — table of existing language-cleanup policies
2. Build `PolicyEditor` — CRUD form for policies
3. Build `subtitle-policies.ts` API client
4. Wire up audit (dry-run shows what would be removed) and apply (queues jobs)

### Phase 5 — Settings
1. Build `SubtitleSettings` component with all config sections
2. Build settings API client (may need backend endpoint — see §2.3)
3. Wire up connection test, path mapping with live preview, naming preview
4. Add settings tab to existing `/settings` page

---

## 8. Technical Notes

### 8.1 Media File ID Resolution
The subtitle API uses `media_file_id`, not `movie_id`. Movies in the library
list already carry `media_file_id`. Use `POST /api/movies/{movie_id}/subtitles/inspect`
as a convenience when you have a movie ID but not a media file ID.

### 8.2 Plan → Confirm → Execute Flow
1. **Plan**: `POST /api/media-files/{id}/subtitle-plans` → `planned` job with
   before/after, warnings. Plans expire after configurable TTL (default 15 min).
2. **Review**: Show the plan's before/after diff and warnings to the user.
3. **Confirm**: `POST /api/media-jobs/{job_id}/confirm` → re-validates (checks
   file hasn't changed, disk space, hardlink safety) → queues for worker.
4. **Monitor**: Watch SSE at `GET /api/media-jobs/{job_id}/events` for progress
   events: `preflight.start`, `remux.start`, `validate.start`, `replace.start`,
   `done.complete`.
5. **Result**: `GET /api/media-jobs/{job_id}` returns final state + error info
   if failed.

### 8.3 Safety Features the UI Should Surface
- Hardlinks protected by default — warn user before breaking
- Backups opt-in — show toggle with storage estimate
- External deletions go to quarantine, not permanent (configurable)
- File signature check between plan and execution — show "Plan stale" if file
  changed
- Per-file locks prevent concurrent mutations — show "File busy" if locked

### 8.4 Container Capabilities
- **MKV (Matroska)**: Full — remove, embed text+bitmap, edit metadata, extract
- **MP4**: Limited — embed text only (mov_text, no ASS), limited remove, extract ok
- **Other containers**: Read-only inventory only

The `capabilities` field from the API tells you what's possible per file.
Show disabled buttons with tooltip reasons for unavailable operations.

### 8.5 Data Flow Summary
```
Movie list:  GET /api/library/movies → subtitle_coverage, media_file_id
Movie detail: GET /api/media-files/{id}/subtitles → all tracks + actions
Generate:     POST /api/media-files/{id}/subtitle-generations → job_id → SSE
Plan:         POST /api/media-files/{id}/subtitle-plans → job_id → plan diff
Confirm:      POST /api/media-jobs/{job_id}/confirm → queued
Monitor:      EventSource(/api/media-jobs/{job_id}/events)
Settings:     Need to build endpoint or read from existing config
Subgen health: GET /api/subtitle-generators → online/offline + version
```

---

## 9. Design & UX Guidelines

### 9.1 Visual Style
Use Marquee design tokens from `MARQUEE_HANDOFF.md §1`:
- Dark background: `var(--ink)` (#0c0d11)
- Panel surfaces: `var(--panel)` (#15171e)
- Text: `var(--text)` (#e8e9ef), muted: `var(--muted)` (#8a909f)
- Accent: `var(--gold)` (#ffc24b)
- Semantic colors: `--good` (green), `--warn` (amber), `--bad` (red),
  `--info` (blue), `--dovi` (purple)

### 9.2 Component Patterns
- Reuse existing: `TabBar`, `SectionHeader`, `ConfirmDialog`, `Toast`,
  `PosterThumb`, `Icon`
- Match table styles from `FilmList.svelte`
- Use Svelte 5 runes exclusively (`$state`, `$derived`, `$props`, `$effect`)
- SSE: native `EventSource` with cleanup via `$effect` return

### 9.3 UX Behaviors
- **Language pills**: compact colored pills showing 2-letter codes on movie rows
- **Expand**: click movie row to expand inline detail panel below it
- **Selection**: checkboxes with shift-click for range select
- **Progress**: inline progress bars during mutations with stage labels
- **Confirmation**: always show before/after diff before confirming mutations
- **Errors**: display structured error messages from API responses

---

## 10. Testing & Verification

- Add mock subtitle data to `frontend/src/lib/api/mock.ts` so the UI builds
  without a running backend
- Test naming convention formatter with: basic `Movie.en.srt`, forced
  `Movie.en.forced.srt`, sdh `Movie.en.sdh.srt`, combined
  `Movie.en.forced.sdh.subgen.srt`, 3-letter `Movie.eng.srt`
- Test language code mappings for all supported languages
- After backend integration, verify plan→confirm→execute flow end-to-end
- Test SSE reconnection on connection loss

---

## 11. Backend Gaps to Flag

These pieces do NOT exist yet. Flag them to the user rather than blocking:
1. **Extract embedded → external operation** — may need new `subtitle_extract`
   mutation type, or implement as multi-step client flow
2. **Subtitle settings CRUD endpoint** — currently env-var only, needs
   read/write API (can follow `/api/config/pipeline` pattern)
3. **Batch operations** — no bulk endpoint for subtitle mutations (deferred
   in design docs; policy `apply` creates batches internally)

---

_Compiled against the running backend on 2026-06-22. Source of truth for API
shapes is the actual route code in `marquee/api/routes/subtitles.py`,
`marquee/api/routes/subtitle_policies.py`, and
`marquee/api/routes/subtitle_generators.py`._
