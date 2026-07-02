# 02 — Job Manager Hardening & Progress Enrichment

> **For Hermes:** Use `subagent-driven-development` skill to implement this plan group-by-group.
> **Critical:** Group A (context) must be completed first. Group C (the fix) is the highest priority.

**Goal:** Harden the central job backend against connection-pool exhaustion during batch
audio/subtitle operations; enrich ALL loading bars across EVERY job type with
operation-specific detail (file name, track language/kind/title, crop params, model info,
progress counts) so the user knows exactly what is happening under the hood at every
stage; verify and optimize the queue scheduler so non-conflicting jobs can run in
parallel; fix the batch quick-delete bug where not all selected languages are removed.

**Architecture:** The fix is primarily in `legacy_media.py`'s emit bridge (which opens a
fresh DB session per progress tick during concurrent batch remuxes) and the mutation
execution path (which can pass richer stage info). The pool starvation root cause lives
in the combination of: N concurrent workers × per-tick DB session × dual-emit
(media_job_manager.emit + job_manager.emit) × long-running remux subprocesses. The
solution is to batch intermediate progress emits, reduce session churn, and cap the
maximum concurrent mutation operations.

**Tech Stack:** Python 3.12+ async, SQLAlchemy async + asyncpg, FastAPI, ffmpeg/mkvmerge
subprocess orchestration, SvelteKit frontend.

---

## Pre-Research Summary

The job system is a dual-layer architecture:

| Layer | Database | Responsibility |
|---|---|---|
| Generic Job (`Job` table) | `marquee/core/jobs/` | Scheduling, resource admission, worker leases, UI stream |
| Media Job (`MediaJob` table) | `marquee/core/media_jobs/` | Detailed operation record, mutation plan/result, per-stage events |
| **Bridge** | `marquee/core/jobs/legacy_media.py` | Wires the two together — `_run_media` is registered as the handler for all media operations |

**Job lifecycle (media mutation):**
1. Frontend enqueues → `media_job_manager.create_job()` → creates `MediaJob` row + generic `Job` row
2. `DurableWorker.claim_next()` → picks up `Job` → resolves handler → `_run_media()`
3. `_run_media()` → marks `MediaJob.status=running` → calls `dispatch()` → `mutation.execute_job()`
4. `execute_job()` → preflight → `asyncio.create_subprocess_exec(ffmpeg/mkvmerge)` → validation → `os.replace` → rescan
5. Every progress tick: `emit()` callback fires → **opens a fresh DB session** → writes `MediaJobEvent` + mirrors into `JobEvent` → commits

**The critical bottleneck (legacy_media.py:43–72):**

```python
async def emit(_db, _job_id, stage, state, *, message=None, progress=None, persist=True):
    # Opens a NEW session per call — fires "frequently during long encodes/remuxes"
    async with factory() as emit_db:
        await media_job_manager.emit(emit_db, ...)   # persist → DB write + commit
        current = await emit_db.get(Job, job.id)
        # ... mirror into generic Job stream …
        await job_manager.emit(emit_db, current, ...)  # another persist → DB write
        await emit_db.commit()
```

During `execute_job()`, the only progress tick that fires during the long remux is
`await emit(db, job.job_id, "remux", "start", message=f"Running {binary}")` at line 732
— but the `_run_media` bridge wraps that in an extra DB session open/commit cycle.
Each emit call = 1 new session + 2 DB commits.

**When this explodes:** A batch of 50 track_remove operations, JOB_WORKER_CONCURRENCY=3,
3 concurrent workers running `mkvmerge` remuxes on 40–80 GB files, each firing emit().
The asyncpg connection pool (default ~20 connections) gets saturated. New API requests
(like `GET /api/jobs?queued_only`) can't acquire a connection → timeout → CancelledError
teardown → GC warns about non-checked-in connections → backend 500s.

**Evidence from logs (user-provided):**
```
sqlalchemy.pool.impl.AsyncAdaptedQueuePool: Exception terminating connection
asyncio.exceptions.CancelledError: Cancelled via cancel scope
SAWarning: The garbage collector is trying to clean up non-checked-in connection
```

---

## Group A — End-to-End System Context (Mandatory First)

### Task A1: Read every file in the job system

**Objective:** Understand the full architecture before touching anything.

**Files to read in order:**
1. `marquee/core/jobs/__init__.py` — entry point (just re-exports)
2. `marquee/core/jobs/handlers.py` — handler registry + `register()`/`resolve()`
3. `marquee/core/jobs/manager.py` — `JobManager`: create, claim, emit, finish, fail, recover, cancel (816 lines — read it all)
4. `marquee/core/jobs/worker.py` — `DurableWorker`: claim loop, heartbeat, lease renewal, run_claim
5. `marquee/core/jobs/supervisor.py` — `WorkerSupervisor`: embeds worker/scheduler subprocesses from API
6. `marquee/core/jobs/builtin_handlers.py` — all non-media job handlers (poster_pipeline, letterbox, taste, etc.)
7. `marquee/core/jobs/legacy_media.py` — **the bridge**: registers all media ops under `_run_media`
8. `marquee/core/jobs/labels.py` — human-readable job type labels
9. `marquee/core/jobs/child_tracking.py` — orphan PID tracking for ffmpeg/PaddleOCR
10. `marquee/core/jobs/scheduler.py` — periodic job scheduler (read if exists)
11. `marquee/core/media_jobs/__init__.py`
12. `marquee/core/media_jobs/manager.py` — `MediaJobManager`: create, emit, SSE stream, batch progress
13. `marquee/core/media_jobs/handlers.py` — dispatch table: scan, mutate, extract, generate, policy, restore
14. `marquee/core/subtitles/mutation.py` — **the heavy lifter**: `build_plan`, `preflight`, `execute_job`, `_build_argv` (1112 lines — read it all)
15. `marquee/core/subtitles/adapters/matroska.py` — mkvmerge args for remove/embed/metadata
16. `marquee/core/subtitles/adapters/mp4.py` — MP4 adapter (read if exists)
17. `marquee/models/media_job.py` — `MediaBatch`, `MediaJob`, `MediaJobEvent` ORM models
18. `marquee/api/routes/jobs.py` — generic Job API endpoints + SSE streaming
19. `marquee/api/routes/media_jobs.py` — MediaJob API endpoints
20. `marquee/config.py` — all `JOB_*` settings (concurrency, timeouts, slots, pool size)

