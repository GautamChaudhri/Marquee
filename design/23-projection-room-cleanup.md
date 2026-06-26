# 23 — Projection Room: Resource & Worker Row Cleanup

The Projection Room (`/projection-room`) was added to monitor the durable job
platform. Two panels — **Resource pools** and **Workers** — render every row
from their respective tables with no filtering, exposing unbounded row
accumulation in `job_resources` and `job_workers`.

---

## Problem 1: Per-file mutex rows dominate the Resource pools panel

### What users see

Hundreds/thousands of `media-file:NNN` stat cards showing `0/1 — available`. A
few logical pools (GPU, media_read, etc.) are buried among them.

### Root cause

**Row creation** — Every job that operates on a specific media file (subtitle
scan, DoVi analysis, letterbox detect/apply/re-encode, HDR analysis) includes a
resource key `media-file:{media_file_id}` in its `resource_request`. When
`JobManager.create()` runs, it auto-creates a `JobResource` row for that key if
one doesn't already exist (`manager.py:99-101`):

```python
for key in request:
    if await db.get(JobResource, key) is None:
        db.add(JobResource(key=key, capacity=self.resource_capacity(key)))
```

**No cleanup** — Unlike `Job` rows (purged by `job_retention_purge`), there is
no mechanism that ever deletes `JobResource` rows. Once created, they are
permanent.

**Unfiltered metrics endpoint** — `GET /api/jobs/metrics` (`jobs.py:199-200`)
returns every `JobResource` row with no filtering:

```python
resources = (await db.execute(select(JobResource))).scalars().all()
```

**Flat rendering** — `ResourcePoolPanel.svelte` renders every resource as a
`StatCard` in a CSS grid. No grouping, hiding, or collapsing for per-file
entries.

### Why per-file mutexes exist (they are correct)

The design document 15 explicitly calls them out:

> per-physical-file `media-file:{media_file_id}` (cap 1). Every operation that
> touches one file — letterbox detect/apply/remove **and** the subtitle/reencode
> media jobs — shares the `media-file:{id}` key, so a read-only detect can never
> overlap a crop write or a re-encode of that file.

This is sound. The problem isn't the mutex pattern — it's that the declaration
rows persist forever and get rendered in a UI that serves a different purpose.

### Where per-file keys are assigned

| Call site | Key |
|-----------|-----|
| `marquee/core/media_jobs/manager.py:208` | `media-file:{media_file_id}` (all legacy media jobs) |
| `marquee/api/routes/hdr.py:647,696,752` | `media-file:{media_file.id}` (DoVi analysis) |
| `marquee/api/routes/letterbox.py:75` | `media-file:{media_file.id}` (letterbox detect/apply) |

### The six "logical" pools (what the panel should show)

Bootstrap resources seeded by `bootstrap_resources()`:

| Key | Default capacity | Purpose |
|-----|-----------------|---------|
| `gpu` | 1 | GPU-accelerated work (CLIP, PaddleOCR) |
| `media_read` | 2 | Concurrent read-only file access |
| `media_write` | 1 | Write/modify file access (shared with transcode) |
| `transcode` | 1 | ffmpeg encode/remux (shared with media_write) |
| `network_external` | 4 | Outbound HTTP calls (TMDB, Radarr, Sonarr) |
| `maintenance_exclusive` | 1 | Global admission fence for maintenance tasks |

### Recommendations

1. **Filter `media-file:*` out of the `/metrics` response** (or only include rows
   with `in_use > 0`). The logical pools are what operators care about; per-file
   contention is visible inline on queued jobs via the "waiting on" chip.

2. **Add periodic cleanup** to the existing `job_retention_purge` handler (or a
   separate scheduled job): delete `JobResource` rows for keys matching
   `media-file:*` where `in_use = 0` (no active reservations). Safe because
   the row is re-created on next use — it's a stateless capacity declaration,
   not a stateful object.

3. **Optionally**: group `media-file:*` into a single summary card in the UI
   (e.g. "Media file locks: 3 active / 1,847 total") if you want visibility
   into file-level contention without the wall.

---

## Problem 2: Historical worker rows accumulate without bound

### What users see

Dozens/hundreds of worker rows, most with status `stopped` or `dead`, all
showing "heartbeat Xh ago — stale". Only 1-2 are actually `running`.

### Root cause

**Unique ID on every start** — `DurableWorker.__init__()` (`worker.py:31`)
generates a UUID:

```python
self.id = f"{socket.gethostname()}-{uuid4().hex[:12]}"
```

Every process restart (clean shutdown, crash, Docker container recycle) produces
a **new** `JobWorker` row. Docker container hostnames are the container hash, so
they change on every container restart, producing yet more distinct IDs.

**No row deletion** — The `recover()` method in `manager.py:719-723` transitions
stale workers to `status = "dead"` but never DELETEs them:

```python
await db.execute(
    update(JobWorker)
    .where(JobWorker.status.notin_(("stopped", "dead")), JobWorker.heartbeat_at < cutoff)
    .values(status="dead")
)
```

Design doc 15 claims recovery "reaps `job_workers` rows whose heartbeat went
stale so `/api/jobs/metrics` doesn't report ghosts" — but "reaps" here only
means "sets status to dead", not "removes the row". The ghosts remain.

The existing `job_retention_purge` handler deletes terminal `Job` rows (and
cascading `JobAttempt`, `JobEvent`, `JobResourceReservation`) but does **not**
touch `JobWorker` or `JobResource` — those tables have no foreign-key
relationship to `Job`.

**Unfiltered metrics endpoint** — Returns all `JobWorker` rows ordered by
`heartbeat_at` desc with no status or age filter:

```python
workers = (
    (await db.execute(select(JobWorker).order_by(JobWorker.heartbeat_at.desc())))
    .scalars()
    .all()
)
```

**Cosmetic "stale" label** — `WorkerHealthPanel.svelte` applies a client-side
check: if `heartbeat_at` is older than 30s (3 × `JOB_HEARTBEAT_SECONDS`), it
appends "— stale". This is redundant for `stopped`/`dead` workers and provides
no real information beyond the status already shown.

### Worker lifecycle stack

```
Worker start → INSERT (status="running", new UUID id)
  ↓ heartbeat loop (every JOB_HEARTBEAT_SECONDS=10s) → UPDATE heartbeat_at
  ↓
Clean shutdown → UPDATE (status="stopped", final heartbeat)
Crash/OOM → row stays "running" with stale heartbeat
  ↓ next recover() run → UPDATE (status="dead")
```

No path ever DELETEs a worker row.

### Worker spawn count

- **Embedded mode** (dev): `WorkerSupervisor` spawns `max(1, JOB_EMBEDDED_WORKER_COUNT)` workers plus 1 scheduler
- **Compose mode** (prod): Dedicated `marquee-worker`/`marquee-scheduler` containers
- Respawn on crash creates a new row; the supervisor's backoff caps at 30s

### Recommendations

1. **Filter the `/metrics` response** to only return workers whose
   `status IN ('running', 'draining', 'starting')` OR whose
   `heartbeat_at` is within the last `JOB_LEASE_SECONDS` (60s). This hides
   stopped/dead/stale workers without losing truly live ones.

2. **Add periodic cleanup** to `job_retention_purge`: DELETE `JobWorker` rows
   where `status IN ('stopped', 'dead')` AND `heartbeat_at` is older than N
   days (e.g., 7 days — long enough for post-mortem inspection).

