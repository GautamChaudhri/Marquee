# 01 — Poster Pipeline Landing Page + Restoration

> **For Hermes:** Use `subagent-driven-development` skill to implement this plan group-by-group.

**Goal:** Replace the direct `/pipeline` page with a landing dashboard that shows
pipeline stats, exposes critical settings (poster filename, restoration method,
heal-scan interval), and links to the existing pipeline runner (now at
`/pipeline/movies`) and a future-TV stub. Add a second poster-restore method
(local disk backup) alongside the existing cache+download restore.

**Architecture:** A new Svelte landing page at `/pipeline` consumes a summary
stats API. The existing pipeline runner page moves to `/pipeline/movies` and
keeps all its functionality (just a route rename + navigation update). Poster
filename config uses the existing `MOVIE_POSTER_FORMAT` / `render_filename`
infrastructure; three presets exposed as radio buttons with a custom
`<base_filename>` template field. Restoration gains a local-backup method: on
deploy, the poster file is also copied to `data/backups/posters/{movie_id}.jpg`.
The periodic heal scan already exists; we expose its interval as a
user-configurable dropdown.

**Tech Stack:** SvelteKit (frontend), FastAPI + SQLAlchemy async (backend),
existing `PosterService` / `heal.py` / `pipeline_config.py`.

---

## Pre-Research Summary

The restoration system **already exists in substantial form**:

| Capability | Status | Location |
|---|---|---|
| Poster deploy (atomic copy, cache, DB, audit) | Done | `marquee/core/poster_service.py::deploy()` |
| Poster restore from cache / re-download from source URL | Done | `marquee/core/poster_service.py::restore()` |
| Periodic heal scan (stat deployed posters, restore missing) | Done | `marquee/core/heal.py::heal_scan()` |
| Heal interval config (`HEAL_INTERVAL_MINUTES`, `HEAL_ENABLED`) | Done | `marquee/config.py` |
| Poster filename format (`MOVIE_POSTER_FORMAT` with `{movie_basename}`) | Done | `marquee/config.py`, `poster_service.py::render_filename()` |
| Pipeline config hot-reload via `GET/PUT /api/config/pipeline` | Done | `marquee/api/routes/config.py` |
| `ArtworkEvent` audit trail | Done | `marquee/models/artwork_event.py` |
| `poster_user_approved`, `poster_source_url`, `poster_deployed_filename` on Movie | Done | `marquee/models/base.py::ArtworkMixin` |

**What is genuinely NEW:**

1. A landing-page UI with dashboard stats
2. A `/api/pipeline/summary` endpoint for aggregate counts
3. Poster filename config UI (three presets + custom template)
4. On filename change: trigger a library re-scan to detect active/missing posters
5. A second restore method: copy deployed poster to dedicated local backup dir
6. UI to choose restore method (download vs local backup)
7. User-configurable heal-scan interval exposed on the landing page

---

## Group A — Navigation & Route Restructure

### Task A1: Move existing pipeline page to `/pipeline/movies`

**Objective:** Preserve the current pipeline page exactly as-is, but at a new route.

**Files:**
- Move: `frontend/src/routes/pipeline/+page.svelte` to `frontend/src/routes/pipeline/movies/+page.svelte`
- Move: `frontend/src/routes/pipeline/+page.ts` to `frontend/src/routes/pipeline/movies/+page.ts`
- Keep: `frontend/src/routes/pipeline/runs/[run_id]/+page.svelte` (unchanged)

**Steps:**
1. `mkdir -p frontend/src/routes/pipeline/movies`
2. `git mv frontend/src/routes/pipeline/+page.svelte frontend/src/routes/pipeline/movies/+page.svelte`
3. `git mv frontend/src/routes/pipeline/+page.ts frontend/src/routes/pipeline/movies/+page.ts`
4. In the moved `+page.svelte`, change `<SectionHeader title="Poster pipeline">` to `title="Movie posters"`
5. Verify: `cd frontend && npm run build` — no broken imports

### Task A2: Update sidebar

**Objective:** The sidebar "Pipeline" link already points to `/pipeline` which will be the new landing page — no change needed. The existing page moves to `/pipeline/movies`, accessed from the landing page's action buttons.

**Files:**
- No change needed

### Task A3: Add a TV poster stub route

**Objective:** Create a placeholder route at `/pipeline/tv` that shows a "Coming soon" stub.

