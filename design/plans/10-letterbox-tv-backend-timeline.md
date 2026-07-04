# 10 letterbox tv backend timeline

- completed: none
- in progress: phase 0 verification, legacy movie-query guard sweep, pre-schema snapshot coverage
- next steps: add `/api/letterbox/status` and `/api/letterbox/candidates` regression snapshots; add `media_type='movie'` guards to every legacy movie letterbox query/event path; implement schema migration and model updates for L1/L7/L8/L10
- deviations from the plan and why: full `pytest -q` baseline is currently blocked in this thread by workspace policy forbidding unsandboxed execution; sandboxed pytest cannot open the PostgreSQL socket required by `tests/conftest.py`
- pending operator actions: none yet
