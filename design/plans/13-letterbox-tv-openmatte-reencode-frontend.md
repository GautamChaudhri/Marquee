# 13 — Letterbox TV: Open Matte/Pillarbox, Reencode, Batch Confidence — Frontend

> **For the implementing agent:** Executed only after
> `design/plans/13-letterbox-tv-openmatte-reencode-backend.md` is fully implemented —
> FIRST verify the backend actually shipped (the routes in its §8 exist in
> `marquee/api/routes/letterbox.py`); STOP and report if not. Route code is the source
> of truth over the contract table — verify every path/payload before writing client
> code. Never modify anything under `marquee/`. `design/plans/05-television-frontend.md`
> §2 rules apply verbatim (trackJob poll-first, null guards, job labels, reuse over
> forking). Gates per commit: `npm run check`, `npm run lint`, `npm run build`.
> Re-locate every line anchor before editing.
>
> **Timeline doc (shared with the backend plan):**
> `design/plans/13-letterbox-tv-openmatte-reencode-timeline.md` already exists when you
> start (the backend agent created it) — read it in full, verify its claims against
> `git log`, then APPEND your frontend progress to the same file after every commit
> (completed with hash, in progress, next steps, deviations, pending operator actions).

**Goal:** Surface open-matte/pillarbox classification (badges, chips, heatmap, filters,
escape hatches), bring the TV confidence dropdown to movie parity (frame-agreement
summary + color-coded crop pairs), make clear/sampled-clear previews instant and
single-frame, add confidence-filtered batch apply/revert wired to the new job endpoints,
build the TV reencode UX (single episode + season/show batch + artifact review + bulk
replace), and render persistent job progress bars on the show detail page.

Primary files: `frontend/src/routes/letterbox/tv/[id]/+page.svelte` (most work),
`frontend/src/lib/api/letterbox.ts`, `frontend/src/lib/api/types.ts`,
`frontend/src/lib/display.ts`, `frontend/src/lib/job-labels.ts`,
`frontend/src/lib/components/LetterboxDetail.svelte` (helper extraction ONLY),
`frontend/src/lib/components/letterbox/BatchReencodeModal.svelte` (generalize),
`frontend/src/lib/components/subtitles/EpisodeHeatmap.svelte` (bucket colors — verify
how bucket→color is supplied before editing; it is shared with subtitles/HDR pages).

---

## 1. Locked UX decisions

| # | Decision |
|---|---|
| C1 | **Badges/buckets.** `open_matte` → label "Open Matte", `pillarbox` → label "Pillarbox"; two DISTINCT muted tones (pick two existing CSS tone vars not already used by letterbox buckets — e.g. a desaturated steel and a desaturated violet; verify the palette in the app styles). Add to the page's bucket/verdict meta maps, season bucket-count chips (`SEASON_BUCKET_ORDER`, ~line 423), heatmap legend + cell colors, and the verdict filter dropdown. Chips render as the existing count chips do — an "OM N" chip and a "PB N" chip, only when count > 0. |
| C2 | **Row treatment for OM/PB.** No expand chevron, no dropdown, no preview, no Detect/Apply/Reencode/Ignore/Clear actions. The row shows its badge + source-AR label (backend supplies `aspect_label`) and a single "Scan anyway" action that calls the episode-scope detect with `{episode_id, force: true, include_open_matte: true}` and rehydrates the job. Dropdown suppression also applies to `unanalyzed` and `ineligible` buckets (nothing useful to show). |
| C3 | **Season escape hatch.** Season header gains an "Include OM/PB" checkbox rendered only when `bucket_counts.open_matte + bucket_counts.pillarbox > 0`, next to Exhaustive/Force; when checked, season detect sends `include_open_matte: true`. Show-level controls do NOT get the checkbox (backend ignores it at show scope anyway). |
| C4 | **Confidence dropdown parity.** Extract `pairKey`, `pairColorMap`, `BAR_COLORS` and the agree-count computation from `LetterboxDetail.svelte` (~226-248, 1170-1216) into a shared module (e.g. `$lib/letterbox-samples.ts`); refactor `LetterboxDetail.svelte` to import them with byte-identical rendering (this is the only allowed touch to that component besides C7 reuse). TV sample gallery gains: header line "All N agree" / "X/Y agree" (dominant top/bottom pair over ok samples) and a colored ● per sample row keyed by its pair (✕ in `--bad` for failed samples) — matching the movie expander. |
| C5 | **Prefetch + single frame.** `PAIR_PREVIEW_BUCKETS` stays `{candidate, tagged}` (pair rendering). New `PREFETCH_BUCKETS = {candidate, tagged, clear, sampled_clear}` drives `runPrefetchPass` (~143-171): fetch detail JSON for all four; warm `preview_urls.before` AND `.after` for pair buckets, `.before` ONLY for clear/sampled_clear. Keep the worker pool + generation guard exactly as is. |
| C6 | **Single-frame dropdown meta.** For clear/sampled_clear expands: show ONE "Dimensions" + "Aspect ratio" pair (from `source_width`/`source_height`), no "Before/After" prefixes, no Crop row when the crop is 0/0. Candidates/tagged keep the full before/after grid. |
| C7 | **Reencode UX.** Generalize `BatchReencodeModal.svelte` (it already has the confidence checkboxes high/medium/low/variable and the simple/advanced settings form — verify its props) so the TV page can drive it: props become subject-agnostic (items + onStart payload), movie usage in `letterbox/movies/+page.svelte` (~972) stays behaviorally identical. TV usage: (a) **season/show "Reencode" button** opens it with the scope's candidate episodes as items → `POST /tv/{id}/reencode {season_number?, confidence_levels, settings}` → toast queued/skipped summary → rehydrate `parent_job_id`; (b) **single-episode "Reencode"** in the dropdown (replaces the "not supported" toast, ~390-396): create plan via the new per-episode endpoint, show a compact plan modal (source/encoder summary, warnings, settings form reused from the same component, Confirm/Cancel), confirm via existing `confirmJob` client, then track the media job (poll `getMediaJob`; reuse the movie page's tracking approach — verify how `LetterboxDetail` tracks reencode jobs before writing a new one). |
| C8 | **Artifact review surfaces.** (a) Show page gains a "Reencode artifacts" section (below the season tables): `GET /reencode-artifacts?series_id=` rows — episode code, sizes (original vs candidate, % saved), status chip, per-row Replace original / Restore / Delete (existing artifact clients); plus season/show **"Replace all ready (N)"** buttons calling the new replace-ready endpoint (confirm dialog first). (b) Episode dropdown gains a compact artifact panel when an artifact exists for its media file (status, sizes, same actions) mirroring the movie panel's information. |
| C9 | **Apply/Revert wiring.** Season/show Apply and Revert call the updated endpoints and now receive **202 job summaries** — replace the season-revert client-side loop (~298-332) with the new `/revert` route; rehydrate returned jobs. Apply/Reencode buttons get a small confidence popover (checkboxes High/Medium/Low/Variable + "All", default High-only) showing a live count of matching candidates from the loaded detail; the button label shows the count ("Apply (7)"). Add show-level Apply/Revert/Reencode buttons in the header actions. Single-episode actions unchanged (no filter). |
| C10 | **Persistent progress bars.** Render `activeRuns` (already tracked, ~128-238) as a stack of labeled bars under the page header: job label (from `$lib/job-labels.ts` — register `letterbox_apply_tv_scope`, `letterbox_revert_tv_scope`, `letterbox_reencode_tv_batch` with the exact backend strings), percent bar (reuse `ProgressBar.svelte`), status text, cancel button (`cancelJob`). Bars persist across reloads via the existing `active_job_ids` rehydration. The shows-list page banner is untouched. |

