# Design Directory Cleanup

**Goal:** Reorganize the `design/` directory from a chronologically-numbered
dump of mixed document types into a clean structure with clear separation between
authoritative design docs, planning/ideation docs, and historical archives.

---

## Current Problem

The `design/` directory has ~50 files at root level with chronological numbering
(01, 02, ... 30) that no longer reflects structure. Three distinct types of
documents are mixed together:

- **Authoritative design docs** — reconciled descriptions of how the system
  actually works (e.g. `04-revised-pipeline-design.md`,
  `22-radarr-overlay.md`)
- **Investigation/debug reports** — post-mortems for specific issues
  (e.g. `23-pipeline-ranking-debug.md`)
- **Planning/ideation docs** — high-level thinking before building
  (e.g. `29-poster-pipeline-ux-redesign.md`, `30-ranking-v2-concept-notes.md`)

These all live at the same level with similar-looking filenames, making it hard
to find the authoritative docs when you need them.

---

## Target Structure

```
design/
├── overview.md                  # App overview: tech stack, goals, feature summaries
├── poster-pipeline.md           # Pipeline, taste, learned head, OCR, scoring, tuning
├── hdr-overlay.md               # Radarr Overlay, HDR detection, CF scores
├── letterbox.md                 # Letterbox detection, crop tags, heal, reencode
├── audio-subs.md                # Audio & subtitle management, Subgen, policies, jobs
├── library.md                   # Media library, sync, models, config, films + shows
├── job-platform.md              # Durable job platform, workers, supervisor
├── timeline.md                  # TODOs completed, current, remaining, and deferred
├── plans/                       # High-level ideation before concrete design
│   ├── README.md                # Convention for plans/ documents
│   ├── 29-poster-pipeline-ux-redesign.md
│   ├── 30-ranking-v2-concept-notes.md
│   └── ... (all planning/ideation docs moved here)
└── archive/                     # Original historical docs (kept for reference)
    ├── 01-marquee-project-spec.md
    ├── 04-revised-pipeline-design.md
    ├── 22-radarr-overlay.md
    └── ... (all existing docs moved here)
```

Additionally, create a **project root `README.md`** at `/forge/Marquee/README.md`
with general project information (getting started, tech stack, what the app
does, development setup).

---

## Phase 1 — Gather Information

### 1.1 Read all existing design docs

Read every file in `design/` including subdirectories (`plans/`,
`more-features/`, `13-gauntlet-prep/`). Build a mental map of what information
lives where.

### 1.2 Read the project context files

- `/forge/Marquee/AGENTS.md` — repo guidelines, commands, architecture overview
- `/forge/Marquee/CLAUDE.md` — architecture layers, pipeline stages, design decisions

### 1.3 Scan the codebase for verification

For each major feature area below, read the relevant source files to verify that
the design docs are still accurate and to fill in any gaps. Do not blindly
regurgitate code — the goal is to produce design documentation, not API
reference. Verify claims from the design docs against actual code; correct any
stale information.

**Poster pipeline:**
- `marquee/pipeline/runner.py` — stage order, execution flow
- `marquee/pipeline/gate.py` — gate thresholds and logic
- `marquee/pipeline/scorer.py` — `select_scorer()`, `WeightedScorer`, `LearnedScorer`
- `marquee/pipeline/features.py` — feature extraction stages
- `marquee/pipeline/deduper.py` — SHA-256 + pHash dedup
- `marquee/pipeline/ocr_filter.py` — PaddleOCR gate
- `marquee/pipeline/output.py` — result archiving and deployment
- `marquee/pipeline/types.py` — data records flowing between stages
- `marquee/ml/` — all ML modules (embedding, dino, aesthetic, face, person,
  visual_features, zeroshot, calibration, taste_store, learned_head,
  head_trainer, normalize, hardware)
- `marquee/core/pipeline_config.py` — `pipeline_settings` singleton
- `marquee/api/routes/pipeline.py` — pipeline API routes
- `marquee/api/routes/test_pipeline.py` — test pipeline endpoint

