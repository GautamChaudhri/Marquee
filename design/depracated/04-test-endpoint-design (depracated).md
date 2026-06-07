# Test Pipeline Endpoint — Design Reference

Documents the test pipeline endpoint (`POST /api/test/pipeline/movie/{movie_id}`) for use
when designing the new pipeline guts.  Covers the two fixed sections (Stage 1: Fetch/Download
and Stage 5: Sort/Place/Re-download) that will be retained while the middle stages are replaced.

---

## Stage 1 — Fetching & Downloading Posters

### Step 1: DB Lookup

```
movie_id from URL → SELECT Movie WHERE id = movie_id
→ get: title, tmdb_id
```

Returns 404 if movie not found, 400 if DB is empty ("run sync first"), 400 if movie has no tmdb_id.

### Step 2: Create Output Directory

```python
safe_title = _sanitise_filename(movie.title)  # replaces :/<>|\ etc. with _
out_dir = _EXPERIMENTS_DATA / safe_title       # marquee/experiments/ocr-first/Nosferatu/
out_dir.mkdir(parents=True, exist_ok=True)
```

The base path `_EXPERIMENTS_DATA` is `marquee/experiments/ocr-first/`.  Changing this constant
moves all output.  The movie title becomes a subfolder, sanitised for filesystem safety.

### Step 3: Query TMDB

```python
candidates = await tmdb.get_movie_images(movie.tmdb_id)
```

Returns `list[PosterCandidate]` — each has:

| Field | Type | Example |
|---|---|---|
| `file_path` | str | `/gnb54uIjX2M81c6RWPM4uQwulMt.jpg` |
| `width` | int | 2000 |
| `height` | int | 3000 |
| `vote_average` | float | 6.8 |
| `vote_count` | int | 42 |
| `language` | str\|None | "en", null = language-neutral |
| `.url(size)` | method | `https://image.tmdb.org/t/p/w500/gnb54...` |

TMDB image sizes: `w92`, `w154`, `w185`, `w342`, `w500`, `w780`, `original`.

### Step 4: Download All

```python
async with httpx.AsyncClient(timeout=60) as client:
    tasks = [_download_poster(p, out_dir, client) for p in candidates]
    results = await asyncio.gather(*tasks)
```

All downloads are concurrent, limited by `asyncio.Semaphore(5)`.  `_download_poster()`:

1. Extracts filename: `poster.file_path.lstrip("/").split("/")[-1]` → `gnb54uIjX2M81c6RWPM4uQwulMt.jpg`
2. Skips if file already exists → returns `"skipped"` (safe for re-runs)
3. Downloads w500 from TMDB CDN → writes bytes to `out_dir/filename`
4. Returns `"downloaded"` or error string

**Download size:** `w500` (controlled by `_DOWNLOAD_SIZE` constant at top of file).

### Step 5: Build Candidate Map

```python
candidate_map: dict[str, PosterCandidate] = {}
for i, candidate in enumerate(candidates):
    filename = candidate.file_path.lstrip("/").split("/")[-1]
    candidate_map[filename] = candidate
```

Maps filename → `PosterCandidate`.  **Critical for Stage 5** — used to re-download top-5 at
"original" resolution.  Keyed by filename (TMDB basename), not by rank or title.

### Step 6: Gather All Files

```python
all_files = sorted(
    p for p in out_dir.iterdir()
    if p.is_file() and p.suffix.lower() in IMAGE_EXTENSIONS
)
```

At this point `out_dir/` contains ONLY flat poster files — no subdirectories:

```
Nosferatu/
├── 1deeFtyoV1DnFjRcJhkTo9EoeTx.jpg
├── 1mduULGlfJzIGDweVHDQQBt17Nt.jpg
├── ...
└── znqwi99YvZbhPvkE3c4TvZzO5xt.jpg   (50–170 files, all w500)
```

**This is the handoff to the pipeline guts.**  `all_files` is `list[Path]` — the full set of
downloaded posters.  The middle stages consume this list, reduce it through filtering/scoring,
and produce a ranked result that Stage 5 consumes.

---

## Stage 5 — Final Sorting, Placement & Re-download

After all middle stages complete, the final result is `clip_result: ScorerResult` containing
a ranked list of `CandidateScore` objects.

### `CandidateScore` dataclass

```python
@dataclass
class CandidateScore:
    image_path: Path          # path to the w500 poster file
    final_score: float        # weighted combined (emb + color)
    emb_similarity: float     # CLIP cosine sim to taste centroid
    color_similarity: float   # LAB cosine sim to colour centroid
    neg_sim_max: float        # highest cosine sim to any negative prompt
    rejected_by: str | None   # None = accepted, str = rejection reason
```

### Step 1: Create Clip Directories