## 2. Build order (commit per step, gates green each time)

1. **API clients/types.** `letterbox.ts`: `detectLetterboxTv` opts + `include_open_matte`;
   `applyLetterboxTv` + `confidence_levels` (and a union return: inline result or job
   summary); new `revertLetterboxTv`, `createTvReencodePlan`, `batchReencodeTv`,
   `replaceReadyTvArtifacts`, `listReencodeArtifacts` extra params, `resetTvLetterbox`
   (dev). `types.ts`: bucket unions gain `open_matte`/`pillarbox`; new payload types.
   Verify every path against route code first.
2. **Buckets/badges/heatmap/filters** (C1, C2 display parts, C3 checkbox): meta maps,
   chips, `EpisodeHeatmap` colors + legend, verdict filter options, OM/PB row rendering.
3. **Dropdown parity + single frame** (C4, C6, C2 suppression): shared sample helpers +
   `LetterboxDetail` refactor commit FIRST (movie page must build + behave identically),
   then the TV gallery summary/dots, single-frame meta, expand suppression.
4. **Prefetch extension** (C5).
5. **Apply/Revert jobs + confidence popover + show-level buttons** (C9) + job labels
   for the two new scope types.
6. **Reencode** (C7, C8): generalize `BatchReencodeModal` (commit with movie page
   verified), TV batch button+flow, single-episode plan/confirm modal, artifacts section
   + bulk replace + dropdown artifact panel, `letterbox_reencode_tv_batch` label.
7. **Progress bars** (C10) + final polish; run the manual smoke.

## 3. Manual smoke (record honestly in the timeline; GPU box + live media)

OM/PB badges + AR labels on a known mixed show; "Scan anyway" on an OM row produces a
real verdict and the badge disappears; season detect with "Include OM/PB" scans them;
mixed season default-detect produces per-episode verdicts (no sampled-clear); clear and
sampled-clear dropdowns open instantly with a single frame + single dims pair; agree
summary + colored dots match the movie page for the same kind of data; season Apply with
default High skips medium candidates (count chip matches); Revert at season scope runs
as a job with a visible bar; single-episode reencode plan→confirm→encode→artifact panel
→ replace original; season batch reencode with confidence popover queues jobs + parent
bar; "Replace all ready" replaces the reviewed set; bars survive a page reload
mid-detect; movie letterbox pages unchanged end-to-end.

## 4. Out of scope

Any backend change. Movie workspace redesign (only the `BatchReencodeModal`
generalization + shared-helper extraction touch movie-adjacent code, both
behavior-preserving). Landing-page changes. A global artifacts page (the show-page
section is the TV review surface for now). Resets UI (endpoint is operator-only).