**Files:**
- Create: `frontend/src/routes/pipeline/tv/+page.svelte`

**Content:** A simple centered stub using the existing `<Stub>` component:
```svelte
<script lang="ts">
  import SectionHeader from '$lib/components/SectionHeader.svelte';
  import Stub from '$lib/components/Stub.svelte';
</script>
<SectionHeader title="TV posters" subtitle="Coming in a future release" />
<Stub message="The TV poster pipeline is not yet implemented. Check back soon." />
```

---

## Group B — Landing Page Dashboard + Summary API

### Task B1: Create summary stats API endpoint

**Objective:** A single endpoint returning all the counts the landing page needs.

**Files:**
- Modify: `marquee/api/routes/pipeline.py` — add new route

**Endpoint:**
```
GET /api/pipeline/summary
```

**Response shape:**
```json
{
  "total_movies": 843,
  "movies_with_poster": 720,
  "movies_missing_poster": 123,
  "movies_in_review": 8,
  "movies_in_run": 2,
  "running_jobs": [
    {"job_id": "abc123", "status": "running", "movie_count": 50, "progress_detail": {}}
  ],
  "last_heal": {
    "checked": 843, "restored": 3, "failed": 1, "last_run": "2026-07-01T12:30:00Z"
  }
}
```

**Implementation notes:**
- Use SQLAlchemy `func.count()` for aggregate queries
- For `movies_with_poster`: count movies where `poster_path IS NOT NULL`
- For `running_jobs`: join `PipelineRun` + `Job` where `PipelineRun.status='running'` and `batch_id = Job.job_id`
- Reuse `heal_state` dict from `marquee/core/heal.py` for last-heal info

### Task B2: Create the landing page

**Objective:** New Svelte page at `/pipeline` that shows dashboard stats.

**Files:**
- Create: `frontend/src/routes/pipeline/+page.svelte`
- Create: `frontend/src/routes/pipeline/+page.ts` (load function)

**Layout (top to bottom):**

1. **Section Header:** "Poster Pipeline" with subtitle "Manage poster selection and restoration"

2. **Stat cards row** (reuse `StatCard` component):
   - Total movies
   - Posters deployed (green if >90%)
   - Missing posters (gold/red)
   - In review
   - Running jobs (with progress indicator if active)

3. **Quick actions row:**
   - Button → "Movie posters" → navigates to `/pipeline/movies`
   - Button → "TV posters" → navigates to `/pipeline/tv` (styled as disabled/stub)

4. **Running jobs section** (if any):
   - Inline `RunProgress` component for each running batch job

5. **Settings panels** (see Groups C, D, E below) — collapsible sections

**Page load function (`+page.ts`):**
- Call `GET /api/pipeline/summary` and pass as `data.summary`
- Call `GET /api/config/pipeline` to get current settings (for the settings panels)

### Task B3: Add frontend API helpers

**Files:**
- Modify: `frontend/src/lib/api/pipeline.ts` — add `getPipelineSummary()`
- Modify: `frontend/src/lib/api/types.ts` — add interfaces

```typescript
// types.ts — add:
export interface PipelineSummary {
  total_movies: number;
  movies_with_poster: number;
  movies_missing_poster: number;
  movies_in_review: number;
  movies_in_run: number;
  running_jobs: RunningJob[];
  last_heal: HealState | null;
}

export interface RunningJob {
  job_id: string;
  status: string;
  movie_count: number;
  progress_detail: Record<string, unknown>;
}

export interface HealState {
  checked: number;
  restored: number;
  failed: number;
  last_run: string;
}
```

```typescript
// pipeline.ts — add:
export function getPipelineSummary(fetchFn: Fetch): Promise<PipelineSummary> {
  return apiGet<PipelineSummary>(fetchFn, '/pipeline/summary');
}
```

---

## Group C — Poster Filename Configuration

### Task C1: Add filename config section to the landing page

**Objective:** Radio-group UI with three presets for poster filename.

**Files:**
- Modify: `frontend/src/routes/pipeline/+page.svelte` — add a settings panel section

**UI specification:**

A collapsible panel titled "Poster filename" with description text.

Three radio options:
1. **Movie filename** — stores format as `{movie_basename}.jpg` — "Use the movie file's name (e.g., Dune (2021).jpg)"
2. **Poster** — stores format as `poster.jpg` — "Use a fixed 'poster.jpg' name (Radarr/Plex default)"
3. **Custom** — shows a text input — "Enter a custom name"

