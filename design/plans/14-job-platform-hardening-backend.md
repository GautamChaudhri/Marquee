# 14 — Job Platform + Re-encode Hardening — Backend

> **For the implementing agent:** Read `CLAUDE.md`, the shared backend ground rules in
> `design/plans/04-television-backend.md` §0, and this document IN FULL before writing any
> code. Every decision below is final — do not redesign, do not ask questions. "Verify in
> code" markers mean you must read that module before writing. Re-locate every line anchor
> before editing (the file may have drifted). **No Alembic migration is needed anywhere in
> this plan** — if you think you need one, you have misread a decision; stop and re-read.
>
> **Timeline doc (shared with the frontend plan):**
> `design/plans/14-job-platform-hardening-timeline.md`. FIRST check whether it exists. If it
> exists, read it, verify its claims against `git log` and the working tree, and RESUME from
> where it leaves off. If missing, create it before your first commit. After EVERY commit
> append: completed (with hash), in progress, exact next steps, deviations and why, pending
> operator actions.

**Goal:** Make long-running media jobs (letterbox re-encode above all) unable to wedge the
platform. This plan is the direct result of a verified production incident (2026-07-09/10):
a single-episode NVENC re-encode stalled 8 seconds in, the 6-hour job timeout's own cleanup
was cancelled mid-transaction and abandoned Postgres transactions holding row locks, the
user's Cancel clicks then blocked forever on those locks, and `recover()`'s
`FOR UPDATE SKIP LOCKED` silently skipped the wedged (locked) attempt every cycle. The
`gpu`/`media_write`/`transcode` reservations stayed held, freezing the single-worker
platform for 13+ hours.

Primary files: `marquee/core/letterbox_reencode.py`, `marquee/core/jobs/worker.py`,
`marquee/core/jobs/manager.py`, `marquee/core/jobs/legacy_media.py`,
`marquee/core/media_jobs/manager.py`, `marquee/database.py`, `marquee/config.py`,
`marquee/api/routes/letterbox.py`.

---

## 1. Locked decisions

