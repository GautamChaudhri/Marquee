# Plan 13 Letterbox TV Open Matte/Reencode Timeline

## Status

- completed: none
- in progress: phase 1 — classification + rollups
- exact next steps: implement TV dimension classification, rollup buckets/verdicts, API bucket overrides, and phase 1 tests
- deviations from the plan and why: none
- pending operator actions: none

## After phase 1

- completed: phase 1 — classification + rollups (`d499284633f3a3dd4892643a56a62a34a8378fd4`)
- in progress: phase 2 — scan semantics
- exact next steps: add `include_open_matte` request/payload forwarding, skip OM/PB by default before force, implement mixed-season auto-exhaustive scan behavior, pass payload through the job handler, and add phase 2 tests
- deviations from the plan and why: none
- pending operator actions: none

## After phase 2

- completed: phase 2 — scan semantics (`9147b710f1b1f5db0ebb0a89aeb3d8d5c91590e6`)
- in progress: phase 3 — scanned-clear preview warm
- exact next steps: add a clear-preview warm helper, schedule one before-frame for `not_letterboxed` TV detects, and add phase 3 tests
- deviations from the plan and why: none
- pending operator actions: none

## After phase 3

- completed: phase 3 — scanned-clear preview warm (`0425996785053cb95a7de008f2a7e496fd6c72b4`)
- in progress: phase 4 — scoped apply/revert jobs + confidence filter
- exact next steps: add TV apply confidence validation/filtering, extract scoped apply/revert helpers, register durable scope handlers, route scoped apply/revert to jobs, and add phase 4 tests
- deviations from the plan and why: none
- pending operator actions: none

## After phase 4

- completed: phase 4 — scoped apply/revert jobs + confidence filter (`003e8d014f9df8477c7aed75097439b1e48f3d11`)
- in progress: phase 5 — TV reencode
- exact next steps: add single-episode TV reencode planning, confirm fan-out, artifact stamping/fan-out, TV batch reencode parent jobs, artifact filters/labels, bulk replace-ready, and phase 5 tests
- deviations from the plan and why: none
- pending operator actions: none

## After phase 5

- completed: phase 5 — TV reencode (`0800321a4845806e3a3b8c511ff27362c38ca9a9`)
- in progress: phase 6 — TV letterbox dev reset
- exact next steps: add `POST /api/letterbox/tv/dev/reset-all`, purge episode previews, extend route-order protection, add phase 6 tests, run full gates, and commit
- deviations from the plan and why: none
- pending operator actions: none

## Backend complete — frontend agent begins

- completed: backend plan 13 in full, including phase 6 (`74d98afc835222854ac3e872663dd56d723957de`). All 9 §8 routes re-verified present in `marquee/api/routes/letterbox.py` at the start of the frontend agent's session (GET /tv, POST /tv/dev/reset-all, POST /tv/detect, GET /tv/{id}, GET .../episodes/{id}, POST /tv/{id}/detect, GET .../preview, POST /tv/{id}/apply, POST /tv/{id}/revert, POST .../episodes/{id}/reencode-plan, POST /tv/{id}/reencode, GET /reencode-artifacts, POST /tv/{id}/reencode-artifacts/replace-ready)
- in progress: frontend build order step 1 (API clients/types)
- exact next steps: `letterbox.ts`/`types.ts` additions per plan §2 step 1, then step 2 (buckets/badges/heatmap/filters)
- deviations from the plan and why: none
- pending operator actions: none

## After frontend steps 1–2

- completed: step 1 — API clients/types (`0e966fbfa771123257a5cf117836a7e1e98ec9a1`, "add tv api clients and types for plan 13"); step 2 — C1/C2/C3 buckets/badges/heatmap/season escape hatch (`2da71f67a9973f678f127424509f02c15f8a3483`, "add open_matte/pillarbox buckets badges heatmap and season escape hatch")
- in progress: step 3 — C4 dropdown parity + C6 single-frame meta + C2 suppression
- exact next steps: split the shared sample-helper extraction from the TV gallery wiring into two commits per plan §2 step 3, then C6 single-frame meta
- deviations from the plan and why: **these two commits landed without a timeline append**, breaking the "append after every commit" protocol. Caught and corrected retroactively by the next agent turn (this entry backfills both). No code deviation — route paths/payloads and UX decisions all verified to match the locked plan.
- pending operator actions: none

