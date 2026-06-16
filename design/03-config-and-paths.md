# Marquee — Path Handling & Config Refinements

Phase 1 design notes — to be implemented before writing Phase 1 code.

---

## 1. Path Translation (Bazarr-style)

### The Problem

Sonarr and Radarr report file paths using **their** view of the filesystem. If Marquee runs in a Docker container with different volume mounts, those paths don't exist on Marquee's filesystem.

**Example:**
- Radarr sees: `/data/media/Movies/Dune (2021)/poster.jpg`
- Marquee sees: `/media/Movies/Dune (2021)/poster.jpg`
- They're the same folder — different mount points.

### The Solution

User configures a prefix mapping. Marquee translates every path from *arr APIs before touching the filesystem.

```bash
# .env
PATH_MAPPING_FROM=/data/media    # what the *arr reports
PATH_MAPPING_TO=/media           # what Marquee sees
```

Result: `/data/media/Movies/Dune` → `/media/Movies/Dune`

### Current Implementation (`config.py`)

Already exists:
```python
def translate_path(self, path: str) -> str:
    if self.path_mapping_configured and path and path.startswith(self.PATH_MAPPING_TO):
        return self.PATH_MAPPING_FROM + path[len(self.PATH_MAPPING_TO):]
    return path
```

**Bug:** The naming is backwards. `PATH_MAPPING_TO` is the *arr prefix (what we're translating FROM), and `PATH_MAPPING_FROM` is the local prefix (what we're translating TO). The variable names should be the other way around, or the logic should swap them. Currently confusing.

**Fix:** Rename for clarity:
```python
ARR_PATH_PREFIX = "/data/media"      # what Sonarr/Radarr reports
LOCAL_PATH_PREFIX = "/media"          # what Marquee sees

def translate_arr_path(self, arr_path: str) -> str:
    """Translate a path from *arr's mount namespace to Marquee's."""
    if not arr_path or not self.arr_path_prefix:
        return arr_path
    if arr_path.startswith(self.arr_path_prefix):
        return self.local_path_prefix + arr_path[len(self.arr_path_prefix):]
    return arr_path
```

---

## 2. Path Traversal Hardening

### Threat Model

The *arr APIs are on the local network and you control them — they're not an attack surface in the traditional sense. But bugs happen, and defense-in-depth costs nothing.

### Hardening Layers

**Layer 1: Resolve canonical path**

`Path.resolve()` eliminates `..`, `.`, and symlinks, giving the true filesystem path:

```python
from pathlib import Path

def safe_resolve(raw_path: str) -> Path:
    """Resolve to canonical path, rejecting traversal attempts."""
    p = Path(raw_path).resolve()
    # Reject if resolution changed the path in suspicious ways
    return p
```

This turns `/media/Movies/../../etc/passwd` into `/etc/passwd`, which we catch in Layer 2.

**Layer 2: Validate against allowed prefixes**

After resolution, verify the path starts with an allowed directory:

```python
ALLOWED_ROOTS = {"/media", "/tvshows"}  # from config

def is_safe_path(path: Path) -> bool:
    """Check that resolved path is within an allowed root."""
    path_str = str(path)
    return any(path_str.startswith(root) for root in ALLOWED_ROOTS)
```

If someone manages to inject a path outside `/media/` or `/tvshows/`, it's rejected here.

**Layer 3: Null byte rejection**

Null bytes in paths are invalid and indicate an attack attempt:

```python
def validate_path(raw: str) -> None:
    if "\0" in raw:
        raise ValueError("Path contains null byte")
```

**Combined guard function:**

```python
def safe_translate_and_validate(arr_path: str) -> Path:
    """Translate a *arr path and validate it's safe to use."""
    if "\0" in arr_path:
        raise ValueError(f"Path contains null byte: {arr_path!r}")

    local = translate_arr_path(arr_path)
    resolved = Path(local).resolve()

    if not any(str(resolved).startswith(root) for root in settings.media_roots):
        raise ValueError(f"Path outside allowed roots: {resolved}")

    return resolved
```

