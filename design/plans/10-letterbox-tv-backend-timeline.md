# 10 letterbox tv backend timeline

- completed: none
- in progress: phase 0 verification, legacy movie-query guard sweep, pre-schema snapshot coverage
- next steps: add `/api/letterbox/status` and `/api/letterbox/candidates` regression snapshots; add `media_type='movie'` guards to every legacy movie letterbox query/event path; implement schema migration and model updates for L1/L7/L8/L10
- deviations from the plan and why: full `pytest -q` baseline is currently blocked in this thread by workspace policy forbidding unsandboxed execution; sandboxed pytest cannot open the PostgreSQL socket required by `tests/conftest.py`
- pending operator actions: none yet

- 2026-07-04: verified prior checkpoint commit `6c769ec` only added movie snapshot coverage in `tests/test_letterbox.py`; no schema work started. Full baseline re-run succeeded unsandboxed on current `backend`: `740 passed, 32 failed` (baseline ceiling is now 32, not the older ~27 from Plan 08). `tests/test_letterbox.py::test_status_snapshot_preserves_movie_payload_shape` is already red before Plan 10 changes because the honored/not-honored client lists drifted from the frozen snapshot; treat that as pre-existing baseline. In progress: legacy `LetterboxState`/`LetterboxEvent` guard sweep and phase 0 schema/model patch. Next: add `media_type='movie'` guards everywhere legacy movie paths join/query letterbox tables, then implement the L1/L7/L8/L10 migration + ORM updates. Deviations: none from Plan 10 yet. Pending operator actions: none.
