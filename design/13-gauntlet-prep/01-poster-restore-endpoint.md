# 01 — Planned Manual Poster Restore Endpoint

**Status:** `[PLANNED]` route. Not implemented in the current codebase.

The codebase has `PosterService.restore()` and uses it from webhook/self-heal
flows, but it does not expose:

```text
POST /api/movies/{movie_id}/poster/restore
```

## Existing Building Blocks

- `marquee/core/poster_service.py`
  - `PosterService.restore(db, movie, new_folder=None, source="webhook")`
- `marquee/api/routes/webhooks.py`
  - Radarr upgrade restore background task.
- `marquee/core/heal.py`
  - poster existence heal scan.
- `GET /api/movies/{movie_id}/artwork-events`
  - implemented in `marquee/api/routes/pipeline.py`.

## Planned Route Contract

```text
POST /api/movies/{movie_id}/poster/restore
```

Suggested behavior:

- `404` when the movie is not found.
- Without force, return `restored=false` when the poster already exists.
- With force, call `PosterService.restore(..., source="manual")`.
- Return `409` with a structured response for "nothing to restore".
- Return non-5xx structured failures for cache miss or download failure.

## Implementation Notes

- Add route tests for found/missing movie, existing poster without force,
  successful restore, nothing-to-restore `409`, cache miss, and download
  failure.
- If implemented, update `design/more-features/01-poster-restoration.md` in the
  same change.
