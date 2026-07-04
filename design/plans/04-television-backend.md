# 04 — Television Integration: Backend

> **For the implementing agent:** This document is your complete instruction set.
> Implement it **phase by phase, in order** — each phase leaves the repo green
> (`ruff check marquee tests` + `pytest`) and movies fully working. Do not skip
> ahead: later phases assume earlier ones verbatim. Where this document names a
> file, function, column, or default, use exactly that name. Where it says
> "mirror X", open X and copy its structure/idioms rather than inventing new ones.
> Read `CLAUDE.md` and the **Ground rules** below before writing any code.

**Goal:** Extend Marquee's poster machinery — library, selection pipeline,
review/feedback, restoration/heal, text profiles, and the taste engine — to TV
**show posters** and **season posters**, sourced from Sonarr + TMDB, without
changing any movie behavior. Frontend work is specified separately in
`design/plans/05-television-frontend.md` and is **out of scope here**, but every
endpoint promised in that doc's API contract (§15 below) must exist and work.

---

## 0. Ground rules (read first)

1. **Movies must be bit-for-bit unaffected.** Movie endpoints, movie run archives,
   movie job payloads, and the movie taste profile keep their exact current
   behavior. Any query that previously returned only movie rows must still return
   only movie rows (see §13.1 — the `media_type='movie'` guard).
2. **Never touch `marquee/pipeline/runner.py`'s movie logic.** All TV pipeline
   execution goes through `marquee/pipeline/batch_runner.py` (a single season is
   a batch of one asset). `runner.py` may only be touched if a shared leaf helper
   needs a parameter added with a default preserving current behavior.
3. **Lint gate is `ruff check marquee tests` only. Never run `ruff format`.**
4. Use the project venv (`source .venv/bin/activate`). ML extras may be
   installed; tests that need real models are already skip-guarded.
5. **Alembic:** write migrations, verify them with `alembic upgrade head --sql`
   (offline SQL), and make sure `pytest` passes (tests create the schema from the
   ORM models via `create_all` against SQLite — so ORM models and migration must
   agree, and every new column must be SQLite-tolerant). **Do not run
   `alembic upgrade head` against the live PostgreSQL DB** — the operator applies
   migrations manually.
6. **Known pre-existing failures — do not chase these:**
   - `test_effective_ocr_workers_caps_cuda_unless_gpu_forced` fails on this
     machine on clean HEAD (environment issue).
   - A number of older migration-related tests are known-stale. Before starting,
     run `pytest -q` once on clean HEAD and record the failing set; your changes
     must not grow that set.
7. Commit at the end of each phase with a short imperative message
   (e.g. `add tv poster subject schema`), matching the repo's existing style
   (lowercase, no scope prefixes).
8. JSON responses that echo archived feature values must pass through the same
   NaN→null sanitization the movie endpoints use (see `tests/test_archive_nan.py`
   and how `build_results_payload` handles it). Starlette renders with
   `allow_nan=False`; a raw NaN is a 500.

---

## 1. Locked product decisions

These were decided with the operator and are **not open for reinterpretation**:

| # | Decision |
|---|---|
| D1 | Show poster filename default: **`show.jpg`**; season poster default: **`season{season:02d}.jpg`** (→ `season01.jpg`). **All TV posters live in the series root folder** (`Series.series_path`), never in season subfolders. Both formats are user-configurable like `MOVIE_POSTER_FORMAT`. |
| D2 | Specials are supported: Sonarr season 0 syncs like any season and gets `season00.jpg` — but only when it has downloaded episodes (the global eligibility rule). |
| D3 | **Eligibility rule (applies everywhere):** a series with zero downloaded episodes is invisible to Marquee; a season with zero downloaded episodes is invisible/ineligible even when its series is visible. Implemented once as shared SQL predicates (§5.3), reused by every TV query. |
| D4 | **One combined TV taste namespace** (profile + learned head) shared by show and season posters, separate from the movie namespace. Exemplars carry `asset_kind: "show" \| "season"` metadata. Taste-map visualization for TV includes only `asset_kind == "show"` exemplars. |
| D5 | Taste seeding folders: `data/taste_seeding/movies/`, `data/taste_seeding/shows/`, `data/taste_seeding/seasons/`. The movie rebuild's `training_dir` source now points at `taste_seeding/movies`; the TV rebuild unions `shows/` + `seasons/` (kind-tagged). |
| D6 | Text profiles become **scoped**: `movie`, `show`, `season` — one store, three scopes, each with built-ins + custom profiles + its own default. Season scope built-ins: `textless`, `title_only`, `title_and_season`; **season scope default = `title_and_season`**. New OCR category `season` (designator text like "Season 3", "2nd Season"). |
| D7 | Per-entity text-profile overrides for TV are **series-level only**: `Series.show_text_profile_id` and `Series.season_text_profile_id` (the latter applies to all of that show's seasons). No per-season granularity. |
| D8 | **Official-pick mode:** per-scope toggle; ON by default for seasons, OFF for shows and movies. Anchored on the TMDB *primary poster* (`poster_path` of the TV/season details), applied by **promoting the primary poster's stack to stack rank 1** after stacking. Fallback when the primary is absent/gated: `OFFICIAL_PICK_FALLBACK = "ranked"` (default; do nothing) or `"largest_stack"`. |
| D9 | When a season run ends with no rankable candidates, it is flagged for manual review (existing `flagged_manual` status), and the review API exposes a one-click **"use show poster"** action (§10.7). |
| D10 | Restoration + heal stay **one system**, generalized to all three subject types. No duplicate services. |
| D11 | Sonarr webhook restoration and TV cold-start onboarding are **deferred** — do not build them. (A Sonarr webhook stub already exists in `webhooks.py`; leave it.) |
| D12 | Season-designator patterns include ordinal forms ("2nd Season", "Second Season"), "The Final Season", Part/Vol/Book/Chapter forms, roman numerals, and bare season numbers (full pattern list in §7.4). |

---

## 2. Current state you are building on (verified facts)

Everything below was verified against the code — trust it, but re-verify line
numbers as you go (the file may have drifted slightly).

**Already exists and works:**
- `Series`, `Season`, `Episode` models (`marquee/models/`); `Series` and `Season`
  carry `ArtworkMixin` (all `poster_*` columns + `needs_poster`).
- Sonarr sync (`marquee/core/sync_service.py::_sync_series/_sync_seasons/_sync_episodes`)
  upserts all three + `EpisodeMediaFile` links; `Episode.episode_file_path` is
  populated from Sonarr episode files. Season 0 is currently **skipped**
  (`if season_num < 1: continue`) — Phase 1 changes this.
- `_check_existing_poster` / `_resolve_poster_path` in sync already resolve
  Series/Season poster paths using `settings.SERIES_POSTER_FORMAT` /
  `settings.SEASON_POSTER_FORMAT` (season posters in the series root).
- `TMDBClient` (`marquee/core/poster_sources/tmdb.py`) already has:
  `get_tv_images(tmdb_id)`, `get_season_images(tmdb_id, season_number)`,
  `get_tv_external_ids(tmdb_id)`, and `find_by_external_id(external_id, source)`
  (supports `source="tvdb_id"`; TV matches come back under `["tv_results"]`).
- Config already defines `SERIES_POSTER_FORMAT` (default `poster.jpg` — Phase 1
  changes to `show.jpg`) and `SEASON_POSTER_FORMAT` (default
  `season{season:02d}-poster.jpg` — Phase 1 changes to `season{season:02d}.jpg`).
- The settings GET route already returns `poster_formats.{movie,series,season}`.

**Hard-keyed to movies today (the actual work):**
- `PipelineRun.movie_id` — non-nullable FK (`marquee/models/pipeline_run.py`).
- `ArtworkEvent.movie_id` — non-nullable FK (`marquee/models/artwork_event.py`).
- `PosterService` (`marquee/core/poster_service.py`) — `deploy(db, movie, ...)`,
  `restore(db, movie, ...)`, `render_filename(movie)`, cache at
  `data/cache/posters/movies/{tmdb_id}.jpg`, backups at
  `settings.poster_backup_path / f"{movie.id}.jpg"`, path translation hardcoded
  `source="radarr"`.
- Heal (`marquee/core/heal.py`) walks only `Movie`.
- Feedback (`marquee/api/routes/feedback.py::apply_feedback_request`) loads a
  `Movie`, deploys to it, adds exemplars via `_add_to_profile(..., movie)`.
- `batch_runner.run_batch(job_id, movies=[(id, title, tmdb_id)], tmdb, extractor,
  progress, should_cancel, ocr_meta={movie_id: OcrGateContext})`; per-item context
  dataclass `_BatchMovie`.
- Job handlers in `marquee/core/jobs/builtin_handlers.py`: `poster_pipeline`,
  `poster_pipeline_batch`, `poster_heal`, `poster_rescan`, `poster_backup_all`,
  `poster_maintenance`, `poster_deploy_reset`, `taste_rebuild`, `taste_map`,
  `learned_head_train`, `pipeline_cache_clear`.
- Taste stack: single global namespace — `pipeline_settings.TASTE_PROFILE_PATH`,
  `LEARNED_HEAD_PATH`, `TRAINING_DATA_DIR` (currently `data/training/positive`),
  `NEGATIVE_DATA_DIR`; `taste_trainer.rebuild_profile(training_dir=...)`;
  `head_trainer.train_from_labels()`; `feedback_store` (labels under
  `data/feedback/`); `profile_updater.add_exemplar(...)`;
  `artifact_registry.KIND_TASTE_PROFILE / KIND_LEARNED_HEAD`;
  `scorer.select_scorer(config)`.
- Text profiles (`marquee/core/text_profiles.py`) — single flat store in
  `data/text_profiles.json`, built-ins `title_only`/`textless`,
  `OcrGateContext.from_movie(movie)`; routes in
  `marquee/api/routes/text_profiles.py`; per-movie override column
  `Movie.text_profile_id`.
- Movie pipeline routes (`marquee/api/routes/pipeline.py`): `_downloaded()`
  predicate, `_review_queue_latest()`, `/batch`, `/summary`, `/review-queue`,
  `/review-queue/approve-auto`, `/review/reset`, `/metrics`, cache + maintenance
  + backup endpoints, `movies_router` run-history endpoints.
- Stacking: `CandidateScore.stack_id/stack_rank/stack_pos/stack_label/stack_size`
  (`marquee/pipeline/types.py`); auto-pick = `stack_rank==1 & stack_pos==1`
  (`find_auto_pick_candidate`); stacks assigned in `assign_stacks`
  (`marquee/pipeline/stacker.py`).
- The `official_family` *feature* already exists: `fetch_candidates` returns
  `primary_name` from `get_movie_primary_poster`, and
  `FeatureExtractor.extract_style_batch(..., primary_name=...)` scores CLIP
  cosine to it. Do not confuse that scoring feature with the new hard
  **official-pick** behavior (D8) — both exist side by side.

---

## 3. Architecture in three sentences

1. A **`PosterSubject`** value object (movie | series | season) becomes the single
   currency for "the thing a poster belongs to" — filename rendering, folder +
   path-translation source, cache paths, backup paths, DB artwork columns, and
   audit events all resolve through it.
2. **TV pipeline execution happens only in the batch engine**, generalized from
   `_BatchMovie` to asset items; every TV asset (one show poster, one season
   poster) produces a completely standard `PipelineRun` + run archive, so the
   entire downstream stack (run results endpoint, rescore, stacks, OCR labels,
   feedback) works unchanged.
3. The **taste stack is parameterized by namespace** (`movies` | `tv`): profile
   path, head path, training/negative/feedback dirs, artifact kinds — movie paths
   unchanged, TV added alongside.

---

## 4. Phase 0 — Schema + subject groundwork

### 4.1 New module `marquee/core/poster_subjects.py`

```python
MEDIA_TYPE_MOVIE = "movie"
MEDIA_TYPE_SERIES = "series"
MEDIA_TYPE_SEASON = "season"

@dataclass(frozen=True)
class PosterSubject:
    media_type: str
    movie: Movie | None = None
    series: Series | None = None
    season: Season | None = None   # when set, series must also be set

    @classmethod
    def from_movie(cls, movie: Movie) -> PosterSubject: ...
    @classmethod
    def from_series(cls, series: Series) -> PosterSubject: ...
    @classmethod
    def from_season(cls, season: Season, series: Series) -> PosterSubject: ...
```

Properties/methods (all pure; no DB access):

- `entity` → the row that carries `ArtworkMixin` (`movie` / `series` / `season`).
- `id` → `entity.id`.
- `title` → display title: movie title; series title; or
  `f"{series.title} - Season {season.season_number:02d}"` (specials:
  `Season 00`). Used for run work dirs and logs.
- `tmdb_id` → `movie.tmdb_id` for movies; `series.tmdb_id` for **both** TV kinds.
- `folder_raw` → `movie.folder_path` | `series.series_path` (both TV kinds).
- `path_source` → `"radarr"` for movies, `"sonarr"` for TV (feeds
  `safe_translate_and_validate`).
- `render_filename()` → move the logic of `poster_service.render_filename` here:
  - movie: current behavior verbatim (`MOVIE_POSTER_FORMAT`, `{movie_basename}`).
  - series: `settings.SERIES_POSTER_FORMAT` (no template vars).
  - season: `settings.SEASON_POSTER_FORMAT.format(season=season.season_number)`,
    wrapping format errors into `PathValidationError` like the movie path does.
  - all results pass through the existing `sanitize_poster_filename`.
- `cache_paths()` → `(jpg, meta_json)`:
  - movie: `poster_cache_path / "movies" / f"{tmdb_id}.jpg"` (**unchanged**).
  - series: `poster_cache_path / "tv" / f"{tmdb_id}.jpg"`.
  - season: `poster_cache_path / "tv" / f"{tmdb_id}-s{season_number:02d}.jpg"`.
  - Returns `None` when `tmdb_id` is `None` (mirror current movie behavior).
- `backup_file()` →
  - movie: honor `movie.poster_local_backup_path` else
    `settings.poster_backup_path / f"{movie.id}.jpg"` (**unchanged** — existing
    backup files keep working with no migration).
  - series: honor `poster_local_backup_path` else `poster_backup_path / f"series-{id}.jpg"`.
  - season: honor `poster_local_backup_path` else `poster_backup_path / f"season-{id}.jpg"`.
- `event_fk_kwargs()` → dict for constructing `ArtworkEvent`:
  `{"media_type": ..., "movie_id": ... or None, "series_id": ..., "season_id": ...}`.
- `run_fk_kwargs()` → same shape for `PipelineRun`.

Keep `poster_service.render_filename` as a one-line delegate
(`return PosterSubject.from_movie(movie).render_filename()`) if anything still
imports it; grep for callers (`sync_service._build_movie_poster_filename` is a
separate copy used at sync time — Phase 1 §5.5 consolidates it or leaves it, your
choice, but behavior must match `render_filename` for movies).

### 4.2 Model changes

`marquee/models/pipeline_run.py`:
- `movie_id` becomes nullable.
- Add `media_type: Mapped[str]` — `String(10)`, non-null, default `"movie"`,
  `server_default="movie"`, index it.
- Add `series_id: Mapped[int | None]` — FK `series.id`, `ondelete="CASCADE"`, index.
- Add `season_id: Mapped[int | None]` — FK `seasons.id`, `ondelete="CASCADE"`, index.
- Add a `CheckConstraint` named `ck_pipeline_runs_subject`:
  ```sql
  (media_type = 'movie'  AND movie_id  IS NOT NULL) OR
  (media_type = 'series' AND series_id IS NOT NULL) OR
  (media_type = 'season' AND season_id IS NOT NULL)
  ```

`marquee/models/artwork_event.py`: identical treatment
(`movie_id` nullable, + `media_type`/`series_id`/`season_id`,
check constraint `ck_artwork_events_subject`).

`marquee/models/series.py` — add columns (all nullable):
- `tagline: Text` — TMDB TV tagline (OCR gate).
- `director: String(500)` — first `created_by` name from TMDB (fills the same
  OCR-gate slot the movie director does; TV posters rarely carry creator credits
  but the classifier plumbing is shared).
- `production_companies_json: JSON` — deduped names from TMDB `networks` +
  `production_companies` (studio-branding tokens for the OCR gate).
- `show_text_profile_id: String(64)` — per-series override, show scope (D7).
- `season_text_profile_id: String(64)` — per-series override, season scope (D7).

`marquee/models/season.py` — add:
- `episode_count: Mapped[int]` — non-null, default 0, `server_default="0"`.
- `episode_file_count: Mapped[int]` — non-null, default 0, `server_default="0"`,
  **index this one** (it powers every eligibility query).

### 4.3 Alembic migration

One revision (`add tv poster subjects`), down_revision = current head. Use plain
`op.add_column` / `op.alter_column(nullable=True)` / `op.create_check_constraint`
/ `op.create_index` — production is PostgreSQL, no batch mode needed. Existing
rows are all movies, so `server_default="movie"` backfills `media_type`
correctly; no data migration needed. Verify with `alembic upgrade head --sql`.

### 4.4 Tests (Phase 0)

New `tests/test_poster_subjects.py`:
- filename rendering per kind incl. specials (`season00.jpg` once Phase 1 flips
  the default — until then assert against `settings.SEASON_POSTER_FORMAT`),
  `{movie_basename}` behavior parity with old `render_filename`,
  sanitization rejection cases (path separators, empty).
- cache/backup path shapes per kind; movie backup honors
  `poster_local_backup_path`.
- `PosterSubject.from_season` without series → raise `ValueError`.
- ORM smoke: create a `PipelineRun` with `media_type="season"`,
  `season_id=...`, `movie_id=None` via the test session (conftest's SQLite
  `create_all`) — insert succeeds; movie-shaped rows unchanged.

---

## 5. Phase 1 — Sync, eligibility, config defaults

### 5.1 Resolve `Series.tmdb_id`

In `_sync_series`, after the identity fields, when `series.tmdb_id is None` and
`self.tmdb` is set:
1. If `tvdb_id`: `await self.tmdb.find_by_external_id(str(tvdb_id), "tvdb_id")`
   → `result["tv_results"][0]["id"]` when non-empty.
2. Else if `imdb_id`: same with `"imdb_id"`.
3. Wrap in try/except, log a warning, leave NULL on failure (retries next sync).

Throttle like movie enrichment (a semaphore of 5 if you batch it; a straight
sequential call inside the per-series loop is also acceptable given library
sizes — pick the simpler and note it).

### 5.2 TV text-metadata enrichment

Mirror `_needs_tmdb_enrichment` / `_enrich_movies_from_tmdb` exactly:
- `_needs_tv_enrichment(series)` → `series.tmdb_id` set and
  (`director is None or production_companies_json is None`).
- New `TMDBClient.get_tv_details(tmdb_id) -> TVDetails` — a frozen dataclass
  mirroring `MovieDetails`: `director: str | None` (first `created_by[].name`),
  `production_companies: list[str]` (names from `networks` + `production_companies`,
  deduped, order-preserving), `tagline: str | None`. Single `GET /tv/{id}` — the
  payload contains all three; no credits call.
- Reset the three columns when `tmdb_id` changes, exactly like the movie path
  does on `previous_tmdb_id != movie.tmdb_id`.

### 5.3 Eligibility predicates — new module `marquee/core/tv_queries.py`

```python
def season_downloaded():
    """Season has at least one downloaded episode file."""
    return Season.episode_file_count > 0

def series_visible():
    """Series has at least one downloaded season (D3)."""
    return exists(
        select(Season.id).where(
            Season.series_id == Series.id, Season.episode_file_count > 0
        )
    )
```

Every TV route in later phases imports these two. Never inline the rule.

### 5.4 Season episode counts

In `_sync_seasons`:
- Change the skip to `if season_num < 0: continue` (**season 0 now syncs**, D2).
- From the Sonarr season dict: `stats = sdata.get("statistics") or {}`;
  set `season.episode_count = int(stats.get("episodeCount") or 0)` and
  `season.episode_file_count = int(stats.get("episodeFileCount") or 0)`.
- **Fallback:** in `_sync_series` after `_sync_episodes(series)` returns, if any
  season of the series has `episode_file_count == 0` but Episode rows with a
  non-null `episode_file_path` exist for that `(series_id, season_number)`,
  recompute both counts from Episode rows. (Covers Sonarr payloads without
  statistics; one grouped `SELECT count(*) ... GROUP BY season_number`.)

### 5.5 Config default changes + settings surface

`marquee/config.py`:
- `SERIES_POSTER_FORMAT` default → `"show.jpg"` (D1).
- `SEASON_POSTER_FORMAT` default → `"season{season:02d}.jpg"` (D1). Update both
  descriptions.
- Add `"SERIES_POSTER_FORMAT"` and `"SEASON_POSTER_FORMAT"` to
  `SETTINGS_OVERRIDE_KEYS`.

`marquee/api/routes/settings.py`:
- The posters update model gains `series_poster_format` and
  `season_poster_format` optional fields.
- Add `_validate_series_poster_format` (must sanitize to a bare `.jpg` name; no
  template variables allowed) and `_validate_season_poster_format` (must contain
  a `{season` placeholder; test-render with `season=1` **and** `season=0` and
  sanitize both results). Mirror `_validate_movie_poster_format`'s error style.
- Wire both into `app_updates` exactly like `MOVIE_POSTER_FORMAT`.

Note: `sync_service._build_movie_poster_filename` and `_resolve_poster_path`
already read these settings — after the default change, sync will look for
`show.jpg` / `seasonNN.jpg` automatically. Verify `_resolve_poster_path`'s season
branch uses `.format(season=...)` compatible with the new default (it does —
`settings.SEASON_POSTER_FORMAT.format(season=entity.season_number)`).

### 5.6 Tests (Phase 1)

Extend/add:
- Sonarr sync fixture with a series payload containing seasons 0..3 with
  statistics: assert Season rows incl. season 0, counts stored, fallback path
  (statistics absent → derived from Episode rows).
- tmdb_id resolution: fake TMDB client returning `tv_results`; assert set once,
  not re-queried when already set; failure leaves NULL without failing sync.
- TV enrichment: director/companies/tagline populated; reset on tmdb_id change.
- `tv_queries` predicates: series with only undownloaded seasons is invisible;
  partially downloaded shows expose only downloaded seasons.
- Settings route round-trip for the two new format fields incl. validation
  rejections (season format without `{season`, names with `/`).

---

## 6. Phase 2 — PosterService, heal, poster jobs

### 6.1 `PosterService` generalization

Rework `marquee/core/poster_service.py` so `deploy` and `restore` take a
`PosterSubject` instead of a `Movie`:

```python
async def deploy(self, db, subject: PosterSubject, source_file: Path, *, source=..., ai_selected=..., user_approved=..., poster_source=..., poster_source_url=...) -> DeployResult
async def restore(self, db, subject: PosterSubject, *, new_folder: str | None = None, source: str = "webhook") -> RestoreResult
```

Line-by-line, the logic is the current one with these substitutions:
- `render_filename(movie)` → `subject.render_filename()`.
- `safe_translate_and_validate(movie.folder_path, source="radarr")` →
  `safe_translate_and_validate(subject.folder_raw, source=subject.path_source)`.
- All `movie.poster_*` writes → `subject.entity.poster_*`.
- `cache_paths(movie.tmdb_id)` → `subject.cache_paths()`.
- `backup_path(movie)` → `subject.backup_file()`.
- `_log_event(db, movie.id, ...)` → `_log_event(db, subject, ...)` constructing
  `ArtworkEvent(**subject.event_fk_kwargs(), action=..., source=..., detail=...)`.
- `restore`'s `movie.folder_path = folder_raw` on finalize → only for movies
  (`new_folder` is a Radarr-upgrade concept); for TV subjects ignore `new_folder`.
- Log lines use `subject.title`.

**Update every call site** (grep `poster_service.deploy` / `poster_service.restore`):
`feedback.py::_deploy_pick`, `heal.py`, `webhooks.py::_restore_after_upgrade`,
and any others — movies wrap with `PosterSubject.from_movie(movie)`.

Keep module-level `cache_paths(tmdb_id)` working for movies (the library
delete-poster route imports it) — it can stay as the movie-only helper.

### 6.2 Heal scan

`marquee/core/heal.py::heal_scan`:
- Keep the movie walk as-is, then add two more walks in the same session:
  - `Series` with `poster_path` set (+ grace window on `poster_deployed_at`).
  - `Season` with `poster_path` set — join/fetch its `Series` (one query with
    `select(Season, Series).join(Series, Series.id == Season.series_id)`).
- Each miss calls `poster_service.restore(db, subject, source="heal")`.
- Result dict: keep top-level `{"checked": n, "restored": n, "failed": n}`
  (summed — the existing UI reads these) and add
  `"by_type": {"movie": {...}, "series": {...}, "season": {...}}`.
- `latest_heal_summary` passes `by_type` through when present.

### 6.3 Poster maintenance jobs (`builtin_handlers.py`)

For each handler below, open it, and extend its movie query to also cover
`Series` and `Season` using the same per-entity pattern; keep existing result
keys and add per-type counts where a count is returned:
- `poster_rescan` — re-stat deployed posters / detect drift: apply identical
  logic per subject table (season needs its series for path resolution).
- `poster_backup_all` — copy each deployed poster to `subject.backup_file()`,
  set `poster_local_backup_path`, same error tolerance as movies.
- `poster_maintenance` — read what it does for movies (cache/backup
  reconciliation) and mirror per subject type; `dry_run`/`force` semantics
  unchanged.
- `poster_deploy_reset` — payload gains `{"library": "movies" | "tv" | "all"}`
  (default `"movies"` — **existing movie endpoint behavior unchanged**). For
  `tv`: delete deployed files and reset `poster_*` columns on Series + Season.
- `pipeline_cache_clear` / `marquee/core/pipeline_cache.py` — ensure
  `data/cache/posters/tv/` is included in `cache_sizes()` and the clear walk
  (follow how `posters/movies` is handled).
- `_backup_stats()` in `api/routes/pipeline.py` counts `*.jpg` in the backup dir
  — the new `series-*.jpg`/`season-*.jpg` files match automatically; no change,
  but confirm.

### 6.4 Tests (Phase 2)

- `tests/test_poster_service.py`: parameterize the deploy/restore suites over
  the three subject kinds (tmp dirs as series roots; season deploys land
  `seasonNN.jpg` in the series root; cache files under `posters/tv/`;
  ArtworkEvent rows carry the right `media_type` + FK).
- `tests/test_heal.py`: add series + season cases (missing file → restored from
  backup/cache; `by_type` counts).
- Deploy-reset with `library="tv"` resets only TV rows.

---

## 7. Phase 3 — Scoped text profiles + season OCR category

### 7.1 Store rework (`marquee/core/text_profiles.py`)

- `SCOPES = ("movie", "show", "season")`.
- File format v2 (same path `data/text_profiles.json`):
  ```json
  {"version": 2,
   "scopes": {
     "movie":  {"_default_profile": "...", "profiles": [...]},
     "show":   {"_default_profile": "...", "profiles": [...]},
     "season": {"_default_profile": "...", "profiles": [...]}}}
  ```
  `_read_raw` migration: a legacy file (no `"scopes"` key) is interpreted as the
  `movie` scope's content; first save rewrites v2. No separate migration script.
- `TextProfileSettings` gains `allow_season: bool = False` (validated in
  `settings_from_dict` like the other booleans).
- Built-ins per scope (`_builtin_profiles(scope)`):
  - `movie`: `title_only` (fallback/default), `textless` — **unchanged**.
  - `show`: `title_only` (default), `textless`.
  - `season`: `textless`, `title_only`, and `title_and_season` =
    `TextProfileSettings(mode="custom", allow_title=True, allow_season=True,
    require_title=False)`. Season scope's fallback/default id =
    `"title_and_season"` (D6). `require_title=False` because legitimate season
    art is sometimes textless-with-number; the title is *allowed*, not required.
- `gate_payload()`: built-ins whose mode is `title_only`/`textless` keep
  returning `{"mode": mode}`; the `title_and_season` built-in (mode `custom`)
  returns the full `asdict(self.settings)` like custom profiles do.
- Every public function gains a leading `scope: str` argument
  (`load_profiles(scope)`, `get_default_profile_id(scope)`,
  `get_active_profile(scope, override_id=None)`, `create_profile(scope, ...)`,
  `update_profile(scope, ...)`, `delete_profile(scope, ...)`,
  `set_default_profile(scope, ...)`). Validate scope, raise `TextProfileError`
  on unknown. Name/id uniqueness is **per scope**.

### 7.2 `OcrGateContext`

- Add fields: `scope: str = "movie"`, `season_number: int | None = None`.
- `from_movie` — unchanged except it now calls
  `get_active_profile("movie", movie.text_profile_id)`.
- New `from_series(series)` → scope `"show"`; director/studios/tagline from the
  new Series columns; profile
  `get_active_profile("show", series.show_text_profile_id)`.
- New `from_season(series, season)` → scope `"season"`,
  `season_number=season.season_number`; same metadata source; profile
  `get_active_profile("season", series.season_text_profile_id)`.
- `default()` keeps movie scope.

### 7.3 OCR gate (`marquee/pipeline/ocr_filter.py`)

- The allow-map construction (~line 638) gains
  `"season": bool(prof.get("allow_season", pipeline_settings.OCR_ALLOW_SEASON))`.
- Add `OCR_ALLOW_SEASON: bool = False` to `PipelineSettings` (follows the
  existing `OCR_ALLOW_*` pattern).
- `classify_text_box` gains a `season` category, checked **after** title
  matching but **before** the tagline/generic fallbacks. A box is `season` when
  its normalized text fully matches one of the patterns in §7.4.
- Thread whatever context is needed so the classifier can see the patterns —
  follow how director tokens travel into the worker
  (`_init_worker(title_tokens, director_tokens)`): the season patterns are
  static regexes (module-level compiled list), so no per-task state is needed;
  only the allow-map entry varies per task.

### 7.4 Season designator patterns (D12)

Compile once, case-insensitive, matched against the whole normalized box text
(anchored `^...$`, whitespace collapsed):

```
season\s*\d{1,3}
s\d{1,2}
\d{1,2}(st|nd|rd|th)\s+season
(first|second|third|fourth|fifth|sixth|seventh|eighth|ninth|tenth)\s+season
season\s+(one|two|three|four|five|six|seven|eight|nine|ten)
(the\s+)?(final|last)\s+season
part\s+(\d{1,2}|one|two|three|four|five|six)
vol(ume)?\.?\s*\d{1,2}
book\s+\w+
chapter\s+\w+
[ivxlc]{1,4}          # roman numeral, box must be exactly the numeral
\d{1,2}               # bare season number box (common on key art)
```

The bare-number and roman patterns are deliberately loose — they are only
consulted when `allow_season` is true (season scope), so movie/show behavior is
untouched.

### 7.5 Routes (`marquee/api/routes/text_profiles.py`)

Reshape (frontend is updated in the companion plan; no legacy compat needed):
- `GET  /api/text-profiles` → `{"scopes": {scope: {"profiles": [...], "default_id": ...}}}` — one fetch for the whole panel.
- `POST /api/text-profiles/{scope}` — create.
- `PUT  /api/text-profiles/{scope}/default/{profile_id}` — set default.
- `PUT  /api/text-profiles/{scope}/{profile_id}` — update.
- `DELETE /api/text-profiles/{scope}/{profile_id}` — delete.
- Per-movie override endpoints unchanged
  (`GET/PUT /api/text-profiles/movie/{movie_id}` — ensure `"movie"` never
  collides with a scope path segment: register the movie routes first or use
  explicit path ordering; add a test).
- New per-series override endpoints:
  - `GET /api/text-profiles/series/{series_id}` →
    `{"show_profile_id": ..., "season_profile_id": ..., "effective_show": {...}, "effective_season": {...}}`
  - `PUT /api/text-profiles/series/{series_id}` — body
    `{"show_profile_id": str | null, "season_profile_id": str | null}`
    (null clears; validate ids exist in their scope).

### 7.6 Tests (Phase 3)

- v1→v2 file migration; per-scope defaults; per-scope name uniqueness; built-in
  immutability per scope; season default = `title_and_season`.
- `gate_payload` shapes: pinned-mode built-ins vs `title_and_season` full dict.
- Classifier: each pattern row classifies as `season`; "SEASON 3" rejected under
  `title_only` season profile but accepted under `title_and_season`; movie scope
  unaffected by a bare "3" box.
- Route tests incl. series override CRUD and the `/movie/` vs `/{scope}/` path
  non-collision.

---

## 8. Phase 4 — Taste namespaces

### 8.1 Config (`marquee/core/pipeline_config.py`)

- `TRAINING_DATA_DIR` default → `_DATA_DIR / "taste_seeding" / "movies"` (D5).
- New: `TV_TRAINING_SHOW_DIR` default `_DATA_DIR / "taste_seeding" / "shows"`;
  `TV_TRAINING_SEASON_DIR` default `_DATA_DIR / "taste_seeding" / "seasons"`.
- New: `TASTE_PROFILE_TV_PATH` default `_DATA_ML_DIR / "taste_profile.tv.clip-vit-b-32.npz"`
  and `LEARNED_HEAD_TV_PATH` default `_DATA_ML_DIR / "learned_head.tv.clip-vit-b-32.npz"`,
  with the same `AI_MODEL`-suffix rewrite in `model_post_init` the movie paths get.
- New: `NEGATIVE_DATA_TV_DIR` default `_DATA_TRAINING_DIR / "negative_tv"`.
- Ensure the three seeding dirs are created at startup wherever the existing
  data dirs are ensured (follow how `data/training` is handled; add `.gitkeep`s).

### 8.2 New module `marquee/ml/namespaces.py`

```python
@dataclass(frozen=True)
class TasteNamespace:
    library: str                      # "movies" | "tv"
    profile_path: Path
    head_path: Path
    training_dirs: dict[str, Path]    # asset_kind -> dir
    negative_dir: Path
    feedback_dir: Path                # labels/events root for this library
    map_history_dir: Path
    artifact_kind_profile: str        # "taste_profile" | "taste_profile_tv"
    artifact_kind_head: str           # "learned_head"  | "learned_head_tv"

def get_namespace(library: str) -> TasteNamespace   # raises on unknown
MOVIES: TasteNamespace   # existing paths; training_dirs={"movie": TRAINING_DATA_DIR}
TV: TasteNamespace       # training_dirs={"show": ..., "season": ...}
```

`feedback_dir`: movies = the current `feedback_store` root (read
`feedback_store.labels_path()` to find it — keep it byte-identical); tv = a
`tv/` sibling under the same parent.

### 8.3 Parameterize the consumers

Each of these currently reads a global path; give each an optional
namespace/path parameter **defaulting to current movie behavior**, then thread
the namespace from callers:
- `taste_trainer.rebuild_profile(...)` → accepts `namespace: TasteNamespace`.
  Scans every `(kind, dir)` in `training_dirs`, tags each exemplar's metadata
  with `asset_kind=kind` (movies: `"movie"`). Negative dir from the namespace.
  Writes to `namespace.profile_path`.
- `taste_store` / profile loading — path already injectable
  (`NumpyTasteStore(path)`); no logic change, just callers.
- `profile_updater.add_exemplar(...)` → gains `namespace` + `asset_kind` kwargs
  (movie default) — writes into the namespace's profile npz + exemplar dir
  convention (read `_profile_path()`/`_load_profile()` and parameterize).
- `head_trainer.train_from_labels(...)` → gains `namespace`; reads labels from
  `namespace.feedback_dir`, writes `namespace.head_path`.
- `feedback_store` — functions gain an optional namespace/dir argument
  (default = movies). TV labels/events live under the TV dir with identical
  file formats.
- `learned_head.LogisticHead.load()` → `load(path: Path | None = None)`;
  `scorer.select_scorer(config, namespace=MOVIES)` resolves the head path via
  the namespace and logs which library it scored for.
- `taste_map.build_map(...)` → gains `namespace`; for `tv`, filter exemplars to
  `asset_kind == "show"` before clustering/rendering (D4); history under
  `namespace.map_history_dir`.
- `artifact_registry` — kinds come from the namespace
  (`register_active_artifact(db, ns.artifact_kind_profile, ...)`); extend the
  kind→stem mapping (`taste_profiles`/`learned_heads` stems) to accept the two
  new kinds with `_tv`-suffixed stems.
- `FeatureExtractor` — add `set_taste_namespace(ns: TasteNamespace)` which swaps
  the taste store + calibration arrays (they live in the profile npz) **without
  reloading any ONNX session**. `run_manager._ensure_extractor()` keeps
  defaulting to movies; the TV batch handler calls
  `extractor.set_taste_namespace(TV)` before feature stages, and the movie batch
  handler explicitly sets movies (cheap idempotent call) so an interleaved
  worker never scores against the wrong profile.

### 8.4 Jobs + routes

- `taste_rebuild`, `taste_map`, `learned_head_train` job handlers: payload gains
  `"library": "movies" | "tv"` (default `"movies"`); resolve the namespace and
  pass it down. `source="library"` for tv gathers deployed **show + season**
  posters (kind-tagged) — mirror `_gather_library_posters`.
- `marquee/api/routes/taste.py`: every endpoint gains a `library` query param
  (default `"movies"`, validated): `/status`, `/retrain`, `/retrain/cancel`,
  `/head/retrain`, `/profiles`, `/profiles/{id}`, `/profiles/{id}/activate`, the
  map endpoints, and the exemplar-stats helpers. Status payload includes
  `"library"` and, for tv, per-kind exemplar counts
  (`{"show": n, "season": n}`).

### 8.5 Tests (Phase 4)

- Namespace resolution; movie namespace paths equal the previous literals
  (regression pin).
- `rebuild_profile` over a fixture seeding tree tags `asset_kind` per subfolder
  (use tiny stub embeddings — follow existing trainer tests' approach to
  skipping real CLIP).
- `select_scorer` with a TV namespace and no TV head → weighted scorer (no
  crash); with a fake TV head file → learned.
- Taste route `library` param round-trips; unknown library → 400.
- Movie taste endpoints with no param behave exactly as before (snapshot a
  response shape).

---

## 9. Phase 5 — Batch engine generalization + official pick

### 9.1 TMDB client additions

- `get_tv_primary_poster(tmdb_id) -> str | None` — `GET /tv/{id}` →
  `poster_path`; normalize to the bare filename exactly the way
  `get_movie_primary_poster` does (read it and mirror, including error
  handling).
- `get_season_primary_poster(tmdb_id, season_number) -> str | None` —
  `GET /tv/{id}/season/{n}` → `poster_path`, same normalization.

### 9.2 Asset model

In `batch_runner.py`:

```python
@dataclass(frozen=True)
class AssetSpec:
    media_type: str            # movie | series | season
    subject_id: int            # movie.id / series.id / season.id
    title: str                 # display title (PosterSubject.title convention)
    tmdb_id: int | None        # movie tmdb or series tmdb
    series_id: int | None = None
    season_id: int | None = None
    season_number: int | None = None
```

- Generalize `_BatchMovie` → rename to `_BatchItem` carrying an `asset: AssetSpec`
  (keep a module alias `_BatchMovie = _BatchItem` if any test imports the old
  name — check `tests/test_batch_runner.py` and update imports there instead if
  trivial).
- New public entry:

```python
async def run_batch_assets(*, job_id, assets: list[AssetSpec], tmdb, extractor,
                           taste_namespace, progress=None, should_cancel=None,
                           ocr_meta: dict[tuple[str, int], OcrGateContext] | None = None) -> dict
```

  `ocr_meta` keyed by `(media_type, subject_id)`.
- The existing `run_batch(job_id, movies=[(id, title, tmdb_id)], ...)` becomes a
  thin adapter that builds movie `AssetSpec`s and calls `run_batch_assets` with
  the movies namespace — **its signature and observable behavior must not
  change** (`test_batch_runner.py` is the guard).

Inside the shared stages, the only kind-dependent points are:
1. **Fetch**: movie → existing `fetch_candidates(tmdb, ...)` path;
   series → `get_tv_images(tmdb_id)` + `get_tv_primary_poster`;
   season → `get_season_images(tmdb_id, season_number)` +
   `get_season_primary_poster`. Everything downstream consumes the same
   `PosterCandidate` list + `primary_name` string.
2. **OCR title tokens/text**: for TV kinds, tokens derive from the **series
   title** (the same tokenization the movie title goes through — find where the
   movie title becomes `title_tokens`/`title_text` for OCR tasks and feed the
   series title for both TV kinds).
3. **Work dir**: `settings.runs_work_path / _sanitise_filename(asset.title)` —
   the `" - Season NN"` suffix in the display title keeps season dirs distinct
   from the show dir.
4. **Persistence** (`_persist_running` + finalize): construct `PipelineRun` with
   the subject columns (`media_type`, `movie_id`/`series_id`/`season_id`) from
   the asset. Movie assets keep exactly today's row shape.
5. **Scoring**: call `extractor.set_taste_namespace(taste_namespace)` once
   before the style-feature stage, and `select_scorer(pipeline_settings,
   namespace=taste_namespace)` for ranking.

Run archives need two additive keys: `"media_type"` and `"subject"`
(`{"series_id": ..., "season_id": ..., "season_number": ..., "title": ...}`)
so `build_results_payload` consumers can label TV runs. Movie archives gain
`"media_type": "movie"` — additive, harmless.

### 9.3 Official pick (D8)

`pipeline_config.py` additions:
```python
OFFICIAL_PICK_MOVIE: bool = False
OFFICIAL_PICK_SHOW: bool = False
OFFICIAL_PICK_SEASON: bool = True
OFFICIAL_PICK_FALLBACK: str = "ranked"   # "ranked" | "largest_stack" (validate)
```

New helper in `batch_runner.py` (unit-testable, pure):

```python
def apply_official_pick(ranked: list[CandidateScore], primary_name: str | None,
                        *, enabled: bool, fallback: str) -> dict:
    """Post-stacking promotion. Returns the official_pick summary dict."""
```

Behavior:
- If not enabled or `primary_name` is None or `ranked` empty → return
  `{"enabled": enabled, "primary_name": primary_name, "applied": None}`.
- Find the ranked record whose `orig_filename == primary_name`.
  - Found: if its `stack_rank != 1`, **promote its whole stack to rank 1** and
    shift the stacks that were ahead down by one; recompute every affected
    member's `stack_label` (labels are `f"{stack_rank}{letter(stack_pos)}"` —
    read `assign_stacks`/`stack_label` construction and reuse its labeling
    helper). `applied = "primary_stack"`.
  - Not found (primary gated out or absent) and `fallback == "largest_stack"`:
    promote the stack with the largest `stack_size` (ties → keep current
    order). `applied = "largest_stack"`, `fallback_used = True`.
  - Otherwise `applied = None, fallback_used = True`.
