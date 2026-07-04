# 11 — Letterbox: TV Support + Landing: Frontend

> **For the implementing agent:** Executed after
> `design/plans/10-letterbox-tv-backend.md` is fully implemented. Contract
> summary is that doc's §5; the route code in
> `marquee/api/routes/letterbox.py` is the source of truth — verify every
> path/payload before writing client code. Never modify anything under
> `marquee/`. `design/plans/05-television-frontend.md` §2 rules apply
> verbatim (trackJob poll-first, null guards, job labels, reuse over
> forking). Gates: `npm run check`, `npm run lint`, `npm run build` — green
> per commit.

**Goal:** Restructure `/letterbox` into a landing page (workflow funnel,
verdict breakdown, aspect-ratio distribution, reencode/space-reclaimed
stats) with the current movie workspace moved to `/letterbox/movies`, and
add the TV surfaces: show list with rollups and a show detail page with a
season × episode letterbox heatmap plus scoped re-run controls.

---

## 1. Locked UX decisions

| # | Decision |
|---|---|
| C1 | Routes: `/letterbox` landing (new) · `/letterbox/movies` (the entire current tabbed workspace — Cleared/Candidates/Staging/Preview/Processed — moved intact, plus query-string filter/tab reading for deep links; old `/letterbox` bookmarks redirect) · `/letterbox/tv` · `/letterbox/tv/[id]`. Sidebar stays "Letterbox" → landing. |
| C2 | Landing funnel: two card rows (Movies / Television) with one clickable tile per workflow tab (Candidates / Staging / Preview / Processed) deep-linking to the corresponding tab+filter of the list pages. TV tiles link to `/letterbox/tv?verdict=…` filters. |
| C3 | Landing breakdown: verdict donut/tiles per library — clear · sampled-clear (TV) · letterboxed-untreated · tagged · **reencoded** · variable (needs review) · ineligible · unanalyzed; aspect-ratio distribution bar (2.39:1 / 1.85:1 / 16:9 / …); analyzed-coverage percent; **space reclaimed by reencodes** card (+ artifacts awaiting decision, saved originals on disk, linking to the existing artifacts UI); "variable AR needs review" card. Batch actions: "Detect movies" (existing), "Detect TV" with an **Exhaustive** toggle (default off = season triage; tooltip explains sampled-clear). |
| C4 | TV list rows: verdict chip (`clean` green / `treated` teal / `needs action` amber / `mixed` purple / `unanalyzed` grey), segmented episode bucket bar, fraction text, dominant AR label, uniformity chip, active-job spinner. Filters (verdict, uniformity, has-candidates, search) from the query string. Header actions: batch detect selected/all with exhaustive toggle. |
| C5 | Detail centerpiece: season × episode heatmap via the shared `EpisodeHeatmap.svelte` (from plan 09/07 — reuse, don't fork). Bucket colors: clear green · **sampled-clear pale/desaturated green** (legend: "season sample said clear — not individually scanned") · candidate amber · tagged teal · reencoded gold · variable purple · error red · ineligible/unanalyzed grey (hatched for unanalyzed). Tooltip: `S01E03 · title · bucket · AR · confidence`. Cells scroll to the episode row. Specials row last, de-emphasized, excluded-from-status hint. |
| C6 | **Scoped re-runs everywhere**: episode row action "Re-analyze", season row buttons "Detect season" / "Verify fully" (force + per-episode, shown when the season has sampled-clear cells), show-level "Analyze show" with Exhaustive + Force toggles. All via trackJob; batch progress chips per season while running. |
| C7 | Episode table: SxxEyy, title, resolution, AR label, recommended crop (px + target AR), applied crop, status/confidence chips, provenance cell ("reencoded from 2.39:1 · 2026-07-01" when `resolved_by` present), eligibility reason when ineligible, actions (detect / apply / remove / ignore / mark-not-letterboxed) mirroring the movie row actions. Season bulk-apply button on season rows when candidates exist ("Apply crop to N episodes"). |

## 2. Pages

### `/letterbox` landing
1. Funnel rows (C2). 2. Verdict + AR breakdown (C3). 3. Reencode/space
card + variable-AR card + coverage card. 4. Batch action buttons.
5. Jump buttons to both list pages.

### `/letterbox/movies`
Moved workspace, functionally identical; adds URL-driven initial tab +
filters (needed by C2). Commit the move before any content change.

### `/letterbox/tv` + `/letterbox/tv/[id]`
Per C4–C7. Detail header: title/year, verdict chip + segmented bar +
fraction, uniformity, dominant AR, show-level actions (C6). Season summary
rows between heatmap and table: bucket counts, AR, sampled-vs-verified
count, season actions. Filter table by season + bucket.

## 3. API clients, labels, components

Clients: `getLetterboxSummary`, `getLetterboxTv`, `getLetterboxTvDetail`,
`detectLetterboxTv(seriesId?, opts)`, `applyLetterboxTv`, per-episode
actions. Verify each against the route code; letterbox job progress uses
the existing letterbox events endpoint — reuse the movie page's job-event
consumption helper rather than writing a second one. Register job labels
for the new TV batch types (exact strings from backend). Components:
reuse `EpisodeHeatmap`, `SegmentedBar`, `UniformityChip`, `StatCard`;
new `AspectRatioBar.svelte` (landing distribution) and
`VerdictChip.svelte` if no equivalent exists.

## 4. Build order

1. API clients + types against live responses.
2. Route move + redirect (`/letterbox/movies`) — commit before new UI.
3. Landing. 4. `/letterbox/tv` list. 5. Detail (heatmap → season rows →
   episode table → actions). 6. Deep links + labels + gates; manual smoke:
   landing counts vs tabs, run a triage detect on one show and watch
   sampled-clear cells appear, verify-fully a season, apply+remove a crop
   on one episode, confirm movie workspace unchanged.

## 5. Out of scope

TV reencode UI (backend defers it). Movie workspace redesign. Audio-subs
pages (plan 09). Any backend change.
