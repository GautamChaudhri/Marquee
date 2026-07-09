# 12 letterbox tv refinements timeline

- completed: none
- in progress: not started
- next steps: chunk 1 (exhaustive semantics + library force pass-through)
- deviations from the plan and why: none
- pending operator actions: none

- 2026-07-09: completed chunk 1 (exhaustive semantics + library `force` pass-through). Exhaustive TV detect now rescans only `sampled_clear` episodes without forcing real verdict rows, `POST /api/letterbox/tv/detect` forwards `force` through parent and child payloads, the frontend TV library detect client accepts `force`, and focused regressions cover rerunning triaged seasons plus the library API payloads. Required backend gate is green: `ruff check marquee tests` clean and `pytest tests/test_letterbox.py tests/test_letterbox_tv_api.py` passed (`116 passed`). In progress: chunk 2 write-path/read-fallback aspect labels plus rollup guards. Next steps: implement the TV-only scanned-clear aspect-label write/read path, land the bar-bearing rollup guard in the same commit, and extend rollup/API coverage. Deviations from the plan and why: restored pre-existing backend gate drift before proceeding so the mandated slice could actually go green on `backend` — added movie-preview compatibility wrappers for renamed helpers, aligned the status snapshot expectation with the current route constants, and fixed episode-scoped TV apply to fan out across shared active media rows. Pending operator actions: none.
