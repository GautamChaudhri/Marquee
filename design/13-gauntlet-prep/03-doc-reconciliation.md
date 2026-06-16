# 03 — Doc reconciliation (drift items #2, #3, #5, #6) + bookkeeping

**Type:** Documentation only (no code)
**Drift items:** #2, #3, #5, #6 — code is already the sensible design; the docs are stale.

Apply each edit exactly. Line numbers are guides; match on the quoted text. After all edits, do the bookkeeping at the bottom.

---

## #3 — Subtitle `scan` is inline, not queued

**File:** `design/more-features/03-subtitle-management.md` §25.1 (≈ line 1410).

**Find:**
```
| POST | `/api/media-files/{id}/subtitles/scan` | Queue forced refresh |
```
**Replace:**
```
| POST | `/api/media-files/{id}/subtitles/scan` | Inline forced re-scan (single file): runs ffprobe synchronously and returns the inventory directly — no job_id, no SSE |
```

Rationale: `marquee/api/routes/subtitles.py:scan_subtitles` calls `service.get_inventory_dict(db, media_file_id, force=True)` and returns the inventory with `200`. A single-file scan is fast enough to block; the durable queue is for mutations/batches.

---

## #5 — Letterbox preview uses `minute`, not `t`

**File:** `design/more-features/04-letterbox-cropping.md` §18 (≈ line 705).

**Find:**
```
| `GET /movies/{id}/preview?t=<sec>&mode=before\|after` | Preview frame (webp) | `FileResponse`, path-confined like the poster route |
```
**Replace:**
```
| `GET /movies/{id}/preview?minute=<int>&mode=before\|after` | Preview frame (webp) | `FileResponse`, path-confined like the poster route. `minute` (default 5) aligns with the detector's per-minute sample granularity. |
```

Rationale: `marquee/api/routes/letterbox.py:movie_preview` takes `minute: int = 5` and `mode: str = "before"`.

---

## #6 — Letterbox single-detect is synchronous (batch is async)

**File:** `design/more-features/04-letterbox-cropping.md` §18 (≈ line 702).

**Find:**
```
| `POST /movies/{id}/detect` | Detect one movie | 202 + job_id, or sync for a single file |
```
**Replace:**
```
| `POST /movies/{id}/detect` | Detect one movie | Synchronous: runs detection and returns the updated `LetterboxState` (200). For async batch detection (202 + job_id + SSE) use `POST /detect`. |
```

Rationale: `detect_one` is synchronous (`return _state_to_dict(...)`, 200); `detect_batch` is `status_code=202` and returns a `job_id` with SSE at `/jobs/{job_id}/events`.

---

## #2 — Subtitle-batches endpoints are a design target, not built

**File:** `design/more-features/03-subtitle-management.md` §25.3 (≈ lines 1431–1450).

The existing "Implementation note" already concedes this. Strengthen it so the table cannot be mistaken for an implemented surface.

**Find** the batch table rows:
```
| Method | Endpoint | Purpose |
|---|---|---|
| POST | `/api/subtitle-batches/plan` | Build per-file child plans and aggregate warnings |
| POST | `/api/subtitle-batches/{id}/confirm` | Queue all still-valid children |
| GET | `/api/subtitle-batches/{id}` | Aggregate and per-item progress |
| POST | `/api/subtitle-batches/{id}/pause` | Stop claiming new children |
| POST | `/api/subtitle-batches/{id}/resume` | Resume |
| POST | `/api/subtitle-batches/{id}/cancel` | Cancel queued children and request active cancellation |
```
**Replace** (prepend a status column marking every row NOT IMPLEMENTED and point to the real mechanism):
```
> **None of the `/api/subtitle-batches/...` routes below are implemented.** They are a
> design target only. The shipped mechanism for batch subtitle work is
> `POST /api/subtitle-policies/{id}/apply` (creates a `MediaBatch` and queues per-file
> `subtitle_remove` jobs) plus the `/api/media-jobs/...` lifecycle routes; jobs created
> by one apply share a `batch_id`. Use those for any batch testing.

| Method | Endpoint | Purpose | Status |
|---|---|---|---|
| POST | `/api/subtitle-batches/plan` | Build per-file child plans and aggregate warnings | NOT IMPLEMENTED |
| POST | `/api/subtitle-batches/{id}/confirm` | Queue all still-valid children | NOT IMPLEMENTED |
| GET | `/api/subtitle-batches/{id}` | Aggregate and per-item progress | NOT IMPLEMENTED |
| POST | `/api/subtitle-batches/{id}/pause` | Stop claiming new children | NOT IMPLEMENTED |
| POST | `/api/subtitle-batches/{id}/resume` | Resume | NOT IMPLEMENTED |
| POST | `/api/subtitle-batches/{id}/cancel` | Cancel queued children and request active cancellation | NOT IMPLEMENTED |
```

Leave the rest of §25.3 (policies rows) unchanged.

---

## #4 footnote — library filter limitations (from `02-library-filters.md`)

**File:** `design/more-features/03-subtitle-management.md` §25.1, immediately after the filter list (≈ line 1416).

**Append:**
```
> Implemented on `GET /api/library/movies` only (series filters are deferred).
> `policy_violation` and `inventory_state=stale` are not yet backable by the coverage
> data and return `400`; supported `inventory_state` values are `scanned` and `unscanned`.
```

(Only add this once `02` lands. If `02` chose different semantics, match them.)

---

## Bookkeeping

### `design/todos.md` → "API design drift follow-ups" (lines 88–93)

Tick all six and annotate the disposition:

```
- [x] **Reconcile poster restore endpoint docs.** Implemented `POST /api/movies/{movie_id}/poster/restore` (design/13-gauntlet-prep/01). Doc §14 now accurate.
- [x] **Reconcile subtitle batch endpoint docs.** §25.3 batch routes marked NOT IMPLEMENTED; policy-apply + media-jobs is the shipped mechanism.
- [x] **Update subtitle scan API wording.** §25.1 now states inline forced re-scan (no job/SSE).
- [x] **Update library filter API docs or implement filters.** Server-side filters implemented (design/13-gauntlet-prep/02); §25.1 footnoted for unsupported filters.
- [x] **Update letterbox preview query docs.** §18 now documents `minute=<int>`.
- [x] **Update letterbox single-detect contract docs.** §18 now documents sync single-detect vs async batch.
```

### `design/12-readonly-endpoint-test-plan.md` §11 ("Current Design Drift Notes Found During Planning")

Refresh the table so the rows reflect reconciliation. For each of the 6 rows, update the "Test-plan disposition" cell to note the drift is now resolved (e.g. for poster-restore: "Now implemented — covered by the gauntlet, no longer excluded from the endpoint count"; for the doc-only items: "Docs updated to match code; test as the current implementation"). Bump the prose count note in §14 if the two new endpoints (poster restore, library filters with params) change the count — the poster-restore route is **+1 implemented endpoint**.

---

## Verification

```bash
ruff check marquee tests   # no-op for docs, but confirms nothing else broke
grep -n "minute=<int>" design/more-features/04-letterbox-cropping.md
grep -n "Inline forced re-scan" design/more-features/03-subtitle-management.md
grep -n "NOT IMPLEMENTED" design/more-features/03-subtitle-management.md
grep -n "Synchronous: runs detection" design/more-features/04-letterbox-cropping.md
grep -c "\[x\]" design/todos.md   # the 6 drift items now checked
```
