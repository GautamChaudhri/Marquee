# Salvage manifest

What was carried over from the old Marquee codebase, what was left behind, and what the
rebuild has to attach before any of it runs.

- **Source:** `Marquee.old` at commit `814ac81` (`main`, 380 commits, last commit 2026-09-03).
  The working tree had five uncommitted changes. Three of the changed files were copied in
  their working-tree form: `.gitignore`, `README.md`, and `.env.development.example` (the
  last two under `_salvage/docs/`).
- **Copied:** 356 files, all byte-identical to the originals. Nothing was edited.
- **Not verified by running:** no tests were run. The old `.venv` is a macOS virtualenv and
  this WSL machine has no Python packages or Node installed yet. Every copied `.py` compiles.

## Decisions taken

| Question | Decision |
|---|---|
| Job manager | Rebuild small: our own Postgres-table queue, one worker, GPU slot of 1, ML in a child process |
| Taste seed | Each movie folder's `poster.jpg` was picked by hand, so it seeds the taste profile as a liked example at full weight (1.0). Movies without a `poster.jpg` are ignored entirely |
| AI pick flow | Deploy immediately (with backup), then review, re-rank, or override |
| Database | Postgres, one fresh baseline schema, no data migrated |

## Layout

```
marquee/            the selection engine and the new skeleton, in the target layout
                    (config/, db/, fs/, integrations/, ml/, pipeline/, library/, taste/,
                    api/, cli, doctor, model_fetch);
                    docs/architecture.md describes each package
tests/              mirrors marquee/: the original engine tests plus the engine tests
                    lifted from _salvage/tests/
frontend/           build config, design tokens, and API-independent UI primitives
data/models/        model files (not committed; moved here from marquee/ml/models/)
_salvage/           reference only: good logic that must be re-homed onto the new skeleton
  backend/          old module paths preserved (marquee/..., pyproject.toml)
  frontend/         old pages and API-bound components
  tests/            tests for logic not yet re-homed, plus the old conftest
  deploy/ ci/ docs/ compose files, the CodeQL and Dependabot configs, design docs, old README
```

`_salvage/` is not part of the package. Ruff, pytest, and import-linter exclude it. Lift code
out of it as each subsystem is rebuilt, and delete it when it is empty of anything useful.

## Kept as-is (`marquee/`, 52 files, about 14,600 lines)

Paths below are where each module lives now. They were moved out of `marquee/core/` and `ml/`
in the M0+M1 session without logic changes; `git log --follow` reaches their history.

| Area | Modules | Why it stays |
|---|---|---|
| Gate-then-rank stages | `pipeline/runner.py`, `orchestrator.py`, `gate.py`, `scorer.py`, `features.py`, `types.py` | Clear stage order, cheapest signal first, every decision recorded per candidate |
| Text gate | `pipeline/ocr_filter.py` (2,749 lines) | The most heavily tuned logic in the project. Large, but it works and is self-contained |
| Design grouping | `pipeline/stacker.py`, `deduper.py`, `output.py` | Groups same-design variants instead of deleting them |
| Taste matching | `ml/taste_store.py`, `calibration.py`, `namespaces.py`, `pipeline/normalize.py` | Exemplar k-NN with negative exemplars and per-exemplar evidence weights |
| Learning | `ml/residual.py`, `taste/trainer.py`, `taste/profile_enrich.py`, `taste/map.py` | Pairwise residual already understands `rank` events with an order and a hate list |
| Inference wrappers | `ml/embedding.py`, `dino.py`, `aesthetic.py`, `face.py`, `person.py`, `zeroshot.py`, `visual_features.py`, `hardware.py` | ONNX sessions with provider selection per hardware tier |
| Knobs | `config/pipeline.py`, `config/pipeline_meta.py` | One settings class for every threshold and weight, with a reproducibility snapshot |
| Integrations | `integrations/arr/`, `integrations/tmdb/` | Thin, correct HTTP clients with error wrapping |
| Filesystem | `fs/boundary.py` | Confined read/write roots with symlink rejection. The base for media manipulation later. Its FastAPI file response moved to `api/files.py` in M3 |
| Small leaves | `cancellation.py`, `fs/download_guard.py`, `pipeline/text_profiles.py`, `host_metrics.py`, `api/rate_limit.py`, `integrations/urls.py`, `logging.py` | No internal dependencies |

No model file is carried over any more. Since M2, `marquee models fetch` builds or downloads
every model into `data/models/` (gitignored), the aesthetic head and the SCRFD face detector
included, each pinned by its checksum. The salvage once copied the old installation's model
files (338 MB); among them was `negative_prompts.npy`, which no kept code references.

### Import seams

Closed. There are no dangling imports in `marquee/` or `tests/`.

