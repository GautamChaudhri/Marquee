# Marquee — AI Pipeline Design

Phase 3 design document. Covers the poster pipeline architecture, salvage assessment
from the old project's experiments, and the build plan.

---

## 1. Pipeline Overview

```
POST /api/pipeline/movie/{id}
POST /api/pipeline/series/{id}
POST /api/pipeline/missing (all items with poster_path IS NULL)
        │
        ▼
┌─────────────────────────────────────────────────────────────────┐
│                    POSTER PIPELINE                               │
│                                                                  │
│  ┌──────────────┐    ┌──────────────┐    ┌──────────────┐       │
│  │ Stage 1      │    │ Stage 2      │    │ Stage 3      │       │
│  │ FETCH        │───▶│ DEDUP        │───▶│ OCR FILTER   │       │
│  │ TMDB → local │    │ SHA-256      │    │ PaddleOCR    │       │
│  │ staging dir  │    │ + pHash      │    │ text check   │       │
│  └──────────────┘    └──────────────┘    └──────┬───────┘       │
│                                                  │               │
│                                                  ▼               │
│  ┌──────────────┐    ┌──────────────┐    ┌──────────────┐       │
│  │ Stage 5      │    │ Stage 4      │    │              │       │
│  │ DEPLOY       │◀───│ AI SCORE     │◀───┘              │       │
│  │ write file   │    │ CLIP + LAB   │                    │       │
│  │ update DB    │    │ taste match  │                    │       │
│  └──────────────┘    └──────────────┘                    │       │
│                                                                  │
│  Prerequisites (Stage 0):                                        │
│    export CLIP → ONNX  +  train taste profile → .npz             │
└─────────────────────────────────────────────────────────────────┘
```

Each stage is built, tested, and validated independently before wiring them
together. The orchestrator is the last piece.

---

## 2. Prerequisites — Stage 0

### Stage 0a: CLIP ONNX Export

Adapt the proven pattern from `experiments/profiling/export_dinov2.py`.

**Model:** `openai/clip-vit-base-patch32`
- Input: (1, 3, 224, 224)
- Output: (1, 512) — single pooled image embedding
- Estimated ONNX size: ~150 MB — well under the 2 GB protobuf limit, no external data needed
- Export via TorchScript ONNX exporter (opset 14), do_constant_folding=True
- Verify with ONNX Runtime + CPUExecutionProvider

**Design decisions:**
- CLIP chosen over DINOv2 per the project spec. CLIP's image-text training gives
  it semantic understanding (genre, mood, composition) that aligns better with
  "do I like this poster style?" vs DINOv2's fine-grained visual detail matching.
- ViT-B/32 is the smallest CLIP variant — 512-dim embeddings, fast inference on
  CPU/ANE. If it's too crude for taste differentiation, upgrade to ViT-B/16
  (512-dim, more patches) or ViT-L/14 (768-dim). All configurable via `AI_MODEL`.

**Output:** `marquee/ml/models/clip_vit_b32.onnx`

### Stage 0b: Taste Profile Training

Adapt from `experiments/profiling/taste_trainer.py`.

**Training data:** `experiments/data/training_data/` (430 manually curated posters, one per movie).

**Process:**
1. Scan training directory
2. For each poster: CLIP embedding (512-dim) + LAB color histogram (48-dim)
3. Compute L2-normalized centroid (mean of all vectors, re-normalized)
4. Save `.npz` with: centroid_emb, centroid_color, all individual vectors,
   poster names, model name (for future diagnostics)
5. Print diagnostics: mean/std of similarity scores, top-5/bottom-5 posters

**Diagnostics interpretation:**
- Visual mean > 0.65 → cohesive taste, sharp selections expected
- Visual mean < 0.50 → diverse taste, consider curating training set
- Visual std < 0.10 → consistent style preferences
- Visual std > 0.15 → wide stylistic range in training set

The human inspects top-5/bottom-5 to verify the taste profile makes sense
before accepting it for pipeline use.

**Output:** `marquee/ml/taste_profile.npz`

---

## 3. Stage-by-Stage Design

### Stage 1: Fetch (`PosterFetcher`)

**Source:** TMDB client (already extended in Phase 2 with movie/TV/season endpoints).

**Flow:**
1. Given a Movie (tmdb_id), Series (tvdb_id → resolve tmdb_id), or Season (series tmdb_id + season number)
2. Query TMDB for all poster images
3. Filter by `DEDUP_MIN_POSTER_WIDTH` (default 500px) — TMDB minimum poster width
4. Download each poster candidate to staging directory (`poster_staging_path`)
5. Return list of `PosterCandidate` objects

