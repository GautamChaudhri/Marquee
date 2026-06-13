# Marquee — Poster Restoration on Radarr Upgrade

**Status:** Design phase — not yet implemented  
**Date:** 2026-06-12  
**Purpose:** Define how Marquee detects Radarr movie upgrades and automatically restores posters that were deleted during the upgrade process.

---

## Table of Contents

1. [The Problem](#1-the-problem)
2. [Detection: Push via Radarr Webhooks](#2-detection-push-via-radarr-webhooks)
3. [Storage: Local Cache](#3-storage-local-cache)
4. [Data Model Changes](#4-data-model-changes)
5. [The Full Flow](#5-the-full-flow)
6. [Edge Cases and Their Resolution](#6-edge-cases-and-their-resolution)
7. [What About Sonarr?](#7-what-about-sonarr)
8. [API Endpoint Overview](#8-api-endpoint-overview)
9. [Open Questions](#9-open-questions)

---

## 1. The Problem

When Radarr upgrades a movie (better quality release, proper release, repack), it deletes the entire movie folder and creates a new one. This process destroys any custom poster artwork that was deployed in that folder. The user is left with whatever default poster Plex/Jellyfin's metadata agent generates — losing the AI-selected or manually-approved poster.

The goal: detect this event and automatically restore the poster to the new folder without user intervention.

---

## 2. Detection: Push via Radarr Webhooks

### Mechanism

Radarr has a native webhook system (Settings → Connect → Webhook). When configured to point at Marquee, Radarr POSTs JSON to `POST /api/webhooks/radarr` on every significant event.

### The Relevant Event

The event we care about is `Download` with `isUpgrade: true`. Radarr fires this AFTER the upgrade completes — the new folder exists, the new media file is in place, and the old folder (with our poster) is gone.

### Payload (Relevant Fields)

```json
{
  "eventType": "Download",
  "isUpgrade": true,
  "movie": {
    "id": 123,
    "title": "Die Hard",
    "year": 1988,
    "tmdbId": 562,
    "folderPath": "/plunder/movies/Die Hard (1988)"
  },
  "movieFile": {
    "relativePath": "Die Hard (1988).mkv",
    "path": "/plunder/movies/Die Hard (1988)/Die Hard (1988).mkv"
  }
}
```

### Why Push, Not Pull

- **Push is instant.** The poster is restored within seconds of the upgrade, before Plex/Jellyfin has time to scan and generate default artwork.
- **Push has zero overhead.** No polling loop, no API rate limiting concerns.
- **Radarr already supports it.** The webhook is a first-class feature in *arr apps. No custom scripting, no cron jobs, no Radarr API key needed on Marquee's side.
- **The user configures it once.** Settings → Connect → Webhook → URL: `http://marquee:3165/api/webhooks/radarr`. That's it.

### What Events to Listen For

| Radarr Event | Action |
|---|---|
| `Download` (isUpgrade: true) | Restore poster to new folder |
| `Download` (isUpgrade: false) | New movie added — no poster to restore yet (it never had one). Log info. |
| `Rename` | Folder renamed. Update `folder_path` in DB if it changed. Poster file survived the rename — just update path. |
| `MovieDelete` | Movie removed from Radarr. Optionally mark poster as orphaned or delete from cache. Deferred. |

Only `Download` with `isUpgrade: true` triggers poster restoration. Everything else is informational or path-updating.

---

## 3. Storage: Local Cache

### Strategy

At the moment a poster is deployed — whether by the AI pipeline or by manual user selection — Marquee saves a copy to a local cache directory. This cached copy is the restoration source.

### Why Local Cache Over Re-Download

| | Local Cache | TMDB Re-download |
|---|---|---|
| Speed | Instant (local FS copy) | 1-3 seconds (HTTP + CDN latency) |
| Reliability | 100% if file exists | Depends on TMDB CDN availability and poster retention |
| Storage cost | ~150 KB/poster at w780. 1,000 movies ≈ 150 MB | None |
| What gets restored | Exactly what was deployed | May differ if TMDB replaced the poster image |

The cache is the primary restoration source. Re-download from TMDB is a fallback only.

### Cache Structure

```
data/posters/cache/
├── movies/
│   ├── 562.jpg            ← Die Hard (tmdb_id=562), w780 JPEG
│   └── 562.meta.json      ← source provenance for fallback
└── series/
    └── ...                 ← future: series/season posters
```

The `meta.json` sidecar stores enough information to re-download from TMDB if the cache file is somehow lost:

```json
{
  "source": "tmdb",
  "source_url": "https://image.tmdb.org/t/p/original/abc123def456.jpg",
  "sha256": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
  "phash": "a1b2c3d4e5f6a7b8",
  "deployed_filename": "poster.jpg",
  "deployed_at": "2026-06-12T17:00:00Z",
  "quality": "w780",
  "width": 780,
  "height": 1170
}
```

### When the Cache is Populated

The cache is written at deployment time — one of two moments:

1. **Pipeline auto-deploy:** When the pipeline ranks posters and deploys the winner (Stage 8, future).
2. **Manual user approve/override:** When the user clicks Approve or Use This Instead in the frontend.

A poster is only cached when it is SELECTED, not when it merely passes the pipeline gates. Every poster in the cache represents a deliberate choice.

### Cache Maintenance

- **Cache is write-once per movie, overwrite on re-select.** If the user runs the pipeline again and picks a different poster, the cached copy is replaced.
- **Cache is never auto-deleted.** Even if a movie is removed from Radarr, the cache persists. It's 150KB — not worth the complexity of garbage collection.
- **Cache grows linearly with library size.** For a large library (5,000 movies), cache is ~750 MB. Acceptable on any modern storage.

---

## 4. Data Model Changes

### What Already Exists (ArtworkMixin)

| Column | Type | Purpose for Restoration |
|---|---|---|
| `poster_path` | TEXT | The deployed file path. Checked for existence on webhook. |
| `poster_source` | VARCHAR(20) | "tmdb" — tells us where to re-download from if cache is missing. |
| `poster_source_url` | TEXT | Full TMDB image URL. Used as fallback re-download target. |
| `poster_ai_selected` | BOOL | Was the poster AI-selected? Both AI and manual posters get restored. |
| `poster_sha256` | VARCHAR(64) | Hash of deployed poster. Can verify cache integrity, detect tampering. |
| `poster_phash` | VARCHAR(16) | Perceptual hash. Useful for cache validation if needed. |

### What Needs Adding

| New Column | Type | Purpose |
|---|---|---|
| `poster_user_approved` | BOOL, default=False | Distinguish human-approved posters from AI auto-picks. Both get restored. Distinction matters for learning system confidence. |
| `poster_deployed_filename` | VARCHAR(255) | The filename written to the movie folder (typically "poster.jpg"). Needed so restoration writes the same filename that Plex/Jellyfin expects. |

### What Does NOT Need a DB Column

The cache path is derived: `data/posters/cache/movies/{tmdb_id}.jpg`. Storing it in the DB would create a sync problem (cache file deleted, DB still points to it). The filesystem IS the source of truth for cache existence.

---

## 5. The Full Flow

### 5.1 Deployment: Populating the Cache

```
Pipeline selects winner (or user approves/overrides)
        │
        ▼
1. Write poster to movie folder:
   /plunder/movies/Die Hard (1988)/poster.jpg
        │
        ▼
2. Copy to local cache:
   data/posters/cache/movies/562.jpg          ← the image
   data/posters/cache/movies/562.meta.json    ← provenance + metadata
        │
        ▼
3. Update DB:
   poster_path = "/plunder/movies/Die Hard (1988)/poster.jpg"
   poster_source = "tmdb"
   poster_source_url = "https://image.tmdb.org/t/p/original/abc123.jpg"
   poster_ai_selected = True
   poster_user_approved = False (or True if manual)
   poster_deployed_filename = "poster.jpg"
   poster_sha256 = "e3b0c4..."
   poster_phash = "a1b2c3..."
```

### 5.2 Restoration: Responding to Upgrade Webhook

```
Radarr fires webhook → POST /api/webhooks/radarr
        │
        ▼
1. Parse payload. If eventType != "Download" or isUpgrade != true → 200 OK, no action.
        │
        ▼
2. Extract radarr_id (movie.id) from payload.
   Look up Movie by radarr_id in DB.
   If not found → 200 OK (this movie isn't tracked by Marquee).
        │
        ▼
3. If movie.poster_path IS NULL → 200 OK (never had a poster, nothing to restore).
        │
        ▼
4. Stat poster_path on disk. Does the file still exist?
   │
   ├── YES → poster survived the upgrade. Skip restoration.
   │         Update folder_path if it changed from the webhook.
   │         Log: "UPGRADE NO-OP | movie=Die Hard | poster survived at path"
   │
   └── NO → poster is gone. Proceed to restoration.
            │
            ▼
5. Check local cache: data/posters/cache/movies/{tmdb_id}.jpg
   │
   ├── EXISTS → Primary path: restore from cache.
   │            │
   │            ▼
   │   a. Translate new folder path from webhook (RADARR_PATH_PREFIX → RADARR_MEDIA_PATH)
   │   b. Copy cached file to: {new_folder}/{poster_deployed_filename}
   │   c. Update DB:
   │      poster_path = new path
   │      folder_path = new folder
   │      updated_at = NOW()
   │   d. Log: "POSTER RESTORED | movie=Die Hard | from=cache | tmdb_id=562 | to=new_path"
   │   e. Return 200 OK with restoration details.
   │
   └── MISSING → Fallback: re-download from TMDB.
                 │
                 ▼
      a. Use poster_source_url from DB to re-download original poster.
      b. If download succeeds → same flow as cache restore above.
      c. If download fails → log error, flag movie for re-pipeline.
         "POSTER RESTORE FAILED | movie=Die Hard | cache_missing=true | download_failed=true"
      d. Return 200 OK with failure details (NOT 500 — this is a data problem, not a server error).
```

### 5.3 Path Translation During Restoration

Radarr's webhook payload contains Radarr's internal paths (e.g., `/plunder/movies/...`). If Radarr runs in Docker, these may be container paths that don't map to the host filesystem Marquee writes to.

The existing `safe_translate_and_validate()` in `path_utils.py` handles this:

```python
new_folder = safe_translate_and_validate(
    webhook_payload["movie"]["folderPath"],
    source="radarr"
)
restored_path = new_folder / movie.poster_deployed_filename
```

If path translation is not configured (dev/testing mode), the webhook paths are used directly.

---

## 6. Edge Cases and Their Resolution

### 6.1 Upgrade in Same Folder (In-Place Upgrade)

Sometimes Radarr upgrades a movie without changing the folder — it just replaces the media file. The poster file is not affected.

**Detection:** `poster_path` still exists on disk at stat time → skip restoration. Update `folder_path` if webhook says it changed. Log as a no-op.

### 6.2 Multiple Rapid Upgrades

Radarr might upgrade back-to-back (e.g., WebDL arrives, then BluRay 30 seconds later). Two webhooks fire in rapid succession.

**Resolution:** Each webhook handler is independent. The first webhook restores the poster. The second webhook starts, stats `poster_path`, finds it EXISTS (restored by the first webhook), and skips. No conflict.

### 6.3 Plex/Jellyfin Regeneration Window

Between the upgrade and the restoration, Plex's metadata agent may scan the new folder and generate default poster artwork.

**Resolution:** Marquee overwrites whatever is there with the cached poster. Plex will detect the file change on its next scan and refresh. This window is typically seconds (webhook is instant) vs Plex's scan interval (minutes), so the default poster is rarely visible to the user.

### 6.4 User Manually Replaced the Poster on Disk

The user opens the movie folder and drops in a custom poster. Later, Radarr upgrades.

**Resolution:** The cache stores what Marquee deployed — not whatever is on disk. Restoration reverts to the Marquee-deployed poster. This is correct behavior: the system restores what it is responsible for. If the user wants a custom poster to survive upgrades, they should use Marquee's frontend to approve it (which writes it to the cache).

### 6.5 Cache File was Deleted

The cache directory was cleaned or the file corrupted.

**Resolution:** Fall back to re-downloading from TMDB using `poster_source_url`. If that also fails, flag the movie for re-pipeline. Log error prominently.

### 6.6 TMDB Poster Was Removed or Changed

The original poster is no longer available at the stored URL.

**Resolution:** This is rare but happens. Flag the movie for re-pipeline — it needs a fresh set of posters. Do not silently deploy nothing.

### 6.7 Webhook Arrives Before Folder Exists

Race condition: Radarr fires the webhook before fully finalizing the folder.

**Resolution:** The webhook handler should retry the stat/copy a few times with short delays (100ms, 500ms, 1s) before giving up. Radarr's webhook fires after file import, so this window is tiny.

### 6.8 New Movie (Not an Upgrade)

Radarr sends `Download` with `isUpgrade: false` for new additions.

**Resolution:** `poster_path` is NULL (no poster was ever deployed for this movie). Webhook is a no-op. Log info. The movie may have been synced to Marquee's DB earlier — if so, the sync service already has it queued for pipeline processing.

---

## 7. What About Sonarr?

Sonarr upgrades individual episode files within a season folder. It does NOT delete the whole folder. The series poster (`poster.jpg` in the show root) and season posters (`season01-poster.jpg`) survive upgrades.

However, some edge cases in Sonarr CAN affect posters:
- Series folder rename (change root folder)
- Season pack upgrade that replaces the entire season folder

These are less common and lower priority. The same webhook mechanism applies — Sonarr also supports webhooks with a nearly identical payload format. The existing `POST /api/webhooks/sonarr` stub is ready for this when needed.

**Recommendation:** Build Radarr restoration first. Extend to Sonarr later using the same cache mechanism and webhook handler pattern.

---

## 8. API Endpoint Overview

| Endpoint | Purpose |
|---|---|
| `POST /api/webhooks/radarr` | Receives Radarr events. Handles Download (upgrade) by restoring poster from cache. Handles Rename by updating `folder_path`. |

No additional API endpoints are needed for the restoration feature itself. The deployment-time cache population happens inside the existing pipeline output stage and the feedback endpoint.

---

## 9. Open Questions

1. **Poster filename convention:** Should Marquee always deploy as `poster.jpg` (the Plex/Jellyfin convention), or should the user be able to configure a custom filename pattern? Currently assuming `poster.jpg` as the universal convention.

2. **Sonarr restoration priority:** Worth building alongside Radarr (same webhook handler pattern) or defer until after Phase 4?

3. **Cache size monitoring:** Should the taste status panel include "cache: 432 posters, 68 MB"? Minor addition, useful for awareness.

4. **What if the user manually deleted the poster before the upgrade?** The poster is already gone. The webhook will see `poster_path` is NULL (or file missing) and attempt restoration — which is desirable. If `poster_path` was set to NULL by the manual deletion, we need `_check_existing_poster` to handle this (already on the Phase 3 todo list).

5. **Should cache use original resolution?** Currently proposing w780 — good enough for Plex/Jellyfin display, small on disk. If the user wants original quality, they can re-run the pipeline which re-downloads at original. This is a storage/quality trade-off left to the user.

---

# Part 2 — Implementation Plan

**Status:** Implementation-ready design — discussed before build
**Date:** 2026-06-12
**Depends on:** the shared foundation from design 09 §20 step 1 (Alembic migrations, new artwork columns).

## 10. Decisions on the Open Questions (§9)

1. **Filename convention** — Already solved in config: `MOVIE_POSTER_FORMAT` exists in `marquee/config.py:305` with `{movie_basename}` templating (plus series/season formats). Deployment renders the template and stores the result in the new `poster_deployed_filename` column; restoration writes exactly that name even if the format setting changed since.
2. **Sonarr priority** — Defer the restoration logic, but parse Sonarr payloads from day 1: the webhook handler validates/dispatches both, and Sonarr's `Rename`/`SeriesDelete` get the same path-update treatment. Sonarr file-level upgrades don't destroy posters (§7), so this loses nothing.
3. **Cache size monitoring** — Yes: `GET /api/system/status` (§14) reports cached poster count + bytes (one `os.scandir` walk, trivially cheap at this scale).
4. **Manually deleted poster** — Covered by the self-heal scan (§13), which is being pulled into this feature: it detects missing files, sets `poster_path = NULL` where appropriate, and restores from cache. This also closes the existing Phase-3 todo "`_check_existing_poster` should NULL stale `poster_path`" (`todos.md`).
5. **Cache resolution** — Cache the **exact deployed bytes**, not a w780 transcode. At deployment time we already hold the original-resolution file (the pipeline re-downloads top-5 at original — `pipeline/output.py:84`), so the cache copy is a `shutil.copy2`. Benefits: `poster_sha256` in the DB matches the cache file exactly (integrity check is a hash compare), and restoration reproduces the deployed poster bit-for-bit. Cost: ~300 KB–1.5 MB per movie instead of ~150 KB — still under 2 GB for a 5,000-movie library. The w780 idea is dropped.

## 11. `PosterService` — the Single Write Path (new, shared with design 09)

The feature that actually matters here is not the webhook — it's that **all poster writes go through one service**. Today nothing writes posters to movie folders at all (pipeline output lands in `experiments/runs/`); design 09's approve/override needs deployment, and this design needs restoration. One module serves both: `marquee/core/poster_service.py`.

```
async deploy(db, movie, source_file, *, ai_selected, user_approved) -> DeployResult
    1. Render filename from MOVIE_POSTER_FORMAT.
    2. Validate destination: movie.folder_path through safe_translate_and_validate()
       (core/path_utils.py:34) — this also implements the open Phase-3 todo
       "file write path validation" before any write.
    3. Atomic write: copy to {folder}/.poster.tmp, os.replace() to final name.
    4. Cache: copy bytes + write meta.json sidecar (§3 schema) under
       settings.poster_cache_path / "movies" / f"{tmdb_id}.jpg".
    5. DB: poster_path, poster_source, poster_source_url, poster_ai_selected,
       poster_user_approved, poster_deployed_filename, poster_deployed_at,
       poster_sha256, poster_phash (imagehash, already a dependency).
    6. Log an artwork_events row (§12) — action="deploy".

async restore(db, movie, new_folder) -> RestoreResult
    cache hit  → verify sha256 against DB → copy to {new_folder}/{poster_deployed_filename}
    cache miss → re-download poster_source_url (httpx, original size) → same copy
    both fail  → set poster_path=NULL, log action="restore_failed" (movie shows up
                 in the needs-poster index, ix_movies_missing_poster, models/movie.py:53)
    success    → update poster_path/folder_path, log action="restore"
```

Path-translation note: webhook payloads carry Radarr-namespace paths; `safe_translate_and_validate(payload.movie.folderPath, source="radarr")` handles prefix mapping and root validation exactly as §5.3 describes — that part of Part 1 stands unchanged.

## 12. Data Model Changes

Via the shared Alembic migration (design 09 §20 step 1):

- `ArtworkMixin` (`models/base.py:32`) gains `poster_user_approved` (BOOL, default False), `poster_deployed_filename` (VARCHAR(255)), `poster_deployed_at` (DATETIME) — the third is new vs Part 1 §4; it's what lets the UI say "deployed 3 weeks ago, restored twice since".
- New table `artwork_events`: `id`, `movie_id` (FK, indexed), `action` (`deploy` / `restore` / `restore_failed` / `heal_restore` / `webhook_noop`), `source` (`pipeline` / `feedback` / `webhook` / `heal`), `detail` (TEXT JSON: old/new paths, cache hit/miss, error), `created_at`. This is the restoration-history feed for the frontend and the debugging trail for webhook behavior. Cheap, append-only.

The cache path stays derived (no DB column), per Part 1 §4 — but note the location is the **existing** `settings.poster_cache_path` → `data/cache/posters/` (`config.py:58`), not the `data/posters/cache/` spelled in §3. Structure: `data/cache/posters/movies/{tmdb_id}.jpg` + `{tmdb_id}.meta.json`.

## 13. Webhook Handler + Self-Heal Scan

**`api/routes/webhooks.py`** (replacing the 501 stubs):

- Pydantic models for the payload (`RadarrWebhookPayload` with `eventType`, `isUpgrade`, nested `movie`/`movieFile`) — tolerant parsing (`extra="ignore"`), since Radarr adds fields between versions.
- **Event dispatch**, two additions over Part 1 §2's table: `Test` (Radarr's Connect-UI test button — must return 200 with a friendly body or the user can't even save the webhook) and `MovieFileDelete` with `deleteReason: "upgrade"` (fires *before* the upgrade's `Download`; explicitly ignored so it doesn't race the restore).
- **Fast ACK**: the handler validates, looks up the movie, and schedules restoration as an `asyncio` background task, returning 200 immediately — Radarr's webhook timeout is short and a cache-miss restore includes a TMDB download. Retry-on-missing-folder inside the task: 0.1 s / 0.5 s / 1 s / 3 s backoff (§6.7).
- **Auth (new)**: optional `WEBHOOK_TOKEN` setting; when set, the handler requires `?token=...` on the URL (Radarr's webhook URL field carries it; its basic-auth fields work too but a URL token is zero-config on our side). Default unset = open, consistent with the LAN-trust model of the rest of the API.
- **Dry-run (new)**: `WEBHOOK_DRY_RUN=true` logs and records `artwork_events` without touching the filesystem — for the first days after the user wires Radarr up.
- Idempotency for rapid double-upgrades (§6.2) needs one addition to Part 1's stat-based reasoning: the background task takes a per-movie `asyncio.Lock` so two near-simultaneous webhooks can't both pass the stat check before either copies.

**Self-heal scan** (pulled forward from Phase 5; config already anticipates it — `HEAL_INTERVAL_MINUTES`, `config.py:280`): an asyncio task started in `lifespan()` (`main.py:45`) every N minutes walks movies where `poster_path IS NOT NULL`, stats the file, and on miss calls `PosterService.restore`. This catches everything webhooks can't see: deletions while Marquee was down, Plex agent overwrites, manual cleanup. Also exposed on demand as `POST /api/system/heal`.

## 14. API Surface

| Endpoint | Method | Purpose |
|---|---|---|
| `/api/webhooks/radarr` | POST | Event dispatch per §13 |
| `/api/webhooks/sonarr` | POST | Payload parse + `Rename` path updates only (decision 2) |
| `/api/movies/{movie_id}/poster/restore` | POST | Manual restore button (force=true overwrites existing file) |
| `/api/movies/{movie_id}/artwork-events` | GET | Restoration/deploy history for the movie detail UI |
| `/api/system/heal` | POST | On-demand heal scan, returns `{checked, restored, failed}` |
| `/api/system/status` | GET | Cache stats (count/bytes), heal-scan last-run, webhook last-received |

## 15. Config Additions (`marquee/config.py`)

`WEBHOOK_TOKEN: str | None = None`, `WEBHOOK_DRY_RUN: bool = False`, `HEAL_ENABLED: bool = True` (interval already exists). No new dependencies — httpx, imagehash, Pillow all present.

## 16. Build Order & Verification

1. **PosterService + columns** (after the shared migration) — *verify:* unit tests with `tmp_path` folders: deploy writes file + cache + meta + DB row + event; restore from cache; restore via download fallback (mocked httpx); sha256 mismatch handling; path-validation rejection for a folder outside `MEDIA_ROOTS`.
2. **Webhook router** — *verify:* pytest with captured real Radarr payloads (Test, Download/upgrade, Download/new, Rename, MovieFileDelete) against the in-memory app (`tests/test_app.py` pattern); double-webhook race test asserting single copy.
3. **Heal scan + system/manual endpoints** — *verify:* delete a deployed poster on disk, run `POST /api/system/heal`, confirm restoration + `heal_restore` event.
4. **Live smoke test**: point a real Radarr at a test library, trigger a manual upgrade, watch the `artwork_events` trail.

Sonarr restoration (season-pack folder replacement) remains a follow-up; the service and handler are shaped so it's additive.