| File | Was | Now |
|---|---|---|
| `pipeline/runner.py` | `marquee.core.runtime_settings` (`runs_work_path`, `DEBUG`) | `marquee.config.settings` (`runs_dir`, `DEBUG`) |
| `pipeline/runner.py` | `marquee.models.Movie` | `pipeline.types.SubjectRef` (`id`, `title`, `tmdb_id`) |
| `pipeline/orchestrator.py` | `marquee.core.runtime_settings` (`TMDB_READ_ACCESS_TOKEN`) | `marquee.config.settings`, secret unwrapped at the call site |
| `pipeline/orchestrator.py` | a transient `Movie` | a `SubjectRef` |
| `taste/map.py` | `marquee.core.runtime_settings` (`db_url_resolved`, `poster_cache_path`) | `settings.database_url_sync`, `settings.poster_cache_dir` |
| `taste/profile_enrich.py` | `marquee.core.runtime_settings` (`db_url_resolved`, TMDB token) | `settings.database_url_sync`, `settings.TMDB_READ_ACCESS_TOKEN` |

`taste/map.py` and `taste/profile_enrich.py` query library tables. `movies` exists since M3,
with the five columns they read (`id`, `title`, `year`, `tmdb_id`, `genres`); `series` and
`seasons` are never created while TV is out of scope, so their series branches stay
unreached. `taste/map.py` still builds poster links in the old API's shape
(`/api/library/movies/<id>/poster`); M3's route is `/api/movies/<id>/poster`.

## Mostly good, re-home (`_salvage/`)

| Logic worth lifting | Where it is | What to change |
|---|---|---|
| Library models | `backend/marquee/models/{series,season,episode,media_file}.py` | The TV models wait for TV; `MediaFile` is the unit for future media work. `movie.py` and `base.py` became `marquee/db/` in M3 and were deleted |
| Sonarr sync | `backend/marquee/core/sync_service.py` | The TV half. The movie half (upsert, retire, TMDB enrichment) became `library/sync.py` in M3 and was deleted; the shared poster-path helpers, `_upsert_movie_media_file` (media management), and `_extract_media_info` (video resolution) stay |
| Poster naming | `backend/marquee/core/{poster_naming,poster_files,poster_subjects,poster_thumbs}.py` | Small and correct |
| Poster write path | `backend/marquee/core/jobs/handlers_poster_mutations.py`, `mutation_documents.py` | Keep backup, validate, atomic publish, restore. Strip fencing, evidence documents, artifacts |
| Heal and rescan | `backend/marquee/core/jobs/{handlers_rescan,handlers_maintenance}.py`, `schedules.py` | Keep the logic and the three schedules |
| Library taste seed | `backend/marquee/core/tv_taste_scan.py`, `api/routes/onboarding.py`, `core/onboarding_review.py` | The "library" path is the chosen seed; extend it to movies |
| Feedback events | `backend/marquee/api/routes/feedback.py`, `models/taste_preference.py`, `core/taste_preferences.py` | Keep append-only events with exposed candidates, order, and hates. Drop successors, coordinators, publications, acknowledgements |
| Run records | `backend/marquee/models/pipeline_run.py`, `core/jobs/poster_pipeline*.py`, `api/results.py`, `api/explanations.py` | Keep one row per run plus the JSON archive; drop job, attempt, and fence columns |
| Multi-title batching | `backend/marquee/pipeline/poster_group_runner.py` | Stage-major batching keeps models loaded across titles. Worth keeping for GPU throughput |
| Request limits | `backend/marquee/api/{request_limits,deps}.py` | Rate limiting and lockout wait for M6 and M8. The API-key auth became `api/auth.py` in M3, without its bypasses, and was deleted |
| Review UI | `frontend/src/lib/components/pipeline/RunResultsView.svelte` (1,929 lines), `PosterRankPanel`, `PosterStack`, `PosterCandidateTile` | The drag-to-rank and hate-pile panel is the core of the new product. Split the big view |
| Library and taste pages | `frontend/src/routes/{films,television,taste}/` and their components | Trim; rebuild against the new API types |
| Settings | `frontend/src/routes/settings/`, `components/settings/` | About 4,200 lines for eight tabs. Cut to connections, paths, naming, and a few knobs |
| Deployment | `deploy/` | Shape is right (postgres, api, worker, web); scheduler folds into the worker |
| CI extras | `ci/codeql.yml`, `ci/dependabot.yml` | The main workflow is re-homed in `.github/workflows/ci.yml`; these two wait for M8 |

M3 also lifted, and deleted from `_salvage/`: `core/path_utils.py` and its tests (now
`fs/paths.py`), `database.py` and `alembic.ini` (now `marquee/db/`), and `api/auth.py` and its
tests (now `api/auth.py`). The movie list, detail, and poster routes left
`api/routes/library.py` (now `api/routes/library.py`; the series and season routes and the
poster reset routes stay). `api/library_serializers.py`, `core/movie_queries.py`, and
`core/sort_title.py` stay whole: the salvaged pipeline and onboarding routes still use them,
and Radarr supplies the sort title.