| # | Decision |
|---|---|
| H1 | **Drain stderr concurrently.** `_run_encode_attempt` (letterbox_reencode.py:868) launches ffmpeg with `stderr=PIPE` but reads it only after the stdout loop ends — a chatty encode (DoVi warnings) fills the 64KB pipe and deadlocks ffmpeg at 0% CPU. Spawn a concurrent stderr-reader task that keeps a bounded tail (last ~200 lines / 64KB, `collections.deque`) for diagnostics and discards the rest. Apply the same treatment to `_run_checked` (:1107 — `stderr=PIPE`, polled `wait_for` loop reads nothing until the end). `_piped_ffmpeg_to_dovi` is already safe (ffmpeg stderr=DEVNULL, dovi stderr drained by `communicate`) — do not change it. |
| H2 | **Stall watchdog.** New setting `JOB_ENCODE_STALL_SECONDS` (int, default 120, `ge=30`). If no progress packet arrives from ffmpeg for that long, terminate the process (SIGTERM, then SIGKILL after ~5s), delete the partial output, and fail the attempt with error code `encode_stalled` carrying the stderr tail from H1. Never auto-retry (operator decision): `letterbox_reencode` stays out of `RETRYABLE`. Implement inside the stdout loop (e.g. `asyncio.wait_for(proc.stdout.readline(), timeout=...)` against a last-progress monotonic clock). Emit a persisted job event explaining the stall so the Projection Room shows why. |
| H3 | **Cancellation-safe subprocess cleanup.** The `except asyncio.CancelledError` path in `_run_encode_attempt` (:924) only unlinks the output today — the ffmpeg process survives as an orphan. It must also terminate the process. All cleanup that runs under cancellation (process kill, `clear_child_pid`) must be wrapped in `asyncio.shield(...)` with a hard `wait_for` timeout, and `clear_child_pid` must tolerate a broken session (it already opens its own — verify in `marquee/core/jobs/child_tracking.py`). No DB call may be the last thing standing between a cancelled task and its `finally` completing. |
| H4 | **Transaction hygiene in the media executor.** `_run_media` (legacy_media.py:135) holds `dispatch_db` open for the entire dispatch; `execute_job` mutates the MediaJob on it (`job.progress_done/total/stage`, letterbox_reencode.py:907-909) and `db.refresh(job, ["cancel_requested"])` (:911) autoflushes those mutations — an uncommitted UPDATE whose row lock is held for the whole encode. Fix: during the encode loop, NO ORM mutations on `dispatch_db`. Per-tick persistence (progress/stage) moves into the emit short-session path (`emit` in legacy_media.py:72 already opens one short session per persisted tick and mirrors into `Job.progress` — extend it/`media_job_manager.emit` to also update the MediaJob progress columns if they don't already; **verify in code**). The cancel poll reads `cancel_requested` on a fresh short session (or reuses the emit session's read). After the fix, `dispatch_db` is only used before the loop (resolve/plan checks) and after it (artifact insert + commit), never held flushed-dirty across an `await` on the subprocess. Audit the other media handlers dispatched through `_run_media` for the same pattern; fix any found. |
| H5 | **Engine-level safety nets.** `database.py`: pass asyncpg `server_settings` on the engine — `lock_timeout` (new setting `DB_LOCK_TIMEOUT_MS`, default 10000, 0 = disabled) and `idle_in_transaction_session_timeout` (new setting `DB_IDLE_TXN_TIMEOUT_MS`, default 300000, 0 = disabled). Applies to every app connection (API + worker). SQLite (tests) ignores them — guard on the URL scheme so `tests/conftest.py` still works. With these, an abandoned transaction dies within 5 minutes instead of wedging forever, and a blocked write errors within 10s instead of hanging an endpoint. |
| H6 | **Worker finalization on fresh sessions.** `_run_claim` (worker.py:65) finalizes with the claim-scoped `db` session that may be hours old (the incident's `job_manager.fail(db, ...)` at :160 died on it, leaving the job `running` forever). All terminal transitions — the `TimeoutError` branch, `except Exception` → `fail`, `else` → `finish`/`fail`, `CancelledError` → `interrupt` — must re-load the job/attempt on a FRESH session and finalize there. The `TimeoutError` branch additionally gets a belt-and-braces wrapper: if finalization raises for any reason, a last-resort raw `UPDATE` on another fresh session marks the job `failed` (error `{type: "TimeoutError"}`), marks the attempt finished, and releases its reservations — the job MUST terminalize. Also emit a persisted `timeout` job event before takedown so the UI can explain the kill. `terminate_tracked_children` (:94) must use a fresh session too, not the claim `db`. |
| H7 | **Force-cancel escalation (automatic, ~30s).** New setting `JOB_CANCEL_FORCE_SECONDS` (int, default 30). `recover()` (manager.py:757) gains a second pass: jobs whose `cancel_requested` is true (or status `cancelling`) and still non-terminal, whose newest attempt either has a stale heartbeat (existing cutoff) or whose cancel is older than `JOB_CANCEL_FORCE_SECONDS` without the attempt finishing — determine cancel age from the `cancellation requested` job event (no schema change). For each: `terminate_child_pids` (existing helper, already PID-alive-guarded — verify), finalize attempt `interrupted` + job `cancelled`, release reservations, `_sync_media_job_status`, emit event. The existing stale-attempt pass keeps `FOR UPDATE SKIP LOCKED` — H5 guarantees abandoned locks now expire, so SKIP LOCKED can no longer skip forever. |
| H8 | **Non-blocking cancel endpoint.** `request_cancel` (manager.py:676) must never hang: set `cancel_requested`/status on the jobs row and commit promptly; `_bridge_media_cancel` becomes best-effort — its own short transaction; if it errors (e.g. `lock_timeout` from H5) log + skip, because the worker's `watch_cancel` (worker.py:105) and the H7 recovery pass bridge it anyway. Same best-effort treatment for the child-cancel loop inside `request_cancel`. The endpoint returns 202 within a bounded time no matter what state the worker is in. |
| H9 | **Persistent re-encode bars (backend half).** `_active_tv_job_ids` (api/routes/letterbox.py:563) additionally returns non-terminal jobs with `subject_type == "media_file"` whose `subject_id` (stringified MediaFile id) belongs to the series — join through `EpisodeMediaFile`/`MediaFile.is_active` exactly like `_load_tv_episode_rows` (:528) does. Single-episode re-encode bridge jobs (created with `subject_type="media_file"` in `media_jobs/manager.py:245`) then rehydrate on the show page. Keep result ordering (newest first) and de-dupe against the series-subject list. |
| H10 | **Regression guards.** Movie letterbox routes/behavior unchanged. `letterbox_reencode` stays `max_attempts=1`, non-retryable. No change to healthy-path job lifecycle semantics (claim → start → finish), the resource model, or the scheduler. Full pytest baseline must not grow (known env failure on this box: `test_effective_ocr_workers_caps_cuda_unless_gpu_forced`). `ruff check marquee tests` only — never `ruff format`. |

## 2. Phase 1 — subprocess safety (H1, H2, H3)

`marquee/core/letterbox_reencode.py`:

1. `_run_encode_attempt`: add the stderr drain task (bounded tail), the stall watchdog
   around `readline()`, and process termination on the CancelledError path; shield cleanup.
   On any failure path the returned diagnostic is the stderr tail (replaces the post-hoc
   `proc.stderr.read()` at :917, which must be removed — the drain task owns the pipe).
2. `_run_checked`: same drain; keep its existing timeout/cancel loop semantics.
3. New config knob `JOB_ENCODE_STALL_SECONDS` in `config.py` next to the other `JOB_*`
   fields.
4. Stall failure surfaces as `ReencodePlanError("encode_stalled", <tail excerpt>)` so the
   existing error plumbing (media job error_json, job error) carries it; also emit a
   persisted `encode`/`stalled` event before raising.

Tests (`tests/`): fake subprocess (a tiny script via `sys.executable -c`) that (a) floods
stderr and confirms no deadlock + tail captured, (b) prints progress then sleeps past a
shrunk stall timeout and confirms termination + `encode_stalled`, (c) is cancelled
mid-stream and confirms the process is reaped (no zombie pid).

## 3. Phase 2 — transaction hygiene (H4, H5)

1. `letterbox_reencode.execute_job` + `_run_encode_attempt`: remove per-tick ORM mutations
   on the dispatch session; route progress/stage/cancel-poll through short sessions per H4.
   **Verify in code** how `media_job_manager.emit` persists (marquee/core/media_jobs/
   manager.py) before extending it.
2. Audit every handler reachable from `_run_media`'s `dispatch(...)` (marquee/core/
   media_jobs/handlers.py registry) for long-held dirty sessions across subprocess awaits;
   fix with the same pattern. Record findings in the timeline.
