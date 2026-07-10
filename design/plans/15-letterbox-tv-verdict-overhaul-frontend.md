# 15 — Letterbox TV Verdict / Terminology / Uniformity Overhaul — Frontend

> **For the implementing agent:** Executed only after
> `design/plans/15-letterbox-tv-verdict-overhaul-backend.md` is fully implemented — FIRST
> verify it shipped: the TV rollup payloads carry `widescreen`/`sampled_widescreen` bucket
> keys, `content_types`, the new `verdict`/`uniformity` values (check
> `marquee/core/letterbox_rollups.py` + the timeline). STOP and report if not. Route code
> beats this doc. Never modify anything under `marquee/`.
> `design/plans/05-television-frontend.md` §2 rules apply verbatim. Gates per commit:
> `npm run check`, `npm run lint`, `npm run build`. Re-locate every line anchor.
>
> **Timeline doc (shared with the backend plan):**
> `design/plans/15-letterbox-tv-verdict-overhaul-timeline.md` already exists (the backend
> agent created it) — read it, verify against `git log`, APPEND after every commit. Never
> create a second timeline.

**Goal:** Content-descriptive vocabulary and unique filled colors across every TV letterbox
surface: Widescreen / Open Matte / Pillarbox verdict badges (multi-badge, no "Mixed", no
color blends), the new Uniform / Clean Mix / Dirty Mix uniformity chips, solid heatmap
fills, OM/PB segments + fixed counter on the shows list, and the unstyled-Apply-button fix.
**TV surfaces only** — movie workspace and the movie half of the landing page unchanged.

Primary files: `frontend/src/routes/letterbox/tv/+page.svelte` (shows list),
`frontend/src/routes/letterbox/tv/[id]/+page.svelte` (show detail),
`frontend/src/routes/letterbox/+page.svelte` (landing — TV panel only),
`frontend/src/lib/components/subtitles/EpisodeHeatmap.svelte` (letterbox mode only),
`frontend/src/lib/components/UniformityChip.svelte`,
`frontend/src/lib/components/letterbox/ConfidencePopover.svelte`,
`frontend/src/lib/api/types.ts`, `frontend/src/app.css`.

---

## 1. Locked decisions