**Verification:** After reading, the agent should be able to trace a single `track_remove`
operation from the frontend "Remove" button click through every function call to the
mkvmerge subprocess and back to the UI progress bar.

---

## Group B — Global Progress Bar Enrichment (ALL job types)

**Scope:** Every job type that shows a loading bar — not just track mutations.
The agent must audit ALL handlers and their progress emit paths, then enrich
every stage with meaningful detail: what file, what movie, what track, what
language, what operation, how many items remaining, etc.

### Task B0: Audit every progress emit across all handler types

**Objective:** Catalog every call site where `emit()`, `job_manager.emit()`,
or `_update_progress()` is called, noting what context is available but not
currently passed.

**Handlers to trace (from `builtin_handlers.py`):**

| Handler | Progress mechanism | What's missing |
|---|---|---|
| `poster_pipeline` | `JobProgressBridge.callback` → emits per-stage detail (movie title, survivors, stage) | Already rich — verify it shows stage names |
| `poster_pipeline_batch` | Same bridge, but batch-level progress shows `movie_index/movie_total` + `title` | Already decent — verify |
| `letterbox_detect` | `job_manager.emit()` on parent for `child_progress` with `movie_id` + `title` | Good — verify |
| `letterbox_reencode` (media) | Goes through `_run_media` bridge → mutation stages | Missing: letterbox params (top/bottom crop), source info |
| `taste_rebuild` | No incremental progress at all — just start/finish | Missing: stage progress (loading exemplars → extracting → training) |
| `taste_map` | No progress — runs as a single `asyncio.to_thread(build_map)` call | Missing: progress ticks |
| `library_sync` | No progress | Missing: sync stage (scanning Radarr → Sonarr → TMDB) |
| `subtitle_scan_all` | No progress — just queues N children then returns count | Missing: "Scanning file N/M" |
| `dovi_analyze_batch` | Check `dovi_handlers.py` | Verify |
| `subtitle_generate` | Goes through `_run_media` bridge → `run_generation_job()` | Missing: generation model, target language, progress % |

### Task B1: Enrich mutation stage messages with track detail

**Objective:** Instead of `"Running mkvmerge"`, show `"Removing English subtitle (Commentary) — mkvmerge"`.

**Files:**
- Modify: `marquee/core/subtitles/mutation.py` — around line 732 (the `emit(db, job.job_id, "remux", "start", message=...)` call)

**Current code (simplified):**
```python
await emit(db, job.job_id, "remux", "start", message=f"Running {binary}")
```

**What we have available at that point:** `operation`, `request` (contains `track_ids`, `audio_stream_indices`),
`plan` (contains `before.tracks` with all track metadata — language_tag, kind, title, is_forced, is_sdh, is_commentary).

**Implementation:** Build a descriptive message before the emit call by adding a helper function
and using its result:

```python
removing = _describe_operation(operation, request, plan)
await emit(db, job.job_id, "remux", "start", message=f"{removing} — {binary}")
```

Where `_describe_operation()` returns strings like:
- `"Removing 2 subtitles (eng, spa)"` for subtitle_remove
- `"Removing English audio (Commentary) & French subtitle"` for track_remove
- `"Embedding English subtitle (SRT)"` for subtitle_embed
- `"Editing metadata: eng subtitle → Forced"` for subtitle_metadata
- `"Reordering 3 audio streams"` for audio_reorder

The function should be ~40 lines max. Use the plan data that's already loaded. If plan is
None or tracks aren't available, fall back to the existing generic message.

### Task B2: Enrich ALL non-media handler progress with stage detail

**Objective:** Every progress emit in `builtin_handlers.py` should include a
meaningful `message` and `stage` that tells the user what's happening.

**Files:**
- Modify: `marquee/core/jobs/builtin_handlers.py` — `_update_progress()` and all handler emits

**Implementation per handler:**

1. **`taste_rebuild`:** Add stage-based progress emits. The `rebuild_profile` and
   `train_from_labels` calls run via `asyncio.to_thread()` — wrap them with
   pre/post emits:
   ```
   emit: stage="gather" message="Gathering exemplars..."
   emit: stage="feature_extract" message="Extracting features..."  
   emit: stage="train" message="Training taste model..."
   emit: stage="done" message="Taste model rebuilt"
   ```

2. **`taste_map`:** Add a pre-emit: `stage="build" message="Building taste map..."` 
   before `asyncio.to_thread(build_map)`, and a post-emit. If `build_map` can report
   intermediate progress, add it; otherwise the pre-emit alone is a big improvement
   over a silent spinning bar.

3. **`library_sync`:** Add emits between sync phases:
   ```
   emit: stage="sync_movies" message="Syncing movies from Radarr..."
   emit: stage="sync_series" message="Syncing series from Sonarr..."
   emit: stage="sync_tmdb" message="Enriching with TMDB metadata..."
   ```

4. **`subtitle_scan_all`:** Add a progress emit for each file scanned:
   ```
   for i, mf in enumerate(media_files):
       emit: stage="scan" message=f"Scanning file {i+1}/{len(media_files)}"
   ```

5. **`letterbox_reencode`** (the media bridge handler): When dispatching
   `_letterbox_reencode`, include the crop parameters in the message:
   ```
   emit: stage="reencode" message="Re-encoding with crop top={top} bottom={bottom}"
   ```

6. **`subtitle_generate`:** When dispatching `_generate`, include the target language
   and model in the emit:
   ```
   emit: stage="generate" message="Generating {lang} subtitles via {model}"
   ```

### Task B3: Surface per-file context in batch subtitle jobs

