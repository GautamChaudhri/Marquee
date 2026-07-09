# 12 — Letterbox: TV Refinements (exhaustive fix, dropdown parity, per-season tables)

> **For the implementing agent:** Executes after `design/plans/10-letterbox-tv-backend.md`
> and `11-letterbox-tv-frontend.md` are implemented (they are). The route code in
> `marquee/api/routes/letterbox.py` is the source of truth — verify every path/payload
> before writing client code, and re-locate every line anchor in this doc before editing
> (do not trust line numbers blindly). `design/plans/05-television-frontend.md` §2 rules
> apply verbatim (trackJob poll-first, null guards, job labels, reuse over forking).
> Gates per commit: backend `ruff check marquee tests` + the focused pytest slices named
> per chunk; frontend `npm run check`, `npm run lint`, `npm run build`. Update
> `design/plans/12-letterbox-tv-refinements-timeline.md` after every commit — it is the
> centralized progress doc for this plan. Known baseline failure on this box:
> `test_effective_ocr_workers_caps_cuda_unless_gpu_forced` (env issue, not a regression).

**Goal:** Fix the exhaustive-detect no-op (a season with `sampled_clear` rows re-run with
Exhaustive scans nothing and returns in milliseconds — root cause: the per-episode skip
filter at `letterbox_manager.py:902` gates on `force` only and `sampled_clear` counts as
detector truth), give every individually scanned episode an aspect label so scanned-clear
is distinguishable from never-scanned, bring the TV episode dropdown to movie-detail
parity (dims/AR before-vs-after, crop px, confidence, sample-frames gallery), fix the
"NaN%" confidence column and always-"None" provenance column, restructure the show detail
page into Sonarr-style per-season collapsible tables with multi-expand episode dropdowns,
and make candidate previews open instantly via client-side prefetch.

---

## 1. Locked decisions

| # | Decision |
|---|---|
| R1 | **Exhaustive** individually scans every episode lacking a real per-episode scan (`unanalyzed` + `sampled_clear`), preserving genuine verdicts (clear, candidate, tagged, reencoded, variable, ignored). **Force** stays "rescan absolutely everything". Implemented by widening the skip predicate at `letterbox_manager.py:902` — no new param, `detect_episode_group_and_store` untouched. |
| R2 | Exhaustive still respects the resolution **prefilter** (line 906 stays force-only) — spec L5 (plan 10) says exhaustive skips *triage*, not the prefilter; `sampled_clear` rows are prefilter candidates by construction so they pass anyway. |
| R3 | `POST /tv/detect` (library) gains pass-through `force: bool = False` in `TvLibraryDetectRequest` instead of the hardcoded `force=False`; include it in the parent payload. UI keeps exposing only Exhaustive library-wide. |
| R4 | `aspect_label` for scanned-clear stamped **TV-only** in `detect_episode_blocking` (`letterbox_manager.py:595-612`) when `status == "not_letterboxed"`; the shared `result()` builder (`letterbox_detect.py:493-509`) is untouched (movies unchanged); `variable_unsafe` and `sampled_clear` stay `None`. |
| R5 | No migration, no backfill: read-time fallback in the TV read builders computes the label from `source_width/height` for legacy scanned-clear rows (`status == "not_letterboxed"` and `last_detected_at` set). |
| R6 | Rollup guard ships in the **same commit** as R4: restrict `_dominant_aspect_label` to bar-bearing buckets `{candidate, tagged, reencoded, variable}` (shared constant with `_season_uniformity`, which already filters and needs no change); rework `show_rollup`'s synthetic-episode dominant calc into a plain counter over season dominant labels; apply the same bucket filter to `/summary` `tv_aspects`. |
| R7 | Conf column renders the categorical enum (`high/medium/low/variable/none`) as a chip (port `confidenceTone` from `LetterboxDetail.svelte:250-256`, ideally into `$lib/display`), never a percentage — the current `parseFloat(ep.confidence)` is what produces "NaN%". |
| R8 | Episode dropdown reaches movie parity: before dims/AR vs after dims/AR, crop T/B px, confidence chip, detect-method tag, variable-AR note, per-minute sample-frames gallery (click a sample row → re-preview that exact frame via `?minute=…&exact=true`). No reencode flow (TV reencode unsupported). |
| R9 | Multi-expand: `expandedEpisodeId` scalar → `SvelteSet<number>`; `loadingEpisodeId` → set; keep the `episodeDetails` SvelteMap cache. |
| R10 | One collapsible table per season, Sonarr-style: season header row = name + verdict chip + `UniformityChip` + bucket counts + Exhaustive/Force checkboxes + Detect/Apply/Revert (reuse existing handlers). The separate "Season Overview & Actions" block is deleted. All episodes rendered — no pagination, no page-size select, no season filter dropdown. |
| R11 | Default collapse: seasons with `candidate + tagged === 0` start collapsed; state initialized once from first detail load (not re-derived on refresh polls, which would clobber user toggles). Search + verdict filter apply within each season; seasons with matches auto-expand, matchless seasons show a muted "no matches" header. |
| R12 | Prefetch on client mount: worker pool (3-4 concurrent) fetches episode-detail JSON for `PAIR_PREVIEW_BUCKETS` episodes and warms preview images via `new Image()` (guard for absent `preview_urls` on preview-blocked rows). On job completion (`rehydrateJob` onDone), clear `episodeDetails`/`episodeDetailErrors`, `refreshDetail()`, re-run prefetch — this also fixes the stale-after-detect dropdowns. No backend warm widening (detect already warms candidate previews; the ffmpeg preview endpoint is concurrency-gated). |