| # | Decision |
|---|---|
| C1 | **Labels.** Widescreen · Sampled Widescreen · **Letterboxed** (bucket `candidate` — renamed from "Candidate"/"Letterboxed (Untreated)") · Tagged · Reencoded · Variable · Open Matte · Pillarbox · Error · Ineligible · Unanalyzed. Applied to: `BUCKET_META` ([id]/+page.svelte:775-787), heatmap `LETTERBOX_STATUS_META` (EpisodeHeatmap.svelte:73-129), season bucket chips, verdict filter dropdowns, landing TV panel labels (letterbox/+page.svelte:348-354 "Clear"/"Sampled Clear" rows). |
| C2 | **Unique colors.** New `app.css` tokens `--teal` and `--magenta`, defined in BOTH the dark root (:17-33 block) and light theme (:61-73 block) with hues that read on their backgrounds. Bucket map (no tone shared by two buckets): widescreen=`--good` · sampled_widescreen=pale/desaturated tint of `--good` (same hue on purpose — same content, lower certainty) · letterboxed(candidate)=`--warn` · tagged=`--info` · reencoded=`--gold` · variable=`--dovi` · open_matte=`--teal` · pillarbox=`--magenta` · error=`--bad` · ineligible=slate/`--muted` · unanalyzed=dark gray/`--ink`-derived. Sweep every TV surface that colors buckets (BUCKET_META, heatmap META, `segmentsForRollup` tones) to this single mapping — consider a shared map in `$lib` so the three files can't drift. |
| C3 | **Verdict cells (shows list + season headers).** Replace `VERDICT_META` (tv/+page.svelte:85-94) and `SEASON_VERDICT_META` ([id]:791-797): render a state badge only when not ok — Needs Action=`--warn`, Treated=`--info`, Unanalyzed=`--muted` — followed by one content badge per entry in the rollup's `content_types` (Widescreen=`--good`, Open Matte=`--teal`, Pillarbox=`--magenta`), each with its count on hover (title attr). Fully-ok shows render content badges only. No "Mixed" badge, no blended colors, anywhere. |
| C4 | **UniformityChip.** New META: `uniform`="Uniform" (`--good`) · `clean_mixed`="Clean Mix" (`--info`) · `dirty_mixed`="Dirty Mix" (`--warn`); render nothing when uniformity is null. Uniformity keeps its own chip color family (collisions with bucket colors are acceptable across families). Update both call sites and the shows-list uniformity filter options (`uniform_by_season` option removed). |
| C5 | **Shows-list segments + counter.** `segmentsForRollup` (tv/+page.svelte:96-112): rename the clear segment key/count to widescreen+sampled_widescreen, add `open_matte` (`--teal`) and `pillarbox` (`--magenta`) segments, and give every segment its C2 tone. `getClearFraction` (:114-118): count widescreen + sampled_widescreen + open_matte + pillarbox over total — same counter, now correct for OM/PB shows (fixes the empty "0/6" bar). |
| C6 | **Heatmap solid fills (letterbox mode ONLY).** `LETTERBOX_STATUS_META`: cell `bg` becomes the full tone (no `color-mix` dilution); episode-number text color must auto-contrast (dark text on bright fills — pick per-meta text color rather than a runtime luminance calc if simpler; both themes must stay legible). Keep a slightly darker border of the same hue. Legend swatches use the same filled style. `SUBTITLE_STATUS_META` and any HDR usage of the component are untouched — the metas are per-mode; verify no shared CSS bleeds across modes before committing. |
| C7 | **ConfidencePopover styling fix.** The component uses `btn btn-outline btn-sm` but defines no `.btn` styles — Svelte scoping leaves the Apply split-button as unstyled native buttons (the unreadable grey pill). Add the missing button styles inside the component, visually identical to the page's `.btn.btn-outline.btn-sm` (copy from `letterbox/tv/[id]/+page.svelte`'s style block), including a readable disabled state (keep outline + reduced opacity + visible label — the "(0)" state must be legible without hover). |
| C8 | **Types + filters.** `types.ts`: bucket unions gain `widescreen`/`sampled_widescreen` (drop `clear`/`sampled_clear`), verdict union becomes `needs_action|treated|ok|unanalyzed`, uniformity `uniform|clean_mixed|dirty_mixed|null`; `ShowUniformity`/`SeasonUniformity` updated. Shows-list verdict filter dropdown options: Needs Action, Treated, Unanalyzed, Widescreen, Open Matte, Pillarbox (content-membership filters — backend V5). Remove `clean`/`mixed` options. Sweep remaining `clear`/`sampled_clear` literals in TV pages (`PREFETCH_BUCKETS`/`PAIR_PREVIEW_BUCKETS` sets in [id]/+page.svelte reference bucket names — verify and rename; the API *status* strings inside detail payloads that still say `sampled_clear` etc. must be left alone where they are statuses, not buckets). |
| C9 | **Scope guard.** Movie workspace (`letterbox/movies/+page.svelte`, `LetterboxDetail.svelte`) and the movie half of the landing page keep "Cleared"/"Clear" vocabulary — zero diffs there. The landing TV panel gets the new labels only (keys renamed by backend V5). |

## 2. Build order (commit per step, gates green each time)

1. **Types + color tokens** (C2 tokens in app.css, C8 types). Build will flag every stale
   union usage — fix mechanically with the new names.
2. **Shows list** (C3 verdict cells, C4 chip, C5 segments/counter, filter options).
3. **Show detail** (C1/C2 meta maps, C3 season headers, C4, C8 literal sweep).
4. **Heatmap solid fills** (C6) — screenshot letterbox mode AND a subtitles page before/after
   to prove the other modes are untouched.
5. **ConfidencePopover fix** (C7).
6. Final sweep: `rg -n "clear|Clean|clean" frontend/src/routes/letterbox/tv frontend/src/lib`
   — every remaining hit must be justified in the timeline (e.g. `clearInterval`,
   status-string comparisons).

## 3. Manual smoke (record honestly in the timeline)

Shows list: an all-OM show renders "Open Matte" badge, teal-filled bar, correct N/N counter;
a mixed OM+PB show renders both badges + two-color bar; a show with candidates renders
"Needs Action" + content badges; uniformity chips show Uniform / Clean Mix / Dirty Mix
correctly (incl. a show with one variable episode → Dirty Mix) and no chip for unanalyzed
shows; filters by Widescreen/OM/PB and by the new uniformity values work. Show detail:
season headers match, heatmap cells are solid-filled with readable numbers in dark AND light
theme, legend filled, Apply split-button is styled and its disabled "(0)" state is readable.
Subtitles + HDR heatmaps unchanged. Movie letterbox pages unchanged end-to-end.

## 4. Out of scope

Any backend change. Movie vocabulary. Plan 14's cancel/progress work. Heatmap changes to
subtitles/HDR modes. New components beyond the shared color map.
