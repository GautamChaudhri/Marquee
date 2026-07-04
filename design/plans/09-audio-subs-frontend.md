# 09 — Audio & Subtitles Management: Frontend

> **For the implementing agent:** Executed after
> `design/plans/08-audio-subs-backend.md` is fully implemented. The API
> contract summary is that doc's §6.5/§7, but the route code
> (`marquee/api/routes/audio_subs.py`, `subtitle_generators.py`,
> `subtitles.py`) is the source of truth — verify every path/payload before
> writing client code. Never modify anything under `marquee/`. The general
> frontend rules from `design/plans/05-television-frontend.md` §2 apply
> verbatim (trackJob for job progress — poll first, SSE as enhancement;
> null guards; job-label registration; reuse over forking).
> Gates: `npm run check`, `npm run lint`, `npm run build` — green per commit.

**Goal:** Restructure `/audio-subs` into a landing page (coverage metrics for
movies + TV, global preferred-languages editor, Subgen management card,
insights) with the current inventory workspace moved to `/audio-subs/movies`,
and add TV surfaces: show list with coverage rollups and a granular show
detail page with a season × episode coverage heatmap.

---

## 1. Locked UX decisions

| # | Decision |
|---|---|
| B1 | Routes: `/audio-subs` landing (new) · `/audio-subs/movies` (current Inventory tab content moved) · `/audio-subs/movies/[id]` (current `[id]` detail moved; `/audio-subs/[id]` gets a redirect `+page.ts`) · `/audio-subs/tv` · `/audio-subs/tv/[id]` · `/audio-subs/policies` (current Policies tab content moved to its own page). The current "AI Generation" tab dissolves: its Subgen surface becomes the landing's generator card; per-title generation actions live on the movie/TV detail pages. The "Jobs Queue" tab is dropped in favor of the app's existing jobs surfaces (link from the landing). Sidebar entry stays "Audio & Subs" → `/audio-subs`. |
| B2 | The **preferred-languages editor moves to the landing** (shared list + optional audio/sub split toggle — extract the existing drafts UI from the current page into `PreferredLanguagesEditor.svelte`). Saves via the plan 08 preferences route. Series-level overrides get a small editor on the TV detail page header. |
| B3 | **Subgen card** on the landing shows: deployment mode chip (Embedded / External / Disabled), child state (running / starting / crashed) or reachability, structured versions, model + device + compute, parsed queue depth ("2 processing · 5 queued") with last-activity line, webhook heartbeat, and actions: **Test** (A12 probe, shows detected language + latency), **Restart** (embedded), **Logs** (drawer tailing `GET .../logs`), and **Configure** — a modal with: model dropdown from `GET .../hardware` (each entry shows VRAM estimate + verdict; `too_big` entries disabled with "too large for this system"; translate-incompatible entries disabled in translate mode with the reason), auto-selected recommendation (star + "recommended" tag + reason string), `custom` option revealing a free-text HF repo/path field, device dropdown (CPU + each GPU with name/VRAM), compute type, concurrency. Saving PUTs settings and (embedded) shows the restart progress. |
| B4 | TV list rows: coverage status chip (`ok` green / `gaps` amber / `none met` red / `unknown` grey), segmented episode bar (ok/audio-gap/sub-gap/both/unknown), fraction text ("38/40 covered"), missing-language union chips (e.g. `−en audio`), dub-coverage percent, uniformity chip. Row click → detail. Filters (status, uniformity, missing language, search) read from the URL query string for landing deep links. |
| B5 | TV detail centerpiece: **season × episode heatmap** — reuse/generalize the heatmap component from plan 07 (`HdrHeatmap.svelte`) into a shared `EpisodeHeatmap.svelte` taking a cell-color/tooltip mapping (if plan 07 shipped it; otherwise create it here and plan 07's page adopts it later). Cell colors: ok green · audio-gap amber · sub-gap blue · both-gap red · unknown grey/hatched. A small dot on cells whose truth is tier-2 ("probed") vs tier-1 ("synced"); legend explains both axes. Specials row last, de-emphasized, "excluded from status" hint. |
| B6 | Generation UX: per-episode row action + per-season and per-show buttons open a **Generate modal**: source language (from the union of that scope's audio languages; maps to `forceLanguage`), output (external SRT / embedded), and an "Advanced" disclosure → exact audio track picker (tier-2 streams with language/channels/title) + per-request translate-to-English, which routes via the `/asr` path. The modal states plainly when translate is unavailable for the configured model. Jobs tracked with trackJob; batch parents render progress chips on the page. |
| B7 | Landing metric tiles are clickable and deep-link into pre-filtered lists (movies tiles → `/audio-subs/movies?...` — the moved page must read initial filters from the query string; TV tiles → `/audio-subs/tv?status=...`). |

## 2. Pages

### `/audio-subs` landing
1. Metric row — Movies: audio-ok / audio-gap / sub-ok / sub-gap / both-gap /
   unknown (clickable, B7).
2. Metric row — Television: episode-level same six + show status counts +
   uniformity counts.
3. Preferred-languages panel (B2).
4. Subgen card (B3).
5. Insights: unknown-language tracks tile ("N tracks with no language tag"),
   AI-generated subtitles tile, forced + SDH coverage tiles (for the
   preferred languages), **dub-coverage spotlight** (top seasons missing
   preferred audio, linking to their shows), policy strip (active policies,
   last audit, link to `/audio-subs/policies`), deep-scan card (enabled
   toggle + hour, "Scan movies / TV / all now" buttons with trackJob, last
   run + pending count).
6. Jump buttons: "Movie library" / "Television library".

### `/audio-subs/movies`
Current inventory workspace moved intact minus the tabs shell + preferred
editor; add query-string filter reading (B7). Detail page moves verbatim
with redirect.

### `/audio-subs/tv` and `/audio-subs/tv/[id]`
List per B4. Detail: header (title/year, rollup chip + fraction + segmented
bar, uniformity, missing-language unions, series preferred-language override
editor), heatmap (B5), season rows (per-season status counts, dub %, missing
langs, deep-scan + generate buttons), episode table (SxxEyy, title, audio
langs with channel labels when tier-2, subtitle langs, forced/SDH badges,
tier badge, status, generate action). Filter by season + status.

## 3. API clients & types

Extend `frontend/src/lib/api/`: `getAudioSubsSummary`, `getAudioSubsTv`,
`getAudioSubsTvDetail`, `putAudioSubsPreferences`, `putSeriesPreferences`,
`deepScan(scope)`, `deepScanSeries`, `getSubgenHardware`,
`putSubgenSettings`, `restartSubgen`, `getSubgenLogs`, `testSubgen`,
`generateTv`. Job labels: register the new batch/job types surfaced by the
backend (verify exact `job_type`/`subject_type` strings in the route code —
e.g. TV generation batch, deep-scan jobs).

## 4. Components

New: `PreferredLanguagesEditor.svelte`, `SubgenCard.svelte` +
`SubgenConfigModal.svelte`, `GenerateSubtitlesModal.svelte`,
`CoverageChips.svelte`. Shared: `EpisodeHeatmap.svelte` (B5),
`SegmentedBar`, `UniformityChip` (from plan 07 if present — reuse, don't
fork). Reuse `StatCard`, `Toast`, `trackJob`.

## 5. Build order

1. API clients + types against live responses.
2. Route moves + redirects (`/audio-subs/movies`, `/audio-subs/movies/[id]`,
   `/audio-subs/policies`) with content intact — commit before new UI.
3. Landing (metrics → preferred editor → Subgen card/modal → insights).
4. `/audio-subs/tv` list. 5. `/audio-subs/tv/[id]` detail. 6. Generate
   modal + deep links + job labels + full gate pass; manual smoke: landing
   numbers vs lists, save preferred langs, Subgen configure/test/restart
   round-trip, one episode generation end-to-end, one season deep scan.

## 6. Out of scope

Letterbox pages (plan 11). Policy editor redesign (moved, not changed).
Native whisper UI. Movie inventory behavior changes.