## 2. Work chunks

### Chunk 1 — Backend: exhaustive semantics + library force (fixes the Murderbot bug)

Root cause recap: `detect_episode_batch_and_store` (`marquee/media/letterbox_manager.py:827`)
skips any episode with detector truth unless `force` (line 902) *before* the exhaustive
branch (line 943); `sampled_clear` sets `last_detected_at` (`mark_sampled_clear`, line 800)
and is in `DETECTOR_TRUTH_STATUSES` (`marquee/core/letterbox_prefilter.py:18-27`,
`state_has_detector_truth` lines 105-121). So exhaustive-without-force on a triaged season
empties `pending_items` → instant return at line 939 having scanned nothing.

- `letterbox_manager.py:902` →
  `if not force and episode_state_has_detector_truth(state) and not (exhaustive and state.status == "sampled_clear"): continue`
  — with a short comment explaining that `sampled_clear` counts as detector truth, which is
  exactly why exhaustive used to no-op. Line 906 (prefilter category check) stays force-only (R2).
- `marquee/api/routes/letterbox.py`: `TvLibraryDetectRequest` (line 1074) gains
  `force: bool = False`; the child builder at line 1252 passes `force=body.force`; add
  `"force": body.force` to the parent payload (line 1258). The series/season route
  (line 1272) already passes force — untouched. Handler `letterbox_detect_tv_scope`
  (`marquee/core/jobs/builtin_handlers.py:338`) — untouched.
- `frontend/src/lib/api/letterbox.ts`: `detectLetterboxTvLibrary` body type gains
  `force?: boolean`. No UI change.
- **Tests** (`tests/test_letterbox.py`, reuse the `_seed_tv_detect_scope` + monkeypatch
  pattern near the triage/escalation/force tests at ~632/679/726):
  - triage a season (producing `sampled_clear` rows), rerun with `exhaustive=True, force=False`:
    only the `sampled_clear` episodes are scanned; real-verdict rows keep their original
    `last_detected_at`.
  - exhaustive does not rescan a `candidate` or `tagged` row.
  - extend `tests/test_letterbox_tv_api.py:331` (`test_library_detect_builds_one_child_per_show`)
    to assert child payload `force` follows the request body (default-false and explicit-true).
- Gate: `ruff check marquee tests` + `pytest tests/test_letterbox.py tests/test_letterbox_tv_api.py`.

### Chunk 2 — Backend: aspect_label for scanned-clear + rollup guards (single commit)

The write path and the rollup guard must land together: without the guard, labels on clear
rows would flip season/show `dominant_aspect_label`.