3. **Guard against stale `running` rows**: The existing `recover()` transition
   to `dead` works, but if a worker row somehow stays `running` forever without
   being caught, the cleanup in (2) wouldn't touch it. Consider a second pass
   that also deletes `running`/`draining` rows with heartbeat older than, say,
   24 hours (a worker stuck for 24h is not coming back).

4. **Optionally**: in the UI, separate "Live workers" from "Stopped/dead" with
   a collapsible section, or limit the stopped/dead list to the most recent N.

---

## Problem 3: Resource pool cap enforcement is configuration-driven, not DB-driven

*This is a lower-severity follow-up identified while investigating problems 1
and 2.*

### Observation

The `bootstrap_resources()` method (manager.py:49-62) seeds the six logical
pools from `settings` values on startup. However, `resource_capacity()` (a
`@staticmethod`) is called for **every** resource lookup — including
auto-created `media-file:*` keys — and always returns the live config value.

This means:
- If you change `JOB_GPU_SLOTS` in config and **don't** restart, the
  `JobResource.capacity` in the DB stays stale. `resource_capacity()` returns
  the new value but `_reserve()` reads the DB column, not the method.
- `bootstrap_resources()` only runs on worker/scheduler startup (embedded or
  dedicated), not periodically. The API's own startup does NOT call it.
- In compose mode (embedded workers disabled), the API process never calls
  `bootstrap_resources()`. If the worker container restarts after a config
  change, it updates the DB capacities — but there's a window where the UI
  shows stale numbers.

### Recommendation

This is low-priority but worth noting. The current approach works in practice
because capacity changes are rare and accompany restarts. If it becomes
problematic, `bootstrap_resources()` should be called from the API lifespan
too, not just from the worker.

---

## Problem 4: Audio track removal mislabeled as "Subtitle Remove"

### What users see

A job that removed only audio tracks (no subtitles) shows `Subtitle Remove` in
the Projection Room's Running/History tabs, the JobList component, and the job
detail page. There is no way to visually distinguish audio-only, subtitle-only,
or mixed removal jobs.

### Root cause

**No separate `audio_remove` operation** — The system reuses `subtitle_remove`
for both subtitle and audio track deletion. Every layer treats them identically:

| Layer | File:line | What happens |
|-------|-----------|--------------|
| API model | `subtitles.py:150` | `PlanRequest.operation` only lists `subtitle_remove \| subtitle_embed \| subtitle_metadata \| audio_reorder` — no `audio_remove` |
| Plan builder | `mutation.py:346` | `build_plan()` handles `operation in ("subtitle_remove", "track_remove")` as one branch, adding audio removal logic inside it |
| ARGV builder | `mutation.py:674` | `_build_argv()` again checks `operation in ("subtitle_remove", "track_remove")`, building a combined `RemovePlan` with both subtitle and audio deltas |
| Alias map | `manager.py:28-30` | `_OPERATION_ALIASES = {"track_remove": "subtitle_remove"}` — `track_remove` is rewritten to `subtitle_remove` before DB storage |
| Handler table | `handlers.py:120-123` | Both `"track_remove"` and `"subtitle_remove"` map to the same `_mutate` handler |
| Generic job type | `manager.py:create_job()` | The generic `Job.type` is set to the (aliased) operation — e.g. `"subtitle_remove"` — and this is what the frontend renders |
| Frontend display | `RunningJobCard.svelte:20-25` | `humanizeType(job.type)` splits on `_` and capitalizes: `subtitle_remove` → "Subtitle Remove" |
| Frontend display | `HistoryTable.svelte:66` | Same `humanizeType(job.type)` call |
| Frontend display | `JobList.svelte:123` | `job.operation.replace('subtitle_', '').toUpperCase()` — strips the `subtitle_` prefix |

**Why audio and subtitle removal share a code path** — Both operations use the
same `RemovePlan` + mkvmerge/ffmpeg remux with track exclusion. The only
difference is which track indices are passed (`track_ids` for subtitles,
`audio_stream_indices` for audio). The remux handles both in one pass, which is
correct architecturally — the issue is purely labeling.

### The existing `audio_reorder` precedent

There IS already an audio-specific operation: `audio_reorder` (added for audio
stream reordering). It has its own handler entry, its own plan branch, and
appears correctly in the frontend as "Audio Reorder." This proves the pattern
works — `audio_remove` should follow the same approach.

### Recommendations

1. **Add `audio_remove` as a first-class operation** — distinct from
   `subtitle_remove`. Register it in handlers, plan, and the manager's resource
   mapping. The actual remux code path can remain shared (both operations build
   the same `RemovePlan`), but the operation label stored in the DB must
   differentiate.

2. **Keep `track_remove` as a combined operation** for cases where both audio
   and subtitle tracks are removed in one job. The frontend should display
   "Track Remove" (or "Audio + Subtitle Remove").

3. **Update the `PlanRequest` model** to accept `audio_remove` and `track_remove`
   alongside the existing operations.

4. **Remove the `_OPERATION_ALIASES` mapping** — aliasing `track_remove` to
   `subtitle_remove` erases information. If a UI sends `track_remove`, store it
   as `track_remove`.

5. **Update the frontend** `humanizeType()` already handles arbitrary
   `snake_case` strings, so new operation names work automatically. The
   `JobList` component's `job.operation.replace('subtitle_', '')` hack should
   be replaced with a proper humanize utility.

---

## Problem 5: Track detail info not surfaced in job reports

### What users see

In the job detail page (`/projection-room/jobs/{job_id}`), the "Result" section
shows only:

```json
{
  "path": "/plunder/movies/Title/Title.mkv",
  "operation": "subtitle_remove",
  "external_removed": []
}
```

There is no information about **which tracks** were removed, their languages,
whether they were audio or subtitle, or the before/after counts. The same
poverty applies to embed, extract, metadata-edit, and letterbox-reencode jobs.

### Root cause

**Minimal result return** — `execute_job()` in `mutation.py:644-648` returns
only three fields:

```python
return {
    "path": str(resolved.path),
    "operation": operation,
    "external_removed": external_result,
}
```

The `request` dict (containing `track_ids`, `audio_stream_indices`,
`audio_stream_order`, `edits`, `backup`, `allow_break`) is available at
execution time but is never included in the result.

**No cross-reference in the generic Job** — The generic `Job` record stores
`payload = {"media_job_id": "..."}` — the track details live only in
`MediaJob.request_json`. The generic Job API (`/api/jobs/{id}`) returns
`payload`, but that only contains the `media_job_id` reference. The frontend
would need a second API call to fetch the `MediaJob` row to get track info.

**The plan IS stored** — `MediaJob.plan_json` contains the full before/after
plan with track arrays, warnings, and coverage. But `serialize.py:job_dict()`
only includes it as `"plan": json.loads(job.plan_json)` — and the generic Job's
`job_summary()` doesn't include it at all.

### What should be in results (per operation)

| Operation | Result should include |
|-----------|----------------------|
| `subtitle_remove` / `audio_remove` / `track_remove` | Removed track IDs, language tags, stream indices, before/after counts |
| `subtitle_embed` | Embedded track IDs, languages, source paths |
| `subtitle_extract` | Extracted track ID, language, output path |
| `subtitle_metadata` | Edited track IDs, changed fields (old → new) |
| `audio_reorder` | Old order → new order, audio stream indices |
| `letterbox_reencode` | Cropped edges (px), encoder, preset, CRF, input/output resolution, codec |

### Recommendations

