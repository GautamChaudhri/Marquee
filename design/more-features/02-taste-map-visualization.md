# Marquee — 3D Taste Map Visualization

**Status:** Design phase — not yet implemented  
**Date:** 2026-06-12  
**Purpose:** A 3D interactive visualization of the user's taste profile that shows visual style clusters, genre patterns, and how new candidates relate to established taste — useful for understanding the model and debugging rankings.

---

## Table of Contents

1. [What This Shows and Why It's Useful](#1-what-this-shows-and-why-its-useful)
2. [The Data Chain](#2-the-data-chain)
3. [Dimensionality Reduction: UMAP vs PCA](#3-dimensionality-reduction-umap-vs-pca)
4. [Genre Data and Coloring Modes](#4-genre-data-and-coloring-modes)
5. [Automatic Cluster Detection](#5-automatic-cluster-detection)
6. [Candidate Overlay](#6-candidate-overlay)
7. [Interactive Features](#7-interactive-features)
8. [Data Model: What the Taste Profile Needs](#8-data-model-what-the-taste-profile-needs)
9. [API Endpoint Overview](#9-api-endpoint-overview)
10. [Frontend Recommendations](#10-frontend-recommendations)
11. [Open Questions](#11-open-questions)

---

## 1. What This Shows and Why It's Useful

The taste profile contains ~430 CLIP embeddings — each a 512-dimensional vector encoding the visual style of a poster you like. These vectors live in a high-dimensional space where proximity = visual similarity. Two posters with similar composition, color palette, and subject matter are neighbors. Different visual styles occupy different regions.

A 3D projection of this space reveals:

- **The shape of your taste.** Clusters of visually similar posters show what styles dominate. Gaps show styles you have no exemplars for.
- **Genre-blind visual grouping.** Posters cluster by visual style, not by genre. A dark atmospheric drama may sit next to a dark atmospheric horror — the viz shows you what your taste *actually* cares about visually.
- **Debugging rankings.** When a pipeline candidate ranks surprisingly high or low, overlay it on the taste map and see which exemplar cluster it's pulling from.
- **Profile health.** A profile that's all one tight cluster means you like one visual style. A profile with multiple separated clusters means your taste is genuinely multimodal — which validates the k-NN design over a centroid.

---

## 2. The Data Chain

```
taste_profile.npz
    embeddings: (430, 512) ──── CLIP B/32 vectors
    poster_names: (430,) ────── "Movie Title (Year).jpg"
    genres: (430,) ──────────── ["Action", "Science Fiction"]  (new)
    years: (430,) ───────────── [1988, 2021, ...]              (new)
    neg_embeddings: (M, 512) ── optional disliked exemplars
         │
         ▼
    UMAP (n_components=3) ──── 3D coordinates, 1-2 seconds for 430 vectors
         │
         ▼
    HDBSCAN on 3D coords ───── cluster labels per point
         │                              │
         ▼                              ▼
    Combined JSON payload ──→ frontend renders 3D scatter
```

---

## 3. Dimensionality Reduction: UMAP vs PCA

### UMAP (Primary)

UMAP (Uniform Manifold Approximation and Projection) preserves both local neighborhoods and global structure. Two posters that CLIP considers visually similar will be neighbors in the 3D projection. Different taste clusters stay separated. The projection is *meaningful* — proximity in the view actually means visual similarity in CLIP space.

UMAP requires `umap-learn` (pip package). For 430 vectors × 512 dimensions, projection takes ~1-2 seconds on CPU. The result is cached and only recomputed when the taste profile changes.

Parameters are fixed and not user-tunable:
- `n_components=3`
- `n_neighbors=15` (local vs global balance — 15 works well for ~400-500 points)
- `min_dist=0.1` (how tightly points cluster — 0.1 gives visible clusters without smearing)
- `metric='cosine'` (matches CLIP's similarity metric)
- `random_state=42` (deterministic — same profile always produces same visualization)

### PCA (Fallback)

When `umap-learn` is not installed, PCA serves as a zero-dependency fallback. It's fast and deterministic but less informative — clusters may overlap more since PCA optimizes for global variance, not local neighborhood preservation.

### Caching

The 3D coordinates are computed once and stored as a companion file: `taste_map.clip-vit-b-32.npz`. This contains only the 3D coordinates + cluster labels, not the full 512-dim vectors. Regenerated on taste profile rebuild. The API serves this cached data directly.

---

## 4. Genre Data and Coloring Modes

### Where Genre Comes From

The taste profile is built from `experiments/training_data/` — files named `Movie Title (Year).jpg`. To add genre data, the taste trainer looks up each movie on TMDB at profile build time:

1. Parse title and year from filename
2. Look up against the Marquee DB if synced (fast, local)
3. If not in DB, search TMDB API (one-time cost at profile build)
4. Store genres as an array per poster: `["Action", "Thriller", "Science Fiction"]`

This genre data is stored in the `.npz` as a new key `genres` (object array of lists) and `years` (int array). A migration script can retrofit existing profiles with genre data without rebuilding the entire profile (skip the TMDB lookup for any poster already in the DB).

### Coloring Modes

The frontend offers multiple ways to color the points:

| Mode | Data Source | What it shows |
|---|---|---|
| **Genre** (default) | TMDB primary genre per poster | Distribution of genres across taste clusters |
| **Cluster** | HDBSCAN auto-detected | Natural visual style groupings |
| **Year** | Release year | Whether taste skews toward certain eras |
| **Colorfulness** | Hasler-Süsstrunk metric on the poster image | Visual property of the poster itself |
| **Aesthetic score** | LAION head score | Perceived production quality |

Each mode maps a scalar or category to a color, with a legend. Genre uses a fixed palette (action=red, horror=purple, sci-fi=blue, drama=green, etc.). Cluster uses a distinct qualitative palette.

---

## 5. Automatic Cluster Detection

After UMAP reduces to 3D, HDBSCAN (Hierarchical Density-Based Spatial Clustering) detects natural groupings without requiring a pre-set number of clusters.

### How it works

HDBSCAN finds regions of high density and labels them as clusters. Points in sparse regions are labeled as noise (cluster -1). This is important — it means the algorithm doesn't force every point into a cluster. A poster that's genuinely unique in your taste profile stands alone.

### Cluster Naming

Clusters are not manually labeled. Instead, each cluster gets an auto-generated name from its members:

1. Find the most common primary genre in the cluster → gives the "character"
2. Find the most common adjectives in the cluster (from TMDB overviews or just use genre) → gives the "vibe"
3. Example output: "Dark atmospheric horror (23 posters)" or "Vibrant animated family (17 posters)"

If TMDB overview data isn't available, cluster names are purely genre-based: "Horror/Thriller (23 posters)".

### When Clustering is Meaningful

HDBSCAN on 430 points in 3D is reliable. The projection is low-dimensional enough that density-based clustering works well. Larger profiles (1,000+ exemplars from the feedback loop) will produce more defined clusters. Very small profiles (<50) won't have meaningful clusters — the frontend should note this.

---

## 6. Candidate Overlay

The most powerful interactive feature: project active pipeline candidates into the same 3D space and show where they land relative to established taste.

### How it Works

1. UMAP is fit once on the taste profile embeddings. The resulting transform is saved.
2. When a pipeline runs, each candidate poster gets a CLIP embedding (already computed during the style features stage).
3. The candidate embeddings are projected through the SAME UMAP transform — not re-fit. This ensures they land in the same coordinate space.
4. Candidates are rendered as different marker shapes (diamonds or stars) with size proportional to their rank.
5. When the user hovers or clicks a candidate, lines are drawn to its k nearest exemplars — showing WHY it scored the way it did.

### What This Reveals

- A candidate deep inside a dense taste cluster → the model has high confidence, knn_sim will be high.
- A candidate in empty space between clusters → the model is uncertain, knn_sim will be middling.
- A candidate in the negative exemplar region → the junk penalty may fire.
- The top-ranked candidate pulling from a cluster the user doesn't consciously recognize → insight into what the model is picking up on.

### Implementation

The candidate overlay is optional — enabled by a query parameter on the taste map endpoint or a separate endpoint that accepts candidate embeddings and returns projected coordinates. The frontend renders both datasets on the same plot with different marker styles.

---

## 7. Interactive Features

### Point-Level Interactions

| Action | Response |
|---|---|
| Hover | Tooltip: poster thumbnail (small), title, year, genre, cluster name |
| Click | Side panel: full-size poster image, all metadata, feature breakdown if candidate |
| Click + candidate | Draw lines to k nearest exemplars, highlight the neighborhood |
| Double-click | Zoom to this point's cluster |

### Global Controls

| Control | Effect |
|---|---|
| Color by dropdown | Switch between genre, cluster, year, aesthetic, colorfulness |
| Genre toggle checkboxes | Hide/show specific genres → see what remains |
| Cluster toggle | Hide/show clusters → isolate one |
| Show/hide negatives | Toggle disliked exemplar markers |
| Show/hide centroid | Toggle the mean taste direction marker |
| Candidate filter | When candidates are overlaid: show top-5 only, all survivors, or by rank threshold |
| Opacity slider | Adjust point opacity to see density in crowded regions |
| Reset view | Return to default camera angle |
| Export PNG | Download current view as high-res image |
| Export HTML | Download standalone interactive plot |

### 3D Navigation

Standard orbit controls: click-drag to rotate, scroll to zoom, right-click-drag to pan. The initial view should show the projection from an angle that maximizes cluster separation (auto-computed by finding the view with maximum inter-cluster distance).

---

## 8. Data Model: What the Taste Profile Needs

### Current `.npz` Keys

```
embeddings:          (N, 512)  float32
poster_names:        (N,)      str
centroid_emb:        (512,)    float32   (diagnostic only)
model_name:          scalar    str       ("clip-vit-b-32")
neg_embeddings:      (M, 512)  float32   (optional)
neg_poster_names:    (M,)      str       (optional)
```

### New Keys for Visualization

```
genres:              (N,)      list of str   ["Action", "Science Fiction"]
years:               (N,)      int           [1988, 2021, ...]
tmdb_ids:            (N,)      int           [562, 565028, ...]  (optional, for candidate overlay)
```

### Companion File: `taste_map.clip-vit-b-32.npz`

```
coords_3d:           (N, 3)    float32   UMAP projection
cluster_labels:      (N,)      int       HDBSCAN cluster ID (-1 = noise)
cluster_names:       (K,)      str       ["Dark atmospheric horror (23)", ...]
projection_method:   scalar    str       "umap" or "pca"
projection_params:   scalar    str       json dump of UMAP parameters
computed_at:         scalar    str       ISO timestamp
```

This file is regenerated whenever the taste profile changes. The API serves it directly.

---

## 9. API Endpoint Overview

| Endpoint | Purpose |
|---|---|
| `GET /api/taste/map` | Returns 3D coordinates + metadata for all exemplars. Query params: `method=umap\|pca`, `recompute=true` to force recomputation. |
| `POST /api/taste/map/candidates` | Accepts a list of candidate poster paths or embeddings, returns their 3D coordinates projected into the same UMAP space. Used for candidate overlay. |

The map data is cached on disk. Re-computation only happens on taste profile rebuild or when `recompute=true` is passed.

---

## 10. Frontend Recommendations

### Option A: Plotly.js (Recommended for v1)

Plotly's `Scatter3d` trace with `mode='markers+text'` gives you everything out of the box: 3D orbit controls, hover tooltips, click events, color scales, legends. Zero WebGL knowledge needed. Works in any React app.

```javascript
// Concept — Plotly handles 3D, hover, zoom, color scales
const trace = {
  x: coords.map(c => c.x),
  y: coords.map(c => c.y),
  z: coords.map(c => c.z),
  mode: 'markers',
  type: 'scatter3d',
  marker: {
    size: 4,
    color: coords.map(c => genreColor(c.genre)),
    colorscale: 'Viridis'
  },
  text: coords.map(c => `${c.title} (${c.year})`),
  hoverinfo: 'text',
  customdata: coords.map(c => c.poster_url)  // for click → show image
};
```

Pros: 1-2 hours to implement, handles all interactions, good docs. Cons: Plotly's 3D is WebGL-based but not as performant as Three.js for thousands of points. For 400-500 points it's fine.

### Option B: Three.js + Custom Shaders (v2)

If you want sprite-based rendering where each point IS the poster thumbnail (a miniaturized version of the actual poster at each 3D coordinate), Three.js with point sprites is the way. More work but dramatically more impressive — you see actual tiny posters floating in 3D space.

Also enables: animated transitions between coloring modes, particle effects, fly-through camera paths. This is the "wow" version for a polished release.

---

## 11. Open Questions

1. **Rebuild vs retrofit for genres:** Current taste profile has no genre data. Should we rebuild it (run taste_trainer with TMDB lookups) or write a migration script that fills in genres from the existing Marquee DB? The DB has 489 movies — most training posters will match.

2. **UMAP dependency:** `umap-learn` is a pure Python package with numba dependency. Adds ~30MB to the venv. Acceptable, or should PCA be the default and UMAP be optional?

3. **Cluster naming from TMDB overviews:** Auto-naming clusters by mining TMDB overview text for adjectives is clever but fragile. Simpler alternative: name clusters by their dominant genres + member count. "Horror/Thriller (23)" is clear and always correct.

4. **Candidate overlay: per-run or on-demand?** Should the pipeline always compute UMAP projections for every candidate (adds ~50ms per poster to the style features stage), or only when the frontend requests it (separate API call, candidate embeddings are already cached)?

5. **Taste evolution snapshots:** If we save the taste map `.npz` every time the profile is rebuilt, we can show a "time slider" where the user scrubs through history and sees how their taste clusters shifted as they added new exemplars. Worth the storage (~15KB per snapshot × N rebuilds)? This is a v2 feature.

6. **What if the profile is too small for clusters?** A profile with <50 exemplars won't have meaningful HDBSCAN clusters. The frontend should detect this and skip clustering, just showing the points colored by genre with a note: "Add more approved posters to reveal visual taste clusters."

---

# Part 2 — Implementation Plan

**Status:** Implementation-ready design — discussed before build
**Date:** 2026-06-12
**Depends on:** design 09 §20 step 1 (`movies.genres` column) and step 2 (run archive, for candidate overlay). Otherwise fully independent of the other two features.

## 12. Decisions on the Open Questions (§11)

1. **Rebuild vs retrofit for genres** — Retrofit. A standalone script `python -m marquee.ml.profile_enrich` adds `genres`/`years`/`tmdb_ids` keys to the existing `.npz` without touching embeddings: parse `"Title (Year)"` from `poster_names` (the `taste_trainer.title_from_filename` regex, `ml/taste_trainer.py:81`), look up the Marquee DB first (`movies.genres`, synced from Radarr per design 09 §15), fall back to TMDB `/search/movie` for the rest. All lookups are memoized in `experiments/training_data/.genre_cache.json` so future full rebuilds are offline; `taste_trainer.main()` gains the same enrichment step so freshly built profiles carry the keys natively. Unresolvable posters get `genres=[]` and render in a neutral "Unknown" color.
2. **UMAP dependency** — Accept it, as an opt-in extra: `viz = ["umap-learn>=0.5.7", "scikit-learn>=1.5"]` in `pyproject.toml`, included in `all`. HDBSCAN comes from `sklearn.cluster.HDBSCAN` (sklearn ≥1.3) — umap-learn already pulls scikit-learn in, so this adds **zero** packages beyond umap-learn itself. PCA fallback is hand-rolled numpy SVD (no sklearn needed), so the endpoint works even without the extra installed (`projection_method: "pca"` in the response tells the UI to show a "install viz extras for better clustering" hint).
3. **Cluster naming** — Genre-based only: top one or two genres by member share + count → `"Horror/Thriller (23)"`. Overview-text mining is dropped (fragile, needs extra TMDB calls, marginal benefit).
4. **Candidate overlay: per-run or on-demand?** — On-demand API call. Nothing is added to the pipeline. Candidate CLIP embeddings are already persisted per-poster by the style stage in `EMBEDDING_CACHE_DIR` (`pipeline/features.py:532` `_save_cached_embedding`, keyed by `sha256(model:orig_filename)`), so the overlay endpoint loads them from disk by filename — no re-encoding, no pipeline coupling.
5. **Taste evolution snapshots** — Do the cheap half now: every map rebuild archives the previous `taste_map.*.npz` to `data/cache/taste_map_history/{timestamp}.npz` (~15 KB each). The time-slider UI is v2, but the data it needs accumulates from day 1.
6. **Too-small profiles** — Server-side: when N < 50, skip HDBSCAN and return `"clustering": null` plus a `note` string; the frontend renders genre coloring only.

## 13. Projection: k-NN Barycentric Placement Instead of a Persisted UMAP Transform

One deliberate change from Part 1 §6. Persisting a fitted UMAP model means pickling it, and unpickling breaks across umap-learn/numba versions — a footgun on a project that runs on both the Mac and the Fedora box. Instead, candidates are placed by **k-NN barycentric projection**:

1. Compute the candidate's cosine similarities to all exemplars in CLIP space (one matvec — the same operation as `taste_store.query_similar`, `ml/taste_store.py:240`).
2. Take the k=10 nearest exemplars and softmax-weight their similarities with the same temperature used for `knn_sim` (`weighted_topk_mean`'s weighting, `ml/taste_store.py:36`).
3. The candidate's 3D position = the softmax-weighted mean of those exemplars' map coordinates.

This is deterministic, version-proof, needs nothing persisted beyond the coords, and — the real win — **is the geometric picture of the `knn_sim` score itself**: the candidate is drawn at the center of mass of exactly the exemplars that produced its style score, with `neighbor` line data coming free from step 2. A candidate with mixed-cluster neighbors lands between clusters (visually "uncertain"), matching §6's intent. Trade-off: candidates always land inside the convex hull of their neighbors, so a wildly off-taste candidate appears "near" its least-bad neighbors rather than in empty space — the response therefore includes each candidate's raw `knn_sim` and mean neighbor distance so the UI can shrink/fade markers for genuinely distant candidates. (If we ever want true out-of-sample placement, `umap_model.transform()` is the documented alternative; the API shape doesn't change.)

## 14. Artifacts & Modules

**`data/cache/taste_map.{AI_MODEL}.npz`** (derived data lives under `data/`, not next to the profile — it's regenerable and shouldn't ride along in `marquee/ml/`):

```
coords_3d:        (N, 3) float32     coords_2d: (N, 2) float32   ← both stored; 2D/3D is a UI toggle
neg_coords_3d:    (M, 3) float32     (negatives projected barycentrically, like candidates)
cluster_labels:   (N,)   int         cluster_names: (K,) str
poster_names / genres / years / tmdb_ids:  mirrored from the profile for self-containedness
self_knn:         (N,)   float64     each exemplar's CLIP k-NN sim to the rest (outlier signal)
projection_method / projection_params / profile_mtime / computed_at
```

`profile_mtime` is the staleness key: the API rebuilds automatically when the profile file is newer than the map (so feedback-loop exemplar appends from design 09 invalidate it for free).

**`marquee/ml/taste_map.py`** — `build_map(profile_path) -> TasteMap` (UMAP→3D/2D, HDBSCAN, cluster naming, self-knn, archive-previous, atomic save), `load_map()` (with staleness check), `project(embeddings) -> coords + neighbors` (barycentric, §13). Build runs in a thread via the run-manager pattern; 430 points ≈ 2–4 s.

**`marquee/ml/profile_enrich.py`** — the genre retrofit CLI (decision 1).

**Thumbnails** — tooltips need images. `build_map` side-generates `data/cache/taste_thumbs/{poster_name}.webp` (height 192, Pillow, ~5–10 KB each, only for missing ones), served by the exemplar image endpoint. Full-size requests stream the original from `experiments/training_data/`.

**Routes** — added to the shared `marquee/api/routes/taste.py` router (created in design 09 §16.5).

## 15. API Contracts

| Endpoint | Method | Purpose |
|---|---|---|
| `/api/taste/map` | GET | Whole map as JSON (below). `?recompute=true` forces rebuild |
| `/api/taste/map/rebuild` | POST | 202 + progress over the design-09 SSE mechanism |
| `/api/taste/map/candidates` | POST | `{"run_id": "..."}` → overlay coords for that run's candidates |
| `/api/taste/exemplars/{name}/image` | GET | `?size=thumb\|full`; name validated against `poster_names` (no path input) |
| `/api/taste/exemplars/{name}/neighbors` | GET | k nearest exemplars to this exemplar (click-to-explore) |

`GET /api/taste/map` response (sized for ~450 points this is ~150 KB JSON — fine without pagination):

```json
{
  "projection": {"method": "umap", "params": {...}, "computed_at": "..."},
  "points": [{"name": "Die Hard (1988).jpg", "x":.., "y":.., "z":.., "x2":.., "y2":..,
               "genres": ["Action","Thriller"], "year": 1988, "cluster": 2,
               "aesthetic": 5.1, "colorfulness": 41.2, "self_knn": 0.71,
               "thumb_url": "/api/taste/exemplars/Die%20Hard%20(1988).jpg/image?size=thumb"}],
  "negatives": [{"name": ..., "x":.., "y":.., "z":..}],
  "clusters": [{"id": 2, "name": "Horror/Thriller (23)", "size": 23,
                 "genre_breakdown": {"Horror": 14, "Thriller": 9}}],
  "outliers": ["A Ghost Story (2017).jpg", ...],
  "clustering": null_or_above, "note": "..."
}
```

`aesthetic` and `colorfulness` per point come straight from the profile's calibration arrays (`CALIB_VALUES` rows for `aesthetic` / `global_colorfulness` — already measured per exemplar by `taste_trainer.measure_exemplar_features`, `ml/taste_trainer.py:134`), so the §4 coloring modes need no new measurement.

`POST /api/taste/map/candidates` loads the run archive (design 09), reads each survivor's cached CLIP embedding, and returns per candidate: coords, `rank`, `final_score`, `knn_sim`, and `neighbors: [{name, similarity}]` for the hover-lines feature (§6).

## 16. Added Features Worth Having

- **2D mode** — `coords_2d` stored alongside 3D; some users genuinely prefer a flat map, and it's one extra UMAP fit at build time.
- **Outlier list (profile curation)** — exemplars whose `self_knn` falls below the 5th percentile are surfaced in the payload. These are the accidental drags-into-the-folder and one-off experiments; clicking one in the UI shows the image so the user can decide to remove it from `training_data/` and rebuild. This turns the map from a toy into a profile-maintenance tool.
- **Neighbor exploration** — the `/neighbors` endpoint makes every point clickable: "what is this poster's taste neighborhood?"
- **Negatives layer** — `negative_data/` exemplars projected and returned separately, rendered as X markers, toggleable (§7's control already lists it).
- **History archive** — decision 5; slider UI deferred.

## 17. Frontend Notes (deferred, recorded for the UI build)

Plotly.js `scatter3d` for v1 exactly as §10 recommends (genre/cluster/year/aesthetic/colorfulness color modes all map to `marker.color` swaps on the same trace; hover thumbnails via a custom hover DOM overlay, since Plotly tooltips can't embed images directly). Camera auto-orientation (§7) and PNG export are Plotly built-ins. Three.js poster-sprite mode stays v2.

## 18. Build Order & Verification

1. **Genre plumbing** — `movies.genres` sync (shared foundation) + `profile_enrich.py`. *Verify:* run enrichment against the live 430-poster profile; spot-check 10 titles; confirm cache file makes a second run hit zero TMDB calls.
2. **`taste_map.py` build + map/exemplar endpoints** — *Verify:* build on the real profile (CPU, seconds); assert determinism (two builds → identical coords); sanity-check clusters against known taste groups; PCA fallback path with umap-learn uninstalled (`pip uninstall` in a throwaway venv or monkeypatched import).
3. **Candidate overlay** — *Verify:* run a movie through the pipeline, overlay its candidates, confirm the #1-ranked candidate's drawn neighbors match the `STYLE FEATURES` log's knn behavior, and that an off-style gated candidate lands far from dense clusters with low `knn_sim` in the payload.
4. **Thumbs + neighbors + outliers** — *Verify:* thumbnail generation idempotence; outlier list eyeball test (they should look like the odd ones out).

Tests that need the real profile/models are marked like the existing ML-dependent pipeline tests (`tests/test_pipeline_revised.py` pattern — skip without ML extras); the barycentric math, staleness check, and JSON shaping get pure-numpy unit tests with synthetic embeddings.
