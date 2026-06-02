# Marquee Codebase Autopsy — Bob the Builder's Analysis

## Project Structure
```
Marquee.old/
├── goals/
│   ├── Marquee.md           # Excellent spec — the blueprint
│   └── 2001_images.txt      # Sample TMDB API response for reference
├── backend/                  # FastAPI app (v0.1.0)
│   ├── app/
│   │   ├── main.py           # App entry, lifecycle, routes
│   │   ├── config.py         # Pydantic-settings config
│   │   ├── database.py       # Async SQLAlchemy + SQLite
│   │   ├── models/           # SQLAlchemy ORM (Movie, Series)
│   │   ├── schemas/          # Pydantic schemas (media analysis — wrong domain!)
│   │   ├── api/routes/       # sync.py, posters.py, library.py
│   │   └── core/
│   │       ├── arr_clients/  # Radarr/Sonarr HTTP clients
│   │       ├── external_databases/  # TMDB client
│   │       ├── sync_service.py      # *arr → DB sync
│   │       ├── image_fetcher.py     # TMDB poster download
│   │       └── library_scanner.py   # Poster existence check
│   └── data/                 # SQLite DB + downloaded posters
├── experiments/
│   ├── filtering/
│   │   ├── filter_posters_ocr.py     # ★ PaddleOCR text filter
│   │   ├── filter_posters.py         # Florence-2 alternative (GPU-heavy)
│   │   └── deduplicate_posters.py    # SHA-256 exact dedup
│   ├── profiling/
│   │   ├── taste_trainer.py          # ★ Train taste profile (DINOv2)
│   │   ├── poster_selector.py        # ★ Score & rank candidates
│   │   ├── profiling_utils.py        # Shared: embeddings, color hist, sim
│   │   ├── export_dinov2.py          # Export DINOv2 → ONNX for CoreML
│   │   └── models/                   # Pre-exported ONNX files
│   └── data/                         # Test datasets (2001, dune2, endgame)
└── CLAUDE.md / GEMINI.md             # Project overview docs
```

---

## WHAT'S GREAT — Salvage and Carry Forward

### 1. Goals Document (`goals/Marquee.md`)
**Grade: A+**
- Comprehensive 14-section spec covering every aspect
- Thoughtful data model design (single `media` table, partial indexes, no derived booleans)
- Realistic GPU/compatibility matrix for consumer hardware
- Clear workflow diagrams for initial setup, poster selection, media upgrade, self-healing
- This IS the blueprint for the rebuild. Almost nothing to change here.

### 2. PaddleOCR Filter Script (`experiments/filtering/filter_posters_ocr.py`)
**Grade: A** — This is the core feature and it's well done.
- **Multiprocessed**: 6 workers, shared PaddleOCR instances via `Pool(initializer=...)`. Smart.
- **Three-pass OCR**: Full image → top strip (4K badges) → bottom strip (credits blocks). Each with tuned confidence thresholds.
- **Blocklist approach**: `FORMAT_BLOCKLIST` catches "4k", "uhd", "bluray", "imax", etc. — immediate rejection.
- **Fuzzy matching**: Uses `difflib.get_close_matches` to handle OCR garbling ("odysse" ≈ "odyssey").
- **Token-based filtering**: Title tokens ± director tokens = acceptable. Everything else = rejected.
- **Substring matching**: Catches OCR concatenations like "parttwo" that fail fuzzy matching.
- **Bottom strip upscaling**: 2× LANCZOS resize before OCR for fine-print credits.
- **Salvage recommendation**: Almost entirely reusable. Needs refactoring into a class with dependency injection (movie title, director) rather than globals.

### 3. DINOv2 Profiling Pipeline (`experiments/profiling/`)
**Grade: A** — Professional-grade ML engineering for consumer hardware.
- **ONNX + CoreML**: Exports PyTorch model to ONNX, then runs via onnxruntime with CoreMLExecutionProvider on Apple Silicon. No GPU required.
- **Dual embedding**: CLS token (global composition/style) + mean of patch tokens (local details) concatenated and L2-normalized. 1536-dim for ViT-B.
- **LAB color histogram**: 48-dim L2-normalized color signature. Perceptually uniform color space.
- **80/20 scoring**: DINOv2 visual similarity weighted at 0.8, color at 0.2.
- **Diagnostics**: Mean/std dev of similarity scores, top-5/bottom-5 lists. Smart.
- **External data workaround**: Handles ORT CoreML EP bug for >2GB models.
- **Salvage recommendation**: The ONNX inference approach is excellent. Needs to be adapted if we switch to CLIP (the goals doc specifies CLIP, not DINOv2). Regardless, the pattern (export → ONNX → onnxruntime) is the right one.

### 4. Arr Client Architecture (`backend/app/core/arr_clients/`)
**Grade: A-** — Clean base class pattern.
- `ArrClient` base class with shared `connect()`, `disconnect()`, `_request()` error wrapping
- `RadarrClient` and `SonarrClient` as thin subclasses
- Proper custom exceptions (`ArrConnectionError`, `ArrAuthenticationError`, etc.)
- Stored on `app.state` at startup, injected via helpers
- **Salvage recommendation**: Carry this forward essentially as-is.

### 5. TMDB Client (`backend/app/core/external_databases/tmdb.py`)
**Grade: B+** — Clean and focused.
- `PosterCandidate` dataclass with `url(size=)` method
- Proper Bearer token auth
- Error wrapping with custom exceptions
- Sorted by vote_average descending
- **Salvage recommendation**: Keep the pattern. Extend to also support `/tv/{id}/images` and `/find/{external_id}` endpoints.