## After frontend step 3

- completed: step 3, split into two commits per plan §2 — `af759d1` "extract letterbox sample helpers" (moved `pairKey`/`pairColorMap`/`agreeCount`/`BAR_COLORS` out of `LetterboxDetail.svelte` into `frontend/src/lib/letterbox-samples.ts`; refactored `LetterboxDetail.svelte`'s inline agree-count computation to call the shared `agreeCount()` helper — verified byte-identical rendering, movie page unaffected); `c41fc2c` "add tv confidence dropdown parity and single-frame meta" (TV episode gallery gains the "All N agree"/"X/Y agree" header + colored pair dots matching the movie page, and the confidence-expand meta grid now shows a single Dimensions/Aspect-ratio pair with the Crop row suppressed at 0/0 for `clear`/`sampled_clear`, vs. the existing Before/After grid for `candidate`/`tagged`)
- in progress: step 4 — C5 prefetch extension
- exact next steps: add `PREFETCH_BUCKETS` (`candidate`, `tagged`, `clear`, `sampled_clear`), extend `runPrefetchPass` to warm `before`-only previews for `clear`/`sampled_clear` and `before`+`after` for pair buckets
- deviations from the plan and why: none this step
- pending operator actions: none

## After frontend step 4

- completed: step 4 — C5 prefetch extension (`017a253` "extend prefetch to clear and sampled_clear buckets"). `PREFETCH_BUCKETS = {candidate, tagged, clear, sampled_clear}` now drives `runPrefetchPass`'s episode selection (previously `PAIR_PREVIEW_BUCKETS`); `.before` is warmed for all four buckets, `.after` only for `PAIR_PREVIEW_BUCKETS` members. Worker pool + generation guard untouched.
- in progress: step 5 — C9 apply/revert jobs + confidence popover + show-level buttons
- exact next steps: migrate `runSeasonApply`/`runSeasonRevert` to the job-summary-returning endpoints (`applyLetterboxTv`/`revertLetterboxTv`), add the confidence popover, add show-level Apply/Revert/Reencode buttons, register `letterbox_apply_tv_scope`/`letterbox_revert_tv_scope` in `job-labels.ts`
- deviations from the plan and why: none this step
- pending operator actions: none

## After frontend step 5

- completed: step 5 — C9 apply/revert jobs + confidence popover + show-level buttons (`6327dcb` "wire tv apply/revert jobs and confidence popover"). `runSeasonApply`/`runSeasonRevert` replaced by scope-generic `runScopeApply`/`runScopeRevert` (`seasonNumber: number | null`, `null` = show-wide) that call `applyLetterboxTv`/`revertLetterboxTv` and rehydrate the returned `JobSummary.job_id` via the existing `rehydrateJob`; the old client-side `Promise.all` revert loop over `removeLetterboxTvEpisode` is gone for season/show scope (single-episode revert is untouched, per the plan's "unfiltered" rule). Added a new shared `ConfidencePopover.svelte` (`frontend/src/lib/components/letterbox/`) — self-contained checkbox dropdown (High/Medium/Low/Variable + All, outside-click-to-close via a bound container ref, no a11y warnings) driving `confidence_levels` on Apply and showing a live "Apply (N)" count computed client-side from the loaded `detail` (candidate-bucket episodes matching the selected levels). Wired for both season headers and a new show-level action row in `SectionHeader`'s `action` snippet (Apply popover + Revert button). Registered `letterbox_apply_tv_scope`/`letterbox_revert_tv_scope` in `job-labels.ts`. The component is written to be reused by the step-6 Reencode button (same confidence-filter UX) rather than forked.
- in progress: step 6 — C7 reencode UX + C8 artifact surfaces
- exact next steps: generalize `BatchReencodeModal.svelte` first as its own commit (verify movie page unchanged), then TV season/show reencode button + flow, single-episode plan/confirm modal, artifacts section + bulk replace + dropdown artifact panel, register `letterbox_reencode_tv_batch`
- deviations from the plan and why: none this step
- pending operator actions: none