1. **Enrich `execute_job()` results** — Include the parsed `request` fields
   alongside the current minimal fields. The request dict is already parsed at
   `mutation.py:555` — propagate the relevant fields into the return value.

2. **Cross-reference `MediaJob.request_json` in the generic Job detail** — In
   `jobs.py`'s job detail endpoint, after fetching the generic `Job`, also
   fetch the linked `MediaJob` row (via `payload.media_job_id`) and include
   `request` and `plan` in the response. This gives the frontend structured
   track info without a second round-trip.

3. **Add a "Request" section to the frontend detail page** — Show the decoded
   request parameters (track IDs, languages, operation-specific settings) in a
   readable format, not just raw JSON.

4. **Surface plan in history results** — The `job_summary()` function could
   include a `request_summary` field (e.g. `"Removed 2 audio tracks (eng, jpn)"`)
   for compact display in the HistoryTable.

---

## Problem 6: Error info is sparse — no operational context on failure

### What users see

When a job fails, the "Error" section in the job detail page shows:

```json
{
  "error": "remux_failed: mkvmerge exited with code 2: ...",
  "type": "PreflightError"
}
```

While this tells you *that* remux failed, it doesn't tell you:
- Which file was being operated on (though the subject title is separate)
- Which tracks were targeted for removal/embed/edit
- What stage it was in when it failed
- What the remux command arguments were
- Whether it was an audio or subtitle operation

### Root cause

**Error capture is generic** — `legacy_media.py:82-89` catches every exception
with:

```python
except Exception as exc:
    ...
    media_job.error_json = json.dumps({"error": str(exc), "type": type(exc).__name__})
```

This throws away all contextual information available at the call site:
- `job.request_json` — the full request parameters
- `operation` — what was being done
- The current stage (available from the emit calls)
- File path (available from `resolved`)

**`PreflightError` carries structured codes** — `mutation.py:42-47` defines
`PreflightError` with a machine-readable `code` (`"remux_failed"`,
`"validation_failed"`, `"plan_stale"`, `"path_not_writable"`, etc.) plus a
human `message`. But `legacy_media.py` collapses these into `str(exc)`, losing
the structured code.

**No attempt to include stage** — The `emit` callback has already published
stage transitions to the SSE stream, so the frontend's event log shows the
sequence. But the error JSON doesn't capture what the last successful stage was.

### Recommendations

1. **Capture structured error context** — In `legacy_media.py`'s exception
   handler, build a richer error dict:

   ```python
   error = {
       "error": str(exc),
       "type": type(exc).__name__,
       "code": getattr(exc, "code", None),       # PreflightError.code
       "operation": job.operation if media_job else None,
       "file_path": str(resolved.path) if resolved else None,
   }
   ```

2. **Include request context in errors** — Parse `job.request_json` (or use the
   already-parsed `request` dict from `execute_job`) and include a summary of
   what was requested (e.g. `track_ids`, `audio_stream_indices`, `edits`). This
   makes post-mortem debugging self-contained.

3. **Record the last successful stage** — Track the current stage (already
   available via `emit` calls) and capture it on failure. The event log shows
   the sequence but the error JSON should surface the immediate context.

4. **For `PreflightError` subclasses**, preserve the `code` field in the
   structured error so the frontend can display targeted messaging (e.g.
   "Insufficient disk space" vs. generic "remux failed").

5. **Propagate structured errors to the generic Job** — Currently
   `job_manager.complete_job()` or the `_run_media` wrapper stores the
   `media_job.error_json` on the media row, but the generic `Job.error` should
   also carry a useful summary (at minimum the `code` and a one-line message).

---

## Problem 7: Job type names are raw snake_case — no human-readable labels

### What users see

Every job in the Projection Room displays its internal `job.type` value,
capitalized by splitting on underscores:

| Raw type | Current display |
|----------|----------------|
| `subtitle_remove` | Subtitle Remove |
| `subtitle_scan` | Subtitle Scan |
| `letterbox_detect` | Letterbox Detect |
| `letterbox_heal` | Letterbox Heal |
| `poster_heal` | Poster Heal |
| `dovi_analyze` | Dovi Analyze |
| `taste_rebuild` | Taste Rebuild |
| `poster_pipeline_batch` | Poster Pipeline Batch |
| `learned_head_train` | Learned Head Train |
| `pipeline_cache_clear` | Pipeline Cache Clear |
| `poster_deploy_reset` | Poster Deploy Reset |
| `job_retention_purge` | Job Retention Purge |
| `system_metrics_purge` | System Metrics Purge |
| `system_noop` | System Noop |
| `subtitle_scan_all` | Subtitle Scan All |
| `radarr_upgrade` | Radarr Upgrade |
| `letterbox_reencode` | Letterbox Reencode |
| `audio_reorder` | Audio Reorder |
| `dovi_convert` | Dovi Convert |
| `backup_create` | Backup Create |
| `taste_map` | Taste Map |
| `library_sync` | Library Sync |

This produces awkward, stilted labels: "Learned Head Train" is nonsense to an
operator, and "System Noop" reveals implementation details. Noun-verb ordering
is inconsistent ("Subtitle Remove" vs. "Backup Create").

### Root cause

**No label registry** — The `humanizeType()` function in
`RunningJobCard.svelte:20-25` and `HistoryTable.svelte:10-15` is a mechanical
transformer:

```typescript
function humanizeType(type: string): string {
    return type.split('_').map((w) => w.charAt(0).toUpperCase() + w.slice(1)).join(' ');
}
```

There is no mapping from internal job types to operator-friendly labels. The
generic Job table stores the raw type string; the API returns it as-is. The
media-job `JobList` component goes further with a hack:
`job.operation.replace('subtitle_', '').toUpperCase()` — stripping the prefix
instead of providing a real name.

### Complete job type registry

All registered job types across the codebase:

| Internal type | Registered in | Suggested label |
|--------------|--------------|-----------------|
| `poster_heal` | `builtin_handlers.py:22` | Poster Healing |
| `letterbox_heal` | `builtin_handlers.py:29` | Letterbox Healing |
| `letterbox_detect` | `builtin_handlers.py:36` | Letterbox Detection |
| `letterbox_detect_batch` | `letterbox.py:620` | Letterbox Detection (Batch) |
| `letterbox_apply` | `builtin_handlers.py:97` | Letterbox Crop Apply |
| `letterbox_apply_batch` | `letterbox.py:812` | Letterbox Crop Apply (Batch) |
| `letterbox_remove` | `builtin_handlers.py:117` | Letterbox Crop Remove |
| `letterbox_reencode` | `legacy_media.py:107` | Letterbox Re-encode |
| `backup_create` | `builtin_handlers.py:130` | Backup Creation |
| `taste_rebuild` | `builtin_handlers.py:137` | Taste Model Rebuild |
| `taste_map` | `builtin_handlers.py:176` | Taste Map Generation |
| `library_sync` | `builtin_handlers.py:183` | Library Sync |
| `subtitle_scan` | `legacy_media.py:98` | Subtitle Scan |
| `subtitle_scan_all` | `builtin_handlers.py:225` | Subtitle Scan (All) |
| `subtitle_remove` | `legacy_media.py:100` | Subtitle Removal |
| `audio_remove` | *(proposed — Problem 4)* | Audio Track Removal |
| `track_remove` | `legacy_media.py:99` | Track Removal |
| `subtitle_embed` | `legacy_media.py:101` | Subtitle Embedding |
| `subtitle_metadata` | `legacy_media.py:102` | Subtitle Metadata Edit |
| `subtitle_extract` | `legacy_media.py:103` | Subtitle Extraction |
| `subtitle_generate` | `legacy_media.py:104` | Subtitle Generation |
| `subtitle_policy` | `legacy_media.py:105` | Subtitle Policy Apply |
| `subtitle_restore` | `legacy_media.py:106` | Subtitle Restore |
| `audio_reorder` | `handlers.py:126` | Audio Stream Reorder |
| `radarr_upgrade` | `builtin_handlers.py:263` | Radarr Upgrade |
| `poster_pipeline` | `builtin_handlers.py:281` | Poster Pipeline |
| `poster_pipeline_batch` | `builtin_handlers.py:327` | Poster Pipeline (Batch) |
| `learned_head_train` | `builtin_handlers.py:396` | Learned Model Training |
| `pipeline_cache_clear` | `builtin_handlers.py:409` | Pipeline Cache Clear |
| `poster_deploy_reset` | `builtin_handlers.py:423` | Poster Deployment Reset |
| `dovi_analyze` | `dovi_handlers.py:57` | Dolby Vision Analysis |
| `dovi_analyze_batch` | `hdr.py:763` | Dolby Vision Analysis (Batch) |
| `dovi_convert` | `dovi_handlers.py:130` | Dolby Vision Conversion |
| `job_retention_purge` | `builtin_handlers.py:571` | Job Retention Cleanup |
| `system_metrics_purge` | `builtin_handlers.py:628` | Metrics Cleanup |
| `system_noop` | `handlers.py:35` | Health Check |

