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

Needed for the pipeline's deduplication stage. Keep it minimal — we add what we'll actually use in Phase 3.

```python
# ------------------------------------------------------------------
# Deduplication
# ------------------------------------------------------------------
DEDUP_PHASH_THRESHOLD: int = Field(
    default=6,
    description="Hamming distance ≤ N means visual near-duplicate. "
                "0 = identical, <6 = near-duplicate, 6-10 = similar, >10 = different.",
)
DEDUP_MIN_POSTER_WIDTH: int = Field(
    default=500,
    description="Skip poster candidates narrower than this (in pixels). "
                "TMDB minimum poster width is 500px.",
)
```

**What these control:**
- `DEDUP_PHASH_THRESHOLD=6`: Two posters with a perceptual hash distance ≤ 6 bits are considered duplicates. The higher-resolution one is kept.
- `DEDUP_MIN_POSTER_WIDTH=500`: TMDB doesn't serve posters below 500px. Set this as a floor — if an API returns a thumbnail, skip it entirely.

That's all the dedup config needs for now. SHA-256 exact dedup has no configurable threshold (it's binary: match or don't). The pipeline code will use these values directly.

---

## 5. Additional Config Fields (Phase 3/4)

The following were added during implementation of the feedback loop, webhooks, and self-heal features:

### marquee/config.py

| Field | Default | Purpose |
|---|---|---|
| `runs_archive_path` | (derived) `data/runs/archive/` | Per-run `pipeline_run.json` copies survive movie re-runs |
| `HEAL_ENABLED` | `True` | Run the periodic self-heal poster existence scan |
| `WEBHOOK_TOKEN` | `None` | When set, webhook requests must carry `?token=<this>` |
| `WEBHOOK_DRY_RUN` | `False` | Log webhook events without touching the filesystem |

### marquee/core/pipeline_config.py

| Field | Default | Purpose |
|---|---|---|
| `FEEDBACK_LABELS_PATH` | `marquee/experiments/feedback/labels.jsonl` | Append-only JSONL of user feedback labels |
| `NEGATIVE_DATA_DIR` | `marquee/experiments/negative_data/` | Disliked exemplars directory |
| `TRAINING_DATA_DIR` | `marquee/experiments/training_data/` | Positive exemplars directory |
| `FEEDBACK_GATE_ALERT_THRESHOLD` | `5` | Gate override count before surfacing a tuning suggestion |
| `FEEDBACK_NEGATIVES_FROM_OVERRIDES` | `False` | Copy overridden auto-pick to negative data dir |
| `FEEDBACK_DEPLOY_DEFAULT` | `True` | Approve/override deploys the selected poster |
| `HEAD_MIN_LABELS` | `150` | Minimum labels to activate learned head |
| `HEAD_MIN_MOVIES` | `5` | Minimum distinct movies with labels |
| `HEAD_AUTO_RETRAIN` | `True` | Auto-retrain learned head after each feedback event |

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