Partly lifted test files in `_salvage/tests/`: `test_sync.py` keeps its TV tests, the three
movie poster-path tests that wait for poster naming, and the no-clients test;
`test_library_enrichment.py` keeps the poster-status, review, resolution, and poster reset
tests that wait for M5; `test_models.py` keeps the TV model tests.
`test_pipeline.py` keeps 1 of its 78 tests
(the system OCR status API route; a stale assertion on the old `Settings.runs_work_path` was
deleted in M2);
`test_text_profiles.py` keeps 8 of its 30 (six API tests that need the app, ORM, and
database, and two that call the old job runner's `_ocr_gate_context`). The rest moved to
`tests/pipeline/`. `test_archive_nan.py` and `test_artifact_codec.py` moved whole.

## Left behind

| What | Size | Why |
|---|---|---|
| Job platform (`core/jobs/`, `models/job*.py`) | about 28,000 lines, 51 test files | Fence tokens, cgroups, advisory-lock gates, a control-pipe runner protocol, PgQueuer. Far more machinery than one user and one GPU need |
| Job API and Activity UI | `routes/jobs.py` 2,494 lines; frontend `lib/activity/` about 8,600 | Bound to that platform's contract |
| Versioned configuration | `core/configuration*.py`, `integration_settings.py`, about 60 `JOB_*` settings | Revisioned config catalog; replace with one small settings module |
| Migrations | 25 Alembic revisions, `db_migration.py` (570 lines) | Fresh baseline instead |
| ML publication layer | `ml/publication_catalog.py`, `core/jobs/ml_publication.py` | Artifact generations and consumer acknowledgements for a profile that is one `.npz` |
| Ops surfaces | system metrics sampler, operations dashboard, DB backup routes, dev OCR label routes | Not needed to pick posters |
| Generated and test scaffolding | `openapi.ts` (8,753 lines), `design/api-schema.json`, Playwright suite and synthetic backend | Regenerated or rewritten against the new API |
| Local state | macOS `.venv`, `node_modules`, SQLite `data/marquee.db`, embedding cache | Rebuilt or re-synced |

Secrets were not copied. The old `.env` files (TMDB token, Radarr and Sonarr URLs and keys,
path mappings) are still in `Marquee.old/` for when the new config needs them.

## Found along the way

These notes describe the old installation as it was when it was salvaged, not this machine
as it is now. Several no longer hold: M2 built the taste profile and fetched the DINOv2 and
YOLO person models, for example.

- There is no taste profile on this machine. The sidebar's "~430 exemplars" is hardcoded text.
- The DINOv2 and YOLO person models are missing, so `dino_knn` (weight 0.12) and the person
  features are off, and design stacking falls back to CLIP vectors.
- Learning thresholds are high for a rank-and-learn loop: 50 liked titles before
  personalization, 25 titles and 200 pairs before the residual trains.
- Text profiles are stored in `data/text_profiles.json`, not the database.
- The old `pipeline_config.py` read `MARQUEE_INTERNAL_RUNNER`, a leftover from the old runner.
  Removed in M0+M1: both settings classes read the same `.env`.

## Rebuild order

The first goal is a production-ready application for movies, on NVIDIA GPUs, deployed with
Docker. The milestones, in order:

1. **M0 and M1, machine and skeleton** (done): the project tooling, the engine moved into the
   target layout with its import seams closed, its tests lifted, and `marquee doctor`.
2. **M2, the engine runs for real** (done): `marquee models fetch`, `marquee taste seed` from
   the library's posters, and `marquee run` for one movie, with no database.
3. **M2b, container proof** (done): one image on a slim Python base, with CUDA from the
   Python wheels, that runs the doctor, fetches the models, and completes a run inside a
   container with the GPU.
4. **M3, database and movie library** (done): one baseline migration, library and artwork
   tables, path translation, Radarr sync, and an API skeleton with key auth.
5. **M4, jobs**: the small Postgres queue, with classes for GPU, network, media-read, and
   media-write.
6. **M5, the product loop**: runs as jobs, deploy with backup and restore, review and
   re-rank, feedback events, exemplars, and the learning loop.
7. **M6, frontend**: shell, library, review and rank, taste, a small activity view, trimmed
   settings.
8. **M8, packaging and production hardening**: the full Compose stack, a fresh-machine path
   from the README, scheduled database backups, and `_salvage/` deleted.

After that, in no fixed order: TV (series and season posters through Sonarr), other
hardware, and media management on top of `MediaFile`, `fs/boundary.py`, and the media job
classes.
