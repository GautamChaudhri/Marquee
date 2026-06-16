# Poster Restoration

**Status:** Reconciled with the codebase on 2026-06-16.

Poster deployment and restoration are implemented for movies through
`marquee/core/poster_service.py`, webhook routes, and the poster heal scan.

## Implemented Behavior

### Deployment

`PosterService.deploy()`:

- renders the movie poster filename from `MOVIE_POSTER_FORMAT`;
- validates the destination folder with `safe_translate_and_validate()`;
- writes atomically;
- caches exact deployed bytes at `data/cache/posters/movies/{tmdb_id}.jpg`;
- writes a `.meta.json` sidecar;
- updates `Movie` poster columns;
- records an `ArtworkEvent`.

Currently deployment is called from the feedback approve/override flow when
`deploy=true`. Pipeline runs alone do not automatically deploy.

### Restoration

`PosterService.restore()`:

- validates the target folder;
- restores from cache when possible;
- falls back to `poster_source_url` download when cache is missing;
- updates poster path/folder state;
- records restore or restore-failed events;
- returns structured `RestoreResult` data instead of treating cache misses as
  server crashes.

### Webhooks

Implemented routes:

- `POST /api/webhooks/radarr`
- `POST /api/webhooks/sonarr`
- `POST /api/webhooks/subgen`

Radarr upgrade downloads schedule a background poster restore, mark letterbox
state stale, and schedule subtitle scan work. Rename events update stored paths.

Sonarr file upgrades do not currently restore series/season posters; current
Sonarr behavior is path/update oriented.

### Self-Heal

Implemented:

- periodic poster heal loop when `HEAL_ENABLED=true`;
- `POST /api/system/heal`;
- status in `GET /api/system/status`.

The heal scan restores missing deployed posters from the same service path.

## API Surface

| Endpoint | Implemented | Purpose |
|---|---:|---|
| `POST /api/webhooks/radarr` | Yes | Radarr event handling and upgrade restore |
| `POST /api/webhooks/sonarr` | Yes | Sonarr event handling/path updates |
| `GET /api/movies/{movie_id}/artwork-events` | Yes | Movie poster event history |
| `POST /api/system/heal` | Yes | On-demand poster heal |
| `GET /api/system/status` | Yes | Cache/heal/webhook status |
| `POST /api/movies/{movie_id}/poster/restore` | No | `[PLANNED]` manual restore button |

## Cache Layout

```text
data/cache/posters/movies/{tmdb_id}.jpg
data/cache/posters/movies/{tmdb_id}.meta.json
```

The cache stores exact deployed bytes, not a resized proxy.

## Planned

- `[PLANNED]` Manual movie restore endpoint. Planned semantics: return `409`
  with a structured response when there is nothing to restore.
- `[PLANNED]` Series/season poster deployment and restoration.
- `[PLANNED]` Cache management UI.

## Safety Notes

- Sonarr season-pack edge cases must be tested before claiming series/season
  restoration support.