(Original B3 — keep as-is. Resolves `MediaFile` → `Movie` title for batch context.)

### Task B4: Verify progress messages survive the full SSE pipeline

**Objective:** End-to-end test: trigger each job type, capture the SSE events,
and verify the `message` field contains the enriched detail.

**Steps:**
1. For each handler type, add a temporary log or inspect SSE output
2. Verify `detail.message` in the frontend (Group E already adds the rendering)
3. Check that the `RunProgress`, `RunningJobCard`, and `JobList` components
   all display `detail.message` where relevant

---

## Group C — DB Connection Pool Exhaustion Fix (CRITICAL)

### Task C1: Batch intermediate progress emits in the legacy bridge

**Objective:** Instead of opening a fresh DB session for every single `emit()` call
during long-running operations, coalesce intermediate (non-persisted) ticks in-memory
and flush them on a timer or at stage boundaries.

**Files:**
- Modify: `marquee/core/jobs/legacy_media.py` — the `emit()` closure

**Problem analysis:** The bridge's `emit()` is called for every progress tick during
encode/remux (hundreds per job). Each call opens a session, writes events, and commits.
With 3 concurrent workers this can saturate the DB pool.

**The key insight:** Most emits during remux have `persist=True` but the actual heavy
work (`proc.communicate()`) at mutation.py:747 is a single blocking call — during that
call there are NO emits at all. The emits happen before (remux start) and after
(validate, backup, replace). So the per-tick problem during remux is actually minimal.

The real churn comes from the bridge wrapping every emit with its own session. Even a
single `emit(persist=True)` already opens a session in `media_job_manager.emit()`, and
the bridge opens ANOTHER session to mirror it into the generic job manager.

**Solution:** In the bridge's `emit()`, don't open a second session. Instead, include
the job_manager emit inside the same session that media_job_manager.emit uses. But
since `media_job_manager.emit()` owns its own session, the fix is to move the mirroring
into that same session.

**Implementation approach — co-locate the mirror in a single session:**

Refactor `media_job_manager.emit()` to accept an optional `mirror_job_id` parameter.
When set, it mirrors the event into the generic job system in the same DB session
instead of requiring a second session in the bridge.

OR, simpler: have the bridge pass the DB session through. Change the emit signature
from a closure that opens its own session to one that reuses the session from
media_job_manager.emit.

**Recommended approach (least invasive):** Make `media_job_manager.emit()` accept a
`mirror_generic_job_id` parameter, and do the mirroring inside its existing session:

```python
# In media_job_manager.emit(), after persisting the MediaJobEvent:
if mirror_generic_job_id is not None:
    from marquee.core.jobs import job_manager
    current = await db.get(Job, mirror_generic_job_id)
    if current is not None:
        current.current_stage = stage
        if progress is not None:
            current.progress = progress
        event_data = { ... }
        event = JobEvent(...)
        db.add(event)
        # Don't commit separately — the caller commits once
```

Then in the bridge, the emit becomes a thin wrapper that calls
`media_job_manager.emit()` with the mirror parameter, eliminating the redundant
second session entirely.

### Task C2: Cap maximum concurrent mutation operations

**Objective:** Limit how many `media_write` resource slots are available to prevent
the worker from launching too many concurrent remux/encode subprocesses.

**Files:**
- Modify: `marquee/config.py` — adjust default or review existing

**Current state:** `JOB_MEDIA_WRITE_SLOTS` (default likely 1–2) controls how many
concurrent media-write jobs can run. Check the actual default in `marquee/config.py`.

**Implementation:**
1. Verify `JOB_MEDIA_WRITE_SLOTS` is set to 1 or 2 (reasonable for spindle/NAS storage)
2. All media mutations (`audio_remove`, `track_remove`, `subtitle_remove`, etc.) request
   `media_write` resource — so they're already gated
3. Check `JOB_WORKER_CONCURRENCY` — if this is higher than media_write slots, workers
   sit idle which is fine

**The real guard is already in place** — the `_reserve()` method in `manager.py` checks
resource capacity before claiming. If media_write=1 and a track_remove is running, no
other track_remove will start.

**But there's still the issue:** All 3 workers can run concurrently if they claim
different resource types (e.g., media_read for subtitle_scan while media_write for
track_remove). The DB pool exhaustion happens because the bridge's per-emit session
pattern doesn't scale with N concurrent workers.

### Task C3: Add connection-pool monitoring and health checks

**Objective:** Expose pool stats (active/idle/overflow connections) so the operator
can diagnose pool exhaustion before it causes 500s.

**Files:**
- Modify: `marquee/database.py` — expose pool stats
- Modify: `marquee/api/routes/system.py` — add pool metrics to /api/system/metrics

**Implementation:**
```python
# In database.py or a metrics helper:
def pool_stats() -> dict:
    from sqlalchemy import inspect
    engine = _get_session_factory().engine
    pool = engine.pool
    return {
        "size": pool.size(),
        "checked_in": pool.checkedin(),
        "overflow": pool.overflow(),
        "total": pool.total(),
    }
```

Add to the existing `/api/system/metrics` response under a `db_pool` key.
The frontend `SystemMetricsPanel` already renders JSON metrics — no frontend
changes needed.

### Task C4: Add jitter to poll intervals to prevent thundering-herd DB connections

**Objective:** Multiple frontend clients polling `/api/jobs` every 1.5s in exact
sync can create connection spikes. Add per-client jitter.

**Files:**
- Modify: `frontend/src/lib/jobs.ts` — `trackJob()` function

**Implementation:** In the `pollMs` timer setup (line 109), add ±20% random jitter:
```typescript
const jittered = pollMs * (0.8 + Math.random() * 0.4);
timer = setInterval(() => void poll(), jittered);
```

This is a low-effort, high-impact change that smooths out connection demand.

---

## Group D — Root-Cause Deep Dive for Backend Unresponsiveness

### Task D1: Verify the subprocess doesn't block the event loop