- Call it per item **after** `assign_stacks` and **before** the archive/output
  are written, resolving `enabled` from the asset kind's config flag. Because
  `find_auto_pick_candidate` and `auto_pick_filename` derive from
  `stack_rank==1 & stack_pos==1`, promotion automatically changes the
  auto-pick, the Review thumbnail, and the deploy target — no other code paths
  change.
- Store the returned summary in the run archive as `"official_pick"` and pass
  it through `build_results_payload` (additive key on the payload root).
- Note: stacking can be disabled (`STACK_ENABLED=False` → no stack metadata).
  In that case skip promotion entirely and record
  `{"applied": None, "reason": "stacking_disabled"}`.

### 9.4 Zero-candidate seasons (D9)

Whatever terminal status the batch engine already assigns a movie whose ranked
list is empty (inspect the finalize path — `flagged_manual` is the expected
status), verify a TV season asset takes the same path, and make sure the TV
review-queue query (§10.5) includes `flagged_manual` runs (the movie one already
does via `_REVIEW_QUEUE_STATUSES`).

### 9.5 Job handler

New handler in `builtin_handlers.py`:

```python
@register("poster_pipeline_tv_batch")
async def poster_pipeline_tv_batch(job: Job) -> dict[str, Any]:
```

Mirror `poster_pipeline_batch` exactly, with:
- payload `{"assets": [{"media_type": "series"|"season", "series_id": int,
  "season_id": int | None}], "scope": str}`;