```python
clip_dir = phash_dir / "clip"           # e.g. Nosferatu/sha256/ocr/phash/clip/
clip_rej_dir = phash_dir / "clip_rejected"  # Nosferatu/sha256/ocr/phash/clip_rejected/
```

These are the final output directories, nested inside the previous stage's accepted folder.

### Step 2: Copy Top-5 with Renaming

```python
for rank, s in enumerate(clip_result.ranked[:5], 1):
    dest = clip_dir / f"{rank}.jpg"
    shutil.copy2(s.image_path, dest)
```

Top 5 survivors are **renamed** to `1.jpg` through `5.jpg`.  The original TMDB filename is lost
in the clip folder but preserved in the JSON response (`original_file` field).

### Step 3: Copy Rank 6+ to Rejected

```python
for s in clip_result.ranked[5:]:
    shutil.copy2(s.image_path, clip_rej_dir / s.image_path.name)
```

Everything ranked 6+ goes to `clip_rejected/` with original TMDB filenames preserved.

### Step 4: Re-download Top 5 at Original Resolution

```python
async with httpx.AsyncClient(timeout=60) as client:
    for rank, s in enumerate(clip_result.ranked[:5], 1):
        orig_filename = s.image_path.name
        poster = candidate_map[orig_filename]       # from Stage 1
        url = poster.url(size="original")
        dest = clip_dir / f"{rank}.jpg"
        resp = await client.get(url)
        dest.write_bytes(resp.content)              # OVERWRITES w500 copy
```

For each of the top 5:
1. Look up filename in `candidate_map` → get `PosterCandidate`
2. Build "original" CDN URL
3. Download full-resolution image
4. **Overwrite** the w500 copy at `1.jpg`–`5.jpg`

After this step, the files in `clip/` are full-resolution originals.  Everything else in the
directory tree (sha256/, ocr/, phash/) remains w500.

### Final Directory Structure

```
experiments/ocr-first/<Movie>/
│
├── <all w500 posters>.jpg               ← Stage 1 flat downloads
│
├── sha256/                              ← survivors from SHA-256 (w500)
│   ├── <survivor>.jpg
│   ├── ocr/                             ← accepted by OCR (w500)
│   │   ├── <accepted>.jpg
│   │   ├── phash/                       ← survivors from pHash (w500)
│   │   │   ├── <survivor>.jpg
│   │   │   ├── clip/                    ★ FINAL OUTPUT ★
│   │   │   │   ├── 1.jpg   ← ORIGINAL SIZE (rank 1)
│   │   │   │   ├── 2.jpg   ← ORIGINAL SIZE (rank 2)
│   │   │   │   ├── 3.jpg   ← ORIGINAL SIZE (rank 3)
│   │   │   │   ├── 4.jpg   ← ORIGINAL SIZE (rank 4)
│   │   │   │   └── 5.jpg   ← ORIGINAL SIZE (rank 5)
│   │   │   └── clip_rejected/           ← rank 6+ (w500, original filenames)
│   │   ├── phash_rejected/              ← removed by pHash
│   │   └── ocr_rejected/                ← rejected by OCR
│   └── sha256_rejected/                 ← removed by SHA-256
```

### Nesting Rule

Each stage's accepted folder is nested inside the *previous* stage's accepted folder.  Each
stage's rejected folder is a **sibling** of its accepted folder at the same nesting level.

```
<parent accepted dir>/
├── <this stage accepted>/        ← next stage processes these files
└── <this stage rejected>/        ← removed files (same level as accepted)
```

---

## Data Handoff Between Stages

### Input to the pipeline guts

| Variable | Type | Description |
|---|---|---|
| `all_files` | `list[Path]` | All downloaded w500 posters (flat, no subdirectories) |
| `out_dir` | `Path` | Root output directory for this movie |
| `movie.title` | `str` | Movie title (sanitised for filesystem) |
| `candidate_map` | `dict[str, PosterCandidate]` | filename → TMDB metadata |
| `candidates` | `list[PosterCandidate]` | Original TMDB API response |

### Output the pipeline guts must produce

A ranked `ScorerResult` (or equivalent) where each `CandidateScore` has:
- `image_path: Path` — pointing to the w500 poster file
- `final_score: float` — the combined score (used for ranking)

The existing Stage 5 code consumes this to produce the final directory structure and JSON
response.  The top 5 from `clip_result.ranked` become `1.jpg`–`5.jpg` and get re-downloaded
at original resolution.

### Constants available

| Constant | Value | Purpose |
|---|---|---|
| `_DOWNLOAD_SIZE` | `"w500"` | Initial download size |
| `_DOWNLOAD_SEMAPHORE` | `Semaphore(5)` | Concurrency limit |
| `_EXPERIMENTS_DATA` | `marquee/experiments/ocr-first/` | Output base path |