### 6. Database Pattern (`backend/app/database.py`)
**Grade: B+** — Good async SQLAlchemy setup.
- Lazy engine creation (no import-time side effects)
- SQLite FK pragma enabled per-connection
- `expire_on_commit=False` to prevent post-commit lazy-load issues
- FastAPI dependency injection pattern (`get_db_session`)
- **Salvage recommendation**: Keep the pattern. The model design needs a redo (see below).

### 7. Config (`backend/app/config.py`)
**Grade: B+** — Standard Pydantic-settings.
- `.env` loading with `env_file_override=False`
- Path mapping for cross-host setups
- Computed properties for config state
- **Salvage recommendation**: Keep the pattern, expand for new features.

---

## WHAT NEEDS TO BE REDONE

### 1. Architecture Disconnect — The Critical Flaw
**Grade: F** — The backend and experiments are completely separate.
- Backend: syncs from Radarr → fetches TMDB posters → downloads them. That's it. No OCR, no dedup, no AI scoring.
- Experiments: has OCR filtering, dedup, taste profiling — but as standalone CLI scripts.
- There is **zero code path** from the backend to the experiment code.
- **Rebuild approach**: The new app needs to be a unified pipeline: sync → fetch → dedup → OCR filter → AI score → select.

### 2. Model Design Violates Its Own Spec
**Grade: D** — The goals doc says:
- Single `media` table with `media_type IN ('movie', 'show')`
- No boolean `has_poster` — derive from `poster_path IS NULL`
- Partial index for missing posters

The actual code has:
- Separate `Movie` and `Series` tables (duplicated schema)
- `Movie.has_poster` boolean (explicitly warned against in the goals doc!)
- No `media_type` column
- No partial index on NULL poster_path
- Series model has HDR tracking fields that belong to a **different project** (MicroManagerr)

### 3. Series Model is Wrong Domain
**Grade: F** — `Series` has fields like `target_hdr`, `target_dv`, `target_imax`, `quality_profile_id`, `monitored`. These are clearly from a different app (media file analysis/tagging). This is dead cruft for Marquee. The goals doc has zero mention of HDR tracking.

### 4. CLIP vs DINOv2 — Undecided
The goals doc specifies **CLIP** for poster embeddings (Section 7). The experiments use **DINOv2**. These are different models with different strengths:
- CLIP: trained on image-text pairs, understands semantic content, language-agnostic visual features
- DINOv2: self-supervised, captures fine-grained visual detail, better for style transfer tasks

For poster taste profiling, either could work. But the goals doc's rationale for CLIP (it "understands composition, color palette, typography style, mood, genre-specific visual language") aligns with the task. Need to decide and commit.

### 5. No OCR Integration
**Grade: F** — The OCR filter (your **primary differentiator**!) only exists as an experiment script. The backend has no awareness of it. In v2, OCR must be a first-class pipeline stage.

### 6. No Deduplication in Backend
**Grade: D** — `ImageFetcher` downloads all posters with zero dedup. `deduplicate_posters.py` exists as a standalone CLI script doing SHA-256 only (no pHash). The goals doc specifies a two-stage pipeline (SHA-256 + pHash) that needs to be integrated.

### 7. No Webhook Endpoint
**Grade: Incomplete** — Goals doc Section 10 specifies `POST /api/webhooks/radarr` and `POST /api/webhooks/sonarr` for real-time media upgrade handling. Not implemented.

### 8. No Poster Cache
**Grade: Incomplete** — Goals doc Section 9 specifies a poster cache keyed by TMDB ID for restoration after upgrades. Not implemented.

### 9. No Web UI
**Grade: N/A** — Not started. The goals doc has a spec for it (Section 11).

### 10. Schemas File is Wrong Domain
`schemas/media.py` contains HDR/Dolby Vision/video codec analysis schemas. This is from a totally different project. None of it is relevant to poster management.

### 11. Inconsistencies
- Port: `CLAUDE.md` says 3113, `GEMINI.md` says 3165, `config.py` says 3165
- README is essentially empty
- Some files reference "MicroManagerr" in comments — a different project entirely

---

## SALVAGE SUMMARY

| Component | Salvage? | Notes |
|---|---|---|
| Goals doc | **YES** (as spec) | Use verbatim as design blueprint |
| PaddleOCR filter | **YES** (refactor) | Multiprocessed 3-pass OCR — needs class wrapper |
| DINOv2 profiling | **MAYBE** | If we use DINOv2 over CLIP, this is mostly reusable |
| ONNX export pattern | **YES** | Proven pattern for consumer GPU inference |
| Arr clients | **YES** (as-is) | Clean base + subclass pattern |
| TMDB client | **YES** (extend) | Add TV endpoints, `/find` endpoint |
| Database setup | **YES** (pattern) | Redo models per goals doc |
| Config | **YES** (extend) | Add new feature configs |
| SHA-256 dedup | **YES** (integrate) | Move from script to pipeline stage |
| Backend models | **NO** | Redesign as single `media` table |
| Series model | **NO** | Remove HDR cruft entirely |
| Schemas | **NO** | Replace with poster-domain schemas |
| ImageFetcher | **PARTIAL** | Keep download logic, integrate dedup + OCR |
| LibraryScanner | **RETHINK** | Merge into unified pipeline |
