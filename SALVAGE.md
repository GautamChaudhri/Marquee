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
| Taste seed | Posters already in the library folders become weak "liked" examples; rankings outweigh them |
| AI pick flow | Deploy immediately (with backup), then review, re-rank, or override |
| Database | Postgres, one fresh baseline schema, no data migrated |

## Layout

```
marquee/            the selection engine, in place at its old module paths
tests/              the engine tests that need nothing outside marquee/
frontend/           build config, design tokens, and API-independent UI primitives
_salvage/           reference only: good logic that must be re-homed onto the new skeleton
  backend/          old module paths preserved (marquee/..., pyproject.toml)
  frontend/         old pages and API-bound components
  tests/            tests for re-homed logic, plus the old conftest
  deploy/ ci/ docs/ compose files, workflows, design docs, old README
```

`_salvage/` is not part of the package. Exclude it from ruff and pytest, lift code out of it
as each subsystem is rebuilt, and delete it when it is empty of anything useful.

## Kept as-is (`marquee/`, 52 files, about 14,600 lines)

| Area | Modules | Why it stays |
|---|---|---|
| Gate-then-rank stages | `pipeline/runner.py`, `orchestrator.py`, `gate.py`, `scorer.py`, `features.py`, `types.py` | Clear stage order, cheapest signal first, every decision recorded per candidate |
| Text gate | `pipeline/ocr_filter.py` (2,749 lines) | The most heavily tuned logic in the project. Large, but it works and is self-contained |
| Design grouping | `pipeline/stacker.py`, `deduper.py`, `output.py` | Groups same-design variants instead of deleting them |
| Taste matching | `ml/taste_store.py`, `calibration.py`, `normalize.py`, `namespaces.py` | Exemplar k-NN with negative exemplars and per-exemplar evidence weights |
| Learning | `ml/residual.py`, `taste_trainer.py`, `profile_enrich.py`, `taste_map.py` | Pairwise residual already understands `rank` events with an order and a hate list |
| Inference wrappers | `ml/embedding.py`, `dino.py`, `aesthetic.py`, `face.py`, `person.py`, `zeroshot.py`, `visual_features.py`, `hardware.py` | ONNX sessions with provider selection per hardware tier |
| Knobs | `core/pipeline_config.py`, `pipeline_config_meta.py` | One settings class for every threshold and weight, with a reproducibility snapshot |
| Integrations | `core/arr_clients/`, `core/poster_sources/tmdb.py` | Thin, correct HTTP clients with error wrapping |
| Filesystem | `core/filesystem.py` | Confined read/write roots with symlink rejection. The base for media manipulation later |
| Small leaves | `core/cancellation.py`, `download_guard.py`, `text_profiles.py`, `system_metrics.py`, `rate_limit.py`, `integration_urls.py`, `logging.py` | No internal dependencies |

Model files in `marquee/ml/models/` (338 MB, gitignored) came along: CLIP ViT-B/32, the
aesthetic head, and the SCRFD face detector.

### Import seams

Four engine files import two modules that do not exist yet. These are the only dangling
imports in `marquee/` and `tests/`.

| File | Missing import | What it uses |
|---|---|---|
| `pipeline/runner.py` | `marquee.core.runtime_settings` | `settings.runs_work_path`, `settings.DEBUG` |
| `pipeline/runner.py` | `marquee.models` | `Movie`, as a carrier for `id`, `title`, `tmdb_id` |
| `pipeline/orchestrator.py` | `marquee.core.runtime_settings` | `settings.TMDB_READ_ACCESS_TOKEN` |
| `pipeline/orchestrator.py` | `marquee.models` | builds a transient `Movie` |
| `ml/taste_map.py` | `marquee.core.runtime_settings` | `settings.db_url_resolved`, `settings.poster_cache_path` |
| `ml/profile_enrich.py` | `marquee.core.runtime_settings` | `settings.db_url_resolved` |

Suggested fix: a slim `marquee/config.py`, and a small subject dataclass in
`pipeline/types.py` so the engine stops depending on the ORM.

## Mostly good, re-home (`_salvage/`)