Call this once, at the boundary where *arr data enters the system, and all downstream code works with already-validated paths.

### Where to Apply

| Operation | Risk | Guard |
|---|---|---|
| Reading existing poster file | Low (read-only) | `safe_translate_and_validate()` |
| Writing new poster file | Medium (file creation) | Same + verify parent dir exists |
| Deleting old poster | Medium | Same |
| Scanning media directories | Low | Root validation only |

---

## 3. Database Path Fix

### Problem

```python
DB_URL: str = "sqlite+aiosqlite:///./data/marquee.db"
```

`./data/` resolves relative to the **process working directory**, not the project root. Run uvicorn from `/home/gautam` and the DB file appears there instead of inside the project.

### Fix

Resolve relative to a known anchor — the config file's parent directory:

```python
from pathlib import Path

# In Settings class:
DATA_DIR: str = "data"

@property
def db_url(self) -> str:
    """Absolute database URL, resolved relative to project root."""
    project_root = Path(__file__).parent.parent  # marquee/config.py → project root
    db_path = project_root / self.DATA_DIR / "marquee.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    return f"sqlite+aiosqlite:///{db_path}"
```

Now `DATA_DIR` is always relative to the project, no matter where uvicorn is launched from.

**Similarly, fix cache/staging paths:**

```python
@property
def poster_cache_path(self) -> Path:
    return Path(__file__).parent.parent / self.DATA_DIR / "cache" / "posters"

@property
def poster_staging_path(self) -> Path:
    return Path(__file__).parent.parent / self.DATA_DIR / "staging"
```

---

## 4. Barebones Dedup Config

**Note:** Dedup configuration (`DEDUP_PHASH_THRESHOLD`, `DEDUP_MIN_POSTER_WIDTH`) ended up in `marquee/core/pipeline_config.py` rather than `config.py`. See that file for the full set of pipeline-specific knobs (scorer weights, gate thresholds, OCR settings, extended features, and feedback-loop config).

---

## 5. Additional Config Fields (Phase 3/4+)

The following were added during implementation of the feedback loop, webhooks, self-heal, letterbox, and subtitle features:

### marquee/config.py

| Field | Default | Purpose |
|---|---|---|
| `CORS_ORIGINS` | `["http://localhost:5173", "http://localhost:3000"]` | Allowed origins for CORS (web UI dev servers) |
| `SHUTDOWN_TIMEOUT_SECONDS` | `10` | Max seconds to wait for clients to disconnect during shutdown |
| `FANART_API_KEY` | `None` | Fanart.tv personal API key |
| `TVDB_API_KEY` | `None` | TheTVDB API key (v4) |
| `SYNC_COOLDOWN_SECONDS` | `300` | Minimum seconds between manual sync triggers (rate limit) |
| `POSTER_CACHE_DIR` | `data/cache/posters` | Directory for cached posters keyed by TMDB ID |
| `POSTER_STAGING_DIR` | `data/staging` | Temporary directory for downloaded poster candidates |
| `MOVIE_POSTER_FORMAT` | `poster.jpg` | Filename for movie posters (supports `{movie_basename}`) |
| `SERIES_POSTER_FORMAT` | `poster.jpg` | Filename for TV series posters |
| `SEASON_POSTER_FORMAT` | `season{season:02d}-poster.jpg` | Filename for season posters (`{season}` = season number) |
| `runs_archive_path` | (derived) `data/runs/archive/` | Per-run `pipeline_run.json` copies survive movie re-runs |
| `HEAL_ENABLED` | `True` | Run the periodic self-heal poster existence scan |
| `WEBHOOK_TOKEN` | `None` | When set, webhook requests must carry `?token=<this>` |
| `WEBHOOK_DRY_RUN` | `False` | Log webhook events without touching the filesystem |

### Letterbox settings (all in `config.py`, ~25 fields)