When "Custom" is selected, show:
- A text input pre-filled with the current custom format
- Helper text: "Use `<base_filename>` as a placeholder for the movie's filename"
- Example preview: "`<base_filename>-poster` → `Dune (2021)-poster.jpg`"
- The extension (`.jpg`) is automatically appended — the user never types it

**Internal mapping:**
The UI shows `<base_filename>` to the user, but translates it to `{movie_basename}` before saving. This is syntactic sugar — the backend's `render_filename()` already supports `{movie_basename}`.

### Task C2: Add poster-rescan endpoint

**Objective:** When the filename format changes, re-stat every movie's poster file.

**Files:**
- Modify: `marquee/api/routes/pipeline.py` — add `POST /api/pipeline/rescan-posters`

**Logic:**
- Walk all movies
- For each, compute expected poster path using `render_filename(movie)`
- Stat the expected file on disk
- Update `movie.poster_path` accordingly
- Return counts: `{updated: N, missing: N, unchanged: N}`

### Task C3: Wire save button to trigger rescan

**Objective:** After the user changes the filename and saves, automatically rescan.

**Flow:**
1. User selects a preset or enters custom name
2. Frontend calls `PUT /api/config/pipeline` with `{"MOVIE_POSTER_FORMAT": "poster.jpg"}` or custom
3. On success, frontend calls `POST /api/pipeline/rescan-posters`
4. Frontend refreshes the summary stats
5. Toast: "Filename updated. Found X posters, Y movies now missing."

---

## Group D — Poster Restoration Configuration

### Task D1: Add restoration settings to config

**Objective:** New settings for restore method and local backup directory.

**Files:**
- Modify: `marquee/config.py` — add fields to `Settings` class

**New settings:**
```python
# How to restore missing posters:
#   "download" = re-download from stored source URL (existing behavior)
#   "local"    = copy from Marquee's poster backup store
POSTER_RESTORE_METHOD: str = Field(
    default="download",
    description="Method to restore missing posters: 'download' or 'local'.",
)

# Directory for local poster backups (one .jpg per movie, keyed by movie ID).
POSTER_BACKUP_DIR: str = Field(
    default="data/backups/posters",
    description="Directory for local poster backups used by 'local' restore.",
)
```

### Task D2: Extend PosterService.deploy() for local backup

**Objective:** When deploying and method is "local", also copy to backup store.

**Files:**
- Modify: `marquee/core/poster_service.py` — in `deploy()`

**Changes:**
After the existing cache copy block in `deploy()`, add:
```python
if settings.POSTER_RESTORE_METHOD == "local":
    backup_dir = settings._project_root / settings.POSTER_BACKUP_DIR
    backup_dir.mkdir(parents=True, exist_ok=True)
    backup_file = backup_dir / f"{movie.id}.jpg"
    _atomic_copy(dest, backup_file)
```

### Task D3: Extend PosterService.restore() for local backup

**Objective:** When restoring and method is "local", try local backup first.

**Files:**
- Modify: `marquee/core/poster_service.py` — in `restore()`

**Changes:**
Before the existing cache-file check, add a local-backup path:
```python
if settings.POSTER_RESTORE_METHOD == "local":
    local_backup = settings._project_root / settings.POSTER_BACKUP_DIR / f"{movie.id}.jpg"
    if local_backup.is_file():
        _atomic_copy(local_backup, dest)
        await self._finalize_restore(db, movie, dest, folder_raw, source, "local")
        return RestoreResult(restored=True, source="local", path=str(dest))
    return RestoreResult(restored=False, source="none",
                         error="local backup missing — re-deploy to recreate")
```

### Task D4: Add restoration settings panel to landing page

**Objective:** UI for choosing restore method.

**Files:**
- Modify: `frontend/src/routes/pipeline/+page.svelte` — settings panel

**UI specification:**

Collapsible panel titled "Poster restoration" with:

1. **Restore method** (radio group, exclusive):
   - "Re-download from source" — re-fetches from TMDB. Works as long as TMDB still hosts the poster.
   - "Local backup" — copies each deployed poster to `data/backups/posters/`. Restores from there. Takes disk space but works offline.

2. **Save button** — calls `PUT /api/settings` or a new dedicated endpoint

