# 05 — Television Integration: Frontend

> **For the implementing agent:** This plan is executed in a **separate session
> after** `design/plans/04-television-backend.md` has been fully implemented.
> The API contract in that document's §15 is the source of truth — **do not
> invent or assume endpoints**; if something you need is missing, verify it in
> `marquee/api/routes/` before writing client code (a previous frontend phase
> was burned by a fictional handoff contract; this one is generated from the
> real backend plan, but trust the code over the doc on any conflict).
> Stack: SvelteKit (`frontend/`), API client modules under `frontend/src/lib/api/`,
> `X-Api-Key` auth via the existing client plumbing.
> Gates: `npm run check`, `npm run lint`, `npm run build` — all green.

**Goal:** Surface the TV backend: a **Television** library page (renamed from
Shows) with season-poster status badges, a show detail page, a TV poster
pipeline page (Run/Review/Metrics) with per-show asset chips and a
series-grouped review experience using an **asset rail**, TV taste management
(library switcher), three-scope text profile management, and TV filename
settings.

---

## 1. Locked UX decisions

| # | Decision |
|---|---|
| F1 | Sidebar label **"Shows" → "Television"**; route moves `/shows` → `/television` (keep a `+page.ts` redirect from `/shows`). Pipeline pages live under `/pipeline/tv` (route dir already exists as a stub). |
| F2 | Shows with zero downloaded episodes never appear anywhere (backend already filters; the frontend never needs a hidden-state UI). Partially-downloaded shows expose only downloaded seasons — season lists come from the API as-is, never padded to `season_count`. |
| F3 | Library grid badge per show: season poster status `complete` / `partial` / `missing`, computed by the backend over downloaded seasons (`season_poster_status` field). Show-poster presence is shown the same way films show poster state. |
| F4 | Review UX is an **asset rail**, not cloned pages: one review page per series with a left/top rail — `Show`, `S00` (specials), `S01`, … — each rail entry remounts the *existing* run-results view (the components behind `/pipeline/runs/[run_id]`) with that asset's `run_id`. |
| F5 | Season runs auto-picked by official-pick mode show an **"OFFICIAL PICK"** badge (data: `official_pick.applied == "primary_stack"` on the run payload) in place of nothing — the score badge stays. |
| F6 | A season run with no rankable candidates (`flagged_no_candidates`) renders a **"Use show poster"** action (D9 endpoint). |
| F7 | Taste page gets a **Movies / Television** switcher; all taste API calls carry `?library=`. The TV taste map only contains show-poster exemplars (backend-filtered) — label it "Show posters only". |
| F8 | Text profiles panel gains three scope tabs: **Movie / Show / Season**. Season scope shows the extra built-in "Title + Season" and the `allow_season` toggle in the custom-profile editor. |
| F9 | Defaults shown in settings: show `show.jpg`, season `season{season:02d}.jpg` → preview `season01.jpg`. |

---

## 2. Known frontend traps (from prior sessions — apply everywhere)

1. **`trackJob`** (`frontend/src/lib/jobs.ts`) is the required pattern for
   live progress: poll job snapshots first, SSE as enhancement. Never rely on
   an SSE stream opened immediately after the triggering POST.
2. Archived-feature payloads may contain `null`s where NaN was sanitized —
   guard number formatting.
3. Use `sort-title.ts` helpers for title sorting parity with the backend.
4. New job types must be added to `frontend/src/lib/job-labels.ts`:
   `poster_pipeline_tv_batch`, and the `library` payload key on
   `taste_rebuild` / `taste_map` / `learned_head_train` (label them
   "Taste rebuild (TV)" etc. when `payload.library === "tv"`).

---

## 3. API client additions (`frontend/src/lib/api/`)

- `library.ts`: `listSeries`, `getSeries`, series/season poster URLs,
  `deleteSeriesPoster`, `deleteSeasonPoster`.