- loads Series (+Season) rows, drops unknown ids silently, builds `AssetSpec`s
  (season assets need `series.tmdb_id` + `season.season_number`) and
  `OcrGateContext.from_series` / `.from_season` per asset;
- skips assets whose series has no `tmdb_id` (count them in the summary as
  `"skipped_no_tmdb"`);
- `extractor.set_taste_namespace(get_namespace("tv"))`;
- calls `run_batch_assets(...)`.

Also update the **movie** `poster_pipeline_batch` handler to pass the movies
namespace explicitly (see §8.3 — idempotent, keeps interleaved workers honest).

### 9.6 Tests (Phase 5)

- `apply_official_pick` unit tests: promotion from rank 3 → 1 with correct
  relabeling; already-rank-1 no-op; primary gated + `largest_stack` fallback;
  primary gated + `ranked` fallback; stacking disabled.
- `run_batch_assets` with a stub TMDB client (subclass/fake with canned
  images + primary): one series + two seasons; assert per-asset PipelineRun
  rows (media_type/FKs), distinct work dirs, series-title OCR tokens, archive
  `media_type`/`subject`/`official_pick` keys. Follow `test_batch_runner.py`'s
  existing stubbing strategy (it already fakes downloads/extractors).
- `run_batch` (movie adapter) — existing tests stay green untouched; add one
  assertion that movie archives now carry `"media_type": "movie"`.