---

## Group E — Heal-Scan Interval Configuration

### Task E1: Add heal interval presets

**Objective:** Expose the scan interval as user-friendly presets on the landing page.

**Files:**
- Modify: `marquee/config.py` — no code change needed; `HEAL_INTERVAL_MINUTES` already exists

**Approach:**
The landing page exposes a dropdown of presets that map to `HEAL_INTERVAL_MINUTES`:
- 15 min → 15
- 30 min → 30
- 1 hour → 60
- 2 hours → 120
- 3 hours → 180
- 6 hours → 360
- 12 hours → 720
- Once daily at [time] → 1440 (with scheduled time TBD)

The frontend calls `PUT /api/settings` with `{"HEAL_INTERVAL_MINUTES": 60}`.

For "once daily at specific time", the heal job scheduling in `marquee/main.py` or the cron scheduler would need a specific-time trigger. This is deferred to a follow-up — the initial implementation maps all presets to simple minute intervals.

### Task E2: Add heal settings panel to landing page

**Objective:** Dropdown for scan interval + enable/disable toggle.

**Files:**
- Modify: `frontend/src/routes/pipeline/+page.svelte` — settings panel

**UI specification:**

Collapsible panel titled "Poster heal scan" with:
- **Enabled** toggle (maps to `HEAL_ENABLED`)
- **Scan interval** dropdown (maps to `HEAL_INTERVAL_MINUTES`) with the preset options
- **Last scan info** — show `last_heal.last_run`, `checked`, `restored`, `failed` from summary

---

## Group F — Database Migration

### Task F1: Add poster_local_backup_path to Movie model

**Objective:** Track the local backup path for the "local" restore method.

**Files:**
- Modify: `marquee/models/base.py::ArtworkMixin` — add field

```python
poster_local_backup_path: Mapped[str | None] = mapped_column(
    Text,
    nullable=True,
    comment="Path to the local poster backup file for 'local' restore method",
)
```

### Task F2: Generate and run Alembic migration

```bash
cd /forge/Marquee
alembic revision --autogenerate -m "add_poster_local_backup_path"
alembic upgrade head
```

---

## Summary of All File Changes

| File | Action | Group |
|---|---|---|
| `frontend/src/routes/pipeline/+page.svelte` | Create (landing) | B, C, D |
| `frontend/src/routes/pipeline/+page.ts` | Create (load) | B |
| `frontend/src/routes/pipeline/movies/+page.svelte` | Move from /pipeline/ | A |
| `frontend/src/routes/pipeline/movies/+page.ts` | Move from /pipeline/ | A |
| `frontend/src/routes/pipeline/tv/+page.svelte` | Create (stub) | A |
| `frontend/src/lib/api/pipeline.ts` | Add getPipelineSummary() | B |
| `frontend/src/lib/api/types.ts` | Add PipelineSummary, etc. | B |
| `marquee/api/routes/pipeline.py` | Add summary + rescan endpoints | B, C |
| `marquee/config.py` | Add POSTER_RESTORE_METHOD, POSTER_BACKUP_DIR | D |
| `marquee/core/poster_service.py` | Local backup in deploy() + restore() | D |
| `marquee/models/base.py` | Add poster_local_backup_path | F |
| `alembic/versions/` | New migration | F |

---

## Verification Checklist

1. **Navigation:** Navigate to `/pipeline` → landing page loads. Click "Movie posters" → lands on `/pipeline/movies` with existing runner UI. Navigate to `/pipeline/tv` → stub page.
2. **Stats accuracy:** Landing page shows correct counts. Compare: `SELECT COUNT(*) FROM movies WHERE poster_path IS NOT NULL`.
3. **Filename change:** Select "Movie filename" preset, save → rescan runs → DB updated. Verify with `SELECT poster_path FROM movies LIMIT 5`.
4. **Local backup:** Set restore method to "local", deploy a poster via feedback approve → verify `data/backups/posters/{movie_id}.jpg` exists on disk.
5. **Restore from local:** Delete the deployed poster file from the media folder, run heal scan (`POST /api/system/heal`) → verify poster is restored from local backup.
6. **Config persistence:** Change settings on landing page → restart Marquee → settings persist (stored in `data/pipeline_overrides.json` or as env/config values).
7. **No regressions:** `pytest` passes. `ruff check marquee tests` clean.
