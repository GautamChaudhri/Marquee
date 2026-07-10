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