**HDR / Radarr Overlay:**
- `marquee/core/arr_clients/radarr.py` — Radarr API client
- `marquee/core/dovi_conversion.py` — DOVI analysis
- `marquee/api/routes/hdr.py` (if it exists) or relevant route files
- Frontend: `frontend/src/routes/hdr/` — the actual page implementation

**Letterbox:**
- `marquee/media/letterbox_detect.py` — detection backends
- `marquee/media/letterbox_manager.py` — batch manager
- `marquee/media/letterbox_preview.py` — preview frames
- `marquee/core/letterbox_service.py` — file mutation
- `marquee/core/letterbox_heal.py` — heal scan
- `marquee/core/letterbox_reencode.py` — reencode logic
- `marquee/api/routes/letterbox.py` — API routes
- `marquee/models/` — LetterboxState, LetterboxEvent models

**Audio & Subtitles:**
- `marquee/core/subtitles/probe.py` — ffprobe container inspection
- `marquee/core/subtitles/service.py` — subtitle service layer
- `marquee/core/subtitles/mutation.py` — mutation planning
- `marquee/core/subtitles/coverage.py` — coverage analysis
- `marquee/core/subtitles/policy.py` — subtitle policies
- `marquee/core/subtitles/config.py` — SubtitleSettings
- `marquee/core/subtitles/generation.py` + `generators/subgen.py` — AI generation
- `marquee/core/subtitles/adapters/` — Matroska, MP4 adapters
- `marquee/core/subtitles/external.py` — external provider integration
- `marquee/core/subtitles/validation.py` — validation
- `marquee/api/routes/subtitles.py` — subtitle API routes
- `marquee/api/routes/subtitle_policies.py` — policy routes
- `marquee/api/routes/subtitle_generators.py` — generator routes
- `marquee/api/routes/media_jobs.py` — media job routes
- `marquee/models/subtitle_inventory.py` — SubtitleInventory model
- Frontend: `frontend/src/routes/audio-subs/` — the audio & subs page
- Frontend: `frontend/src/lib/components/subtitles/` — UI components

**Library:**
- `marquee/models/` — Movie, Series, Season, Episode models
- `marquee/config.py` — Settings singleton, path translation
- `marquee/database.py` — engine/session factory
- `marquee/core/sync_service.py` — library sync
- `marquee/api/routes/library.py` — library API routes
- `marquee/api/library_serializers.py` — serializers
- `marquee/main.py` — FastAPI app, lifespan, routes

**Job Platform:**
- `marquee/core/jobs/manager.py` — job_manager
- `marquee/core/jobs/worker.py` — DurableWorker
- `marquee/core/jobs/scheduler.py` — recurring jobs
- `marquee/core/jobs/supervisor.py` — WorkerSupervisor
- `marquee/core/jobs/handlers.py` — handler registry
- `marquee/core/jobs/builtin_handlers.py` — built-in handlers
- `marquee/core/jobs/legacy_media.py` — legacy media job handlers
- `marquee/core/media_jobs/` — media-specific job infrastructure
- `marquee/models/job.py` — job ORM models

**Deployment:**
- `docker/` — Docker Compose files and profiles
- `alembic/` — migrations
- `pyproject.toml` — dependencies and extras

### 1.4 Identify document types

For each existing file in `design/`, classify it:

- **Authoritative design doc** — describes current behavior, reconciled with
  code, written as "this is how it works"
- **Planning/ideation doc** — describes future work, written as "here's what we
  should build" or "concept notes"
- **Investigation report** — post-mortem of a specific bug or behavior
- **Housekeeping** — scripts, JSON data, .DS_Store, SECURITY.md, etc.

---

## Phase 2 — Create Core Design Docs

Each core design doc should be a **synthesis** of information from multiple
existing design docs, verified against the current codebase. The docs should be
detailed enough to serve as the authoritative reference for someone working on
that feature, but not so detailed that they become brittle — this project is
still in development, so avoid listing exact line numbers or commit hashes.

