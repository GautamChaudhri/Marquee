# 07 — HDR Management: Frontend

> **For the implementing agent:** Executed in a separate session **after**
> `design/plans/06-hdr-management-backend.md` is fully implemented. The API
> contract is that doc's §9, but the route code under `marquee/api/routes/hdr.py`
> is the source of truth — verify every path/payload against it before writing
> client code. The general frontend rules from
> `design/plans/05-television-frontend.md` §2 apply verbatim (trackJob for job
> progress, NaN-null guards, job-labels registration, reuse over forking).
> Gates: `npm run check`, `npm run lint`, `npm run build` — green per commit.

**Goal:** Restructure `/hdr` into a **landing page** (metrics for movies + TV,
preference-target management for Radarr and Sonarr, DoVi coverage, insight
lists) with the movie table moved to `/hdr/movies`, and add the **television
HDR surfaces**: a show list with compliance/uniformity rollups and a granular
show detail page centered on a season × episode heatmap.

---

## 1. Locked UX decisions

| # | Decision |
|---|---|
| G1 | Routes: `/hdr` landing · `/hdr/movies` (current table page, minus the preferences editor) · `/hdr/movies/[id]` (current detail page moved; `/hdr/[id]` gets a redirect `+page.ts`) · `/hdr/tv` · `/hdr/tv/[id]`. Sidebar entry stays "HDR" and points at `/hdr`. |
| G2 | The preference-targets editor moves **out of the movie list page onto the landing page**, rendered twice: Movies (Radarr profiles, existing `PUT /api/hdr/preferences`) and Television (Sonarr profiles, `PUT /api/hdr/tv/preferences`). Extract the current inline drafts UI from `routes/hdr/+page.svelte` into a shared `PreferenceTargetsEditor.svelte` taking `(preferences, onSave)` — both sections use it. |
| G3 | TV list rows show: union HDR badges (uniform tag-set when `rollup.uniformity == "uniform"`, else union + a `Mixed` chip), profile + targets, uniformity chip (`Uniform` / `By season` / `Mixed`), and the compliance status with a **segmented episode bar** (proportions: exceeds/meets = green tones, below = amber, unknown = grey) plus the fraction text ("42/45 meet"). No inline episode expansion — granularity lives on the detail page. |
| G4 | Show detail centerpiece: **season × episode heatmap** — one row per season (specials last, labelled "Specials", visually de-emphasized since they're excluded from rollups), one cell per episode, colored by `bucket` (SDR grey · HDR blue · HDR10 blue · HDR10+ purple · DoVi gold · DoVi-no-fallback red · unknown hatched/grey), tooltip = `S01E03 · title · tags · status`. Cells link/scroll to the episode row in the table below. |
| G5 | Specials: shown in the heatmap + episode table, excluded from the header rollup (backend already excludes; add a small "excluded from status" hint on the specials row). |
| G6 | DoVi: analyze actions only (show-level and per-season buttons on the detail page; library-wide button on the landing page). No conversion UI for episodes; the movie conversion UI is untouched. |
| G7 | Landing metric tiles are **clickable** and deep-link into pre-filtered list pages (e.g. movies DoVi-no-fallback tile → `/hdr/movies?dovi_no_fallback=true`; TV "gaps" tile → `/hdr/tv?preference_status=gaps`). List pages must read their filters from the URL query string. |

---

## 2. API clients (`frontend/src/lib/api/`)

Extend `hdr` client module (or create `hdr.ts` additions alongside existing):
`getHdrSummary`, `getHdrTv(params)`, `getHdrTvDetail(seriesId)`,
`putSonarrPreferences`, `analyzeSeriesDovi(seriesId, seasonNumber?)`,
`analyzeTvDovi(seriesIds?)`. Types: `HdrSummary`, `ShowRollup`, `SeasonRollup`,
`HdrTvListItem`, `HdrTvDetail`, `EpisodeHdrItem` — mirror the shapes in plan 06
§6.2–6.4 but verify against live responses.

Job labels: batch parents with subject_type `dovi_tv_batch` → "DoVi analysis
(TV)"; keep existing dovi labels for movies.

---

## 3. Pages

### 3.1 `/hdr` — landing (new)

Layout in the spirit of the poster pipeline landing:
1. **Metric card row — Movies**: total · SDR · HDR · HDR10+ · DoVi ·
   DoVi-no-fallback (from `summary.movies.distribution`), plus a status
   mini-row (below/meets/exceeds counts). Tiles clickable (G7).
2. **Metric card row — Television**: shows total · episode distribution
   (same six buckets) · show status counts (exceeds/meets/**gaps**/below/
   no-target/unknown) · uniformity counts (uniform / by-season / mixed).
   Tiles clickable into `/hdr/tv` filters.
3. **Preference targets** panel (G2): two sections, Movies and Television,
   each the shared editor + Save. Empty states: "No profiles with HDR
   targets — check your Radarr/Sonarr custom formats".
4. **DoVi analysis** card: per-library coverage (`analyzed / total_dovi`) +
   "Analyze all movies" / "Analyze all shows" buttons (existing movie batch
   endpoint + new TV one), with trackJob progress.
5. **Insights** row:
   - **Worst offenders** list (top shows from `summary.worst_offenders`):
     title, segmented bar, "n below · m unknown", link to `/hdr/tv/[id]`.
   - **DoVi without fallback** spotlight: movie + episode counts → filtered
     lists.
   - **No HDR target** warning card (profile misconfiguration smell) when
     counts > 0.
   - **4K but SDR** upgrade-candidates card: movie + show counts → filtered
     movie list (`/hdr/movies` supports resolution display already; for TV
     link to `/hdr/tv` — no dedicated filter needed, the card copy carries
     the counts).

### 3.2 `/hdr/movies` — movie list (moved)

The current `routes/hdr/+page.svelte` table page relocated, minus the
preferences editor block (G2). Keep every existing filter/sort/URL behavior;
add reading initial filters from the query string if not already supported
(needed for G7 deep links). `/hdr/movies/[id]` = the current `[id]` page
moved verbatim; `/hdr/[id]` keeps a redirect for old links.

### 3.3 `/hdr/tv` — show list (new)

Mirror the movie table's envelope handling (the backend deliberately reuses
the same response envelope): distribution chips header, filter bar
(status, uniformity, tags, profile, dovi-no-fallback), sortable columns
(title / status / coverage). Rows per G3; row click → `/hdr/tv/[id]`.
Empty state respects eligibility ("no downloaded shows").