---

## 10. Phase 6 — TV pipeline API + feedback generalization

New router file `marquee/api/routes/pipeline_tv.py`, prefix `/api/pipeline/tv`,
registered in `main.py` **before** the generic movie pipeline router if there is
any path ambiguity (there shouldn't be — different prefixes). A shared
`series_router = APIRouter(prefix="/api/series")` in the same file hosts the
per-series history endpoints.

Throughout, import `series_visible()` / `season_downloaded()` from
`tv_queries` — never inline eligibility.

### 10.1 Movie-side regression guard (do this first)

In `api/routes/pipeline.py`, add `PipelineRun.media_type == "movie"` to
`_review_queue_latest()`, `_latest_run_per_movie()`, `/metrics`' run query, and
`_repair_stale_batch_pipeline_runs` joins **do not** need it (batch_id join is
type-agnostic and repair is correct for TV too — leave repair shared).
`reset_review_queue` and `approve_review_queue_auto` also get the movie guard.
Add a test: a TV run row in the DB never appears in any movie endpoint.

### 10.2 `GET /summary`

Return (all filtered by eligibility):
```json
{"shows_total": n, "shows_with_show_poster": n, "shows_missing_show_poster": n,
 "seasons_total": n, "seasons_with_poster": n, "seasons_missing_poster": n,
 "shows_fully_covered": n,            // show poster + every downloaded season
 "shows_in_review": n, "running_jobs": [...],   // jobs of type poster_pipeline_tv_batch
 "last_heal": {...}, "heal_schedule": {...},    // same sources as the movie summary
 "backups": {...}}                              // shared _backup_stats()
```
Reuse `latest_heal_summary` + the `poster-heal` JobSchedule row (heal is one
system, D10). Call the stale-run repair helper first, like the movie summary.

### 10.3 `GET /run-queue`

One row per **visible** series that has at least one missing asset:
```json
{"items": [{
   "series": {"id":..., "title":..., "year":..., "tmdb_id":..., "poster_url": ...},
   "show_poster_missing": true,
   "missing_seasons": [{"season_id":..., "number":..., "episode_file_count":...}],
   "assets_to_run": [{"media_type":"series"} , {"media_type":"season","season_id":...,"number":...}],
   "no_tmdb": false }],
 "total": n}
```
`no_tmdb: true` rows are shown but not runnable (frontend disables Run) — a
series without `tmdb_id` after sync is surfaced, not hidden.

### 10.4 Run triggers

- `POST /batch` — body `{"scope": "missing" | "all" | "selected",
  "series_ids": [int] | null}`. Expand to assets server-side:
  - `missing`: for each visible series with `tmdb_id`: series asset if
    `poster_path IS NULL`; season asset per downloaded season with
    `poster_path IS NULL`.
  - `all`: every asset (show + all downloaded seasons) for visible series with
    `tmdb_id`.
  - `selected`: same expansion as `missing`… **no** — `selected` = the `all`
    expansion restricted to `series_ids` (the operator explicitly picked these
    shows; re-running a show should refresh everything). Note this in the
    docstring.
  - 404 when no assets; enforce `len(assets) <= pipeline_settings.PIPELINE_BATCH_MAX_MOVIES`
    (the knob now caps *assets*; update its comment/docstring only).
  - Job: `job_type="poster_pipeline_tv_batch"`, priority 80,
    `resources={"gpu": 1, "network_external": 1}`,
    `subject_type="pipeline_tv_batch"`, `subject_id=scope`, `max_attempts=1`,
    `idempotency_key=f"poster-tv-batch:{scope}:{int(time.time() // 30)}"`.
    Response = `job_summary(job)` + `{"asset_count": n, "series_count": m}`.
- `POST /series/{series_id}/run` — body
  `{"include": "all_missing" | "show" | "seasons", "season_ids": [int] | null}`
  (default `all_missing`). Rate-limit key `f"pipeline:tv:{series_id}"` with
  `settings.RATE_PIPELINE_RUN_SECONDS` (mirror the movie run's
  `enforce_rate_limit` + `record` pattern). Validates: series visible, has
  tmdb_id; requested seasons downloaded. Enqueues the same batch job type with
  that show's assets.

### 10.5 Review queue

- `GET /review-queue?page=&page_size=` — latest unreviewed run per **subject**
  (media_type in `('series','season')`, statuses `completed|flagged_manual`,
  `feedback_event_id IS NULL`), then grouped by series:
```json
{"total_series": n, "items": [{
   "series": {...},
   "show_run": {run summary + auto_pick_poster_url} | null,
   "season_runs": [{"season_number":..., "season_id":..., "run": {...},
                    "auto_pick_poster_url":..., "flagged_no_candidates": bool,
                    "official_pick": {...} | null}],
   "seasons_only": true|false,          // no show_run in queue
   "display_poster_url": ...}]}         // show_run auto-pick, else deployed show poster
```
  Run summary dicts mirror the movie review-queue's `run` shape (run_id, status,
  timestamps, scorer_name, counts, reviewed=false). Individual run detail stays
  on the existing shared `GET /api/pipeline/runs/{run_id}` — it has no movie
  dependency; verify and add a TV run test.
