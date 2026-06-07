# Technical Deep-Dive: What to Keep, What to Fix

## PART 1: The PaddleOCR Filter — Deep Analysis

File: `experiments/filtering/filter_posters_ocr.py` (326 lines)

### What It Does
1. Loads PaddleOCR with English language model
2. Spawns 6 multiprocessing workers, each with its own PaddleOCR instance
3. For each poster image, runs THREE OCR passes:
   - **Full image**: All text, filtered at confidence ≥ 0.75
   - **Top strip** (top 18%): 4K/UHD badges, format logos, confidence ≥ 0.65
   - **Bottom strip** (bottom 18%): Credits blocks, upscaled 2×, confidence ≥ 0.50
4. Concatenates all detected text → checks against `text_is_title_only()`
5. Copies to `accepted/` or `rejected/`

### Text Filtering Algorithm
```
normalise(text):
  → lowercase, strip non-alnum, collapse whitespace

text_is_title_only(detected_text):
  1. If empty → ACCEPT (text-free poster)
  2. Hard-reject if FORMAT_BLOCKLIST hit (4k, uhd, bluray, imax, etc.)
  3. Split into words, subtract TITLE_TOKENS + DIRECTOR_TOKENS
  4. Remove words < 4 chars (OCR noise) UNLESS they're digits
  5. Fuzzy match remaining against allowed tokens (cutoff 0.70)
  6. Substring match: if any remaining word contains a title/director token
  7. If nothing remains → ACCEPT. Otherwise → REJECT.
```

### Strengths
- Multiprocessing is correctly implemented with spawn context
- Three-pass approach handles different text regions with appropriate confidence thresholds
- Bottom strip upscaling is clever — fine-print credits are hard to OCR at native resolution
- Fuzzy matching handles OCR corruption gracefully
- Blocklist catches format badges even when OCR misreads them

### Weaknesses / What to Change for v2
1. **Globals for state**: `TITLE_TOKENS`, `DIRECTOR_TOKENS`, `MOVIE_TITLE` are module-level globals mutated at runtime. Should be instance variables.
2. **Hardcoded Dune test data**: Default MOVIE_TITLE is "dune part 2". Needs to be parameterized per-movie.
3. **No film/TV differentiation**: Title tokenization is identical for movies and shows. Shows have season/episode text that might be on posters.
4. **Format blocklist is English-only**: International releases might have format text in other languages.
5. **No per-movie confidence tuning**: The thresholds are fixed. Some movies have posters with very faint text.
6. **Bottom strip fraction is fixed at 18%**: Some posters have taller/shorter credit blocks.

### Refactoring Target
```python
class PosterTextFilter:
    def __init__(self, movie_title: str, director: str | None = None):
        self.title_tokens = self._normalise(movie_title).split()
        self.director_tokens = self._normalise(director).split() if director else set()
        # ... configurable thresholds ...
    
    def is_acceptable(self, image_path: Path) -> tuple[bool, str]:
        """Returns (accepted, detected_text)"""
        ...
```

---

## PART 2: DINOv2 Profiling Pipeline — Deep Analysis

### Export Pipeline (`export_dinov2.py`)
- ViT-B/14: ~330 MB ONNX, 1536-dim combined embedding
- ViT-G/14: ~4 GB ONNX, 3072-dim combined embedding
- Handles protobuf 2GB limit with dynamo exporter + external data
- CoreML EP bug workaround (pre-loading external tensors)
- Static 518×518 input shape (required by CoreML)

### Embedding Extraction (`profiling_utils.py`)
```python
extract_embedding(session, pixel_values):
  cls_token    = outputs[0]  # (1, 768) — global composition
  patch_tokens = outputs[1]  # (1, 1369, 768) — per-patch features
  patch_mean   = patch_tokens.mean(axis=1)  # (1, 768)
  combined     = concat([cls_token, patch_mean], axis=1)  # (1, 1536)
  return L2_normalize(combined)
```

### Color Histogram (`profiling_utils.py`)
- LAB color space (perceptually uniform)
- 16 bins per channel → 48-dim vector
- L2-normalized for cosine similarity

### Scoring (`profiling_utils.py`)
```python
score = 0.8 * cosine_sim(emb, centroid_emb) + 0.2 * cosine_sim(color, centroid_color)
```

### Taste Training (`taste_trainer.py`)
- Scans directory of curated posters → extracts embedding + color for each
- Computes centroid = mean of all vectors, re-normalized
- Saves to .npz with all individual vectors (for future analysis)

### Strengths
- ONNX + CoreML path works great on Apple Silicon without PyTorch at inference time
- Dual embedding (CLS + patch mean) captures both composition and detail
- Color histogram adds palette awareness
- Diagnostics help evaluate profile quality

### Decision Point: CLIP vs DINOv2
The goals doc specifies **CLIP**. The experiments use **DINOv2**. 

**CLIP** advantages for poster taste:
- Trained on image-text pairs → understands semantic content (genre, mood)
- Better at "is this a minimalist poster?" type questions
- Can use text prompts for zero-shot filtering ("clean poster, no taglines")
- Smaller models available (ViT-B/32: 512-dim, runs on anything)