| Field | Default | Purpose |
|---|---|---|
| `LETTERBOX_ENABLED` | `True` | Enable the letterbox crop-detection feature |
| `LETTERBOX_DETECT_METHOD` | `cropdetect` | Detection backend: `cropdetect` (ffmpeg) or `trim` (ImageMagick) |
| `LETTERBOX_TRIM_FUZZ` | `[5, 15, 25]` | Fuzz percentages for the `trim` backend |
| `LETTERBOX_FFMPEG` | `ffmpeg` | ffmpeg binary path/name |
| `LETTERBOX_FFPROBE` | `ffprobe` | ffprobe binary path/name |
| `LETTERBOX_MKVPROPEDIT` | `mkvpropedit` | mkvpropedit binary path/name |
| `LETTERBOX_MKVMERGE` | `mkvmerge` | mkvmerge binary path/name |
| `LETTERBOX_CONVERT` | `convert` | ImageMagick `convert` binary (trim backend only) |
| `LETTERBOX_MOVIE_SAMPLES_MIN` | `5` | Movie sampling start (minutes) |
| `LETTERBOX_MOVIE_SAMPLES_MAX` | `60` | Movie sampling end (minutes) |
| `LETTERBOX_MOVIE_SAMPLE_STEP` | `5` | Minutes between movie samples |
| `LETTERBOX_TV_SAMPLES` | `[5, 10, 15]` | Sample timestamps for TV episodes (minutes) |
| `LETTERBOX_WINDOW_SECONDS` | `2` | cropdetect accumulation window per sample (seconds) |
| `LETTERBOX_CROPDETECT_LIMIT` | `24` | cropdetect black-luma threshold (0-255) |
| `LETTERBOX_CROPDETECT_HDR_LIMIT` | `80` | cropdetect black-luma threshold for HDR/PQ/HLG sources |
| `LETTERBOX_CROPDETECT_ROUND` | `2` | cropdetect dimension rounding (must be even for codecs) |
| `LETTERBOX_NOISE_PX` | `4` | Bars ≤ this many px count as "no bars" |
| `LETTERBOX_MIN_BAR_PX` | `8` | A bar must exceed this to count as a real scope bar |
| `LETTERBOX_AGREE_PX` | `2` | Max spread across samples for High confidence |
| `LETTERBOX_MEDIUM_SPREAD_PX` | `20` | Spread boundary between Medium and Low confidence |
| `LETTERBOX_ASYM_PX` | `2` | Top/bottom asymmetry tolerance before honoring uneven bars |
| `LETTERBOX_EARLY_STOP_WINDOWS` | `3` | Consecutive no-bar samples that trigger early termination |
| `LETTERBOX_MAX_PARALLEL` | `0` | Batch-detect worker count; 0 = auto (cpu_count - 1) |
| `LETTERBOX_AUTO_APPLY_HIGH` | `False` | Opt-in: auto-apply High-confidence detections |
| `LETTERBOX_ASYMMETRIC` | `False` | Honor uneven top/bottom bars instead of forcing symmetry |
| `LETTERBOX_HEAL_ENABLED` | `True` | Run the periodic letterbox tag-drift verification scan |
| `LETTERBOX_HEAL_INTERVAL_MINUTES` | `360` | Minutes between letterbox tag-drift scans |

### marquee/core/pipeline_config.py

All pipeline-specific knobs are in `PipelineSettings` (loaded by `pipeline_config.py`). Key groups:

- **AI/model:** `AI_MODEL`, `EXECUTION_PROVIDER`, `CLIP_BATCH_SIZE`, `CLIP_MODEL_PATH`, `AESTHETIC_MODEL_PATH`, `FACE_MODEL_PATH`, `TASTE_PROFILE_PATH`, `EMBEDDING_CACHE_DIR`
- **Taste/k-NN:** `K_NEIGHBORS`, `KNN_WEIGHTING`, `KNN_SOFTMAX_TEMP`, `TASTE_NEG_WEIGHT`, `PREFERRED_LANG`
- **Extended features:** `DINO_ENABLED`, `EXTRA_QUALITY_ENABLED`, `CALIBRATION_ENABLED`, `CALIBRATION_BANDWIDTH_SCALE`, `SCORER`, `LEARNED_HEAD_PATH`, `ZEROSHOT_AXES_PATH`
- **Scorer weights:** `WEIGHT_KNN_SIM`, `WEIGHT_AESTHETIC`, `WEIGHT_TITLE_COLORFULNESS`, `WEIGHT_FACE_AREA`, `WEIGHT_TEXT_RESIDUAL`, `WEIGHT_PROVENANCE`, `WEIGHT_SHARPNESS`, `WEIGHT_RESOLUTION`, `WEIGHT_LANG_MATCH`, `WEIGHT_DINO_KNN`, `WEIGHT_TASTE_TYPICALITY`, `WEIGHT_QUALITY_ARTIFACTS`, `WEIGHT_OFFICIAL_FAMILY`
- **Gates:** `GATE_MIN_WIDTH`, `GATE_MIN_AESTHETIC`, `GATE_MIN_KNN_SIM`, `GATE_FAN_JUNK_ENABLED`, `GATE_AESTHETIC_RESCUE_KNN`, `GATE_MIN_AESTHETIC_RESCUED`
- **OCR:** `OCR_WORKERS`, `OCR_DETAIL_PASSES`, `OCR_MAX_RESIDUAL_BOXES`, `OCR_MAX_RESIDUAL_AREA_FRACTION`, `OCR_REQUIRE_TITLE`, `OCR_ACCEPT_NO_TEXT`, `OCR_CONFIDENCE_THRESHOLD`, `OCR_FUZZY_CUTOFF`, and others
- **Face detection:** `FACE_CONFIDENCE_THRESHOLD`, `FACE_NMS_THRESHOLD`, `PERSON_CONFIDENCE_THRESHOLD`
- **Normalization:** `NORM_KNN_MIN`/`MAX`, `NORM_AESTHETIC_MAX`, `NORM_TITLE_COLORFULNESS_MAX`, `NORM_OFFICIAL_MIN`/`MAX`, etc.
- **Dedup:** `DEDUP_PHASH_THRESHOLD`, `DEDUP_MIN_POSTER_WIDTH`
- **Feedback loop:** `FEEDBACK_LABELS_PATH`, `NEGATIVE_DATA_DIR`, `TRAINING_DATA_DIR`, `FEEDBACK_GATE_ALERT_THRESHOLD`, `FEEDBACK_NEGATIVES_FROM_OVERRIDES`, `FEEDBACK_DEPLOY_DEFAULT`, `HEAD_MIN_LABELS`, `HEAD_MIN_MOVIES`, `HEAD_AUTO_RETRAIN`
- **Quality:** `QUALITY_BLOCKINESS_SAT`, `QUALITY_NOISE_SAT`, `PROV_PRIOR_MEAN`, `PROV_CONFIDENCE`, `RESIDUAL_COUNT_SAT`
- **Typicality:** `TYPICALITY_FEATURES` (list of 21 feature names for KDE calibration), `CALIBRATION_MIN_SAMPLES`

UI knob overrides layer on top of env/.env via `data/pipeline_overrides.json` (loaded by `load_overrides()` / `save_overrides()`).

---

## Summary of Changes Needed

| File | Change |
|---|---|
| `src/config.py` | Rename path mapping vars for clarity |
| `src/config.py` | Add `db_url` property resolving relative to project root |
| `src/config.py` | Add `poster_cache_path` and `poster_staging_path` properties |
| `src/config.py` | Add `DEDUP_PHASH_THRESHOLD`, `DEDUP_MIN_POSTER_WIDTH` |
| `src/config.py` | Add `MEDIA_ROOTS: list[str]` for path validation |
| New: `src/core/path_utils.py` | `safe_translate_and_validate()` guard function |
| `src/database.py` | Use `settings.db_url` instead of `settings.DB_URL` |

No code yet per your instructions — these are design notes ready for Phase 1 implementation.