- `POST /review-queue/approve-auto` — body `{"deploy": bool, "series_id": int | null}`
  (null = whole TV queue). Reuses `apply_feedback_request` per run, mirroring
  the movie endpoint's error accounting.
- `POST /review/reset` — TV mirror of the movie reset: mark TV queue runs
  reviewed with a reset key + clear Series/Season poster columns for affected
  subjects (write the ArtworkEvents with the subject FKs).

### 10.6 Feedback generalization (`api/routes/feedback.py`)

- `_load_feedback_run` returns the run + archive + a `PosterSubject` (resolve
  via `run.media_type`: load Movie, or Series, or Season+Series). Movie-shaped
  callers keep working (subject.movie).
- `_deploy_pick(db, subject, pick, run)` — deploy via
  `poster_service.deploy(db, subject, ...)` (§6.1 already changed the
  signature; this phase fixes the caller properly for TV).
- `_add_to_profile` — gains the namespace + `asset_kind` ("movie" | "show" |
  "season") resolved from `run.media_type`, delegating to the parameterized
  `profile_updater` (§8.3). Exemplar title = `subject.title`.
- Label records (`_label_record`, `_ranking_record_v4`): add `"media_type"` and
  `"library"` fields; write via `feedback_store` pointed at the run's
  namespace `feedback_dir`. The `movie` key fields keep their names for movie
  runs (do not rename existing fields — v2/v3 label parsers depend on them);
  for TV runs fill the analogous identity fields (`title`, subject ids).
