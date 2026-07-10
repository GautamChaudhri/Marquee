# 15 — Letterbox TV Verdict Overhaul Timeline

Shared timeline for backend plan 15 and its frontend sibling.

## Initial state — 2026-07-10

- Completed: implementation not started; no plan-15 commit exists.
- In progress: backend Phase 1 — pure TV rollups (V1–V4, V6).
- Baseline: `rtk env DEBUG=false .venv/bin/pytest -q --tb=short` run unsandboxed against
  local PostgreSQL: **807 passed, 33 failed** in 63.81s. This includes the known
  `test_effective_ocr_workers_caps_cuda_unless_gpu_forced` environment failure and 32
  unrelated pre-existing failures. A sandboxed run cannot access the database.
- Exact next steps: re-locate the rollup anchors, rename read-time TV buckets, implement the
  verdict/content-type/uniformity decisions, rewrite the rollup decision-matrix tests, run
  the full baseline comparison and lint gate, then commit Phase 1.
- Deviations: none.
- Pending operator actions: deploy the backend and frontend sibling together after both plans
  ship; no manual verification has been performed.

## Backend Phase 1 — rollups (V1–V4, V6)

- Completed: `1c12e93 plan 15 phase 1 done`.
- In progress: Backend Phase 2 — TV route vocabulary, filters, and summary output (V1, V5).
- Verification: rollup decision-matrix tests passed (13 passed). The first full comparison had
  two stale TV API assertions for retired verdict values; those assertions are corrected as
  part of Phase 2 and the final full comparison remains pending.
- Deviations: the Phase 1 completion entry is recorded after the operator-created commit so
  the shared timeline accurately resumes the backend work.
- Pending operator actions: deploy the backend and frontend sibling together after both plans
  ship; no manual verification has been performed.

## Backend Phase 2 — routes (V1, V5)

- Completed: implementation and verification complete; commit pending operator action because
  this environment cannot create `.git/index.lock` in the read-only Git metadata directory.
- In progress: backend plan complete.
- Verification: focused rollup/TV API suite was **40 passed, 1 failed**; the lone failure,
  `test_tv_dev_reset_all_deletes_episode_rows_and_previews_only`, is in the 33-failure
  baseline. Full pytest was **812 passed, 33 failed** in 63.25s, an improvement of five
  passes with no baseline-failure growth. `rtk .venv/bin/ruff check marquee tests` passed.
- Exact next steps: operator commits the Phase 2 route, test, and timeline changes; deploy
  the backend and frontend sibling together.
- Deviations: no code deviations. The Phase 2 commit is pending only because Git metadata is
  read-only in this environment.
- Pending operator actions: commit the remaining changes, deploy backend and frontend
  together, and re-select any saved legacy TV filter URLs. No manual verification beyond the
  automated suite was performed.

## Frontend Step 1 — types and tokens

- Completed: `5a2709d update tv letterbox types`.
- Timeline reconciliation: the Backend Phase 2 “commit pending” entry is stale; Git history
  confirms `f237d9d plan 15 fully done` committed the route payload contract before frontend
  work began.
- Changes: added typed TV letterbox buckets, verdicts, content types, nullable uniformity, a
  shared TV bucket metadata map, teal/magenta and accessible foreground tokens, and renamed
  stale TV bucket literals exposed by the stricter unions. HDR uniformity types remain separate.
- Verification: `npm run check` passed with the baseline 16 Svelte warnings; `npm run build`
  passed with baseline warnings. `npm run lint` still exits only for the pre-existing Prettier
  drift in prohibited `LetterboxDetail.svelte` and the pre-existing TV detail file; the new
  metadata file is formatted and adds no lint finding.
- Scope guard: no diff in movie letterbox pages, `LetterboxDetail.svelte`, HDR, subtitle, or
  heatmap files.

## Frontend Step 2 — shows list

- Completed: `c72a462 overhaul tv letterbox list`.
- Changes: shows now render only non-OK state badges plus content-type badges with hover counts;
  filters use the V5 verdict/content membership and uniformity values; all bucket segments use
  the locked colors and OM/PB participate in the N/N counter. The landing page changes only its
  TV breakdown to the V5 keys/labels and adds teal/magenta OM/PB dots.
- Verification: `npm run check` passed with the baseline 16 warnings; `npm run build` passed
  with baseline warnings; `npm run lint` remains blocked only by the unchanged Prettier drift in
  `LetterboxDetail.svelte` and TV detail.
- Scope guard: no diff in movie letterbox pages, `LetterboxDetail.svelte`, HDR, subtitle, or
  heatmap files.

## Frontend Step 3 — show detail

- Completed: `c2fb277 update tv letterbox detail`.
- Changes: detail bucket/filter labels and literals now use the V5 vocabulary; season headers
  follow the state-plus-content-badge model; season bucket chips use the shared metadata map;
  UniformityChip renders Uniform, Clean Mix, Dirty Mix, and no chip for null letterbox values.
- Compatibility: UniformityChip retains its existing HDR/subtitle uniformity labels and styling
  for their distinct unions; no HDR or subtitle route/heatmap source changed.
- Verification: `npm run check` and `npm run build` passed with baseline warnings. `npm run
  lint` remains limited to the unchanged existing Prettier drift in `LetterboxDetail.svelte`
  and TV detail.
- Scope guard: no diff in movie letterbox pages, `LetterboxDetail.svelte`, HDR, subtitle, or
  heatmap files.

## Frontend Step 4 — heatmap solid fills

- Completed: `4149b06 solidify letterbox heatmap`.
- Changes: letterbox mode now derives every cell and legend swatch from the shared bucket map,
  using solid fills, same-hue borders, per-bucket accessible text colors, V5 labels, and no
  letterbox hatching. Subtitle metadata and its mode-specific hatching remain unchanged; HDR
  uses a separate component and has no diff.
- Verification: `npm run check` and `npm run build` passed with baseline warnings. `npm run
  lint` remains limited to the unchanged existing Prettier drift in `LetterboxDetail.svelte`
  and TV detail. Static diff check confirms only the letterbox branch of EpisodeHeatmap changed.
- Screenshots: not captured. This environment has no Chromium/Chrome binary and no live library
  data, so before/after letterbox and subtitles screenshots remain a pending operator action.
- Scope guard: no diff in movie letterbox pages, `LetterboxDetail.svelte`, HDR routes/components,
  or subtitle routes; only the letterbox mode branch in the shared heatmap changed.