### Recommendations

1. **Create a shared label registry** — Add a `JOB_LABELS: dict[str, str]`
   constant in a shared location (e.g. `marquee/core/jobs/labels.py` or the
   `job_summary()` module). All frontend and backend display code references
   this single source of truth.

2. **Expose labels in the API** — The `job_summary()` function in
   `api/routes/jobs.py:129` should include a `"label"` field alongside `"type"`.
   Frontend components use `job.label` when available, falling back to the
   mechanical `humanizeType()` for unknown types.

3. **Keep `type` as the raw identifier** — The `job.type` field remains the
   stable, machine-readable string. The `label` is purely cosmetic. This means
   filtering, metrics, and DB queries are unaffected.

4. **Remove the `subtitle_` prefix hack in `JobList`** — Replace
   `job.operation.replace('subtitle_', '').toUpperCase()` with the shared label
   lookup.

5. **Add the proposed `audio_remove` type** to the registry from the start.

---

## Problem 8: Batch jobs don't show which movies they ran on, or per-movie settings

### What users see

A batch letterbox re-encode job appears in the history as a single row showing
"Letterbox Reencode" with child counts (`14/14 done`). The detail page shows
generic metadata (priority, attempts, resources). There is **no way** to see:

- Which 14 movies were in the batch
- What re-encode settings each movie used (encoder, preset, CRF, crop edges)
- Which children succeeded vs. failed, or why
- The per-movie before/after results

The same applies to all batch operations: Dolby Vision analysis batches,
subtitle policy batches, letterbox detect/apply batches, and poster pipeline
batches.

### Root cause

**Parent job stores only counts, not child references** — When a batch is
created via `job_manager.create_batch()` (`manager.py:122`), the parent's
`progress` field tracks `{children_total, children_completed, children_failed}`
but the parent payload/result doesn't reference the child job IDs or their
subjects.

**Child jobs exist but aren't linked back** — Each child `Job` row has a
`parent_id` referencing the batch parent, and a `subject_id` + `subject_type`
identifying the movie/media-file. But:

1. **No API endpoint to list a batch's children** — The `/api/jobs` endpoint
   supports `parent_id` filtering, but this isn't surfaced in the detail page.
   The detail endpoint (`jobs.py:345`) returns only the parent job's own data
   — no child list.

2. **Child payloads aren't summarized** — Each child `Job` stores its
   operation-specific payload (e.g. letterbox re-encode settings, track
   selections), but there's no route that collects and returns all children's
   payloads alongside their subject titles.

3. **`MediaBatch` exists but is unused by the generic system** — The
   `MediaBatch` model (`media_job.py:28`) tracks batch-level stats for media
   jobs, but generic batch jobs (`poster_pipeline_batch`, `letterbox_detect_batch`,
   `dovi_analyze_batch`) don't use it. They're created through `job_manager.create()`
   with `parent_type` but no `MediaBatch` row.

### Current batch patterns (all incomplete for visibility)

| Batch type | Created at | Children created how | Child settings stored where |
|-----------|-----------|---------------------|---------------------------|
| `letterbox_detect_batch` | `letterbox.py:618` | `job_manager.create_batch()` with per-movie child payloads | Each child's `Job.payload` |
| `letterbox_apply_batch` | `letterbox.py:810` | Sequential `job_manager.create()` calls, parent set manually | Each child's `Job.payload` |
| `dovi_analyze_batch` | `hdr.py:761` | `job_manager.create_batch()` with per-movie child payloads | Each child's `Job.payload` |
| `poster_pipeline_batch` | `pipeline.py:309`, `onboarding.py:98` | Pipeline runner spawns PipelineRun children | `PipelineRun` rows, not `Job` children |
| `subtitle_policy` batch | `subtitle_policies.py:206` | `MediaBatch` + per-file `media_job_manager.create_job()` calls | `MediaJob.request_json` |

**The `letterbox_apply_batch` pattern is closest to correct** — it stores
individual re-encode settings in each child's `payload`, but there's no API
that surfaces the collection.

### What a batch detail should show

For a letterbox re-encode batch of 14 movies:
```
┌─ Letterbox Re-encode (Batch) ─────────────────────────────────┐
│ 14 movies · 12 succeeded · 2 failed · 4m 32s                  │
│                                                                │
│ Movie                        Settings              Result      │
│ ───────────────────────────────────────────────────────────── │
│ The Matrix (1999)            CPU, veryslow, CRF 18  ✓ 2m 14s  │
│ Inception (2010)             GPU,  medium,  CRF 20  ✓ 1m 08s  │
│ Tenet (2020)                 CPU, veryslow, CRF 18  ✗ remux   │
│ ...                                                           │
└────────────────────────────────────────────────────────────────┘
```

### Recommendations

1. **Add a batch children endpoint** — `GET /api/jobs/{job_id}/children` that
   returns all child jobs with their `subject` titles, `payload`, `status`, and
   `result`. This works for any job that has `parent_id` children.

2. **Include children in the job detail response** — The existing
   `GET /api/jobs/{job_id}` endpoint should include a `children` array when the
   job has child jobs (or `children_total > 0` in progress).

3. **Add a batch summary to the parent result** — When a batch parent
   completes, its `result` JSON should include a summary of children:
   `{movie_count, succeeded, failed, per_movie_results: [{subject_title, payload, status, error}]}`.

4. **Render children in the frontend detail page** — Show an expandable list
   of child jobs on the batch parent's detail page, with per-movie settings
   and results.

5. **Normalize batch creation** — All batch jobs should use the same pattern
   for creating children and linking them to the parent. Currently
   `letterbox_apply_batch` sets `parent_id` manually after creating children,
   while `create_batch()` sets it automatically. The detail page shouldn't
   need to know which pattern was used.

---

## Problem 9: Resource pool cards are noisy — need a cleaner visual design

### What users see