- `_copy_negative` → namespace-aware negative dir.
- Undo endpoint: resolve the namespace from the run referenced by the event and
  remove from the right store.
- `_maybe_retrain_head()` → namespace-aware (`HEAD_AUTO_RETRAIN` is globally
  False; just pass the namespace through).

### 10.7 `POST /seasons/{season_id}/use-show-poster` (D9)

Preconditions: season exists + downloaded; its series has a deployed show poster
(`poster_path` set). Source bytes resolution order: series `backup_file()` →
series cache file → the deployed `poster_path` file itself. Then
`poster_service.deploy(db, season_subject, source_file, source="show_poster_fallback",
ai_selected=False, user_approved=True, poster_source=series.poster_source,
poster_source_url=series.poster_source_url)`. Mark the season's latest
review-queue run (if any) reviewed with
`feedback_event_id = f"show_poster_fallback_{int(time.time())}"`. Return the
DeployResult summary. 409 when the series has no deployed poster.

### 10.8 History + metrics

- `GET /api/series/{series_id}/runs` — all runs where `series_id` matches
  (show runs + season runs; include `media_type` + `season_number` per row),
  newest first. Mirror `list_movie_runs`' shape.
- `GET /api/series/{series_id}/artwork-events` — events where the subject FKs
  match this series or its seasons.
- `GET /api/pipeline/tv/metrics` — the movie `/metrics` aggregation restricted
  to `media_type != 'movie'`. Refactor the aggregation body in
  `pipeline.py::pipeline_metrics` into a shared helper taking a run query
  filter; both endpoints call it.

### 10.9 Tests (Phase 6)