**PosterCandidate dataclass:**
```python
@dataclass
class PosterCandidate:
    local_path: Path          # downloaded file in staging
    source_url: str           # original TMDB URL
    source: str = "tmdb"
    width: int = 0
    height: int = 0
    vote_average: float = 0.0
    vote_count: int = 0
```

---

### Stage 2: Dedup (`PosterDeduper`)

**Salvage:** `experiments/filtering/deduplicate_posters.py` — SHA-256 code is reusable.

**Two-stage pipeline:**

**Stage 2a — SHA-256 exact dedup:**
- Chunked reading (64KB blocks), identical to old code
- If two files share the same hash, keep the higher-resolution one (larger width×height)
- Cheap, fast, catches identical downloads from different URLs

**Stage 2b — pHash perceptual dedup:**
- Using `imagehash` library (phash)
- Hamming distance ≤ `DEDUP_PHASH_THRESHOLD` (default 6) → near-duplicate
- Among near-duplicates, keep the highest resolution
- Threshold reference: 0 = identical, <6 = near-dupe, 6-10 = similar, >10 = different

**Config:**
- `DEDUP_PHASH_THRESHOLD` (default: 6)
- `DEDUP_MIN_POSTER_WIDTH` (default: 500)

**API:**
```python
class PosterDeduper:
    def deduplicate(self, candidates: list[PosterCandidate]) -> list[PosterCandidate]:
        """Run both dedup stages, return survivors."""
```

---

### Stage 3: OCR Filter (`PosterTextFilter`)

**Salvage:** `experiments/filtering/filter_posters_ocr.py` — 80% reusable. Refactor into a class.

**What it does:** Rejects posters that contain text beyond the movie title and
optionally the director's name. This is Marquee's primary differentiator — most
poster tools don't filter out text-heavy promotional posters.

**3-pass OCR:**
1. **Full image** — confidence ≥ 0.75
2. **Top 18% strip** — catches 4K/UHD badges, format logos, confidence ≥ 0.65
3. **Bottom 18% strip** — credits blocks, **upscaled 2×** (LANCZOS) before OCR,
   confidence ≥ 0.50 (fine print scores lower even when real)

**Text filtering algorithm:**
1. Normalize detected text: lowercase, strip non-alnum, collapse whitespace
2. Hard-reject if any word in `FORMAT_BLOCKLIST`: `{4k, uhd, hdr, bluray, blu, ray, dolby, atmos, imax, dts, hevc, remux, 1080p, 2160p, 720p, ultra, cinerama, panavision, metrocolor}`
3. Subtract TITLE_TOKENS and DIRECTOR_TOKENS from detected words
4. Remove short words (<4 chars, not digits) — OCR noise artifacts
5. Fuzzy match remaining words against allowed tokens (difflib, cutoff 0.70) —
   handles OCR corruption like "odysse" ≈ "odyssey"
6. Substring match: if any remaining word contains a title/director token —
   catches OCR concatenations like "parttwo" containing "part"
7. If nothing remains → ACCEPT. Otherwise → REJECT.

**Digit expansion:** `"2"` → `"two"`, `"3"` → `"three"`, etc. So "Part 2" on a
poster matches "PART TWO" in OCR output.

**Multiprocessing:** Uses `multiprocessing.Pool(spawn)` with `NUM_WORKERS`
workers (configurable via `OCR_WORKERS`, default 6). Each worker initializes its
own PaddleOCR instance to avoid pickling GPU state.

**Improvements over the old code:**
1. **No globals** — state is instance variables, not module-level
2. **Pure function output** — returns `(accepted: bool, text: str)`, doesn't
   copy files to `accepted/`/`rejected/` directories
3. **Media type awareness** — `media_type="movie"` vs `media_type="tv"` for
   future differentiation (TV posters may allow main cast actor names)
4. **No hardcoded defaults** — title and director always explicitly provided

**API:**
```python
class PosterTextFilter:
    def __init__(
        self,
        title: str,
        director: str | None = None,
        *,
        media_type: str = "movie",
        num_workers: int = 6,
    ):
        self.title_tokens = ...
        self.director_tokens = ...

    def filter_batch(
        self, candidates: list[PosterCandidate]
    ) -> tuple[list[PosterCandidate], list[tuple[PosterCandidate, str]]]:
        """Returns (accepted, rejected_with_reasons)."""
        # Uses multiprocessing pool internally

    def is_acceptable(self, image_path: Path) -> tuple[bool, str]:
        """Single-image check. Returns (accepted, normalised_text)."""
```