3. `database.py`: `server_settings` per H5 with the two new config knobs; scheme-guarded.

Tests: two-session test proving a concurrent `UPDATE media_jobs SET cancel_requested` no
longer blocks while a (simulated) encode loop is mid-tick — i.e. the poll cycle commits.
The `lock_timeout`/idle-txn settings are asserted present in the engine connect args for a
postgres URL and absent for sqlite (unit-level; no live PG assertions needed).

## 4. Phase 3 — worker finalization + recovery + cancel (H6, H7, H8)

1. `worker.py` `_run_claim`: fresh-session finalization for every terminal path; hardened
   TimeoutError branch with last-resort raw UPDATE; persisted `timeout` event;
   `terminate_tracked_children` on a fresh session.
2. `manager.py` `recover()`: H7 force-cancel pass (cancel age from the job_events row —
   **verify in code** the emitted message/state strings before querying them).
3. `manager.py` `request_cancel()`: best-effort bridging per H8.
4. New config knob `JOB_CANCEL_FORCE_SECONDS`.

Tests: seeded-row tests against the test DB — (a) running job + stale attempt heartbeat +
cancel_requested → recover() terminalizes cancelled, releases reservations, syncs the
MediaJob row; (b) cancelling job whose cancel event is older than the force window with a
live heartbeat → force pass fires; (c) request_cancel returns and commits the flag while a
second session holds the media_jobs row lock (postgres-only test — skip on sqlite);
(d) `_run_claim` timeout path with a handler that hangs in cleanup still terminalizes the
job (fresh-session last resort).

## 5. Phase 4 — TV active-job discovery (H9)

`_active_tv_job_ids` extension + tests (series with a media-file-subject `letterbox_reencode`
job appears in `active_job_ids`; other series' file jobs do not; terminal jobs excluded;
movie-subject jobs unaffected).

## 6. API contract (changed/observable)

| Route | Change |
|---|---|
| `POST /api/jobs/{job_id}/cancel` | Semantics unchanged (202 + summary) but now guaranteed prompt; cancellation completes ≤ ~`JOB_CANCEL_FORCE_SECONDS` + one recovery cycle even against a wedged handler. |
| `GET /api/letterbox/tv/{series_id}` (+ list variant at :947) | `active_job_ids` now also contains media-file-subject bridge jobs for the series' episodes. |
| Job events | New persisted events: `encode`/`stalled` (with stderr-tail detail), worker `timeout` event, recovery force-cancel event. |

New settings (documented in `.env.example` if one exists — verify): `JOB_ENCODE_STALL_SECONDS`
(120), `JOB_CANCEL_FORCE_SECONDS` (30), `DB_LOCK_TIMEOUT_MS` (10000), `DB_IDLE_TXN_TIMEOUT_MS`
(300000).

## 7. Out of scope

Any frontend change (sibling plan 14-frontend). The verdict/terminology overhaul (plan 15).
Movie letterbox behavior. Retry policy changes. Multi-worker support. Schema migrations.

## 8. Operator notes

- The incident job `f6991787…` is remediated by an operator restart (plan file step 0) —
  do NOT build remediation for it into code.
- After this plan ships, a stalled encode self-terminates in ≤ `JOB_ENCODE_STALL_SECONDS`
  and a cancel click terminalizes in ≤ ~30s worst case; if either fails on the GPU box
  smoke, that is a blocker, not a note.
- `idle_in_transaction_session_timeout` at 5min will kill any future code that legitimately
  holds an idle transaction longer — there is no such code today; keep it that way.
- pytest needs local PostgreSQL (127.0.0.1:5432); conftest provisions an ephemeral schema.
