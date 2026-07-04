# 06 — HDR Management: Backend

> **For the implementing agent:** Complete instruction set — implement **phase
> by phase, in order**, each phase leaving the repo green
> (`ruff check marquee tests` + `pytest -q`) with movie behavior unchanged.
> The **Ground rules** in `design/plans/04-television-backend.md` §0 apply
> verbatim to this plan (venv, lint gate, Alembic offline-only, known-failing
> test baseline, commit style, NaN boundary). Where this doc names a file,
> function, column, or default, use exactly that name; where it says
> "mirror X", open X and copy its structure. The TV posters project
> (plans 04–05) already established the subject-generalization and eligibility
> patterns — reuse them; do not invent parallel ones.

**Goal:** (1) An HDR **summary/landing read-model** feeding a new landing page:
library-wide HDR metrics for movies *and* TV, preference-target management for
both Radarr and Sonarr quality profiles, DoVi analysis coverage, and insight
lists (worst offenders etc.). (2) **Television HDR support**: per-episode HDR
truth synced from Sonarr v4 (no probing), per-season/per-show rollups
(target compliance + uniformity), a granular per-show read-model
(season × episode matrix), Sonarr preference targets, and per-episode Dolby
Vision **analysis** (batch). Frontend is `design/plans/07-hdr-management-frontend.md`.

---

## 1. Locked decisions