**Objective:** Confirm that `proc.communicate()` at mutation.py:747 runs on the
event loop and doesn't block other coroutines.

**Analysis:** `asyncio.create_subprocess_exec` + `proc.communicate()` is async-native
— it yields to the event loop while waiting for the subprocess. This is fine.

**But:** If nice/ionice fails (e.g., binaries not found), the copy/remux runs at
full priority and can saturate disk I/O. The `_nice_ionice_prefix()` function
(mutation.py:104–118) is a best-effort guard. Verify it's actually working:
```bash
# Check if nice and ionice are on PATH
which nice ionice
```

### Task D2: Check for DB pool exhaustion via asyncpg defaults

**Objective:** Confirm the pool size and overflow limits.

**Files:**
- Inspect: `marquee/database.py` — pool configuration

**Expected:** `pool_size=5` or `pool_size=10`, `max_overflow=10` to `20`. With 3 workers
× emit sessions + API request sessions, 20 total connections can saturate quickly.

**If pool_size is low (5), raise it to 10–15 and max_overflow to 20.** But the
structural fix (Task C1) is the permanent solution — raising pool size is a band-aid.

### Task D3: Trace a complete batch job to find every DB session open point

**Objective:** Map every `async with factory() as db:` in the full call path of a
batch media operation.

**Call path to trace:**
1. `DurableWorker._run_claim()` — opens session for claim
2. `handler(current)` → `_run_media()` — opens **multiple sessions**:
   - Session 1: flip status to running + commit (line 34–41)
   - emit() closure: **one session per call** (lines 48–72)
   - Session 2: `dispatch()` → `mutation.execute_job()`
     - `execute_job` uses its own `db` session passed from dispatch
   - Session 3: failure handling (lines 83–112)
   - Session 4: batch progress update (lines 113–117)

**Worst case for a batch of N jobs:** 
- N × (1 flip session + 1 dispatch session + ~3 emit sessions per mutation + 
  1 finalize session) = N × ~6 sessions
- With 3 concurrent: 18 sessions cycling rapidly

**Task C1 addresses the emit sessions specifically.** After C1, the per-job session
count drops from ~6 to ~4.

---

## Group E — Frontend Progress UX

### Task E1: Show message alongside stage in RunProgress

**Objective:** The `detail.message` field from the SSE event should appear in
the progress bar UI, giving context like "Removing 2 subtitles (eng, spa) — mkvmerge".

**Files:**
- Modify: `frontend/src/lib/components/RunProgress.svelte` — add message rendering

**Implementation:** In the `rp-meta` div (line 94), add BEFORE the stage label:
```svelte
{#if detail.message}
    <span class="rp-message">{detail.message}</span>
{/if}
```

Style: `color: var(--text); font-weight: 500;` — more prominent than the stage chip.

### Task E2: Add track-count chip to media job rows in JobList

**Objective:** The `JobList.svelte` component (at `frontend/src/lib/components/subtitles/JobList.svelte`) shows a "Mutating" stage label. Show what's being mutated.

**Files:**
- Modify: `frontend/src/lib/components/subtitles/JobList.svelte` — line 145

**Implementation:** Change the stage column from:
```svelte
<span class="stage" title={job.progress?.message}>{job.progress?.stage || 'Mutating'}</span>
```
to show the message if available:
```svelte
<span class="stage" title={job.progress?.message}>
    {job.progress?.message || job.progress?.stage || 'Mutating'}
</span>
```

---

## Group F — Queue Scheduling Verification & Optimization

### Task F1: Verify non-conflicting jobs skip ahead of blocked jobs

**Objective:** Confirm that the `claim_next()` algorithm in `manager.py:349–396`
correctly allows a newly-queued, non-conflicting job to be claimed ahead of
older queued jobs that are blocked awaiting resources held by running jobs.

**How it works today:**
1. `claim_next()` selects the top 32 candidates ordered by `priority DESC, created_at ASC`
2. For each candidate, it tries `_reserve()` — acquires row-level locks on all requested
   resources and checks capacity
3. If reservation fails, the job is marked `waiting_resource` and the loop continues
   to the NEXT candidate (line 384–386)
4. The next candidate could be a different type with non-overlapping resource needs —
   and it WILL be claimed if its resources are available

**This IS the skip-ahead behavior the user wants.** But it needs verification:

**Verification steps:**
1. Write a test: enqueue 3 `media_write` jobs (conflicting) + 1 `media_read` job
   with a later `created_at`. Verify the `media_read` job is claimed and runs
   while the `media_write` jobs sit in `waiting_resource`.
2. Verify `waiting_resource` jobs are re-considered on the next claim cycle:
   `claim_next()` checks `Job.status.in_(("queued", "retry_scheduled", "waiting_resource"))`
   at line 356 — so yes, they are.