The Resource Pools panel renders every row from `JobResource` as a 150px-wide
StatCard showing `<in_use>/<capacity>` in 24px mono, a sub-label of
"available"/"fully reserved"/"disabled", and a progress bar. After Problem 1's
`media-file:*` filtering, 6 logical pools remain — but the StatCard grid is
oversized for just 6 items and the "0/1 — available" pattern is repetitive
and low-information.

Current: StatCard(grid, 150px min) → `GPU | 0/1 | available | [empty bar]`

The ProgressBar is wasted on boolean (0/1) pools — it's only informative for
multi-slot resources like `network_external` (capacity 4).

### Design constraints from user

> "I don't like all of these 0/1s everywhere, I want something that looks
> simpler and better after the resource pools housekeeping is improved."

Key preferences from memory:
- Distribution bars: SDR/baseline leftmost, ascending
- Color-coded zones: green=meets, gold=exceeds, red=fails/excluded
- Explicit controls preferred over click-to-select
- Labels concise: "DoVi" not "DoVi + HDR fallback"

### Proposed visual redesign

Instead of 6 oversized StatCards, show the 6 logical pools as a compact
horizontal utilization bar:

```
Resource pools  ──────────────────────────────────────────────────
┌────────────────────────────────────────────────────────────────┐
│ GPU         ████░░░░░░░░░░░░░░░░░░░░░░  1/1  fully reserved    │
│ Media Read  ████████░░░░░░░░░░░░░░░░░░  2/2  fully reserved    │
│ Media Write ░░░░░░░░░░░░░░░░░░░░░░░░░░  0/1  available         │
│ Transcode   ██████████████████████████  1/1  fully reserved    │
│ Network Ext ████████████████░░░░░░░░░░  1/4  available          │
│ Maintenance ░░░░░░░░░░░░░░░░░░░░░░░░░░  0/1  available         │
└────────────────────────────────────────────────────────────────┘
```

Each row is a single line with:
- **Label**: human-readable name (not raw key)
- **Utilization bar**: 0–100% fill, color-coded (green ≤70%, gold ≤90%, red >90%)
- **Fraction**: `in_use/capacity`
- **Status word**: "available", "fully reserved", or "disabled"

For multi-slot pools (network_external), the bar is more meaningful than for
0/1 pools. For boolean pools, the bar at least provides consistent visual
scanning — the status word and fraction give the precise state.

### Recommendations

1. **Replace `ResourcePoolPanel`'s StatCard grid** with a compact horizontal
   bar-chart list. One row per logical pool (the 6 from Problem 1).

2. **Add human-readable labels** for each pool key:
   - `gpu` → "GPU"
   - `media_read` → "Media Read"
   - `media_write` → "Media Write"
   - `transcode` → "Transcode"
   - `network_external` → "Network External"
   - `maintenance_exclusive` → "Maintenance"

3. **Color the utilization bar** by severity:
   - Green (≤70%): plenty of capacity
   - Gold (70–90%): getting busy
   - Red (>90%): nearly/fully saturated

4. **Keep the ProgressBar component** but use it inline in a row, not inside a
   150px card. This reuses existing code while fixing the layout.

5. **Consider collapsing when all pools are idle** — If every pool is at 0/N,
   show a single summary line: "All pools idle" to reduce visual noise.

---

## Problem 10: Worker panel shows raw UUIDs — needs a cleaner visual design

### What users see

The Worker Health panel renders every `JobWorker` row as a list item showing a
raw UUID (`plunder-abc123def456`), a status label, and a heartbeat age chip.
After Problem 2's filtering of stopped/dead workers, only 1–2 live workers
remain, but the UUID-centric layout is hard to scan and exposes implementation
details (hostname + hex ID).

Current:
```
● plunder-abc123def456  running  heartbeat 3s ago
● plunder-789ghi012jkl  running  heartbeat 7s ago
```

### Design constraints from user

> "I want a revised look for workers as well, something that makes sense to
> look at."

### Proposed visual redesign

Replace the raw UUID list with a compact worker summary that emphasizes
operational health over identity:

```
Workers  ────────────────────────────────────────
● Primary worker      running · heartbeat 3.4s   [host: plunder]
● Scheduler           running · heartbeat 7.1s   [host: plunder]
```

Each row:
- **Role label**: derived from worker metadata or a config map (Primary,
  Scheduler, Worker-1, etc.) — NOT the raw UUID
- **Status dot**: green (running), grey (draining), red (dead), yellow (starting)
- **Heartbeat age**: human-readable, H:mm:ss or "Xs ago" for recent
- **Host**: short hostname in muted text, for the rare case where multiple
  hosts run workers

The raw worker ID should still be visible on hover/tap (title attribute), but
the default view uses the role label.

After Problem 2's filtering, only live workers appear. The empty state shows
"No workers connected" instead of a list of dead entries.

### Recommendations

1. **Add a worker role mapping** — A simple heuristic: the first worker is
   "Primary", a worker with `scheduler` in its ID is "Scheduler", additional
   workers get numbered labels ("Worker-2", etc.). Alternatively, expose a
   `worker_role` field from the backend.

2. **Replace `WorkerHealthPanel`'s UUID-centric row** with role-labeled rows.
   Raw UUID visible on hover only.

3. **Remove the "stale" label** after Problem 2 filtering — stopped/dead
   workers won't appear, so staleness detection is unnecessary for the live
   list. The heartbeat age alone provides sufficient liveness info.

4. **Show a combined summary** when all workers are running and healthy:

   ```
   Workers  ────────────────────────
   ● 2 workers healthy · host plunder
   ```

   Expandable to show individual workers on click.

5. **Color-code the heartbeat** age: green (≤15s), gold (15–30s), red (>30s)
   to give an at-a-glance health indicator without reading numbers.

---

## Problem 11: GPU encoder/decoder utilization not exposed

### What users see

The System tab shows GPU core utilization and memory controller utilization,
but there is **no encoder or decoder utilization**. When a letterbox re-encode
or subtitle generation job runs, the GPU compute bar may show 10% while the
NVENC encoder is pegged at 100% — but the operator can't see that.

### Root cause

**Encoder is collected but not returned** — `gpu_metrics()` in
`system_metrics.py:176-179` queries NVML for encoder utilization:

```python
try:
    enc = pynvml.nvmlDeviceGetEncoderUtilization(handle)[0]
except Exception:
    enc = None
```

The `enc` value IS returned in the dict at line 188 (`"enc": enc`), but the API
response and frontend never display it. The `SystemMetricsPanel.svelte` GPU
section only shows `util`, `memUtil`, `vramUsed`, `vramTotal`, `temp`, and
`power` — `enc` is absent from both the API schema and the frontend.

**Decoder is not collected at all** — NVML provides
`nvmlDeviceGetDecoderUtilization()` but `gpu_metrics()` never calls it. For
jobs that use NVDEC (hardware-accelerated decode during re-encode), decoder
utilization is invisible.

**The `active_jobs` column already captures job context** — Each sample stores
which jobs were running, so the system has all the data needed to show "during
this re-encode, encoder hit 97%."

### What NVML provides

| NVML call | Returns | Use case |
|-----------|---------|----------|
| `nvmlDeviceGetEncoderUtilization()` | Encoder % + sampling period µs | NVENC usage (re-encode, transcode) |
| `nvmlDeviceGetDecoderUtilization()` | Decoder % + sampling period µs | NVDEC usage (hardware decode) |

### Recommendations

1. **Collect decoder utilization** — Add `nvmlDeviceGetDecoderUtilization()` to
   `gpu_metrics()`, mirroring the encoder pattern. Return it as `"dec"` in the
   GPU dict.