| Logic worth lifting | Where it is | What to change |
|---|---|---|
| Library models | `backend/marquee/models/{movie,series,season,episode,media_file,base}.py` | Keep nearly as-is on a new `Base`. `MediaFile` is the unit for future media work |
| Radarr/Sonarr sync | `backend/marquee/core/sync_service.py` | Good upsert and TMDB enrichment logic; re-attach to new models and settings |
| Path translation | `backend/marquee/core/path_utils.py`, `config.py` (`translate_*_path`, path mappings) | Keep the single-translation-point rule; drop the ceiling and catalog machinery around it |
| Poster naming | `backend/marquee/core/{poster_naming,poster_files,poster_subjects,poster_thumbs}.py` | Small and correct |
| Poster write path | `backend/marquee/core/jobs/handlers_poster_mutations.py`, `mutation_documents.py` | Keep backup, validate, atomic publish, restore. Strip fencing, evidence documents, artifacts |
| Heal and rescan | `backend/marquee/core/jobs/{handlers_rescan,handlers_maintenance}.py`, `schedules.py` | Keep the logic and the three schedules |
| Library taste seed | `backend/marquee/core/tv_taste_scan.py`, `api/routes/onboarding.py`, `core/onboarding_review.py` | The "library" path is the chosen seed; extend it to movies |
| Feedback events | `backend/marquee/api/routes/feedback.py`, `models/taste_preference.py`, `core/taste_preferences.py` | Keep append-only events with exposed candidates, order, and hates. Drop successors, coordinators, publications, acknowledgements |
| Run records | `backend/marquee/models/pipeline_run.py`, `core/jobs/poster_pipeline*.py`, `api/results.py`, `api/explanations.py` | Keep one row per run plus the JSON archive; drop job, attempt, and fence columns |
| Multi-title batching | `backend/marquee/pipeline/poster_group_runner.py` | Stage-major batching keeps models loaded across titles. Worth keeping for GPU throughput |
| Auth and limits | `backend/marquee/api/{auth,request_limits,deps}.py` | API-key auth is fine; simplify |
| Review UI | `frontend/src/lib/components/pipeline/RunResultsView.svelte` (1,929 lines), `PosterRankPanel`, `PosterStack`, `PosterCandidateTile` | The drag-to-rank and hate-pile panel is the core of the new product. Split the big view |
| Library and taste pages | `frontend/src/routes/{films,television,taste}/` and their components | Trim; rebuild against the new API types |
| Settings | `frontend/src/routes/settings/`, `components/settings/` | About 4,200 lines for eight tabs. Cut to connections, paths, naming, and a few knobs |
| Deployment and CI | `deploy/`, `ci/` | Shape is right (postgres, api, worker, web); scheduler folds into the worker |

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

- There is no taste profile on this machine. The sidebar's "~430 exemplars" is hardcoded text.
- The DINOv2 and YOLO person models are missing, so `dino_knn` (weight 0.12) and the person
  features are off, and design stacking falls back to CLIP vectors.
- Learning thresholds are high for a rank-and-learn loop: 50 liked titles before
  personalization, 25 titles and 200 pairs before the residual trains.
- Text profiles are stored in `data/text_profiles.json`, not the database.
- `pipeline_config.py` still reads `MARQUEE_INTERNAL_RUNNER`, a leftover from the old runner.
- Comments reference old design-doc numbers and chunk IDs (`JMC6H`, `design 04 §9.2`) that
  no longer point anywhere.

## Suggested rebuild order

1. Skeleton: `pyproject.toml`, slim config, database base and a single baseline migration.
   Close the two import seams and move the engine tests from `_salvage/tests/` into `tests/`.
2. Library: models and Radarr/Sonarr sync.
3. Jobs: the small queue, with classes for GPU, network, media-read, and media-write.
4. Poster write path: deploy with backup, restore, heal.
5. Runs, review API, feedback events, library taste seed, learning loop.
6. Frontend: shell, library, review and rank, taste, a small activity view, trimmed settings.
7. Media management on top of `MediaFile`, `core/filesystem.py`, and the media job classes.