- New `pipeline-tv.ts`: `getTvSummary`, `getTvRunQueue`, `runTvBatch`,
  `runSeries`, `getTvReviewQueue`, `approveTvAuto`, `resetTvReview`,
  `useShowPoster`, `getTvMetrics`, `getSeriesRuns`, `getSeriesArtworkEvents`.
- `text-profiles.ts`: rework to scoped API (`GET /api/text-profiles` returns all
  scopes; scoped CRUD paths; `getSeriesTextProfiles` / `setSeriesTextProfiles`).
- `taste.ts`: every function gains a `library: 'movies' | 'tv'` param
  (default `'movies'`).
- `config.ts` / settings types: `series_poster_format`, `season_poster_format`.
- `types.ts`: `SeriesListItem`, `SeriesDetail`, `SeasonSummary`,
  `TvRunQueueItem`, `TvReviewGroup`, `OfficialPick`, run payload additions
  (`media_type`, `subject`, `official_pick`).

---

## 4. Pages & components

### 4.1 Television library — `/television` (+ redirect from `/shows`)

Mirror `/films` structure (`films/+page.svelte` + `+page.ts` load):
- Grid of show posters (reuse `PosterThumb`/`FilmGrid` patterns — extract or
  generalize rather than fork if the film components are prop-compatible).
- Per-card: title, year, show-poster state, and the **season badge** (F3):
  e.g. a small chip `Seasons ✓` (complete) / `Seasons 3/5` (partial) /
  `Seasons ✗` (missing) — exact styling to match `StatusDot`/badge idioms.
- Search/sort parity with films.

### 4.2 Show detail — `/television/[id]`

- Header: show poster (or placeholder), title, year, ids.
- **Season posters strip**: one tile per downloaded season (incl. `S00`
  labelled "Specials"), each with its poster or missing state, and per-season
  actions: view, delete poster, "Use show poster" when applicable.
- Actions: Run pipeline (opens include picker: All missing / Show only /
  Selected seasons → `runSeries`), delete show poster.
- **Text profile overrides** card: two selects (show profile, season profile)
  fed by the scoped profiles payload; null = "Use default". (D7: the season
  override applies to all seasons.)
- Run history table (`getSeriesRuns` — rows labelled `Show` / `S01` …) linking
  into the review page rail (§4.4) or directly to `/pipeline/runs/[run_id]`.
- Artwork events feed (mirror the films detail idiom if present).

### 4.3 TV pipeline — `/pipeline/tv`

Replace the stub. Mirror `/pipeline/movies` layout: summary strip + tabs
**Run / Review / Metrics** + cache/maintenance actions stay on the shared
`/pipeline` landing (unchanged).

**Run tab:**
- Header row: `Run all missing (N assets)` (from `run-queue` totals),
  `Re-run whole library`, `Run selected (n)`.
- One row per show needing work: checkbox, mini poster, title/year, and the
  **asset chips** telling exactly what will run (F/chips):
  - missing show + seasons → `Show` `S01–S03` (compress contiguous ranges;
    non-contiguous: `S01, S03`);
  - seasons only → `S02, S05`;
  - show only → `Show`.
- Rows with `no_tmdb: true`: disabled Run + tooltip "No TMDB match — run sync".
- Per-row `Run` button → `runSeries(id, {include: 'all_missing'})`, then
  `trackJob`.

**Review tab:**
- Series-grouped cards from `getTvReviewQueue`:
  - poster = `display_poster_url` (show run auto-pick, else deployed show
    poster);
  - `seasons_only: true` → **"Seasons only"** chip on the card (F/naming as
    decided);
  - sub-line: what's in review, e.g. `Show + 3 seasons` or `2 seasons`;
  - `Approve all` per card (`approveTvAuto({series_id})`) and a queue-level
    `Approve all auto-picks`.
- Clicking a card → `/pipeline/tv/series/[series_id]` review page (§4.4).

**Metrics tab:** same charts as movies (`MetricsChart`) fed by `getTvMetrics`.

### 4.4 Series review page — `/pipeline/tv/series/[series_id]` (F4)