2. **Expose encoder in the API response** — The `enc` field is already returned
   by `gpu_metrics()` but never surfaces in the API. Ensure `GET /api/system/metrics`
   includes `gpu.enc` (and `gpu.dec` once added).

3. **Add encoder/decoder cards to the frontend** — In `SystemMetricsPanel.svelte`,
   add two new StatCards (or compact bars) alongside the existing GPU section:

   ```
   GPU · NVIDIA GeForce RTX 3080          350 W
   ┌──────────┬──────────┬──────────┬──────────┐
   │ Core 12% │ Mem  8%  │ Enc  97% │ Dec  45% │
   └──────────┴──────────┴──────────┴──────────┘
   ```

   Encoder and decoder cards should use the same color-coding as other
   utilization bars (green ≤70%, gold ≤90%, red >90%).

4. **Store encoder/decoder in history samples** — The `SystemMetricsSampler`
   already persists the full `gpu` dict (including `enc`) to
   `SystemMetricsSample.gpu`. Once `dec` is added to `gpu_metrics()`, it
   automatically flows into history without any sampler change.

---

## Problem 12: No time-series graphs for host metrics

### What users see

The System tab shows point-in-time StatCards for CPU, GPU, RAM, disk, and
network. There is no way to see how these metrics changed over time — no
graphs, no history, no trend lines. The backend samples every 15 seconds and
retains 14 days, but none of that data reaches the frontend.

### Root cause

**No history API endpoint** — `GET /api/system/metrics` in `system.py:92-101`
returns only the latest reading:

```python
data = system_metrics.collect(settings.metrics_disk_path)
data["workers"] = await _worker_counts(db)
return data
```

**Phase 1 sampled the data, Phase 2 never built the query** — The
`SystemMetricsSampler` (`system_metrics_sampler.py`) has been writing
`SystemMetricsSample` rows every `METRICS_SAMPLE_INTERVAL_SECONDS` (default 15s)
since the projection room vertical slice commit. The `system_metrics_purge` job
cleans rows older than `METRICS_RETENTION_DAYS` (default 14). The index on
`created_at` is in place. Everything needed for time-series queries exists —
except the endpoint and frontend.

**No frontend chart component** — There is no charting library or component in
the Svelte frontend. Adding graphs requires either a Canvas/SVG chart component
or integrating a lightweight library.

### Data available for graphing

With 14 days of 15-second samples (~80,640 rows maximum):

| Metric | Source column | Notes |
|--------|--------------|-------|
| CPU avg % | `cpu.avg` | Single scalar, excludes per-core |
| GPU core % | `gpu.util` | Null on non-NVIDIA hosts |
| GPU enc % | `gpu.enc` | Already in sample, needs API exposure |
| GPU dec % | `gpu.dec` | Not yet collected (Problem 11) |
| RAM % | `ram.pct` | |
| Disk read rate | `disk.readBytes` delta | Client-side rate from cumulative counters |
| Disk write rate | `disk.writeBytes` delta | Same pattern |
| Network sent rate | `net.bytesSent` delta | Same pattern |
| Network recv rate | `net.bytesRecv` delta | Same pattern |

Per-core CPU utilization is excluded (user preference: overall CPU is enough).

### Proposed API design

```
GET /api/system/metrics/history?since=<ISO>&until=<ISO>&resolution=<seconds>
```

- `since` / `until`: time window (default: last 1 hour)
- `resolution`: downsampling interval in seconds (default: 15, matches sample rate)
- Returns `{series: {cpu: [{t, v}], gpu: [{t, v}], ...}}` — each series is an
  array of `{t: ISO timestamp, v: number}` points
- Resolution > sample interval means the backend downsamples (e.g. avg over 60s
  windows), reducing payload size for multi-day views

### Proposed frontend design

```
System  ───────────────────────────────────────── [1h] [6h] [24h] [7d] [Custom]
┌─ CPU ──────────────────────────────────────────────────────────────────────┐
│  ▁▂▃▄▅▆▇██▇▆▅▄▃▂▁▂▃▄▅▆▇██▇▆▅▄▃▂▁▂▃▄▅▆▇██▇▆▅▄▃▂▁   avg 34% · peak 97%      │
├─ GPU ──────────────────────────────────────────────────────────────────────┤
│  Core  ▁▁▁▁▁▁▁▁▂▃▄▅▆▇█▇▆▅▄▃▂▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁   avg 12% · peak 45%      │
│  Enc   ▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁████████▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁   avg 8%  · peak 97%      │
│  Dec   ▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▃▄▅▆▇█▇▆▅▄▃▁▁▁▁▁▁▁▁▁▁▁▁   avg 6%  · peak 48%      │
├─ RAM ──────────────────────────────────────────────────────────────────────┤
│  ▄▄▄▄▅▅▅▅▅▅▅▅▅▅▅▅▅▅▅▅▅▅▅▅▅▅▅▅▅▅▅▅▅▅▅▅▅▅▅▅▅   avg 52% · peak 54%      │
├─ Disk ─────────────────────────────────────────────────────────────────────┤
│  Read  ▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁   avg 12 MB/s              │
│  Write ▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁   avg 4 MB/s               │
├─ Network ──────────────────────────────────────────────────────────────────┤
│  ↓ ▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▂▃▄▅▆▇█▇▆▅▄▃▂▁▁▁▁▁▁▁▁▁▁▁   avg 45 MB/s              │
│  ↑ ▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▂▃▄▅▆▇█▇▆▅▄▃▂▁   avg 8 MB/s               │
└────────────────────────────────────────────────────────────────────────────┘
```

### Recommendations

1. **Add `GET /api/system/metrics/history` endpoint** — Queries
   `SystemMetricsSample` within the requested time window, supports
   downsampling via SQL `date_trunc` + `AVG` for resolutions > sample interval.

2. **Add a time window selector** to the frontend system tab — preset buttons
   (1h, 6h, 24h, 7d) plus a custom date range picker.

3. **Build a lightweight time-series chart component** — SVG-based sparkline or
   Canvas chart. Avoid heavy charting libraries; a simple inline SVG polyline
   with area fill is sufficient for sparklines. For the full-size graphs, a
   Canvas-based component with proper axes, labels, and tooltips.

4. **Compute disk/network rates server-side for history** — Currently the
   frontend computes rates from cumulative deltas. For history queries, the
   backend should compute rates from consecutive samples to avoid sending raw
   cumulative counters.

5. **Exclude per-core CPU from graphs** — The individual core utilizations
   remain in the point-in-time StatCard block but are not graphed (per user
   preference: overall CPU is enough).

6. **Cache recent history** — For the default 1h window, the endpoint should be
   fast (< 50ms). With 15s sampling, that's only 240 rows. Use the
   `ix_system_metrics_samples_created_at` index for efficient range scans.

---

## Problem 13: Job overlay on metrics graphs (Phase 1 done, Phase 2 incomplete)

### What already exists (Phase 1 — `cadb3b1`)

The projection room vertical slice commit (`cadb3b1`) laid all the groundwork
for correlating metrics to jobs:

| Component | File:line | What it does |
|-----------|-----------|--------------|
| `SystemMetricsSample.active_jobs` | `models/system_metrics.py:28-31` | JSON column storing `[{id, type}]` for every active job at sample time — explicitly documented as "lets a future history overlay correlate spikes to jobs" |
| `SystemMetricsSampler._tick()` | `system_metrics_sampler.py:38-40` | Queries `Job.id, Job.type WHERE status IN ACTIVE` on every tick |
| `SystemMetricsSampler._tick()` | `system_metrics_sampler.py:42-49` | Stores `active_jobs` alongside CPU/GPU/RAM/disk/net in each sample |
| DB migration | `alembic/.../6d7644acfcd3_add_system_metrics_samples.py` | Creates the table with `active_jobs` JSON column |
| Daily cleanup | `builtin_handlers.py:628-655` | `system_metrics_purge` job deletes samples older than `METRICS_RETENTION_DAYS` |
| Config | `config.py:120-134` | `METRICS_SAMPLE_INTERVAL_SECONDS=15`, `METRICS_RETENTION_DAYS=14` |

The comment on `active_jobs` at `system_metrics.py:28-31` is explicit about
the intent:

```
# [{"id": job.id, "type": job.type}, ...] for jobs in manager.ACTIVE at
# sample time — lets a future history overlay correlate spikes to jobs
# without per-process OS-level attribution.
```

### What's missing (Phase 2)

1. **No job timeline query** — The history endpoint (to be built in Problem 12)
   doesn't include job data. To render overlays, the client needs to know which
   jobs ran during the selected time window: their `started_at`, `finished_at`,
   `type`, and `subject.title`.

2. **No overlay rendering** — Even with job timeline data, there's no frontend
   component that renders job bands on top of metric graphs.

3. **No job detail link from overlay** — When a user sees a spike at a specific
   time, they should be able to click the overlay band to navigate to that
   job's detail page.

### How the overlay should work

When the user selects a time window (e.g. "last 1 hour"), the frontend fetches
two things in parallel:

1. `GET /api/system/metrics/history?since=...&until=...` — time-series metric
   data (built in Problem 12)

2. `GET /api/jobs?since=<unix>&until=<unix>&status=succeeded,failed,cancelled`
   — all terminal jobs that ran in the window (existing endpoint, add filters)

The frontend then renders each job as a colored horizontal band behind the
metric graphs. The band's left/right edges map to `started_at`/`finished_at` on
the time axis. The color encodes the job type (consistent with the label
registry from Problem 7).

**Enhanced API response for history with jobs:**

```json
{
  "series": {
    "cpu": [{"t": "2026-06-26T14:00:00Z", "v": 34.5}, ...],
    "gpu_core": [{"t": "2026-06-26T14:00:00Z", "v": 12.0}, ...],
    "gpu_enc": [{"t": "2026-06-26T14:00:00Z", "v": 0.0}, ...]
  },
  "jobs": [
    {
      "job_id": "abc123",
      "type": "letterbox_reencode",
      "label": "Letterbox Re-encode",
      "subject": {"title": "The Matrix (1999)"},
      "started_at": "2026-06-26T14:05:30Z",
      "finished_at": "2026-06-26T14:08:45Z"
    }
  ]
}
```

**Proposed visual design:**

```
System  ───────────────────────────────────────── [1h] [6h] [24h] [7d] [Custom]
┌─ CPU ──────────────────────────────────────────────────────────────────────┐
│  ▁▂▃▄▅▆▇██▇▆▅▄▃▂▁▂▃▄▅▆▇██▇▆▅▄▃▂▁▂▃▄▅▆▇██▇▆▅▄▃▂▁   avg 34% · peak 97%      │
│  ░░░░░░░░░░█████████████████████░░░░░░░░░░░░░░░░░░░  ← job overlay bands   │
│         The Matrix (1999)            Inception (2010)                       │
├─ GPU Core ─────────────────────────────────────────────────────────────────┤
│  ▁▁▁▁▁▁▁▁▂▃▄▅▆▇█▇▆▅▄▃▂▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁   avg 12% · peak 45%      │
│  ░░░░░░░░░░░░░░░░░░█████████████████████░░░░░░░░░░  ← overlay              │
├─ GPU Encoder ──────────────────────────────────────────────────────────────┤
│  ▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁████████▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁   avg 8%  · peak 97%      │
│  ░░░░░░░░░░░░░░░░░░████████░░░░░░░░░░░░░░░░░░░░░  ← overlay              │
│         The Matrix (re-encode)                                             │
└────────────────────────────────────────────────────────────────────────────┘

Job legend (clickable, navigates to /projection-room/jobs/{id}):
■ Letterbox Re-encode  ■ Subtitle Generation  ■ Dolby Vision Analysis
■ Poster Pipeline      ■ Taste Rebuild        ■ Library Sync
```

### Recommendations

1. **Include job timeline in the history response** — The history endpoint
   returns `jobs` array alongside `series`, populated from `Job` rows whose
   `started_at`/`finished_at` fall within the window.

2. **Render job overlay bands** — A `<JobOverlay>` Svelte component that maps
   job time ranges to SVG `<rect>` elements positioned on the chart's time
   axis. Each job type gets a distinct color from an 8-palette map.

3. **Make overlay bands interactive** — Hover shows job type + subject title.
   Click navigates to `/projection-room/jobs/{job_id}`.

4. **Use the same color palette as the label registry** — Problem 7's
   `JOB_LABELS` registry should include a `color` field per job type, keeping
   colors consistent across overlays, history tables, and running job cards.

5. **Handle overlapping jobs** — When multiple jobs run concurrently, stack
   the overlay bands vertically (one row per overlapping job) or use a lighter
   opacity so overlapping regions are visually distinct.

6. **Toggle overlay on/off** — Add a checkbox or toggle in the UI to show/hide
   the job overlay so the raw metrics remain readable when there are many jobs.

---

## Summary of changes needed

