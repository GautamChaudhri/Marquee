# 14 — Job Platform Hardening Timeline

Shared timeline for backend plan 14 and its frontend sibling.

## Initial state — 2026-07-10

- Completed: implementation not started; no plan-14 backend commit exists.
- In progress: Phase 1 — subprocess safety (H1–H3).
- Baseline: `rtk env DEBUG=false .venv/bin/pytest -q` run unsandboxed against local
  PostgreSQL: **798 passed, 36 failed** in 63.34s. This includes the known
  `test_effective_ocr_workers_caps_cuda_unless_gpu_forced` environment failure and 35
  unrelated pre-existing failures. A sandboxed run cannot access the database; the normal
  environment also requires `DEBUG=false` because the local `DEBUG=release` is invalid for
  the boolean setting.
- Exact next steps: re-locate the Phase 1 anchors, implement bounded concurrent stderr
  draining, the encode stall watchdog, and cancellation-safe subprocess cleanup; add focused
  tests; run the full baseline comparison and `ruff check marquee tests`; commit Phase 1.
- Deviations: none.
- Pending operator actions: GPU-box stalled-encode/cancel smoke from plan §8 after the
  backend phases ship.