The centerpiece. Layout:
- **Asset rail** (left on desktop): entries `Show`, `S00`… each with a
  thumbnail, status dot (completed / flagged / reviewed), and score.
- **Main panel**: the existing run-results experience for the selected asset's
  `run_id` — Ranked / SHA-256 / OCR tabs, stacks (flat/sectioned/expand),
  rescore, approve / override / reject, "Mark as OCR false acceptance". **Reuse
  the components behind `/pipeline/runs/[run_id]`** (`PosterRankPanel`,
  `PosterStack`, `PosterCandidateTile`, `ScoreBar`, etc.) by extracting the run
  view from that route into a shared component that takes `run_id` — the
  standalone route keeps working (movie deep links + TV history links use it).
- Per-asset specifics:
  - `OFFICIAL PICK` badge next to the auto-pick when applicable (F5);
  - flagged season with zero candidates → empty-state with **Use show poster**
    button (F6) + "Re-run" once fixed;
  - approving an asset advances the rail selection to the next unreviewed
    asset (snappy review loop for 8-season shows).
- Header: show title + `Approve all remaining auto-picks` for this series.

### 4.5 `/pipeline` landing (shared)

- The **TV posters card** goes live: coverage numbers from `getTvSummary`,
  missing count, link to `/pipeline/tv` (replaces "Later").
- **Poster text profiles panel** (existing `TextProfilePanel` +
  `TextProfileEditor`): add the three scope tabs (F8). Built-ins per scope
  rendered as today; the season editor exposes `allow_season` ("Allow season
  text (Season 3, Part 2 …)"). Active/default management is per scope.
- **Poster Filename card**: becomes three sub-sections (Movie / Show / Season)
  or a tabbed card — Movie keeps its radio presets; Show is a plain filename
  field (default `show.jpg`); Season is a template field with live preview
  rendering `{season:02d}` with season=1 (`season01.jpg`) and a hint that
  `{season}` is required.
- Restoration + Heal cards: unchanged mechanics; totals now include TV
  (backup counts and heal results are shared/summed by the backend — if
  `by_type` is present in heal results, show a small breakdown line).

### 4.6 Taste page — `/taste` (F7)

- Top-level **Movies / Television** segmented switcher; persists in the URL
  (`?library=tv`) so links are shareable.
- All panels re-fetch with the `library` param: status, exemplar stats
  (TV shows per-kind counts: shows n / seasons n), rebuild
  (`source=training_dir` now reads the seeding folders — copy explains:
  "Movies: `data/taste_seeding/movies` · TV: `shows/` + `seasons/`"),
  rebuild-from-library, head retrain ("Key Art Engine"), profile artifact
  list/activate, taste map.
- TV taste map: subtitle "Show posters only" (backend filters season
  exemplars out).
- Job overlays: pass through the `library` payload for labels (§2.4).

### 4.7 Dashboard

- Add a Television coverage stat card next to the movie one (from
  `getTvSummary`): shows covered / total, seasons covered / total.

---

## 5. Out of scope (do not build)

- Episode title-cards, backdrops/banners/logos.
- Letterbox / HDR / Audio & Subs for episodes (those pages stay movie-only).
- Sonarr webhook settings UI; TV onboarding/rank-test.
- Any backend change — if the API doesn't match this doc, check §15 of the
  backend plan and the actual routes, then adapt the client, not the server.

---

## 6. Suggested build order

1. API clients + types (§3) with `check` green against real backend responses.
2. `/television` grid + `/television/[id]` detail (F1–F3).
3. `/pipeline/tv` Run tab (chips + batch/selected runs + trackJob wiring).
4. Shared run-view extraction from `/pipeline/runs/[run_id]` → series review
   page with asset rail (F4–F6).
5. Review tab cards + approve-all flows.
6. Text profile scope tabs + series overrides on the detail page.
7. Taste library switcher.
8. Settings filename fields, `/pipeline` landing TV card, dashboard card,
   job labels.
9. Full pass: `npm run check && npm run lint && npm run build`, then manual
   smoke against the live backend (library → run → review → approve → deploy).
