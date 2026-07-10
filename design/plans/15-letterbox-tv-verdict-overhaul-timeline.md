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