| # | Decision |
|---|---|
| H1 | Sonarr is **v4** — quality profiles carry custom formats + format items exactly like Radarr; episodefile payloads carry `mediaInfo.videoDynamicRange(Type)` and `customFormatScore`. Build against v4 only. |
| H2 | Route/page structure: `/hdr` = landing; movie list at `/hdr/movies`; movie detail `/hdr/movies/[id]`; TV list `/hdr/tv`; show detail `/hdr/tv/[id]`. Backend movie endpoints (`GET /api/hdr`, `GET /api/hdr/{movie_id}`, `PUT /api/hdr/preferences`, DoVi endpoints) are **unchanged** — new endpoints are added alongside. |
| H3 | **Specials (season 0)** appear in the show detail read-model (flagged) but are **excluded** from show-level compliance status and uniformity rollups. |
| H4 | DoVi for episodes: **analysis only** (batch jobs per show / season / library). Episode conversion is deferred; movie conversion is implemented but untested (recorded in `design/timeline.md`). Do not touch conversion code. |
| H5 | Mixed shows report the **union** of episode tags plus a `mixed` uniformity flag; uniform shows report the single shared tag-set. |
| H6 | **No custom-format scoring in this feature.** Do not add CF score columns to Episode, and do not build per-episode CF breakdowns (future page, out of scope). HDR tags + preference targets only. |
| H7 | Sonarr backend parity is built **only to the extent HDR needs it**: quality profiles + custom formats + format items sync, and episode mediaInfo capture. Explicitly *not* built now: Sonarr webhooks, per-episode CF scores, series cutoff/CF display (all listed in `design/timeline.md` Deferred). |
| H8 | Rollups are computed **at read time** (matching the movie page's style). If the TV list ever gets slow, the escape hatch is materializing rollups onto `Series` during sync — note this in code comments, do not build it. |
| H9 | Eligibility follows the TV posters rules (plan 04 D3): invisible series and undownloaded seasons/episodes never appear. Reuse `marquee/core/tv_queries.py`; episode-level predicate = `Episode.episode_file_path IS NOT NULL`. |
| H10 | Landing insights (§6.2): worst-offenders list, DoVi-no-fallback spotlight, unanalyzed-DoVi counts, no-HDR-target counts, and 4K-but-SDR upgrade candidates (movies + shows). Episode `video_width`/`video_height` are captured from the same mediaInfo to power the last one. |

---

## 2. Current state (verified)

- Movie HDR: `Movie.hdr_type_raw` synced from Radarr; `classify_hdr_tags` /
  `distribution_keys` / `overlay_bucket` / `preference_status` and friends in
  `marquee/core/radarr_overlay.py` are **pure, service-agnostic functions** —
  they operate on raw strings, tag sets, and profile-target sets. Reuse them
  for TV unchanged.
- Preference targets: per-Radarr-profile rows in
  `RadarrOverlayProfilePreference` (meet/exceed/excluded); profile HDR
  capabilities derived from `RadarrCustomFormat` + `RadarrProfileFormatItem`
  via `classify_custom_format_tags` / `profile_hdr_targets`. The read-model is
  assembled in `marquee/api/routes/hdr.py::_load_profile_context`.
- Reference-data sync: `_sync_radarr_overlay_reference_data` in
  `marquee/core/sync_service.py` (upserts CFs + profiles + format items,
  deletes stale rows, backfills placeholder CFs from formatItems).
- DoVi: `DoviState` (one row per **movie**, `movie_id` unique) written by the
  `dovi_analyze` job (`marquee/core/jobs/dovi_handlers.py`), which calls
  `dovi_analysis.analyze_path(path)` — **path-based and subject-agnostic**.
  `conversion_eligibility` feeds the movie detail page. Batch analyze uses
  `job_manager.create_batch` with `dovi_analyze` children.
- Episodes: `Episode.has_hdr` / `has_dv` exist but are **never populated**;
  `_sync_episodes` fetches `get_episode_files(series_id)` payloads and
  discards `mediaInfo`. `_extract_hdr(movie_file)` and
  `_extract_media_info(movie_file)` in `sync_service.py` operate on the
  `mediaInfo` dict shape shared by Radarr movieFiles and Sonarr episodefiles —
  reuse both directly.
- `SonarrClient` has `get_series/get_episodes/get_episode_files/get_custom_formats`
  — **no `get_quality_profiles`**.
- `Series.quality_profile_id` is synced. TV eligibility predicates live in
  `marquee/core/tv_queries.py` (`series_visible`, `season_downloaded`).
- Sort helper: `marquee/core/sort_title.py::sort_title` (used by the movie HDR
  sort) — reuse for TV sorting.

---

## 3. Phase 0 — Schema

One Alembic revision (`add tv hdr and sonarr overlay schema`).

### 3.1 `Episode` (marquee/models/episode.py)
Add (all nullable):
- `hdr_type_raw: String(64)` — same semantics/comment as `Movie.hdr_type_raw`.
- `video_width: Integer`, `video_height: Integer` — from mediaInfo (H10).
(`has_hdr` / `has_dv` already exist — Phase 1 populates them.)

### 3.2 Sonarr overlay tables (new file `marquee/models/sonarr_overlay.py`)
Mirror `marquee/models/radarr_overlay.py` exactly, minus
`MovieCustomFormatScore` (H6):
- `SonarrCustomFormat` (`sonarr_custom_formats`)
- `SonarrQualityProfile` (`sonarr_quality_profiles`)
- `SonarrProfileFormatItem` (`sonarr_profile_format_items`)
- `SonarrOverlayProfilePreference` (`sonarr_overlay_profile_preferences`)
Same columns, types, and FK/ondelete structure as their Radarr twins.
Register in `marquee/models/__init__.py`.

### 3.3 `DoviState` subject generalization (marquee/models/dovi.py)
Same pattern as `pipeline_runs` in plan 04:
- `movie_id` → nullable (keep its unique index — multiple NULLs are allowed on
  PG and SQLite).
- Add `media_type: String(10)` non-null, `server_default="movie"`, index.
- Add `episode_id: Integer | None` — FK `episodes.id`, `ondelete="CASCADE"`,
  **unique** index.
- CheckConstraint `ck_dovi_state_subject`:
  `(media_type='movie' AND movie_id IS NOT NULL) OR (media_type='episode' AND episode_id IS NOT NULL)`.

### 3.4 Tests
ORM smoke via conftest `create_all`: episode-shaped `DoviState` row inserts;
movie-shaped rows unchanged; new tables create on SQLite.

---

## 4. Phase 1 — Sync (Sonarr v4 overlay data + episode HDR)

### 4.1 `SonarrClient.get_quality_profiles()`
`GET /api/v3/qualityprofile` — mirror the Radarr client method.

### 4.2 `_sync_sonarr_overlay_reference_data`
In `sync_service.py`, mirror `_sync_radarr_overlay_reference_data` **line for
line** against the Sonarr tables (upsert CFs + profiles, placeholder CFs from
`formatItems`, stale-row deletion, `SonarrProfileFormatItem` full replace).
Call it at the top of `_sync_series` (guard: only when `self.sonarr` is set;
tolerate fetch failures with a warning exactly like the movie path). No
per-episode score capture (H6) — the returned score maps are unused; keep the
function's return shape anyway for symmetry.

### 4.3 Episode HDR capture
In `_sync_episodes` (and the shared `_upsert_episode_media_files` flow), for
every episode that resolves to a file payload `fdata`:
- `hdr_type_raw, has_hdr, has_dv = _extract_hdr(fdata)` — note this helper
  already returns `("SDR", False, False)` when mediaInfo exists without
  dynamic-range fields, and `(None, None, None)` when there is no mediaInfo;
  keep that behavior (NULL = unknown).
- `width, height, _ = _extract_media_info(fdata)` → `video_width/height`.
- Multi-episode files: apply the same values to **every** linked Episode row.
- Episodes without a file: clear all five columns to NULL (file was deleted).

### 4.4 Tests
Sync fixture with a Sonarr series + episodefiles carrying
`videoDynamicRangeType` variants (`""`→SDR, `HDR10`, `HDR10Plus`,
`DV`, `DV HDR10`) and a multi-episode file; assert per-episode columns,
including NULL for a mediaInfo-less file and clearing on file removal.
Sonarr overlay sync: profiles/CFs/items upserted, stale rows deleted,
Radarr tables untouched.

---

## 5. Phase 2 — Rollup engine (`marquee/core/hdr_rollups.py`, new)

Pure functions only — no DB access, fully unit-testable. Inputs are plain
per-episode dicts the routes build:
`{"season_number": int, "episode_number": int, "hdr_type_raw": str | None}`.

```python
@dataclass(frozen=True)
class EpisodeHdr:
    season_number: int
    episode_number: int
    title: str | None
    hdr_type_raw: str | None
    # derived once at construction:
    tags: tuple[str, ...]          # ordered_tags(classify_hdr_tags(raw))
    bucket: str                    # overlay_bucket(raw); "unknown" when raw is None
    distribution: tuple[str, ...]  # distribution_keys(raw)

def episode_status(tags, profile_targets, meet, exceed, excluded) -> str
    # thin wrapper over radarr_overlay.preference_status; returns "unknown"
    # when hdr_type_raw is None (raw None ⇒ no tags AND no mediaInfo — pass a
    # sentinel; do NOT report no-mediaInfo files as sdr/below).

def season_rollup(episodes: list[EpisodeHdr], statuses: list[str]) -> dict
    # {"uniformity": "uniform" | "mixed",         # over episodes with known raw
    #  "uniform_tags": [...] | None,              # the shared tag-set when uniform
    #  "union_tags": [...],                       # ordered union (H5)
    #  "distribution": {key: n},                  # summed distribution_keys
    #  "status_counts": {status: n},
    #  "episodes_total": n, "episodes_known": n, "episodes_unknown": n}

def show_rollup(season_rollups: dict[int, dict]) -> dict
    # Excludes season 0 (H3). Returns:
    # {"status": "exceeds_target" | "meets_target" | "gaps" | "below_target"
    #            | "no_hdr_target" | "unknown",
    #  "uniformity": "uniform" | "uniform_by_season" | "mixed",
    #  "union_tags": [...], "uniform_tags": [...] | None,
    #  "distribution": {...}, "status_counts": {...},
    #  "episodes_total": n, "episodes_known": n, "episodes_unknown": n,
    #  "meeting_fraction": {"met": n, "of": n}}   # episodes ≥ meet target
```

Rollup rules (write these as the docstring):
- **Status**: over non-specials episodes with known status
  (`unknown` excluded from the verdict but reported in counts):
  all `exceeds_target` → `exceeds_target`; all ≥ meet (mix of meets/exceeds)
  → `meets_target`; some ≥ meet and some `below_target` → `gaps`; none ≥ meet
  → `below_target`. All `no_hdr_target` → `no_hdr_target`. No known episodes
  → `unknown`.
- **Uniformity**: computed over episodes with known raw only. Season is
  `uniform` iff all its known episodes share an identical tag-set (SDR's empty
  set counts as a tag-set — an all-SDR season is uniform). Show is `uniform`
  iff all non-specials seasons are uniform **and** share the same set;
  `uniform_by_season` iff each season is uniform but sets differ; else
  `mixed`.
- A season with zero known episodes contributes nothing to uniformity.

Unit tests: every verdict above, specials exclusion, all-unknown shows,
single-season shows, SDR-uniform shows, empty-tag vs None distinction.

---

## 6. Phase 3 — API

All new code in `marquee/api/routes/hdr.py` (it stays one router,
prefix `/api/hdr`). **Register the literal paths (`/summary`, `/tv`, …) before
the existing `/{movie_id}` route** so they don't get captured by it — add a
route-ordering test.

### 6.1 Shared profile context
Refactor `_load_profile_context` into a parameterized helper taking the four
model classes (a small `OverlayModels` namedtuple:
`profile_model, format_item_model, custom_format_model, preference_model`)
with `RADARR_MODELS` / `SONARR_MODELS` instances. The Radarr call sites keep
identical behavior (pin with existing tests).

### 6.2 `GET /api/hdr/summary` — the landing read-model

```json
{"movies": {
    "total": n, "distribution": {sdr, hdr, hdr10, hdr10p, dovi, dovi_no_fallback},
    "status_counts": {below_target, meets_target, exceeds_target, no_hdr_target},
    "dovi_analysis": {"analyzed": n, "total_dovi": n}},
 "tv": {
    "shows_total": n, "episodes_total": n, "episodes_unknown": n,
    "episode_distribution": {...same keys...},
    "show_status_counts": {exceeds_target, meets_target, gaps, below_target,
                            no_hdr_target, unknown},
    "uniformity_counts": {"uniform": n, "uniform_by_season": n, "mixed": n},
    "dovi_analysis": {"analyzed": n, "total_dovi": n}},
 "worst_offenders": [ up to 10 of:
    {"series_id":..., "title":..., "year":..., "status": "gaps"|"below_target",
     "below_count": n, "unknown_count": n, "episodes_total": n,
     "meeting_fraction": {"met": n, "of": n}} ],
 "insights": {
    "dovi_no_fallback": {"movies": n, "episodes": n, "shows_affected": n},
    "unanalyzed_dovi":  {"movies": n, "episodes": n},
    "no_hdr_target":    {"movies": n, "shows": n},
    "four_k_sdr":       {"movies": n, "shows_affected": n, "episodes": n}},
 "profile_preferences": {"radarr": [...], "sonarr": [...]}}   // §6.1 summaries
```

Notes:
- Movie numbers reuse the exact item-building logic of `hdr_index` — extract
  the per-movie item assembly into a helper both endpoints call, rather than
  duplicating it.
- `worst_offenders`: shows with status `gaps` or `below_target`, sorted by
  `below_count` desc then title.
- `four_k_sdr`: width ≥ 3000 (matches `resolution_label`'s 2160p logic — read
  it and use the same threshold) and distribution contains `sdr`.
- `unanalyzed_dovi`: subjects with the `dovi` tag and no `DoviState` row with
  `status == "analyzed"`.
- All TV numbers respect eligibility (H9).

### 6.3 `GET /api/hdr/tv` — show list

Query params: `page`, `page_size` (same bounds as movies), `hdr_tags`
(matched against the show's **union** tags + distribution keys),
`preference_status` (the rollup status values), `uniformity`, `profile_id`,
`dovi_no_fallback: bool`, `sort_by: title | status | coverage`
(`coverage` = met/of fraction), `sort_dir`.

Item shape:
```json
{"id":..., "title":..., "year":..., "poster_available": bool,
 "profile_id":..., "profile_name":..., "profile_targets": [...],
 "meet_target":..., "exceed_target":...,
 "rollup": { show_rollup dict from §5 },
 "seasons_count": n, "episodes_total": n}
```
Plus top-level `distribution` (episode-level, over all visible shows),
`profiles` (Sonarr profiles present, mirroring the movie payload),
`profile_preferences` (Sonarr summaries), `applied_filters` — mirror the
movie endpoint's envelope so the frontend list machinery is reusable.

Implementation: one query for visible series (+ their Sonarr profile), one
grouped query for all their eligible episodes' hdr fields, build
`EpisodeHdr`s + rollups in Python (H8).

### 6.4 `GET /api/hdr/tv/{series_id}` — show detail (the granular read-model)

404 for invisible series. Shape:
```json
{"series": {"id","title","year","tvdb_id","tmdb_id","quality_profile_id"},
 "profile": {"id","name","targets":[...], "meet_target":..., "exceed_target":...,
              "excluded_targets":[...]},
 "rollup": { show_rollup },                       // specials excluded (H3)
 "seasons": [{
    "season_number": 0..n, "is_specials": bool,
    "rollup": { season_rollup },
    "episodes": [{
       "id":..., "episode_number":..., "title":...,
       "hdr_type_raw":..., "hdr_tags":[...], "bucket":...,
       "resolution":...,                           // resolution_label(w, h)
       "preference_status":...,
       "dovi": {"status":..., "profile":..., "el_type":...,
                 "bl_signal_compatibility_id":...} | null}]}],
 "analysis_jobs": [...active dovi tv jobs for this series...],
 "binaries": {"ffprobe": bool}}
```
Seasons ordered ascending, specials last or first is the frontend's problem —
emit ascending (0 first) and let the client reorder. Episodes ordered by
episode_number. This payload is deliberately complete: the heatmap, season
summaries, and episode table all render from this one response.

### 6.5 `PUT /api/hdr/tv/preferences`
Mirror `put_profile_preferences` verbatim against the Sonarr models (same
payload shape, same validation, same `is_valid_preference_pair` rules).

### 6.6 DoVi analysis for episodes (H4)

- Generalize the `dovi_analyze` handler (`dovi_handlers.py`): payload is
  `{"movie_id": n}` **or** `{"episode_id": n}`. Episode path resolution:
  `safe_translate_and_validate(episode.episode_file_path, source="sonarr")`.
  Upsert `DoviState` by subject (`episode_id` unique). Resource locks: episode
  files have MediaFile rows (`sonarr:episode-file:{id}` source keys) — resolve
  via `EpisodeMediaFile` and lock `media-file:{id}` like the movie path;
  when no MediaFile row exists, proceed without the file lock (log it).
- `POST /api/hdr/tv/{series_id}/analyze` — body
  `{"season_number": int | null}` (null = whole show). Children = one
  `dovi_analyze` per eligible episode with `has_dv IS TRUE` and a file
  (mirror the movie batch endpoint: `create_batch`, parent type
  `dovi_analyze_batch`, subject_type `"dovi_tv_batch"`). 400 when no eligible
  episodes. 503 without ffprobe.
- `POST /api/hdr/tv/analyze` — library-wide TV variant (all visible shows),
  same construction, optional `{"series_ids": [...]}` subset — mirror the
  movie `POST /api/hdr/analyze`.
- Movie endpoints untouched.

### 6.7 Tests (Phase 3)
`tests/test_hdr_tv_api.py`: summary numbers against a fixture library
(2 shows: one uniform-meets, one gaps-with-specials + a DoVi-no-fallback
episode + an unknown episode); worst-offenders ordering; list filters/sorts;
detail payload (specials flagged + excluded from show rollup; episode dovi
state join); Sonarr preferences PUT validation matrix (copy the movie PUT
tests); tv analyze batch child construction; route-ordering test
(`/api/hdr/summary` and `/api/hdr/tv` not captured by `/{movie_id}`);
**movie regression**: `GET /api/hdr` response byte-shape unchanged
(snapshot the envelope keys) and Radarr `PUT /preferences` untouched.

---

## 7. Out of scope (do not build — recorded in `design/timeline.md`)

- DoVi **conversion** for episodes; movie conversion testing (separate task).
- Per-episode custom-format scores/breakdowns (future dedicated page).
- Sonarr webhooks; materialized rollups (H8); TV letterbox/subtitle HDR tie-ins;
  notification/alerting on HDR regressions.

## 8. Execution order

Phase 0 (schema) → 1 (sync) → 2 (rollups) → 3 (API). Same per-phase gates and
commit convention as plan 04. The pre-existing failing-test baseline rule
applies (`design/plans/04-television-backend.md` §0.6).

## 9. API contract summary (for the frontend plan)

| Method + path | Purpose |
|---|---|
| `GET /api/hdr/summary` | Landing metrics + insights + both libraries' profile preferences |
| `GET /api/hdr` | Movie list — **unchanged** |
| `GET /api/hdr/{movie_id}` | Movie detail — **unchanged** |
| `PUT /api/hdr/preferences` | Radarr targets — **unchanged** |
| `GET /api/hdr/tv` | Show list with rollups + filters (movie-envelope-compatible) |
| `GET /api/hdr/tv/{series_id}` | Season × episode matrix read-model |
| `PUT /api/hdr/tv/preferences` | Sonarr targets (same payload shape as Radarr PUT) |
| `POST /api/hdr/tv/{series_id}/analyze` | DoVi analysis batch, show or one season |
| `POST /api/hdr/tv/analyze` | DoVi analysis batch, library-wide / subset |
| `POST /api/hdr/{movie_id}/analyze`, `/convert`, `POST /api/hdr/analyze` | Movie DoVi — **unchanged** |

New/changed job surface: `dovi_analyze` payload now also accepts
`{"episode_id"}`; batch parents may carry subject_type `"dovi_tv_batch"`.