| # | What | Where | Priority |
|---|------|-------|----------|
| 1a | Filter `media-file:*` from `/metrics` resources | `api/routes/jobs.py:job_metrics()` | High |
| 1b | Purge orphan `media-file:*` `JobResource` rows | `builtin_handlers.py:job_retention_purge()` | Medium |
| 2a | Filter stopped/dead workers from `/metrics` | `api/routes/jobs.py:job_metrics()` | High |
| 2b | Purge old stopped/dead `JobWorker` rows | `builtin_handlers.py:job_retention_purge()` | Medium |
| 3 | (Follow-up) Call `bootstrap_resources()` from API lifespan | `main.py` or `supervisor.py` | Low |
| 4a | Add `audio_remove` operation (distinct from `subtitle_remove`) | `subtitles.py`, `mutation.py`, `handlers.py`, `manager.py` | High |
| 4b | Remove `track_remove`→`subtitle_remove` alias; keep `track_remove` for mixed | `manager.py` | High |
| 4c | Replace `JobList`'s `operation.replace('subtitle_', '')` hack | `frontend/.../JobList.svelte` | Medium |
| 5a | Enrich `execute_job()` result with request context (track IDs, settings) | `mutation.py:execute_job()` | High |
| 5b | Cross-reference `MediaJob.request_json` in generic Job detail | `api/routes/jobs.py` job detail endpoint | Medium |
| 5c | Add request summary to `job_summary()` for history table | `api/routes/jobs.py:job_summary()` | Low |
| 6a | Capture structured error context (code, operation, file, stage) | `legacy_media.py` exception handler | High |
| 6b | Preserve `PreflightError.code` in error dict | `legacy_media.py`, `mutation.py` | Medium |
| 6c | Propagate structured error to generic `Job.error` | `legacy_media.py`, `jobs/manager.py` | Low |
| 7a | Create shared `JOB_LABELS` registry (35+ types → human labels) | `marquee/core/jobs/labels.py` (new) | High |
| 7b | Expose `label` field in `job_summary()` API response | `api/routes/jobs.py:job_summary()` | High |
| 7c | Frontend uses `job.label` with `humanizeType()` fallback | `RunningJobCard`, `HistoryTable`, `JobList` | Medium |
| 8a | Add `GET /api/jobs/{id}/children` endpoint | `api/routes/jobs.py` (new route) | High |
| 8b | Include `children` array in job detail response | `api/routes/jobs.py` job detail | Medium |
| 8c | Render batch children in frontend detail page | `projection-room/jobs/[job_id]/+page.svelte` | Medium |
| 8d | Normalize batch creation (consistent parent/child linking) | `hdr.py`, `letterbox.py`, `onboarding.py` | Low |
| 9a | Replace StatCard grid with compact utilization bar list | `ResourcePoolPanel.svelte` | Medium |
| 9b | Add human-readable pool labels + color-coded utilization | `ResourcePoolPanel.svelte` | Medium |
| 10a | Replace UUID-centric worker list with role-labeled rows | `WorkerHealthPanel.svelte` | Medium |
| 10b | Add color-coded heartbeat age + collapsible summary | `WorkerHealthPanel.svelte` | Low |
| 11a | Collect GPU decoder utilization via NVML | `system_metrics.py:gpu_metrics()` | Medium |
| 11b | Expose `gpu.enc` and `gpu.dec` in API + frontend | `system.py`, `SystemMetricsPanel.svelte` | Medium |
| 12a | Add `GET /api/system/metrics/history` endpoint | `api/routes/system.py` (new route) | High |
| 12b | Add time window selector to frontend system tab | `projection-room/+page.svelte` system tab | High |
| 12c | Build time-series chart component (SVG sparkline) | `frontend/.../MetricsChart.svelte` (new) | High |
| 12d | Compute disk/network rates server-side for history | `system.py` history endpoint | Medium |
| 13a | Include job timeline in history API response | `system.py` history endpoint | Medium |
| 13b | Build `JobOverlay` component for colored bands on charts | `frontend/.../JobOverlay.svelte` (new) | Medium |
| 13c | Add job type → color palette to label registry | `labels.py` | Low |
| 13d | Make overlay bands clickable (navigate to job detail) | `JobOverlay.svelte` | Low |

---

## References

- Design doc 15: Durable job platform (resource model, worker topology)
- Design doc 23: Subtitle mutation plan/execute (plan JSON shape, track selection)
- `marquee/core/jobs/manager.py` — `JobManager.create()`, `create_batch()`, `_reserve()`, `recover()`, `resource_capacity()`
- `marquee/core/jobs/worker.py` — `DurableWorker` ID generation, heartbeat loop
- `marquee/core/jobs/supervisor.py` — Embedded worker lifecycle
- `marquee/core/jobs/builtin_handlers.py` — All `@register(...)` job type definitions + `system_metrics_purge`
- `marquee/core/jobs/handlers.py` — `_HANDLERS` dispatch table + `register()` decorator
- `marquee/core/jobs/dovi_handlers.py` — DoVi analyze/convert registrations
- `marquee/core/jobs/legacy_media.py` — Media bridge handler + operation registrations + error capture
- `marquee/api/routes/jobs.py:129` — `job_summary()` (generic Job → API dict)
- `marquee/api/routes/jobs.py:199` — `/api/jobs/metrics` endpoint
- `marquee/api/routes/jobs.py:345` — Job detail endpoint (includes payload, result, error)
- `marquee/api/routes/system.py:92` — `GET /api/system/metrics` (point-in-time only, no history)
- `marquee/api/routes/subtitles.py:149` — `PlanRequest` model (operation enum)
- `marquee/api/routes/subtitles.py:199` — `create_subtitle_plan()` (job creation with request)
- `marquee/api/routes/hdr.py:761` — `create_batch()` for DoVi analysis
- `marquee/api/routes/letterbox.py:618` — `create_batch()` for letterbox detect
- `marquee/api/routes/letterbox.py:810` — Manual batch creation for letterbox apply
- `marquee/api/routes/subtitle_policies.py:206` — `MediaBatch` creation for policy batches
- `marquee/api/routes/onboarding.py:98` — Poster pipeline batch creation
- `marquee/api/routes/pipeline.py:309` — Poster pipeline batch via `job_manager.create()`
- `marquee/core/subtitles/mutation.py:346` — `build_plan()` operation routing
- `marquee/core/subtitles/mutation.py:553` — `execute_job()` (minimal result return)
- `marquee/core/subtitles/mutation.py:651` — `_build_argv()` (audio + subtitle removal in one branch)
- `marquee/core/media_jobs/manager.py:28` — `_OPERATION_ALIASES` (track_remove → subtitle_remove)
- `marquee/core/media_jobs/manager.py:create_job()` — Generic Job creation with aliased type
- `marquee/core/media_jobs/handlers.py:120` — `_HANDLERS` dispatch table
- `marquee/core/media_jobs/serialize.py:30` — `job_dict()` (MediaJob → API dict, excludes request_json)
- `marquee/core/system_metrics.py:159` — `gpu_metrics()` (collects `enc`, missing `dec`)
- `marquee/core/system_metrics.py:195` — `collect()` (bundles CPU/GPU/RAM/disk/net for API)
- `marquee/core/system_metrics_sampler.py` — `SystemMetricsSampler` (Phase 1: samples every 15s, stores `active_jobs`)
- `marquee/models/system_metrics.py:19` — `SystemMetricsSample` model (includes `active_jobs` JSON column)
- `marquee/models/job.py:119,169` — `JobResource`, `JobWorker` models
- `marquee/models/media_job.py:28` — `MediaBatch` model (batch_id, operation, counts)
- `marquee/models/media_job.py:58` — `MediaJob` model (operation, request_json, batch_id)
- `marquee/config.py:120` — `METRICS_SAMPLE_INTERVAL_SECONDS` (default 15)
- `marquee/config.py:128` — `METRICS_RETENTION_DAYS` (default 14)
- `marquee/config.py:94` — `JOB_HEARTBEAT_SECONDS` (default 10)
- `marquee/config.py:95` — `JOB_LEASE_SECONDS` (default 60)
- `marquee/config.py:108` — `JOB_RETENTION_DAYS` (governs job purge, but not resource/worker cleanup)
- `alembic/versions/6d7644acfcd3_add_system_metrics_samples.py` — Migration creating `system_metrics_samples` table
- `frontend/src/lib/components/SystemMetricsPanel.svelte` — Point-in-time stat cards (needs graphs + enc/dec)
- `frontend/src/lib/components/ResourcePoolPanel.svelte` — StatCard grid (needs visual redesign)
- `frontend/src/lib/components/StatCard.svelte` — Card component (label, value, sub, bar, tone)
- `frontend/src/lib/components/WorkerHealthPanel.svelte` — Worker list with stale detection (needs visual redesign)
- `frontend/src/lib/components/RunningJobCard.svelte:20` — `humanizeType()` display
- `frontend/src/lib/components/HistoryTable.svelte:10` — `humanizeType()` in history
- `frontend/src/lib/components/subtitles/JobList.svelte:123` — Operation badge (strips `subtitle_` prefix)
- `frontend/src/routes/projection-room/+page.svelte` — Projection Room main page (system tab needs graphs)
- `frontend/src/routes/projection-room/jobs/[job_id]/+page.svelte:94` — Job detail page (progress, meta, result, error — no children)