---

### Stage 4: AI Scoring (`TasteScorer`)

**Salvage:** Pattern from `experiments/profiling/poster_selector.py` + `profiling_utils.py`.

**Two feature vectors per candidate:**
1. **CLIP embedding** (512-dim) — captures visual style, composition, mood, genre
2. **LAB color histogram** (48-dim) — 3 channels × 16 bins, L2-normalized.
   LAB is perceptually uniform — equal distances = equal perceived color differences.
   Extracted via OpenCV (`extract_color_histogram()` — salvaged as-is).

**Scoring formula:**
```
score = 0.8 × cosine_sim(candidate_emb, centroid_emb)
      + 0.2 × cosine_sim(candidate_color, centroid_color)
```

Weights are configurable. The 80/20 split gives visual style primacy while color
palette acts as a meaningful tiebreaker — two posters with similar composition
but different color grading will get different scores.

**API:**
```python
class TasteScorer:
    def __init__(self, profile_path: Path, model_path: Path):
        self.centroid_emb = ...    # (512,) from .npz
        self.centroid_color = ...  # (48,) from .npz
        self.session = ...         # ONNX Runtime session

    def score(self, candidate: PosterCandidate) -> CandidateScore:
        """Extract features and compute taste match score."""

    def rank(
        self, candidates: list[PosterCandidate]
    ) -> list[tuple[CandidateScore, PosterCandidate]]:
        """Score all candidates, return sorted best-first."""
```

**CandidateScore dataclass:**
```python
@dataclass
class CandidateScore:
    final_score: float      # weighted combined
    emb_similarity: float   # CLIP cosine sim
    color_similarity: float # LAB cosine sim
```

---

### Stage 5: Deploy (`PosterDeployer`)

**Flow:**
1. Given the top-ranked `PosterCandidate` and the target entity (Movie/Series/Season)
2. Resolve destination path using `_resolve_poster_path()` (already in sync_service)
3. **Validate** destination path is within `effective_media_roots` before writing
   (this is the "File write path validation" TODO item from Phase 3)
4. Copy poster from staging to destination
5. Compute and store: SHA-256 hash, pHash, CLIP embedding (all in DB)
6. Update DB row:
   - `poster_path` = destination absolute path
   - `poster_source` = "tmdb"
   - `poster_source_url` = original TMDB URL
   - `poster_ai_selected` = True
   - `poster_embedding` = CLIP embedding bytes
   - `poster_sha256` = SHA-256 hash
   - `poster_phash` = perceptual hash
7. Clean up staging directory for this pipeline run

**API:**
```python
class PosterDeployer:
    def __init__(self, db: AsyncSession):
        ...

    async def deploy(
        self,
        candidate: PosterCandidate,
        entity: Movie | Series | Season,
        *,
        series: Series | None = None,
        score: CandidateScore,
    ) -> None:
```

---

## 4. Pipeline Orchestrator

Wires all stages together, runs them in sequence, handles errors per-stage.

```python
class PosterPipeline:
    def __init__(self, db, tmdb, fetcher, deduper, ocr, scorer, deployer):
        ...

    async def process_movie(self, movie_id: int) -> PipelineResult:
        """Run full pipeline for one movie."""

    async def process_series(self, series_id: int) -> PipelineResult:
        """Run for series + all its seasons."""

    async def process_missing(self) -> list[PipelineResult]:
        """Run for all items with poster_path IS NULL."""
```

**PipelineResult:**
```python
@dataclass
class PipelineResult:
    entity_type: str       # "movie" | "series" | "season"
    entity_id: int
    entity_title: str
    status: str            # "deployed" | "no_candidates" | "all_rejected" | "error"
    candidates_fetched: int
    after_dedup: int
    after_ocr: int
    selected_score: float | None
    deployed_path: str | None
    error: str | None
```

---

## 5. Salvage Summary