`tests/test_pipeline_tv_api.py` (mirror `test_poster_pipeline_backend.py`'s
fixtures): summary counts under partial libraries; run-queue asset expansion
(missing show poster only / seasons only / both / specials / no-tmdb rows);
batch scope expansion + cap + idempotency key; review-queue grouping incl.
`seasons_only` and `flagged_no_candidates`; approve-auto scoped to one series;
use-show-poster happy path + 409; feedback approve on a season run deploys to
the series root with `seasonNN.jpg` and adds a kind-tagged TV exemplar; movie
regression guard (§10.1).

---

## 11. Phase 7 — Library API

`marquee/api/routes/library.py`:

- `GET /api/library/series` — add eligibility filter (`series_visible()`),
  pagination unchanged, and enrich items:
  ```json
  {"id":..., "title":..., "year":..., "tmdb_id":...,
   "poster": {"has_poster": bool, "ai_selected": bool, "user_approved": bool,
              "deployed_at": ...},
   "downloaded_seasons": n, "seasons_with_poster": n,
   "season_poster_status": "complete" | "partial" | "missing",
   "season_count": n}
  ```
  `season_poster_status` computed over **downloaded** seasons only (D3).
  Compute the rollups with one grouped query over Season, not N+1.
- `GET /api/library/series/{id}` — 404 when not visible; adds the enriched
  fields above plus `seasons`: downloaded seasons only (incl. specials),
  each `{"id":..., "season_number":..., "episode_count":...,
  "episode_file_count":..., "poster": {...}}`, plus
  `{"show_text_profile_id":..., "season_text_profile_id":...}`.
- `GET /api/library/series/{id}/poster` and
  `GET /api/library/seasons/{season_id}/poster` — `FileResponse` of the
  deployed `poster_path`, 404 when unset/missing (mirror `get_movie_poster`).
- `DELETE /api/library/series/{id}/poster` and
  `DELETE /api/library/seasons/{season_id}/poster` — mirror
  `delete_movie_poster` (delete file best-effort, reset all `poster_*` columns,
  ArtworkEvent with subject FKs, keep cache files).
- `GET /api/library/series/{id}/seasons` (existing) — add
  `downloaded_only: bool = True` query param defaulting to the eligibility
  rule; include counts + poster summary per row.

Tests: extend `test_frontend_gap_routes.py` patterns — visibility filtering,
rollup correctness (0/partial/complete), specials included when downloaded,
poster file endpoints, delete endpoints.

---

## 12. Data & directory summary (new/changed)

```
data/taste_seeding/movies/            # movie exemplar seeding (operator-populated)
data/taste_seeding/shows/             # TV show poster exemplars
data/taste_seeding/seasons/           # TV season poster exemplars
data/training/negative_tv/            # TV negative exemplars
data/feedback/tv/                     # TV labels/events (same file formats)
data/ml/taste_profile.tv.<model>.npz  # TV taste profile
data/ml/learned_head.tv.<model>.npz   # TV learned head
data/cache/posters/tv/{tmdb}.jpg              # deployed show poster cache
data/cache/posters/tv/{tmdb}-s{NN}.jpg        # deployed season poster cache
data/backups/posters/series-{id}.jpg          # show poster local backups
data/backups/posters/season-{id}.jpg          # season poster local backups
data/text_profiles.json               # v2 scoped format (auto-migrated)
```

---

## 13. Cross-cutting invariants & traps

1. **`media_type='movie'` guard** on every pre-existing movie query that touches
   `pipeline_runs` or `artwork_events` (§10.1). This is the highest-risk
   regression in the whole project — test it explicitly.
2. **Dual engines:** movies still have two engines (`runner` + `batch_runner`).
   You are only generalizing `batch_runner`. If a shared leaf helper in
   `runner.py` needs a parameter, default it so the movie single-run path is
   unchanged.
3. **NaN boundary:** TV endpoints echoing archived features reuse the same
   sanitization as movie endpoints (§0.8).
4. **SQLite tests:** conftest builds the schema via `create_all` — every new
   column/constraint must be SQLite-compatible (`postgresql_where` indexes are
   fine; avoid PG-only column types).
5. **OCR worker pickling:** `OcrGateContext` and the profile `gate_payload()`
   dicts are pickled into OCR worker processes — keep them plain
   dataclasses/dicts (no ORM objects, no Paths that matter cross-process).
6. **Rate limits & caps:** TV run cooldowns key on `pipeline:tv:{series_id}`;
   `PIPELINE_BATCH_MAX_MOVIES` caps *assets* for TV batches.
7. **`DEBUG` mode** skips cooldowns via `enforce_rate_limit` — keep that
   behavior for the TV endpoints (use the same dependency).
8. **Do not rename existing label-record fields** — the head trainer parses
   them (§10.6).
9. **`data/` is operator-owned** — never commit contents, only `.gitkeep`s.
10. **`show.jpg` naming (D1) is intentional** — do not "fix" it to `poster.jpg`.

---

## 14. Suggested execution order recap

Phase 0 (schema/subjects) → 1 (sync/eligibility/config) → 2 (service/heal/jobs)
→ 3 (text profiles) → 4 (taste namespaces) → 5 (batch engine/official pick)
→ 6 (TV API/feedback) → 7 (library API). Each phase: implement → tests →
`ruff check marquee tests` → `pytest -q` (compare against the clean-HEAD failing
set) → commit.

---

## 15. API contract summary (for the frontend plan)

| Method + path | Purpose |
|---|---|
| `GET /api/pipeline/tv/summary` | TV dashboard cards (§10.2 shape) |
| `GET /api/pipeline/tv/run-queue` | Run tab rows with per-show asset chips |
| `POST /api/pipeline/tv/batch` | `{scope, series_ids?}` → job summary + asset_count |
| `POST /api/pipeline/tv/series/{id}/run` | `{include, season_ids?}` → job summary |
| `GET /api/pipeline/tv/review-queue` | Series-grouped review queue |
| `POST /api/pipeline/tv/review-queue/approve-auto` | `{deploy, series_id?}` |
| `POST /api/pipeline/tv/review/reset` | Reset TV review queue |
| `POST /api/pipeline/tv/seasons/{season_id}/use-show-poster` | D9 fallback action |
| `GET /api/pipeline/tv/metrics` | TV run metrics |
| `GET /api/pipeline/runs/{run_id}` | **Shared** run results (works for TV runs; payload gains `media_type`, `subject`, `official_pick`) |
| `GET /api/pipeline/runs/{run_id}/posters/{orig}` | Shared candidate image serving |
| `POST /api/pipeline/runs/{run_id}/rescore` | Shared, unchanged |
| `POST /api/feedback` (existing path) | Shared; works for TV run_ids |
| `GET /api/series/{id}/runs` | Run history (show + seasons) |
| `GET /api/series/{id}/artwork-events` | Deploy/restore history |
| `GET /api/library/series` | Television library grid (badges) |
| `GET /api/library/series/{id}` | Show detail (+seasons, overrides) |
| `GET/DELETE /api/library/series/{id}/poster` | Show poster file / reset |
| `GET/DELETE /api/library/seasons/{id}/poster` | Season poster file / reset |
| `GET /api/text-profiles` | All scopes, one payload |
| `POST/PUT/DELETE /api/text-profiles/{scope}[...]` | Scoped CRUD + default |
| `GET/PUT /api/text-profiles/series/{series_id}` | Per-series overrides (D7) |
| `GET /api/taste/... ?library=movies\|tv` | All taste endpoints, per library |
| `PUT /api/settings` posters block | + `series_poster_format`, `season_poster_format` |
| `GET /api/pipeline/summary` etc. | Movie endpoints — **unchanged** |

Job types the frontend will see in job streams: `poster_pipeline_tv_batch`
(new), plus `taste_rebuild`/`taste_map`/`learned_head_train` now carrying a
`library` payload key.