**DINOv2** advantages:
- Better at fine-grained visual detail
- Self-supervised → might capture style better
- Already exported to ONNX with CoreML working

**My recommendation**: Use **CLIP** as specified in the goals doc. DINOv2's fine-grained detail might rank posters by visual similarity to training set rather than aesthetic preference. CLIP's semantic understanding aligns better with "do I LIKE this poster style?" vs "does this look like my other posters?"

The ONNX export pattern from DINOv2 should be replicated for CLIP.

---

## PART 3: Backend — What to Redesign

### Model Layer (Complete Rewrite)

Current (WRONG):
```python
class Movie(Base):  # separate table
    has_poster: bool  # derived state as stored column
    clip_selected: bool  # unnecessary boolean
    manually_approved: bool
    manually_selected: bool

class Series(Base):  # separate table with HDR fields
    target_hdr: bool  # wrong domain entirely
```

Target (per goals doc):
```python
class Media(Base):
    __tablename__ = "media"
    id: int (PK)
    title: str
    tmdb_id: int (UNIQUE)
    tvdb_id: int?
    imdb_id: str?
    media_type: str  # 'movie' | 'show'
    file_path: str
    identified_by: str  # 'radarr' | 'sonarr' | 'standalone'
    radarr_id: int?
    sonarr_id: int?
    poster_path: str?      # NULL = needs poster
    poster_source: str?    # 'tmdb' | 'fanart' | 'tvdb' | 'tvmaze'
    poster_url: str?
    ai_selected: bool      # did AI pick this?
    embedding: bytes?      # CLIP embedding
    sha256: str?
    phash: str?
    created_at, updated_at: datetime
```

Plus `poster_candidates` table for caching fetched candidates.

### Pipeline (Currently Non-Existent)

The new pipeline should be a single orchestrator:

```
SyncService.sync_from_arr()
    ↓
PosterPipeline.process(media_id):
    1. FetchStage → query TMDB, Fanart, TVDB, TVmaze
    2. DedupStage → SHA-256 dedup + pHash near-dedup
    3. OCRFilterStage → PaddleOCR text check
    4. AIStage → CLIP embedding + taste similarity scoring
    5. SelectStage → pick highest scorer
    6. DeployStage → save to media folder + poster cache
```

### API Routes (Expand)

Current:
```
POST /api/sync/movies
POST /api/posters/fetch/{movie_id}
POST /api/library/scan/posters
GET  /health
```

Target:
```
POST /api/sync/all             # full sync (movies + shows)
POST /api/process/{media_id}   # run full pipeline for one item
POST /api/process/missing      # process all items needing posters
POST /api/webhooks/radarr      # webhook receiver
POST /api/webhooks/sonarr      # webhook receiver
GET  /api/library              # list all media
GET  /api/library/{id}         # media detail
GET  /api/library/{id}/posters # poster candidates with scores
POST /api/library/{id}/posters/select  # manual override
GET  /api/settings             # config
PUT  /api/settings             # update config
GET  /api/taste/profile        # taste profile diagnostics
POST /api/taste/train          # rebuild taste profile
GET  /health
```

---

## PART 4: Security Considerations for v2

1. **API keys in env vars only** — No hardcoded keys (already done, keep this)
2. **File path traversal** — Sanitize ALL paths that touch the filesystem. `movie.folder_path` could be anything from Radarr.
3. **Image processing safety** — PIL.Image is generally safe, but validate file extensions before opening. No arbitrary file reads.
4. **Webhook validation** — Consider HMAC signing for Radarr/Sonarr webhooks if supported.
5. **Path mapping** — The `translate_path()` function writes to user's filesystem. Validate mapped paths exist and are within expected directories.
6. **SQL injection** — SQLAlchemy ORM prevents this naturally. Don't use raw SQL strings.
7. **No external network for AI** — All models run locally. No data leaves the machine. This is a selling point — emphasize it.
8. **Rate limiting** — External API calls to TMDB/Fanart already have semaphore-based concurrency control (5 concurrent downloads). Extend to API query rate limits.

---

## PART 5: Technology Choices for v2

| Component | Current | v2 Recommendation | Rationale |
|---|---|---|---|
| Language | Python 3.13 | Python 3.12+ (stable) | 3.13 works but some ML libs lag |
| Backend | FastAPI | FastAPI | Solid choice, async-first |
| Database | SQLite + aiosqlite | SQLite + aiosqlite | Right for scale (personal library) |
| ORM | SQLAlchemy 2.0 async | SQLAlchemy 2.0 async | Modern, typed mappings |
| OCR | PaddleOCR (experiment) | PaddleOCR (integrated) | Works well on CPU, no GPU needed |
| Embeddings | DINOv2 (experiment) | CLIP ViT-B/32 or ViT-L/14 | Goals doc specifies CLIP |
| Vision model | None | Qwen2.5-VL 3B (optional) | For descriptive analysis of top candidates |
| Image hashing | SHA-256 only | SHA-256 + pHash (imagehash lib) | Two-stage dedup per goals doc |
| HTTP client | httpx | httpx | Async, well-maintained |
| Config | Pydantic-settings | Pydantic-settings | Keep |
| Packaging | Manual | Docker + Compose | Per goals doc |
| Tests | None | pytest + pytest-asyncio | TDD from start this time |