| Old File | Verdict | Disposition |
|---|---|---|
| `filtering/filter_posters_ocr.py` | **KEEP** | Refactor into `PosterTextFilter` class. Core algorithm and thresholds are proven. |
| `filtering/filter_posters.py` | **THROW AWAY** | Florence-2 experiment — PaddleOCR won. Discard. |
| `filtering/deduplicate_posters.py` | **KEEP** | SHA-256 code reusable as Stage 2a. Add pHash Stage 2b. |
| `profiling/profiling_utils.py` | **PARTIAL** | Keep `extract_color_histogram()`, `cosine_similarity()`, `score_candidate()`, `load_image_safe()`, `scan_images()`. Rewrite `extract_embedding()` and `preprocess_image()` for CLIP. Keep `create_onnx_session()` pattern. |
| `profiling/taste_trainer.py` | **KEEP** | Architecture stays. Swap DINOv2 → CLIP inference. Diagnostics are model-agnostic. |
| `profiling/poster_selector.py` | **KEEP** | Candidate scoring + ranking stays. Swap DINOv2 → CLIP. |
| `profiling/export_dinov2.py` | **PATTERN** | Replicate the ONNX export pattern for CLIP. Dynamic shape handling, CoreML EP, verification. |
| `profiling/models/` | **REFERENCE** | DINOv2 .onnx files are reference only. New CLIP models go in `marquee/ml/models/`. |
| `data/` | **COPIED** | 399MB copied to `experiments/data/`. 2001/dune2/endgame for OCR testing, training_data for taste profile. |

---

## 6. Testing Strategy

Inherited from the old project's "test by running, inspect results, tune" approach.

### Level 1: Unit Tests (automated)
Each stage tested in isolation with small test fixtures.
- Dedup: known duplicate sets, known pHash near-dupes
- OCR: known-clean and known-text-heavy poster pairs
- Scoring: known-good and known-bad embeddings produce correct ranking order

### Level 2: Stage Validation (human-in-the-loop)
Run each stage against real test data from `experiments/data/`. The human
visually inspects results and adjusts thresholds.

- **OCR:** Run on `dune2/` (146 posters) and `endgame/` (248 posters). Inspect
  accepted/rejected splits. Verify no text-heavy posters slip through, no clean
  posters get rejected.
- **Taste profile:** Train on `training_data/` (430 posters). Inspect diagnostics
  — mean similarity, std deviation, top-5/bottom-5 rankings. Do the "most on-taste"
  posters actually look like something you'd pick?

### Level 3: Integration Test
End-to-end pipeline on one movie from test data. Inspect the final selection.
Adjust stage thresholds based on outcome.

### Level 4: Production Validation
After pipeline is wired to API, run on a small batch of real movies. Review each
result before enabling `POST /api/pipeline/missing`.

### The Golden Rule
The human is the final judge. The pipeline produces a ranked list and a top pick.
Until the human is satisfied with quality across a representative sample, the
pipeline does not auto-deploy without review.

---

## 7. Build Order

1. **Stage 0a** — `export_clip.py`: export CLIP ViT-B/32 → ONNX
2. **Stage 0b** — `taste_trainer.py`: train CLIP taste profile, validate diagnostics
3. **Stage 3** — `PosterTextFilter`: refactor PaddleOCR filter class (you confirmed this is proven)
4. **Stage 2** — `PosterDeduper`: SHA-256 + pHash two-stage dedup
5. **Stage 1** — `PosterFetcher`: TMDB download to staging
6. **Stage 4** — `TasteScorer`: CLIP + LAB color scoring
7. **Stage 5** — `PosterDeployer`: write file + update DB
8. **Orchestrator** — `PosterPipeline`: wire all stages, connect API routes

---

## 8. File Layout

```
marquee/
├── pipeline/
│   ├── __init__.py
│   ├── orchestrator.py      # PosterPipeline class
│   ├── fetcher.py            # Stage 1: TMDB → staging
│   ├── deduper.py            # Stage 2: SHA-256 + pHash
│   ├── ocr_filter.py         # Stage 3: PosterTextFilter
│   ├── scorer.py             # Stage 4: TasteScorer
│   └── deployer.py           # Stage 5: write + DB update
├── ml/
│   ├── __init__.py
│   ├── clip_export.py        # Stage 0a: export CLIP → ONNX
│   ├── taste_trainer.py      # Stage 0b: build taste profile
│   ├── color_histogram.py    # extract_color_histogram() (salvaged)
│   ├── embedding.py          # CLIP inference via ONNX Runtime
│   ├── preprocessing.py      # Image preprocessing for CLIP
│   └── models/               # ONNX model files (.gitignored)
└── core/
    └── path_utils.py         # safe_translate_and_validate() (already built)

experiments/
└── data/                     # Test datasets (399 MB, gitignored)
    ├── 2001/                 # 158 posters — Space Odyssey
    ├── dune2/                # 146 posters — Dune Part Two
    ├── endgame/              # 248 posters — Avengers Endgame
    └── training_data/        # 430 curated taste posters
```