Every core design doc should:
- Start with a brief overview of what the feature does
- List what is implemented vs what is not yet implemented
- Describe the architecture and key modules
- Reference other core design docs by name where relevant (e.g. "See
  `poster-pipeline.md` for details on the scoring system")
- Include relevant configuration knobs and their defaults
- Be written in present tense describing current behavior (not future plans)

### 2.1 `overview.md` — Application Overview

Source material:
- `01-marquee-project-spec.md`
- `CLAUDE.md` (architecture section)
- `AGENTS.md`
- `02-model-schema.md`
- `03-config-and-paths.md`
- `SECURITY.md`
- `10-security-and-hardening.md`
- `learning-roadmap.md`

Content:
- What Marquee is and what it does (high-level)
- Tech stack: FastAPI, PostgreSQL/SQLite, SvelteKit frontend, ONNX ML
- All major features with brief (1-2 paragraph) summaries referencing their
  respective core design docs
- Architecture layers (config → core → pipeline → API)
- Key design decisions (gate-then-rank, cheapest-signal-first, enqueue-vs-inline,
  path translation)
- Development setup and commands
- Currently implemented scope vs not-yet-implemented scope (movies only — TV
  versions of all features are deferred)
- Configuration: Settings vs PipelineSettings vs SubtitleSettings

### 2.2 `poster-pipeline.md` — Poster Pipeline

Source material:
- `04-revised-pipeline-design.md` (primary — the authoritative pipeline reference)
- `07-extended-features-and-calibration.md`
- `08-tuning-knobs.md`
- `09-feedback-loop-design.md`
- `16-production-hardening-fixes.md`
- `17-poster-pipeline-backend-slice.md`
- `18-settings-ocr-gpu-and-text-gate.md`
- `19-preference-ranking-feedback.md`
- `23-pipeline-ranking-debug.md` (the scoring=1 root cause — useful for understanding
  learned head behavior)
- `28-ocr-false-positive-labeling.md`
- `30-ranking-v2-concept-notes.md` (mentions current state for context)
- Pipeline-related sections from `todos.md`

Code verification: poster pipeline sources listed in Phase 1.3.

Content:
- Pipeline architecture: GATE-then-RANK principle
- Implemented stage order with brief description of each stage
- Gate semantics vs rank semantics
- Feature extraction: CLIP (batched), DINOv2 (GPU tiers), CV palettes/composition,
  face/person geometry, quality artifacts, title geometry, typicality
- Taste profile: k-NN over exemplars, negative exemplars, softmax weighting,
  KDE calibration
- Learned head: logistic regression, pairwise training, auto-retrain,
  SCORER=auto resolution
- OCR: PaddleOCR integration, 3-pass, strict title-only gate, residual box
  limits, contrast-enhance retry
- Dedup: SHA-256 exact + pHash near-duplicate
- Scoring: WeightedScorer vs LearnedScorer, feature contributions
- Feedback loop: approve/override/reject, head retraining triggers
- Tuning knobs: pipeline_config.py settings, their defaults, and what they control
- Output: ranked renaming, full-resolution re-download, archiving
- Run management: RunManager, SSE events, process-lifetime FeatureExtractor
- Not implemented: TV/series pipeline, additional poster sources, VLM judge

### 2.3 `hdr-overlay.md` — Radarr Overlay / HDR

Source material:
- `22-radarr-overlay.md` (primary — comprehensive design doc)
- HDR-related sections from `todos.md`

Code verification: HDR sources listed in Phase 1.3.

Content:
- Purpose: surface Radarr metadata not exposed in Radarr's own UI
- HDR tag classification: 5-group upgrade (HDR, HDR10, HDR10+, DoVi, DoVi no
  fallback)
- HDR distribution visualization and filtering
- Custom format score visibility per movie
- Quality profile HDR target detection
- HDR target status (met/below/no target/DOVI warning)
- DOVI fallback analysis
- Tier ladder UX: green/gold/gray zones, collapsible, click-to-set
- Settings toggle: HDR_OVERLAY_DOVI_REQUIRE_FALLBACK
- Read-only design (no mutations to Radarr or Marquee DB)
- Nav placement: Toolbox → Radarr Overlay

### 2.4 `letterbox.md` — Letterbox Cropping

Source material:
- `more-features/04-letterbox-cropping.md` (primary)
- `more-features/04-letterbox-script.sh` and `04-letterbox-script-v2.sh`
  (historical scripts — note they exist but describe current behavior from code)
- Letterbox-related sections from `todos.md`

Code verification: letterbox sources listed in Phase 1.3.

Content:
- Purpose: movie-only letterbox detection and MKV crop-tag application
- Implemented vs not implemented scope
- Detection backends: cropdetect and ImageMagick trim
- Resolution pre-filter (skip already-cropped or low-res)
- Single-movie synchronous detection vs batch detection with SSE
- Before/after preview WebP frames
- MKV crop-tag apply and remove operations
- Ignore/review state management
- Tag-drift heal scan
- Reencode pipeline (letterbox_reencode.py)
- API routes and models (LetterboxState, LetterboxEvent)
- Configuration knobs (LETTERBOX_* settings)
- Not implemented: TV episode/season cascade, auto-apply of high-confidence,
  MP4-to-MKV conversion

### 2.5 `audio-subs.md` — Audio & Subtitle Management

Source material:
- `more-features/03-subtitle-management.md` (primary)
- `17-subtitles-vertical-slice.md`
- `18-subtitles-vertical-slice.md`
- Subtitle-related sections from `todos.md`

Code verification: audio & subtitle sources listed in Phase 1.3.

Content:
- Purpose: container inspection, audio/subtitle track management, policy-driven
  cleanup, AI generation via Subgen
- Implemented vs not implemented scope
- Probe system: ffprobe container inspection producing audio streams + subtitle
  tracks inventory
- Audio track management: viewing codec/channels/language/dispositions, editing
  flags (default, forced, SDH, commentary), reordering, removal
- Subtitle inventory: SubtitleInventory + SubtitleTrack models, per-file
  snapshots, coverage analysis
- Preferred audio/subtitle language preferences (per-movie overrides)
- Subtitle policies: language-cleanup rules, policy evaluation, bindings
- Durable media jobs for mutations: audio_remove, subtitle_remove,
  subtitle_embed, subtitle_metadata, track_remove, audio_reorder
- Media job pipeline: MediaJob, MediaBatch, MediaJobEvent models
- Managed subtitle assets: caching for restore-on-upgrade
- Media backups: pre-mutation file copies
- Subgen integration: AI subtitle generation via external service (localhost:9000)
- API surface: library inventory, scan, track preview/download, policy CRUD,
  generator management
- Frontend: four-tab page (Inventory, AI Generation, Policies, Jobs Queue)
- Matroska and MP4 adapter system for container-specific mutations
- Not implemented: dedicated batch routes (planned in design docs but not built)

### 2.6 `library.md` — Media Library

Source material:
- `01-marquee-project-spec.md` (library sections)
- `02-model-schema.md`
- `03-config-and-paths.md`
- `05-linux-deployment.md`
- `06-multi-platform-deployment.md`
- `20-cold-start-onboarding.md`

Code verification: library sources listed in Phase 1.3.

Content:
- Data model: Movie, Series, Season, Episode with ArtworkMixin
- Database: async PostgreSQL (production) / SQLite (dev), Alembic migrations
- Settings: singleton Settings, path translation for Radarr/Sonarr mount
  namespaces
- Library sync: Radarr/Sonarr polling, upsert logic
- API routes: paginated movie/series listing, detail endpoints, filtering
- Frontend pages: `/films/` (movies), `/shows/` (TV series), movie detail
- Poster deployment: PosterService as single write path, atomic writes,
  ArtworkEvent history
- Cache system: poster cache at data/cache/posters/movies/
- Self-healing: periodic poster heal scan, letterbox heal
- Webhook handling: Radarr/Sonarr webhooks for upgrade restoration
- Currently movies-only — TV versions of pipeline, letterbox, and other features
  are deferred (see timeline.md)

### 2.7 `job-platform.md` — Durable Job Platform

Source material:
- `15-durable-job-platform.md` (primary)
- `21-job-progress-bar-persistence.md`
- `14-internal-backup-strategy.md`
- Job-related sections from `todos.md`

Code verification: job platform sources listed in Phase 1.3.

Content:
- Purpose: resource-aware, durable background job execution decoupled from the
  API event loop
- Architecture: job_manager (sole writer), DurableWorker (claims+runs),
  WorkerSupervisor (process management), scheduler (recurring jobs)
- PostgreSQL row-locking: FOR UPDATE SKIP LOCKED for safe concurrent workers
- Resource reservations: gpu, media_read, media_write/transcode,
  network_external, maintenance_exclusive, per-file media-file:{id}
- Handler registry: server-side handler resolution (never arbitrary callables
  from requests)
- Job lifecycle: created → queued → claimed → running → completed/failed
- SSE progress events
- Embedded workers: JOB_EMBEDDED_WORKERS auto-spawns worker + scheduler as
  supervised child processes
- Enqueue vs inline principle: what goes through jobs vs what stays in the
  request
- Rate limiting: cooldown on expensive endpoints
- Internal backups: DB + managed state archive with rotation

### 2.8 Quality criteria for all core docs

- Every factual claim should be verifiable in the code. If the code contradicts
  a design doc, trust the code and update the design doc.
- Use exact module paths (e.g. `marquee/pipeline/scorer.py`) when referencing
  code locations.
- Mention configuration knobs with their exact env var names (e.g.
  `SCORER=auto`, `OCR_MAX_RESIDUAL_BOXES=0`).
- Keep the tone factual and present-tense. These are reference docs, not
  proposals.
- Cross-reference other core docs: "See `poster-pipeline.md` for details on..."
- Include a brief "Not implemented" section in each doc listing what's planned
  but not yet built.

---

## Phase 3 — Create Timeline Document

### 3.1 `timeline.md`

Source material:
- `todos.md` (primary — already well-structured by phase)
- All other design docs for deferred and planned items
- `phase3-paths.json`
- `phase3-selection-plan.md`
- `movie-selection-plan.md` and `movie-selection-plan-phase2.md`
- `11-next-steps-handoff.md`

Content:
- Phase-by-phase organization (keep the existing phase structure from todos.md)
- Each phase: what was completed (checkboxes filled), what remains
- Deferred section: items postponed with no target phase, drawn from all docs
- Current phase clearly marked
- Important note: all currently implemented features are movie-only; TV/series
  versions of the pipeline, letterbox, subtitle management, and other features
  are deferred to a later phase

---

## Phase 4 — Reorganize Files

### 4.1 Create directory structure

Create these directories if they don't exist:
- `design/plans/`
- `design/archive/`

Move `design/plans/28-ocr-false-positive-labeling.md` into `design/archive/`
(the plans/ folder is for future planning docs only — OCR labeling is already
designed and partially built).

### 4.2 Move planning/ideation docs to `plans/`

Move these files from `design/` root into `design/plans/`:
- `29-poster-pipeline-ux-redesign.md`
- `30-ranking-v2-concept-notes.md`
- `19-preference-ranking-feedback.md`
- `20-cold-start-onboarding.md`
- `21-job-progress-bar-persistence.md`
- `17-subtitles-vertical-slice.md`
- `18-subtitles-vertical-slice.md`
- `17-poster-pipeline-backend-slice.md`
- `18-settings-ocr-gpu-and-text-gate.md`
- `09-feedback-loop-design.md`
- `11-next-steps-handoff.md`
- `16-production-hardening-fixes.md`
- `movie-selection-plan.md`
- `movie-selection-plan-phase2.md`
- `phase3-selection-plan.md`

### 4.3 Move everything else to `archive/`

Move ALL remaining files from `design/` root and subdirectories into
`design/archive/`, preserving subdirectory structure:

- `design/*.md` → `design/archive/` (all root .md files)
- `design/*.json` → `design/archive/` (api-schema.json, phase3-paths.json)
- `design/*.sh` → `design/archive/` (copy_movies_phase2.sh)
- `design/SECURITY.md` → `design/archive/`
- `design/more-features/` → `design/archive/more-features/`
- `design/13-gauntlet-prep/` → `design/archive/13-gauntlet-prep/`
- `design/plans/28-ocr-false-positive-labeling.md` → `design/archive/plans/28-ocr-false-positive-labeling.md`

Delete `.DS_Store` and `._.DS_Store` files — they are macOS metadata, not
project artifacts.

### 4.4 Create `design/plans/README.md`

A short document explaining the convention for the plans/ folder:

```markdown
# Plans

This folder contains high-level planning and ideation documents. These are
pre-build thinking docs — they capture the problem space, proposed approaches,
trade-offs, and general direction before a concrete implementation plan is
drawn up.

When a plan matures into an actual build, the relevant information should be
merged into the appropriate core design doc at `design/` root.

Documents in this folder are **not** authoritative descriptions of how the
system works. For that, see the core design docs at `design/` root.
```

### 4.5 Final directory state

After all moves, `design/` should contain ONLY:

```
design/
├── overview.md
├── poster-pipeline.md
├── hdr-overlay.md
├── letterbox.md
├── audio-subs.md
├── library.md
├── job-platform.md
├── timeline.md
├── design-cleanup.md              # This file
├── plans/
│   ├── README.md
│   └── (planning docs)
└── archive/
    └── (all original files)
```

---

## Phase 5 — Create Project Root README

Create `/forge/Marquee/README.md` at the project root. This is the public-facing
README for the GitHub repository. It should be concise and cover:

### Content

1. **Project name and one-line description** — Marquee: AI-powered media library
   management for Radarr/Sonarr

2. **What it does** — 2-3 bullet points: poster selection with ML pipeline,
   subtitle/audio track management with policies, HDR detection via Radarr
   overlay, letterbox detection and cropping

3. **Current status** — development phase, movies-only (TV support deferred)

4. **Tech stack** — FastAPI backend, SvelteKit frontend, PostgreSQL/SQLite,
   ONNX ML (CLIP, DINOv2, PaddleOCR), Docker deployment

5. **Quick start** — basic setup commands from AGENTS.md/CLAUDE.md (venv, pip
   install, uvicorn, frontend dev server if applicable)

6. **Project structure** — brief directory overview (marquee/, frontend/,
   design/, tests/, docker/, alembic/)

7. **Documentation** — pointer to `design/overview.md` and core design docs

8. **License** — if one exists (check pyproject.toml)

### Style

- Professional, not marketing-speak
- Minimal emojis — a couple to draw attention to important sections is fine,
  but don't decorate every heading
- Accurate to current state — don't promise features that don't exist yet

---

## Phase 6 — Final Verification

After all files are created and moved:

1. Verify that `design/` root contains only the 8 core docs + this cleanup doc
   + `plans/` + `archive/`
2. Verify `design/plans/` contains only planning docs + its README
3. Verify `design/archive/` contains every original file (nothing was deleted,
   only moved)
4. Verify that cross-references between core docs are correct (doc filenames
   match)
5. Do a quick sanity check: pick 3 claims from each core doc and verify them
   against the actual source code

---

## Execution Notes

- All new files should be created with `write_file` or equivalent tools
- All moves should use file system operations (mv/rename), not copy+delete
- Remove `.DS_Store` files — they're macOS metadata artifacts
- Do not commit or push changes — the reorganization should be done on disk
  only, leaving git operations to the user
- If any existing design doc contains information you can't verify in the code,
  flag it in the core doc with a brief note like "(unverified in current code)"
  rather than silently carrying forward potentially stale information
- This is a development-phase project — the core docs should be accurate but
  not overly precise. Avoid listing exact line numbers, commit hashes, or
  byte-level details that will rot quickly
