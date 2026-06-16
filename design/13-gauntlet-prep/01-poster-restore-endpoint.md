# 01 — Implement `POST /api/movies/{movie_id}/poster/restore`

**Type:** Code change
**Drift item:** #1 of `design/todos.md` → "API design drift follow-ups"
**Effort:** Small (thin wrapper over existing `PosterService.restore`)

---

## Why

`design/more-features/01-poster-restoration.md` §14 (line 424) lists:

> `/api/movies/{movie_id}/poster/restore` — POST — Manual restore button (force=true overwrites existing file)

No such route exists. The companions already exist: `GET /api/movies/{movie_id}/artwork-events` ([pipeline.py:289](marquee/api/routes/pipeline.py)) and `POST /api/system/heal` ([system.py:74](marquee/api/routes/system.py)). The whole restore mechanic is already implemented in `PosterService.restore()` ([poster_service.py:193](marquee/core/poster_service.py)). This task adds the missing manual-trigger route.

---

## What already exists (read these first — do not re-implement)

`marquee/core/poster_service.py`:

```python
async def restore(
    self,
    db: AsyncSession,
    movie: Movie,
    *,
    new_folder: str | None = None,
    source: str = "webhook",
) -> RestoreResult:
    """Restore the deployed poster to the (new) movie folder."""
    # - returns RestoreResult(restored=False, source="none", error="movie never had a poster")
    #   if movie.poster_path is None and movie.poster_source_url is None
    # - folder = safe_translate_and_validate(new_folder or movie.folder_path, source="radarr")
    # - cache hit  → sha256 check (warns on mismatch, uses anyway) → _atomic_copy → _finalize_restore → cache
    # - cache miss → httpx GET movie.poster_source_url → _atomic_write_bytes → _finalize_restore → download
    # - both fail  → movie.poster_path = None; logs "restore_failed"; RestoreResult(restored=False, ...)
    # - _finalize_restore logs ArtworkEvent action="restore" (source!="heal") and commits
```

`RestoreResult` fields (confirm exact definition near top of `poster_service.py`): `restored: bool`, `source: str` (`"cache"|"download"|"none"`), `path: str | None`, `error: str | None`.

**Key fact:** `restore()` has **no `force` parameter** — it always overwrites `dest` atomically. The `force` semantics from the design (`force=true overwrites existing file`) therefore belong in the **route**, not the service.

The singleton is `poster_service` (`poster_service.py:265`). Movie-scoped routes are registered on **`movies_router`** in `marquee/api/routes/pipeline.py` (same router that serves `/{movie_id}/artwork-events` and `/{movie_id}/runs`). Confirm its prefix (expected `/api/movies`) and that `movies_router` is already included in `marquee/main.py` — **no new router registration should be needed.**

---

## Implementation

Add to `marquee/api/routes/pipeline.py`, next to `list_artwork_events` (so it shares the `movies_router` + imports). Reuse the existing `get_db`, `Movie`, `select`, and `poster_service` imports already present in the file (verify; add only what's missing).

### Route contract

```
POST /api/movies/{movie_id}/poster/restore
Body (optional): {"force": false}   # also accept ?force=true query param
```

### Behavior

1. Load the movie; `404` if not found (mirror `list_artwork_events` / `get_movie` 404 style).
2. **Force gating (route-level):**
   - If `force` is **false** (default): stat the currently deployed poster (`movie.poster_path`). If it exists on disk, **skip** and return `{"restored": false, "reason": "already_present", "path": movie.poster_path}` with `200`. (This mirrors the heal scan, which only restores on miss — `marquee/core/heal.py`.)
   - If the deployed file is missing, or `force` is **true**, proceed.
3. Call `await poster_service.restore(db, movie, source="manual")`.
   - **Verify first** what values the `ArtworkEvent.source` column accepts. The documented `source` enum (01-poster-restoration §12) is `pipeline | feedback | webhook | heal`. If the column is a free-form `VARCHAR` (likely — check `marquee/models/` for `ArtworkEvent`), `"manual"` is correct and preferred. If it is constrained, use the closest allowed value and note it in the PR description. **Do not** introduce a migration for this — pick a compatible string.
4. Map `RestoreResult` → response:
   - `restored=True` → `200 {"restored": true, "source": result.source, "path": result.path, "force": force}`.
   - `restored=False` (e.g. "movie never had a poster", path-validation failure, download failure) → **do not 500**. Return `200 {"restored": false, "reason": result.error or result.source, "source": result.source}` *or* a `409`/`422` if you prefer a non-2xx for "nothing to restore" — pick one and be consistent; the gauntlet (`05`) will accept either as long as it is not a 5xx. Recommended: `409 {"restored": false, "reason": ...}` when the movie never had a poster, `200` with `restored:false` when a download/cache attempt failed transiently.

### Notes

- Do **not** pass `new_folder`; a manual restore targets the movie's current `folder_path`, which is `restore()`'s default.
- `restore()` already commits and logs the `ArtworkEvent`; the route must not double-commit.
- Path safety is already handled inside `restore()` via `safe_translate_and_validate(..., source="radarr")`.

---

## Tests

Add to the existing poster-service / route test module (find where `PosterService.restore` and the movie routes are already tested — likely `tests/test_poster_service*.py` and a routes test using the in-memory app pattern in `tests/`). Use `tmp_path` folders and a movie row, mirroring existing restore tests.

1. **Missing movie** → `404`.
2. **force=false, poster present on disk** → `200`, `restored=false`, `reason="already_present"`, file untouched (stat unchanged).
3. **force=false, poster missing on disk, cache hit** → `200`, `restored=true`, `source="cache"`, file recreated, `ArtworkEvent action="restore"` written.
4. **force=true, poster present** → overwrites; `restored=true`.
5. **cache miss + download fallback** (mock `httpx`) → `restored=true`, `source="download"`.
6. **movie never had a poster** (`poster_path` and `poster_source_url` both None) → non-5xx (`409` per recommendation), `restored=false`.

---

## Verification

- `ruff check marquee tests` (the only lint gate — never `ruff format`).
- `pytest tests/ -k "poster_restore or restore"`.
- Manual smoke against the live server (port 3165): delete a deployed poster file on disk, then
  `curl -sS -X POST "$BASE_URL/api/movies/{id}/poster/restore"` → expect `restored:true, source:"cache"`;
  confirm a new row appears in `GET /api/movies/{id}/artwork-events`.

---

## Doc follow-up

Once implemented, `design/more-features/01-poster-restoration.md` §14 is accurate — no doc edit needed for #1. Tick item #1 in `design/todos.md` (handled in `03-doc-reconciliation.md`).