- **Write path** — `detect_episode_blocking` return dict (`letterbox_manager.py:~604`):
  `"aspect_label": result.aspect_label or (letterbox_detect.aspect_label(width, height) if result.status == "not_letterboxed" else None)`.
  The group fan-out copies this to every linked episode. Movie paths untouched.
- **Read fallback** — helper `_episode_display_aspect_label(state)` in `letterbox.py`:
  return `state.aspect_label` when set; else compute from `source_width/height` when
  `status == "not_letterboxed"` and `last_detected_at` is set; else `None`. Use it in
  `_episode_letterbox_row` (line 466) and override `detail["aspect_label"]` in
  `tv_episode_detail` after `_state_to_dict`. `sampled_clear`/never-scanned stay `None`.
- **Rollup guards** (`marquee/core/letterbox_rollups.py`):
  - shared constant `BAR_BEARING_BUCKETS = {"candidate", "tagged", "reencoded", "variable"}`,
    used by `_season_uniformity` (already filters inline, line 83 — no semantic change) and
    a restricted `_dominant_aspect_label` (line 69: only count episodes in those buckets).
  - `show_rollup` (lines 135-155) builds *synthetic* episodes (bucket `"unanalyzed"`) to
    compute the show dominant label — the restricted function would always return `None`.
    Replace that block with a plain counter over each non-special season's
    `rollup["dominant_aspect_label"]`, same tie-break `(-count, label)`.
- **Summary** — `/summary` `tv_aspects` (`letterbox.py:823-826`): count only
  `item.bucket in BAR_BEARING_BUCKETS`. Movie aspects (781-784) unchanged.
- **Override consistency** — `mark_tv_episode_not_letterboxed` (`letterbox.py:2032-2060`)
  currently leaves the candidate's letterboxed label on the now-clear row; mirror the movie
  reset (crops → 0, confidence → "none", variable flags cleared) and set the source-AR label
  so the invariant (label on a clear row = source AR) holds.
- **Tests**: `tests/test_letterbox_rollups.py` — clear-with-label episodes don't change
  dominant/uniformity; a candidate's "2.35:1" beats many "1.78:1" clear rows for dominant;
  `tests/test_letterbox_tv_api.py` summary test (~254) — clear labels excluded from
  `tv.aspect_distribution`; unit for the `detect_episode_blocking` fallback (monkeypatch
  detect + probe); API test seeding a legacy `not_letterboxed` row with NULL label →
  matrix row and `tv_episode_detail` return the computed label, `sampled_clear` → `None`.
- Gate: `ruff check marquee tests` + `pytest tests/test_letterbox.py tests/test_letterbox_tv_api.py tests/test_letterbox_rollups.py`.

### Chunk 3 — Backend: `resolved_by` in the matrix row (Provenance column fix)

`_episode_letterbox_row`'s matrix dict (`letterbox.py:475-495`) never emits `resolved_by`,
so the frontend Provenance column always shows "None" even though `LetterboxTvEpisode`
declares the field (`frontend/src/lib/api/types.ts:1841`). Add
`"resolved_by": state.resolved_by if state is not None else None`. Extend
`test_detail_payload_shape` (`tests/test_letterbox_tv_api.py:295`).

### Chunk 4 — Frontend: confidence chip + dropdown parity (R7, R8)

File: `frontend/src/routes/letterbox/tv/[id]/+page.svelte`. Parity reference:
`frontend/src/lib/components/LetterboxDetail.svelte` — dims/AR block (781-790, 1141-1160),
confidence expander + per-minute sample gallery (1174-1221), preview-URL rebuild pattern
(208-224), `confidenceTone` (250-256).

- Replace the Conf cell's `parseFloat` percent (lines 517-523) with the enum chip; "—" for
  null/`none`.