### 3.4 `/hdr/tv/[id]` — show detail (new)

1. Header: title/year, profile + meet/exceed targets, show rollup: status
   badge + segmented bar + fraction, uniformity chip, union/uniform tag
   badges.
2. **Heatmap** (G4) — new `HdrHeatmap.svelte` component. Pure CSS
   grid/flex of colored cells; no chart library. Legend row for the bucket
   colors. Must stay readable for 20+ episode seasons (cells shrink, tooltip
   carries the detail).
3. Season summary rows: per-season uniformity chip, dominant/uniform tags,
   distribution counts, compliance fraction, "Analyze season" DoVi button
   (only when the season has DoVi episodes).
4. Episode table: SxxExx, title, resolution, HDR badges (reuse
   `HdrBadge.svelte`), status, DoVi cell (profile/EL when analyzed, "P?" when
   DoVi-unanalyzed — same iconography as the movie table). Filter by season +
   status. No CF columns (H6).
5. Actions: "Analyze show (DoVi)" with trackJob; active analysis jobs from
   the payload render as progress chips.

---

## 4. Component work

- Extract `PreferenceTargetsEditor.svelte` from the current `/hdr` page (G2)
  — parameterized by library; the drafts/validation logic moves with it.
- New: `HdrHeatmap.svelte`, `SegmentedBar.svelte` (proportional status bar —
  also reused by the worst-offenders list), `UniformityChip.svelte`.
- Reuse: `HdrBadge.svelte`, `StatCard.svelte`, `TabBar`, `Toast`, `trackJob`.

## 5. Out of scope

Episode DoVi conversion UI; per-episode CF breakdowns; any backend change;
letterbox/subtitle tie-ins; HDR trend history.

## 6. Build order

1. API clients + types against live responses.
2. Route moves + redirects (`/hdr/movies`, `/hdr/movies/[id]`) with the
   existing page intact — commit before touching content.
3. Landing page (metrics → targets panel → DoVi card → insights).
4. `/hdr/tv` list.
5. `/hdr/tv/[id]` detail (heatmap → season rows → episode table → analyze
   actions).
6. Deep-link filters (G7) + job labels + full gate pass and manual smoke:
   landing numbers vs list pages, save targets both libraries, run a TV DoVi
   analysis and watch it stream.