3. Verify priority ordering still works: a high-priority conflicting job should
   still be tried before a low-priority non-conflicting job — this is correct
   behavior (priority is the user's intent).

**Files:**
- Inspect: `marquee/core/jobs/manager.py` — `claim_next()` and `_reserve()` methods
- Read: `marquee/config.py` — all `JOB_*_SLOTS` settings
- Tests: Create `tests/test_job_scheduling.py` if one doesn't exist

### Task F2: Prevent `waiting_resource` starvation

**Objective:** Ensure jobs marked `waiting_resource` don't sit in that state
indefinitely if their resources never become free.

**Current behavior:** A job transitions from `queued` → (claim attempt) →
`waiting_resource` → (next claim cycle) → claimed or `waiting_resource` again.
This is a tight loop — the worker polls every `JOB_POLL_SECONDS` (default ~2s)
and re-evaluates all `waiting_resource` jobs. Each cycle re-checks resource
availability. This looks correct and should not starve.

**Edge case to verify:** If a `media_write` job sits in `waiting_resource` while
a long-running `media_write` job holds the slot, does it eventually get claimed
when the running job finishes? Trace: running job finishes → `finish()` →
`_release()` → frees resource → next `claim_next()` cycle picks it up. Yes.

### Task F3: Optimize the claim batch size for large queues

**Objective:** `claim_next()` fetches 32 candidates at a time. If there are 200+
queued jobs and the first 32 are all blocked on the same resource, the worker
won't see the non-conflicting jobs deeper in the queue until the next claim cycle.

**Implementation:** After a `claim_next()` cycle where 0 out of 32 candidates
were claimed (all blocked), double the batch size for the next cycle (up to
a max of 256) to reach deeper into the queue. Reset to 32 on the first successful
claim. This is a progressive-lookahead optimization:

```python
# In DurableWorker.run() or claim_next():
consecutive_empty_cycles = 0
batch_size = 32
# ...
if claim is None:
    consecutive_empty_cycles += 1
    batch_size = min(32 * (2 ** consecutive_empty_cycles), 256)
else:
    consecutive_empty_cycles = 0
    batch_size = 32
```

**Files:**
- Modify: `marquee/core/jobs/manager.py` — accept `limit` param in `claim_next()`
- Modify: `marquee/core/jobs/worker.py` — track consecutive empty cycles

---

## Group G — Batch Quick-Delete Language Removal Bug

### Task G1: Trace a batch quick-delete from UI to execution

**Objective:** Understand the full flow when the user clicks "Remove" on a
language/languages in the batch quick-delete UI, and find where languages
are being dropped/skipped.

**Flow to trace:**
1. Frontend: `audio-subs/+page.svelte` or `SubtitleMovieList.svelte` — the
   batch quick-delete UI that selects languages to remove across many files
2. The API call: `POST /api/media-jobs/batch` or similar — builds a plan per file
3. `media_job_manager.create_job()` — creates one MediaJob per file per language?
4. The plan review flow: `mutation.build_plan()` → `build_argv` → removes tracks
5. HTML UI uses a "quick action" button with preset languages — check what
   `track_ids` are sent in the request

**Suspicious areas:**
- **Plan deduplication:** `supersede_planned_media_jobs()` (media_jobs/manager.py:263)
  cancels older planned jobs with the same operation+media_file_id. If a language-batch
  creates one job per file per language, and two languages target the same file,
  one could supersede the other.
- **Idempotency key collision:** `create_job()` uses `idempotency_key` to prevent
  duplicates. If the key is derived from `(file_id, operation)` without including the
  set of track_ids, a second language removal on the same file could return the
  first job (which only removes one language) instead of creating a new one.
- **Single-job-per-file assumption:** The `create_job` in `media_jobs/manager.py`
  creates ONE MediaJob per operation per media_file_id. It does NOT merge track_ids.
  If the batch UI creates separate jobs (one per language per file), this should
  work — but if it creates one job with all track_ids, verify the plan handles
  multi-track removal correctly (it does — `RemovePlan` has lists of ids).
- **Batch parent-child relationship:** `create_batch()` in `manager.py:124` creates
  a parent job + child jobs. Each child maps to one `_run_media` invocation.
  If children are created for only a subset of languages, the rest are silently dropped.

### Task G2: Inspect the batch plan/build flow for track_id handling

**Objective:** Read the code path from batch API → plan building → job creation
and verify all selected track_ids end up in the execution.

**Files to read:**
- `marquee/api/routes/media_jobs.py` — the batch creation endpoint
- `marquee/core/subtitles/mutation.py` — `build_plan()` around lines 336–528
  (specifically how `track_ids` from params is processed)
- `marquee/core/media_jobs/manager.py` — `create_job()` idempotency_key generation

**Key checkpoints:**
1. Does `build_plan()` reject unknown track_ids silently or raise an error?
   - It raises `PlanError(f"unknown track ids: {sorted(unknown)}")` — good.
   - BUT: does the caller catch this and skip the file, or abort the batch?
2. Does the batch API create one child job per file or per file×language?
3. If a file has 3 subtitles for the same language (e.g., eng forced, eng SDH, eng
   commentary), does "Remove English" in the quick-delete UI select ALL three or
   just one? Check the frontend logic.

### Task G3: Fix the root cause

**Objective:** Once the bug is found, fix it and add a regression test.

**Likely fix locations (to be confirmed by investigation):**

If the issue is **idempotency key collision** (most likely):
- In `media_jobs/manager.py::create_job()`, include the sorted `track_ids` in the
  idempotency_key hash so different sets of track_ids produce different keys.
  Current key format (around line 282 in subtitle_scan_all):
  ```
  key = f"manual:subtitle-scan:{mf.id}:{job.id}"
  ```
  Fix: for removals, include a hash of the track_ids:
  ```
  import hashlib
  track_hash = hashlib.md5(",".join(sorted(str(t) for t in track_ids)).encode()).hexdigest()[:8]
  key = f"manual:{operation}:{mf.id}:{track_hash}"
  ```

If the issue is **supersede cancelling sibling jobs**:
- `supersede_planned_media_jobs()` cancels all planned jobs matching `(operation,
  media_file_id)`. If the batch creates jobs A and B for the same file with
  different languages, and they're created sequentially, B might cancel A.
  Fix: only supersede if the request is identical (same set of track_ids).

If the issue is **frontend not selecting all tracks**:
- Check `SubtitleMovieList.svelte` or the batch quick-delete component for the
  track filtering logic. The "Remove English" button should select all tracks
  where `language_tag == 'eng'`, not just the first one.

**Test:** After fixing, run a batch quick-delete for "Remove English" on a file
that has 3 English subtitle tracks (forced, SDH, commentary). Verify all 3 are
removed.

---

## Group H — Durable Cancellation for ALL Job Types

### Task H0: Audit which handlers respect cancellation and which don't

**Objective:** Catalog every handler and its cancel-check behavior.

**Current cancel flow:**
1. `request_cancel()` → sets `job.cancel_requested = True` in DB
2. If job is pre-execution: `_cancel_before_execution()` → immediate cancel
3. If job is running: `_bridge_media_cancel()` for media jobs, or just sets the flag
4. The running handler must **poll the flag** to actually stop — otherwise it ignores it

**Handlers that DO check cancel_requested:**

| Handler | Mechanism | How often |
|---|---|---|
| `poster_pipeline_batch` | `_watch_cancel()` asyncio task polls DB every 2s, passes `cancel_event` to `run_batch()` | 2s |
| `poster_deploy_reset` | Checks `current.cancel_requested` between each movie | Per iteration |
| `pipeline_backup_posters` | Checks `current.cancel_requested` between each movie | Per iteration |
| Media mutations (`_run_media` → `mutation.py`) | `_raise_if_cancel_requested()` called between stages | At stage boundaries |

**Handlers that IGNORE cancel_requested (broken cancel):**

| Handler | What it does | Why cancel doesn't work |
|---|---|---|
| **`taste_rebuild`** | `asyncio.to_thread(rebuild_profile)` + `asyncio.to_thread(train_from_labels)` | No cancel poll — thread runs to completion no matter what |
| **`taste_map`** | `asyncio.to_thread(build_map)` | No cancel poll |
| **`learned_head_train`** | `asyncio.to_thread(train_from_labels)` | No cancel poll |
| **`library_sync`** | Network calls (Radarr/Sonarr/TMDB) | No cancel poll between phases |
| **`subtitle_scan_all`** | Loop over media files creating child jobs | No cancel poll between files |
| **`letterbox_detect`** | `letterbox_manager.detect_and_store()` | Detector runs ffprobe + analysis — no cancel poll |
| **`poster_pipeline`** (single movie) | `run_manager._execute()` via `JobProgressBridge` | No cancel watcher (only the batch variant has it) |
| `backup_create` | Short-lived, probably fine | Acceptable — completes too fast |
| `radarr_upgrade` | Short-lived, probably fine | Acceptable |
| `poster_heal` / `letterbox_heal` | Instant handlers, run inline | Acceptable |

### Task H1: Add cancel watcher to the worker loop (universal fix)

**Objective:** Instead of adding per-handler cancel watchers (which is fragile and easy to miss),
add a single cancel watcher in `DurableWorker._run_claim()` that monitors the job's
`cancel_requested` flag and raises `asyncio.CancelledError` on the handler task when
cancellation is requested. This works for ALL handler types with zero per-handler changes.

**Files:**
- Modify: `marquee/core/jobs/worker.py` — `_run_claim()` method

**Implementation:** In `_run_claim()`, add a watcher task alongside the existing
`renew_lease()` heartbeat task:

```python
async def _watch_cancel(db_factory, job_id: str, handler_task: asyncio.Task) -> None:
    """Poll the cancel_requested flag and cancel the handler when it's set."""
    while not handler_task.done():
        await asyncio.sleep(2.0)  # check every 2s — same as poster_pipeline_batch
        try:
            async with db_factory() as db:
                current = await db.get(Job, job_id)
            if current is not None and current.cancel_requested:
                handler_task.cancel()
                return
        except Exception:
            pass  # transient DB error — keep watching

# In _run_claim(), after creating the handler task:
handler_task = asyncio.ensure_future(
    asyncio.wait_for(handler(current), timeout=timeout)
)
cancel_watcher = asyncio.create_task(
    _watch_cancel(factory, current.id, handler_task)
)
```

When the watcher cancels the handler task, the `CancelledError` is caught by the
existing `except asyncio.CancelledError` block in `_run_claim()` (line 108-109),
which calls `job_manager.interrupt()` — marking the job as `interrupted`.

**But we want cancellation, not interruption.** Modify the CancelledError handler:

```python
except asyncio.CancelledError:
    # Check whether this was a user-requested cancel or a shutdown
    async with factory() as db:
        current = await db.get(Job, job.id)
        if current is not None and current.cancel_requested:
            # User requested cancel — terminalize properly
            job.cancel_requested = True
            await job_manager.fail(db, current, current_attempt,
                RuntimeError("cancelled by user"), allow_retry=False)
        else:
            await job_manager.interrupt(db, current, current_attempt,
                reason="worker shutdown")
    raise
```

Wait — the `fail()` method already checks `cancel_requested` (line 547-550) and
sets status to `cancelled`. So if we just call `fail()` with `allow_retry=False`,
it will see the flag and set status to `cancelled`. But `CancelledError` isn't
a normal exception — the finally block in `_run_claim()` still runs, and the
CancelledError propagates. Let's handle this more carefully.

**Better approach — don't cancel the task, use a threading.Event:**

For handlers that use `asyncio.to_thread()`, the CancelledError can't interrupt
the thread. Instead, add a protocol: handlers can check `should_cancel()` if they
accept it. For handlers that don't, the watcher just sets the flag and the
handler completes naturally, then `fail()` checks the flag and marks it cancelled.

```python
# Simple approach: just let the handler finish, then fail() detects cancel_requested
# No task.cancel() needed — the fail() method at line 547 already handles this.
# The CURRENT problem is that taste_rebuild takes 5-10 minutes and the user
# thinks cancel doesn't work because nothing happens for 5-10 minutes.
```

**The real fix for taste_rebuild etc.:** The handler must become cancel-aware.
There's no way around it — `asyncio.to_thread()` runs in a separate OS thread and
can't be interrupted from the event loop. The handler must pass a `threading.Event`
to the thread function and the thread function must check it periodically.

### Task H2: Make long-running CPU handlers cancel-aware

**Objective:** Add cancel polling to the heaviest handlers that currently ignore it.

**Files to modify:**

1. **`taste_rebuild`** (`builtin_handlers.py:172-208`):
   ```python
   cancel_event = threading.Event()
   
   async def _watch():
       while not cancel_event.is_set():
           await asyncio.sleep(2.0)
           async with factory() as db:
               current = await db.get(Job, job.id)
           if current is not None and current.cancel_requested:
               cancel_event.set()
               return
   
   watcher = asyncio.create_task(_watch())
   try:
       await asyncio.to_thread(rebuild_profile, training_dir=training_dir,
                               cancel_event=cancel_event)
       if cancel_event.is_set():
           return {"rebuild": "cancelled"}
       # ... continue with head training
   finally:
       cancel_event.set()
       watcher.cancel()
   ```
   
   Then `rebuild_profile()` must accept `cancel_event` and check it periodically:
   ```python
   def rebuild_profile(training_dir, cancel_event=None):
       for img_path in images:
           if cancel_event and cancel_event.is_set():
               raise CancelledError("taste rebuild cancelled")
           # ... process image
   ```

2. **`taste_map`** (`builtin_handlers.py:211-215`):
   ```python
   cancel_event = threading.Event()
   # Same watcher pattern, pass cancel_event to build_map()
   result = await asyncio.to_thread(build_map, cancel_event=cancel_event)
   if cancel_event.is_set():
       return {"build": "cancelled"}
   ```

3. **`learned_head_train`** (`builtin_handlers.py:431-441`):
   ```python
   cancel_event = threading.Event()
   # Same watcher pattern
   head, info = await asyncio.to_thread(train_from_labels, cancel_event=cancel_event)
   if cancel_event.is_set():
       return {"trained": False, "cancelled": True}
   ```

4. **`library_sync`** (`builtin_handlers.py:218-257`):
   Already runs async network calls — just add a cancel check between sync phases:
   ```python
   async with factory() as db:
       current = await db.get(Job, job.id)
   if current is not None and current.cancel_requested:
       return {"sync": "cancelled"}
   ```

5. **`poster_pipeline`** (single-movie, `builtin_handlers.py:316-359`):
   Add the same `_watch_cancel()` pattern as the batch variant already has.

### Task H3: Clean up partial/temp files on cancellation

**Objective:** When a job is cancelled, clean up any partial output (temp files,
half-written posters, partial remux files) so no junk is left behind.

**Files to audit for cleanup:**

| Handler | What needs cleanup |
|---|---|
| Media mutations (`mutation.py`) | `out.unlink(missing_ok=True)` already happens on error paths and cancel checks — verify the cancel path through `_raise_if_cancel_requested()` cleans up `out` |
| `poster_pipeline` / batch | Pipeline `_clear_generated_outputs()` — verify it runs on cancel path |
| `taste_rebuild` | If `training_dir` is a temp dir from `_gather_library_posters()`, the `finally: tmp.cleanup()` block already handles this — but only if we reach `finally` (which we do with cancel) |
| `letterbox_reencode` | Partial encode file — `media_jobs/handlers.py::_letterbox_reencode` should clean up `.partial` files |
| `subtitle_generate` | Partial generation output — check `generation.py` |

**Implementation:** Add a centralized `cleanup_on_cancel` hook to the job system.
Each handler can optionally define what paths/tempfiles to clean up. The worker's
cancel path calls this hook.

Simpler approach: each handler's existing `finally` blocks already handle cleanup.
If we raise `CancelledError` properly, the finally blocks run. The issue is only
with `asyncio.to_thread()` calls that can't be interrupted — they will finish
their work, write output, and THEN the cancel flag is checked. For these, add a
pre-return check that deletes partial output:

```python
if cancel_event.is_set():
    # Clean up any partial work before returning cancelled
    if tmp_train_dir and tmp_train_dir.exists():
        shutil.rmtree(tmp_train_dir)
    return {"rebuild": "cancelled"}
```

### Task H4: Make frontend cancel buttons responsive

**Objective:** After clicking Cancel, the UI should immediately show "Cancelling..."
instead of waiting for the next poll cycle.

**Files:**
- Modify: `frontend/src/lib/jobs.ts` — `trackJob()` 
- Modify: `frontend/src/lib/components/RunProgress.svelte` — cancel button

**Current behavior:** The cancel button calls `cancelJob()` which POSTs to the API,
then waits for the poll/SSE to reflect the status change. This can take up to 1.5s
(poll interval) or longer.

**Implementation:**
1. In `RunningJobCard.svelte` and the job detail page, after calling `cancelJob()`,
   immediately set `liveStatus = 'cancelling'` to show the user their click was
   received.
2. In `RunProgress.svelte`, when status is `'cancelling'`, show a different visual
   (e.g., yellow pulse instead of gold, "Cancelling..." text).
3. Add a `onCancelRequested` callback to `trackJob()` that fires immediately on
   the cancel button click (before the API response).

---

## Summary of All File Changes

| File | Action | Group |
|---|---|---|
| `marquee/core/jobs/builtin_handlers.py` | Add `_update_progress()` calls for taste_rebuild, taste_map, library_sync, subtitle_scan_all | B2 |
| `marquee/core/subtitles/mutation.py` | Add `_describe_operation()` + enrich remux message | B1 |
| `marquee/core/jobs/legacy_media.py` | Co-locate mirror emit in single session (C1), add movie title (B3), enrich letterbox_reencode+subtitle_generate emits | B2, C1, B3 |
| `marquee/core/media_jobs/manager.py` | Add `mirror_generic_job_id` param to `emit()`, fix idempotency_key for multi-track removals | C1, G3 |
| `marquee/database.py` | Expose pool_stats() | C3 |
| `marquee/api/routes/system.py` | Add db_pool to /metrics | C3 |
| `marquee/core/jobs/manager.py` | Add `limit` param to `claim_next()` for progressive lookahead | F3 |
| `marquee/core/jobs/worker.py` | Track consecutive empty claim cycles, pass batch_size | F3 |
| `marquee/api/routes/media_jobs.py` | Inspect batch creation for track_id handling (G1) | G1 |
| `frontend/src/lib/jobs.ts` | Add poll jitter | C4 |
| `frontend/src/lib/components/RunProgress.svelte` | Show `detail.message` | E1 |
| `frontend/src/lib/components/subtitles/JobList.svelte` | Show message in stage column | E2 |
| `tests/test_job_scheduling.py` | Tests for queue skip-ahead + waiting_resource starvation | F1, F2 |
| `tests/test_batch_language_removal.py` | Regression test for multi-language batch delete | G3 |
| `marquee/core/jobs/worker.py` | Add cancel watcher task in `_run_claim()`, improve CancelledError handling | H1 |
| `marquee/core/jobs/builtin_handlers.py` | Add cancel_event pattern to taste_rebuild, taste_map, learned_head_train, library_sync, poster_pipeline | H2 |
| `marquee/ml/taste_trainer.py` | Accept `cancel_event` param in `rebuild_profile()` and `train_from_labels()` | H2 |
| `marquee/ml/taste_map.py` | Accept `cancel_event` param in `build_map()` | H2 |
| `marquee/core/subtitles/mutation.py` | Verify `_raise_if_cancel_requested()` cleans up `out` path | H3 |
| `marquee/core/subtitles/generation.py` | Verify cancel cleanup of partial generation output | H3 |
| `frontend/src/lib/components/RunProgress.svelte` | Show "Cancelling..." state, handle `cancelling` status | H4 |
| `frontend/src/lib/components/RunningJobCard.svelte` | Immediately set cancelling state on button click | H4 |
| `frontend/src/lib/jobs.ts` | Add `onCancelRequested` callback | H4 |

---

## Symptom → Root Cause Map

| Symptom | Root Cause | Fix |
|---|---|---|
| 500 errors during batch track_remove | DB connection pool exhausted by per-emit sessions in legacy bridge | C1: single-session mirror emit |
| "Remuxing" shows no detail | Mutation doesn't pass track info in stage message | B1: `_describe_operation()` |
| Backend stops responding mid-batch | Combined I/O saturation + pool exhaustion | C1 + verify nice/ionice (D1) |
| CancelledError closing connections | Connection acquired, held too long, cancelled by pool timeout | C1 + C2 + C4 |
| GC cleaning non-checked-in connection | Session not properly closed before task cancellation | C1 (fewer sessions = fewer leaks) |
| Progress bar shows "Mutating" generically | JobList reads `job.progress.stage` not `job.progress.message` | E2 |
| Non-mutation jobs (taste, sync) show no progress at all | `builtin_handlers.py` handlers have no incremental emit calls | B2 |
| Batch quick-delete doesn't remove all selected languages | Idempotency key collision (file×op without track_ids hash) or supersede cancelling sibling jobs | G1–G3 |
| Non-conflicting jobs stuck behind conflicting queued jobs | `claim_next()` batch size too small to reach non-conflicting jobs deep in queue | F3 |
| Cancel button on taste_rebuild does nothing | Handler runs `asyncio.to_thread()` with no cancel polling — thread runs to completion ignoring `cancel_requested` | H0–H2 |
| Cancel works for media jobs but not CPU handlers | Only `poster_pipeline_batch` and `_raise_if_cancel_requested()` check the flag; taste/learn/sync handlers don't | H0–H2 |
| Cancel button click shows no immediate feedback | UI waits for poll/SSE cycle (1.5s+) before showing status change | H4 |
| Partial files left after cancel | No centralized cancel-cleanup hook; each handler responsible for its own cleanup | H3 |

---

## Verification Checklist

1. **No pool exhaustion:** Run a batch of 20+ track_remove operations. Verify zero
   `Pool limit` or `CancelledError` messages in logs.
2. **Progress enrichment:** Hover over a running track_remove job in the Projection
   Room. The progress bar shows "Removing 1 English subtitle (SDH) — mkvmerge"
   instead of "Remuxing".
3. **DB pool metrics:** `GET /api/system/metrics` includes `db_pool` with
   size/checked_in/overflow/total values.
4. **No regressions:** `pytest` passes. `ruff check marquee tests` clean.
5. **Batch resilience:** During a running batch, the dashboard, HDR list, and
   library pages load within 2 seconds (no 500s or timeouts).
6. **Concurrent safety:** Run 2 overlapping batches of different operations
   (subtitle_generate + track_remove). Both complete without errors.
7. **Queue skip-ahead:** Enqueue 5 conflicting media_write jobs, then enqueue a
   media_read job. Verify the media_read job runs immediately while the blocked
   jobs sit in `waiting_resource`. Check via `/api/jobs?status=waiting_resource`.
8. **Non-mutation progress:** Trigger a `taste_rebuild` job. The progress bar
   shows "Gathering exemplars..." → "Extracting features..." → "Training taste
   model..." instead of a silent spinning bar.
9. **Library sync progress:** Trigger a `library_sync`. Shows "Syncing movies
   from Radarr..." → "Syncing series from Sonarr..." → "Enriching with TMDB..."
10. **Batch quick-delete all languages:** Select "Remove English" on a file with
    3 English subtitle tracks (forced, SDH, commentary). Verify all 3 are present
    in the plan and all 3 are removed after execution. Count tracks before/after.
11. **Queue scheduling regression test:** `pytest tests/test_job_scheduling.py -v`
    — all tests pass.
12. **Batch language removal regression:** `pytest tests/test_batch_language_removal.py -v`
    — verifies multi-track removal correctness.
13. **Cancel taste_rebuild:** Start a `taste_rebuild` job, click Cancel within 5 seconds.
    Verify: the UI immediately shows "Cancelling...", the job transitions to `cancelled`
    within 15 seconds (not 5-10 minutes), and no partial model files are left in
    `data/models/` or temp directories.
14. **Cancel taste_map:** Start a `taste_map` job, click Cancel. Verify it terminates
    and the status becomes `cancelled`.
15. **Cancel learned_head_train:** Same verification as taste_rebuild.
16. **Cancel library_sync:** Click Cancel during the TMDB enrichment phase. Verify it
    stops within one sync phase (not after all phases complete).
17. **Cancel media mutation mid-remux:** Start a `track_remove` on a large file, click
    Cancel during the mkvmerge remux. Verify: `out` partial file is deleted, job status
    is `cancelled`, source file is untouched.
18. **Cancel cleanup:** After cancelling any job, verify no `.partial`, `.marquee.*`,
    or temp files remain in the working directories.