- Expand-row content (583-620): keep the `LetterboxFrame` pair/single, add beside it —
  before dims `source_width×source_height` + source AR; when a crop exists, after dims
  (`source_height − top − bottom`) + after AR; Crop T/B px (`recommended_*` or `applied_*`);
  `detect_method` tag; `variable_ar_note` when present; confidence expander listing
  `epDetail.samples` (minute, top/bottom bar px, backend, elapsed ms; failed samples render
  the error row). Clicking a sample row sets a per-episode preview-minute override
  (`SvelteMap<number, number>`) and rebuilds the preview URLs locally as
  `/api/letterbox/tv/{seriesId}/episodes/{id}/preview?mode=…&minute=…&exact=true`.
- No reencode flow — keep the existing disabled toast.
- Optional, don't block: consolidate the inline `VERDICT_META` (327-340) into `$lib/display`
  as a bucket-keyed export (the existing `letterboxMeta` is status-keyed, not a drop-in).

### Chunk 5 — Frontend: per-season collapsible tables (R9–R11)

Same file.

- `expandedEpisodeId: number | null` (line 34) + `toggleEpisodeExpand` (39-58) →
  `SvelteSet<number>` (from `svelte/reactivity`); `loadingEpisodeId` → `SvelteSet` too
  (concurrent loads). `episodeDetails` cache stays.
- One table per `detail.seasons` entry. Season header row: name ("Specials" for 0), verdict
  chip, `UniformityChip`, episode count, bucket-count chips, Exhaustive/Force checkboxes and
  Detect/Apply/Revert — reusing `exhaustive`/`forceScan` maps (196-197) and
  `runSeasonDetect`/`runSeasonApply`/`runSeasonRevert` (199-260) unchanged. Delete the
  separate "Season Overview & Actions" block (369-427).
- Collapse state: `collapsedSeasons = new SvelteSet<number>()`, initialized **once** from the
  first-loaded detail (collapse when `bucket_counts.candidate + bucket_counts.tagged === 0`).
- Filters: keep search box + verdict select; delete the season dropdown (442-452) and all
  pagination (`currentPage`/`pageSize`/`paginatedEpisodes`, 156-193 and 626-658). Per-season
  filtered lists via a derived map; active search/filter auto-expands seasons with matches,
  matchless seasons show a muted "no matches" header. Collapsed seasons mount no rows, so
  DOM size is fine.
- Keep the `EpisodeHeatmap` centerpiece and breadcrumbs; heatmap cell click still scrolls to
  the episode row (auto-expand that season if collapsed).

### Chunk 6 — Frontend: prefetch + stale-cache invalidation (R12)

Same file.

- `onMount` (client-only): worker-pool prefetch (3-4 concurrent, abort flag cleared on
  unmount) over episodes with `bucket ∈ PAIR_PREVIEW_BUCKETS` (line 32):
  `getLetterboxTvEpisodeDetail(...)` → `episodeDetails.set(...)` (skip if cached); if
  `preview_urls` present, warm with `new Image().src = preview_urls.before` (and `.after`).
  Same-origin requests share the HTTP cache with the later `<img>` tags → instant expand.
- Clear episodes still fetch on expand (unchanged path).
- In `rehydrateJob`'s `onDone` (~line 124): clear `episodeDetails`/`episodeDetailErrors`
  before `refreshDetail()`, then re-run the prefetch pass.

## 3. Build order

1. Chunk 1 → 2. Chunk 2 → 3. Chunk 3 (backend; per-commit gates above).
4. Chunk 4 → 5. Chunk 5 → 6. Chunk 6 (frontend; npm gates per commit).

Final manual smoke (needs the GPU box + live media — record as a pending operator action if
unavailable): run Exhaustive detect on a previously-triaged season (e.g. Murderbot) → job
children scan exactly the `sampled_clear` episodes; the table shows real verdicts + AR
labels + confidence chips after the post-job refresh; expand multiple dropdowns at once;
candidate previews open instantly; collapse/expand season tables; search auto-expands
matching seasons; Provenance shows real values on resolved rows.

## 4. Out of scope

TV reencode flow. Movie-side `aspect_label` changes (shared `result()` untouched). Any
Alembic migration or data backfill. Backend preview-warm widening. `VERDICT_META`
consolidation (optional courtesy only). Exposing Force on the library TV page UI.
