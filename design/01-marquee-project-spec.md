# Marquee

## AI-Powered Media Artwork Manager

Marquee is a self-hosted, Dockerized application that intelligently manages movie and TV show poster artwork. It fetches poster candidates from multiple free databases, uses AI to learn the user's aesthetic preferences from their existing poster choices, and automatically selects the best poster for new media. It integrates with the *arr ecosystem (Radarr/Sonarr) to detect upgrades and automatically restore artwork that would otherwise be lost.

---

## Table of Contents

1. [Project Overview](#1-project-overview)
2. [Architecture Overview](#2-architecture-overview)
3. [Free Artwork Databases](#3-free-artwork-databases)
4. [Media Scanner and Identification](#4-media-scanner-and-identification)
5. [Image Fetching Pipeline](#5-image-fetching-pipeline)
6. [Image Deduplication](#6-image-deduplication)
7. [AI Preference Learning Engine](#7-ai-preference-learning-engine)
8. [Database Design](#8-database-design)
9. [Poster Caching and Restoration](#9-poster-caching-and-restoration)
10. [Radarr/Sonarr Integration](#10-radarrsonarr-integration)
11. [Web UI](#11-web-ui)
12. [Docker Deployment](#12-docker-deployment)
13. [GPU and Model Compatibility](#13-gpu-and-model-compatibility)
14. [Complete Workflow](#14-complete-workflow)

---

## 1. Project Overview

### Problem Statement

Current media managers like MediaElch and tinyMediaManager fetch poster artwork from online databases but offer no intelligent selection. Users must manually browse dozens of poster variants and pick one. Additionally, when Radarr or Sonarr upgrades a movie or show, the entire folder is deleted and recreated, destroying any custom artwork the user had chosen. The user must then manually re-select posters all over again.

### Solution

Marquee solves both problems:

1. It analyzes the user's existing poster choices to build a visual preference profile, then automatically ranks and selects posters for new media based on learned preferences.
2. It maintains a local cache of all selected posters keyed by TMDB ID, and integrates with Radarr/Sonarr via webhooks to automatically restore posters after media upgrades.

### Core Principles

- Fully self-hosted and deployed via Docker.
- Runs AI models locally on consumer GPUs (no cloud API dependencies for inference).
- Supports a range of hardware from Intel Arc A310 (6GB VRAM) to RTX 3070+ (8GB+ VRAM).
- Uses only free artwork databases — no paid subscriptions required.
- Integrates cleanly with the existing *arr ecosystem and media servers (Plex, Jellyfin, Emby).

---

## 2. Architecture Overview

Marquee consists of four major components:

### 2.1 Media Scanner and Identifier

Supports two identification modes. The preferred mode syncs directly with Radarr and Sonarr via their REST APIs, pulling fully resolved metadata (TMDB IDs, IMDB IDs, TVDB IDs, file paths) with zero ambiguity. The fallback standalone mode scans media directories and identifies content from filenames using guessit, resolving IDs via TMDB search. Both modes can run simultaneously, with *arr sync taking priority.

### 2.2 Image Fetcher and Deduplicator

Queries multiple free artwork databases (TMDB, Fanart.tv, TheTVDB, TVmaze) to retrieve all available poster candidates for each title. Deduplicates results using a two-stage hashing pipeline (SHA-256 for exact matches, perceptual hashing for visual near-duplicates) before passing unique candidates to the AI engine.

### 2.3 AI Preference Learning Engine

Uses CLIP (Contrastive Language-Image Pretraining) to generate image embeddings for all posters. Learns the user's visual preferences by analyzing their existing poster choices. Ranks new poster candidates by similarity to the user's preference profile. Optionally uses a small Vision Language Model (VLM) for deeper analysis of top candidates.

### 2.4 Poster Cache and Restoration System

Maintains a local cache of all selected posters. Listens for Radarr/Sonarr webhook events to detect media upgrades. Automatically restores cached posters to new folder paths after upgrades. Runs periodic filesystem scans as a self-healing safety net.

### 2.5 Web UI

A web-based frontend that displays the media library, shows poster candidates in a grid, highlights the AI's pick, and allows manual overrides. Each override feeds back into the preference model.

### 2.6 Backend API

A Python FastAPI backend serving all API endpoints, orchestrating scans, managing the database, handling webhooks, and interfacing with the AI inference pipeline.

---

## 3. Free Artwork Databases

All databases below are free to use. Some require a free API key obtained by registering an account. The developer registers once and embeds the API key in the application — end users never need to register for anything. This is exactly how MediaElch, tinyMediaManager, Plex, and Jellyfin operate: they ship with their own API keys hardcoded into the application.

### 3.1 Fully Free with API Access

#### TMDB (The Movie Database) — Primary Source

- **URL**: `api.themoviedb.org`
- **Content**: Movies and TV shows
- **API Key**: Free, register an account at themoviedb.org
- **Poster Variety**: Excellent — many titles have dozens of poster variants in multiple languages
- **Image Specs**: Posters must be at least 500×750px, max 2000×3000px, supported aspect ratios 1:1.33, 1:1.41, 1:1.5
- **Artwork Types**: Posters, backdrops, logos
- **Resolution Options**: Multiple sizes available (w185, w342, w500, w780, original)
- **Image CDN**: `https://image.tmdb.org/t/p/{size}/{poster_path}`
- **Rate Limits**: Generous for personal use
- **Used By**: MediaElch, tinyMediaManager, Plex, Jellyfin, Emby, Kodi, and virtually every other media manager
- **Priority**: #1 — this is the single most important source and will provide the bulk of poster candidates

#### Fanart.tv — Secondary Source

- **URL**: `webservice.fanart.tv`
- **Content**: Movies, TV shows, and music
- **API Key**: Free, register at fanart.tv
- **Poster Variety**: Great — provides unique artwork types that TMDB does not carry
- **Artwork Types**: Posters, backgrounds/fanart, HD logos, clear art, banners, thumbnails, disc images, movie art
- **Querying**: Movies by TMDB ID or IMDB ID; TV shows by TheTVDB ID
- **VIP Access**: Optional paid tier for faster access; free tier works fine for personal use
- **Used By**: MediaElch, tinyMediaManager, Jellyfin (via plugin), Kodi
- **Priority**: #2 — complements TMDB with artwork types like clearlogos, disc art, and HD movie logos

#### TheTVDB — TV Show Supplementary Source

- **URL**: `api4.thetvdb.com`
- **Content**: TV shows only
- **API Key**: Free, register at thetvdb.com and obtain a PIN/key
- **Poster Variety**: Good for TV content
- **Artwork Types**: Series posters, season posters, episode thumbnails, banners, backgrounds
- **Artwork Type IDs**: Maps TVDB artwork type IDs to standard artwork types (series artwork, season banners/posters, episode-specific images)
- **Used By**: MediaElch, tinyMediaManager, Jellyfin, Plex, Kodi
- **Priority**: #3 — important supplementary source specifically for TV shows

#### TVmaze — Zero-Friction Fallback

- **URL**: `api.tvmaze.com`
- **Content**: TV shows only
- **API Key**: None required for basic usage (no registration needed)
- **Poster Variety**: Limited — typically one primary poster per show plus a handful of alternatives
- **Artwork Types**: Posters, banners, backgrounds, typography/logos
- **Image Access**: Provides "medium" (resized) and "original" resolution URLs; hotlinking to their CDN is explicitly permitted; images can be cached indefinitely
- **License**: CC BY-SA (must credit TVmaze)
- **Used By**: tinyMediaManager (via TVmaze scraper)
- **Priority**: #4 — great as a no-auth fallback, especially for lesser-known shows

#### OMDb (Open Movie Database) — Single-Poster Fallback

- **URL**: `omdbapi.com`
- **Content**: Movies and TV shows
- **API Key**: Free, register at omdbapi.com
- **Poster Variety**: Very limited — returns only one poster per title
- **Free Tier Limits**: 1,000 requests/day
- **Rate Limits**: tinyMediaManager limits to 10 calls per 15 seconds with its shared key; patron tier allows more
- **Used By**: tinyMediaManager
- **Priority**: Low — only returns a single poster, so not useful for the "choose between options" use case; suitable as an absolute last-resort fallback
- **Note**: Free for non-commercial purposes

#### AniDB — Anime Source

- **URL**: `anidb.net`
- **Content**: Anime only
- **API Key**: Free, registration required
- **Poster Variety**: One primary poster per anime title
- **API Protocol**: Uses UDP-based communication for some features (more complex to integrate than REST APIs)
- **Rate Limits**: Strict rate limiting
- **Used By**: tinyMediaManager, Jellyfin (via plugin)
- **Priority**: Conditional — only relevant if the user has anime in their library

#### Kitsu — Anime Source (Easier Alternative)

- **URL**: `kitsu.io/api`
- **Content**: Anime and manga
- **API Key**: None required
- **API Protocol**: Clean JSON:API REST interface (much easier to work with than AniDB)
- **Poster Variety**: Cover/poster images for anime titles
- **Used By**: Jellyfin (via plugin)
- **Priority**: Conditional — preferred over AniDB for ease of integration if anime support is needed

### 3.2 Free to Browse, No Free Programmatic API

#### ThePosterDB (TPDb)

- **URL**: `theposterdb.com`
- **Content**: Community-curated, fan-made poster sets for movies, TV shows, and collections
- **API Status**: No public search API. TPDb does not permit automated scraping. Tools that pull from TPDb work by parsing saved HTML files or using direct asset URLs (`theposterdb.com/api/assets/{id}`) obtained from manually browsing the site.
- **Pro Tier**: Adds advanced searching capabilities but still no proper programmatic API
- **Metadata**: Uses TMDB API internally for its metadata
- **Quality**: Often the most aesthetically curated posters available; highly popular among Plex/Jellyfin enthusiasts
- **Verdict**: Cannot be programmatically integrated for automated poster fetching. Would only work if the user manually curated sets and fed URLs in. Not viable for Marquee's automated workflow.

#### MediUX

- **URL**: `mediux.pro`
- **Content**: High-quality, professionally designed poster sets, title cards, backdrops
- **API Status**: Beta API access reportedly available (as of March 2025 GitHub issues) but not a public/free API for general use. Existing tools (AURA, scrape-mediux) work by scraping the website with Selenium using login credentials — not through an official API.
- **Integration Tools**: AURA (official visual tool for Plex/Emby/Jellyfin), mediux-posters (Python CLI), scrape-mediux (Selenium scraper)
- **Quality**: Professional-grade artwork; very popular in the self-hosting community
- **Verdict**: No reliable free programmatic API currently available. Monitor for future API availability — if they open a public API, this would be a fantastic addition.

### 3.3 Paid Only (Not Applicable)

#### RPDB (Rating Poster Database)

- **URL**: `ratingposterdb.com`
- **Pricing**: Paid plans starting at $2/month via Patreon; no free tier
- **Purpose**: Overlays rating badges (IMDB, TMDB, Trakt, RT, etc.) onto existing posters — does not provide a gallery of poster options
- **Verdict**: Not useful for Marquee's use case (poster selection) and not free. Excluded.

#### MPDb.tv

- **Purpose**: French movie database; private/paid scraper requiring abo key and username
- **Verdict**: Not free, not relevant. Excluded.

### 3.4 Summary Table

| Source | Content | API Key Required? | Poster Variety | Priority |
|---|---|---|---|---|
| **TMDB** | Movies + TV | Yes (free registration) | Excellent (dozens per title) | #1 Primary |
| **Fanart.tv** | Movies + TV + Music | Yes (free registration) | Great (unique art types) | #2 Secondary |
| **TheTVDB** | TV only | Yes (free registration) | Good | #3 TV supplement |
| **TVmaze** | TV only | No | Limited (1-5 per show) | #4 Fallback |
| **OMDb** | Movies + TV | Yes (free registration) | Single poster only | #5 Last resort |
| **AniDB** | Anime only | Yes (free registration) | Limited | Conditional |
| **Kitsu** | Anime only | No | Limited | Conditional |

TMDB + Fanart.tv alone will cover 90%+ of poster needs. TheTVDB adds extra TV coverage. TVmaze provides a zero-friction fallback.

---

## 4. Media Scanner and Identification

Marquee supports two identification modes. The user selects their preferred mode during setup, or can enable both simultaneously (with *arr sync taking priority where overlap exists).

### 4.1 Mode 1: Radarr/Sonarr API Sync (Preferred)

This is the preferred mode for anyone running the *arr stack. Radarr and Sonarr have already done all the identification work — they know every movie and show's TMDB ID, IMDB ID, TVDB ID, file paths, quality profile, and more. There is zero ambiguity, no filename parsing heuristics, no guessing. Radarr already matched `Interstellar.2014.2160p.UHD.BluRay.x265` to TMDB ID 157336. Marquee just asks for it.

#### 4.1.1 Radarr API

- **Endpoint**: `GET /api/v3/movie`
- **Authentication**: API key (found in Radarr → Settings → General → API Key)
- **Returns**: The entire movie library in a single call

Each movie entry in the response includes:

- `title` — resolved movie title
- `year` — release year
- `tmdbId` — canonical TMDB ID (the universal key for artwork lookups)
- `imdbId` — IMDB ID
- `path` / `folderName` — full folder path on disk
- `hasFile` — whether a media file currently exists
- `movieFile` — details about the actual file (path, quality, size, media info)
- `qualityProfileId` — which quality profile the movie belongs to
- `images` — Radarr's own cached poster/fanart URLs (from TMDB)

Example request:

```
GET http://localhost:7878/api/v3/movie
X-Api-Key: your_radarr_api_key
```

For a single movie: `GET /api/v3/movie/{radarr_id}`

#### 4.1.2 Sonarr API

- **Endpoint**: `GET /api/v3/series`
- **Authentication**: API key (found in Sonarr → Settings → General → API Key)
- **Returns**: The entire TV show library in a single call

Each series entry includes:

- `title` — resolved show title
- `year` — premiere year
- `tvdbId` — TheTVDB ID
- `imdbId` — IMDB ID
- `tmdbId` — TMDB ID (if available; not all entries have this)
- `path` — full folder path on disk
- `seasons` — array of season objects with season numbers and episode counts
- `statistics` — episode file count, size on disk, etc.
- `images` — Sonarr's own cached poster/fanart/banner URLs

For episode-level detail: `GET /api/v3/episode?seriesId={sonarr_id}`

Example request:

```
GET http://localhost:8989/api/v3/series
X-Api-Key: your_sonarr_api_key
```

#### 4.1.3 Why This Mode Is Superior

- **Zero ambiguity**: No filename parsing guesswork. IDs are already resolved and verified by the user within Radarr/Sonarr.
- **Complete metadata**: TMDB ID, IMDB ID, TVDB ID, year, file paths — all available in one API call with no secondary lookups needed.
- **Handles edge cases**: Weird filenames, non-English titles, multi-edition releases, and other naming conventions that trip up guessit are a non-issue because Radarr/Sonarr already resolved them.
- **Library changes detected instantly**: When Radarr adds a new movie or Sonarr adds a new show, a sync call picks it up immediately with full metadata. Combined with webhooks (Section 10), Marquee can react in real-time.
- **Single source of truth**: The *arr apps are already the user's source of truth for what's in their library. Marquee aligns with that rather than maintaining a separate scan.

#### 4.1.4 Sync Strategy

- **Initial sync**: On first run, call `GET /api/v3/movie` and `GET /api/v3/series` to import the full library.
- **Periodic sync**: Every N minutes (configurable, default 15), re-call the endpoints and diff against the local database to detect additions, removals, and path changes.
- **Event-driven sync**: Radarr/Sonarr webhooks (Section 10) notify Marquee of individual changes in real-time. The periodic sync is a safety net that catches anything webhooks missed.

#### 4.1.5 TMDB ID Resolution for Sonarr

Sonarr primarily uses TVDB IDs. Some entries may not have a TMDB ID directly. When `tmdbId` is null in Sonarr's response, resolve it by:

1. Using the IMDB ID (if present) to look up the TMDB ID via TMDB's `/find/{imdb_id}?external_source=imdb_id` endpoint
2. Or using the TVDB ID via TMDB's `/find/{tvdb_id}?external_source=tvdb_id` endpoint

This ensures every entry in Marquee's database has a TMDB ID as the canonical key.

#### 4.1.6 Configuration

```yaml
environment:
  - RADARR_URL=http://radarr:7878
  - RADARR_API_KEY=your_radarr_api_key
  - SONARR_URL=http://sonarr:8989
  - SONARR_API_KEY=your_sonarr_api_key
  - SYNC_INTERVAL=15  # minutes
```

### 4.2 Mode 2: Standalone Filesystem Scan (Fallback)

For users who do not run Radarr/Sonarr, Marquee can scan media directories directly and identify content from filenames and folder names. This mode works independently of any other application.

#### 4.2.1 Directory Scanning

Marquee scans user-configured media library directories (mounted as Docker volumes) and detects movies and TV shows by folder structure and filenames.

#### 4.2.2 Filename Parsing

Use the Python `guessit` library to extract structured metadata (title, year, season, episode) from filenames and folder names. guessit handles most common naming conventions.

Example:
- `The Expanse (2015)/Season 02/The Expanse - S02E01 - Safe.mkv`
- guessit extracts: title="The Expanse", year=2015, season=2, episode=1

#### 4.2.3 ID Resolution

Once a title and year are extracted, query the TMDB API to resolve to a canonical TMDB ID. This ID is the universal key used across all artwork databases and for internal caching.

For TV shows, also resolve to TheTVDB ID (available via TMDB's external IDs endpoint) since Fanart.tv requires TVDB IDs for TV artwork queries.

#### 4.2.4 NFO File Parsing

If existing NFO files are present (from MediaElch, tinyMediaManager, or other tools), parse them to extract TMDB/IMDB/TVDB IDs directly, bypassing the guessit + TMDB search step entirely. This avoids misidentification on titles where filename parsing would be ambiguous.

#### 4.2.5 Limitations Compared to Mode 1

- Filename parsing is heuristic and can fail on unusual naming conventions, non-English titles, or titles with years in them (e.g., "2001: A Space Odyssey")
- TMDB search by title+year can return incorrect matches that require manual correction
- No automatic awareness of library additions without periodic filesystem scans
- Edge cases like multi-edition releases or split movies may not be handled cleanly

### 4.3 Mode Priority (When Both Are Enabled)

If both modes are configured, *arr sync takes priority:

1. All media identified via Radarr/Sonarr API is imported with full confidence
2. Standalone scan runs on any configured directories NOT managed by Radarr/Sonarr (detected by comparing file paths)
3. This supports hybrid setups — e.g., movies managed by Radarr but a separate folder of home videos or concert recordings not in any *arr app

### 4.4 Poster Existence Check

Regardless of identification mode, once a media item is identified and its folder path is known, Marquee checks whether a poster file already exists in the media folder. Common filenames checked:

- `poster.jpg` / `poster.png`
- `folder.jpg` / `folder.png`
- `movie.jpg` (Radarr convention)
- `show.jpg`
- `cover.jpg`

The presence or absence of this file determines completion status in the database (NULL `poster_path` = needs poster).

---

## 5. Image Fetching Pipeline

### 5.1 Query Order

For each media item that needs a poster, query databases in priority order:

1. **TMDB** — fetch all available posters via `/movie/{id}/images` or `/tv/{id}/images`
2. **Fanart.tv** — fetch all movie posters via `/movie/{tmdb_id}` or TV posters via `/tv/{tvdb_id}`
3. **TheTVDB** — (TV only) fetch artwork via `/series/{tvdb_id}/artworks`
4. **TVmaze** — (TV only) fetch images via `/shows/{tvmaze_id}/images`

### 5.2 Rate Limit Handling

Each API has rate limits. Implement:

- Exponential backoff with retry on 429 responses
- Request queuing with configurable delays between calls
- Respect each API's specific rate limit headers

### 5.3 Image Download

Download all poster candidates to a temporary staging directory. Each image is downloaded, hashed, deduplicated (see Section 6), and if unique, stored temporarily for AI analysis.

### 5.4 API Key Management

All API keys are stored as environment variables in the Docker Compose configuration. The application ships with no hardcoded keys — the user provides their own keys (obtained via free registration) during initial setup.

```yaml
environment:
  - TMDB_API_KEY=your_key_here
  - FANART_API_KEY=your_key_here
  - TVDB_API_KEY=your_key_here
```

---

## 6. Image Deduplication

Before feeding poster candidates to the AI engine, duplicates must be removed. The same poster frequently appears across multiple databases, or the same database may host the same poster at different resolutions or with minor compression differences.

### 6.1 Two-Stage Deduplication Pipeline

#### Stage 1: SHA-256 Exact Deduplication

As each image is downloaded, compute its SHA-256 hash on the raw file bytes. Check against a set of hashes already seen for this title. If the hash matches, the image is byte-for-byte identical to one already downloaded — discard it immediately.

- **Time Complexity**: O(1) lookup per image (hash set)
- **What It Catches**: Byte-identical images served by multiple databases, or the same image uploaded multiple times to the same database
- **False Positives**: Zero — cryptographic hashes do not collide in practice

#### Stage 2: Perceptual Hash (pHash) Near-Deduplication

For images that survive Stage 1, compute a perceptual hash. Perceptual hashing works by reducing the image to its essential visual structure:

1. Resize to a small fixed size (e.g., 32×32)
2. Convert to grayscale
3. Apply a DCT (Discrete Cosine Transform)
4. Produce a 64-bit hash from the DCT coefficients

Two visually identical images (even at different resolutions, JPEG compression levels, or with minor watermark/logo differences) will produce perceptual hashes that differ by only a few bits.

**Comparison Method**: Hamming distance — the number of differing bits between two 64-bit hashes. Computed via XOR + popcount (a single CPU instruction on modern hardware).

- **Hamming distance = 0**: Identical images
- **Hamming distance < 6**: Visually near-identical — treat as duplicate
- **Hamming distance 6-10**: Very similar — may or may not be duplicate; threshold is tunable
- **Hamming distance > 10**: Different images — keep both

**Resolution Preference**: When two images are determined to be near-duplicates, keep the one with higher resolution and discard the other.

**Implementation**: Python `imagehash` library with PIL/Pillow:

```python
from PIL import Image
import imagehash

img = Image.open("poster.jpg")
phash = imagehash.phash(img)  # Returns 64-bit perceptual hash

# Compare two hashes
distance = hash1 - hash2  # Returns Hamming distance
if distance < 6:
    # Near-duplicate, discard the lower-resolution one
```

- **Time Complexity**: O(k²) for pairwise comparison where k = number of candidates per title. Since k is typically 20-50 after SHA-256 dedup, this means ~400-1200 comparisons of 64-bit integers — microseconds of compute time.
- **What It Catches**: Same poster at different resolutions, re-compressed versions, minor watermark/badge differences, border variations

#### Alternative: dHash (Difference Hash)

An even simpler perceptual hash. Resizes to 9×8, converts to grayscale, compares adjacent pixels. Produces a 64-bit hash. Slightly less robust than pHash but faster to compute. Can be used as an alternative if pHash proves too slow (unlikely at this scale).

### 6.2 Why Not Use CLIP for Deduplication?

CLIP embeddings capture semantic similarity — two completely different poster designs for the same movie would have similar CLIP embeddings because they're both "about" the same movie. That's not a duplicate; that's a legitimate alternative the user should choose between. Perceptual hashing captures visual pixel-level similarity, which is exactly what deduplication requires.

Use perceptual hashing for dedup. Use CLIP for preference ranking. Each tool for what it's good at.

### 6.3 Storing Hashes for Future Use

Store both the SHA-256 hash and the pHash in the database alongside each image's URL and source. This way, if posters are fetched for the same title again later (e.g., a new poster was added to TMDB), previously seen images can be skipped without re-downloading.

---

## 7. AI Preference Learning Engine

### 7.1 Primary Method: CLIP Embeddings + Lightweight Classifier

CLIP (Contrastive Language-Image Pretraining) by OpenAI is a model that generates embeddings — numerical vectors that capture the visual and semantic content of an image. CLIP understands composition, color palette, typography style, mood, genre-specific visual language, and more, all compressed into a single vector (512-dimensional for ViT-B/32, 768-dimensional for ViT-L/14).

#### Training Phase (runs once on existing library, then incrementally)

1. **Curate** a set of hand-picked poster exemplars (~430 posters, one per movie) representing the user's taste
2. **Embed** each exemplar through CLIP ViT-B/32 ONNX to get 512-dim L2-normalized embeddings
3. **Store** embeddings in a `taste_profile.clip-vit-b-32.npz` file (numpy array on disk — at the scale of a personal media library, the entire store fits in RAM trivially; no vector database needed)
4. **Score** candidates via k-NN: the style feature is the mean cosine similarity to the k nearest exemplars (k≈10), not cosine to a centroid. k-NN over individual exemplars handles multimodal taste (a horror cluster and an animation cluster coexist) without the centroid's blur

#### Inference Phase (when new media arrives)

1. **Detect** new media via filesystem watcher (`watchdog` in Python, or `inotify`) or periodic scan
2. **Identify** the media item (guessit + TMDB API lookup)
3. **Fetch** all poster candidates from TMDB + Fanart.tv + TheTVDB + TVmaze
4. **Deduplicate** candidates using the two-stage hashing pipeline (Section 6)
5. **Embed** each surviving candidate with CLIP
6. **Score** each candidate by cosine similarity to the user's preference profile
7. **Auto-select** the highest-scoring candidate, or present the top N in the web UI for user confirmation

#### Feedback Loop

As the user confirms or overrides AI selections via the web UI, the preference model updates incrementally. A simple "thumbs up / thumbs down" mechanism on selected posters provides explicit feedback.

### 7.2 Optional Enhancement: Vision Language Model (VLM) for Descriptive Analysis

For more nuanced understanding, a small VLM can describe each poster in natural language — things like "dark moody color palette, minimalist design, character centered, blue tint, textless." This builds a structured preference profile that captures why the user prefers certain posters.

#### Recommended VLMs by Hardware Tier

| Model | Size (Quantized) | Min VRAM | Notes |
|---|---|---|---|
| Qwen2.5-VL 3B | ~2 GB | 4 GB | Lightest option; fits on Intel Arc A310 |
| DeepSeek Janus-Pro 1.5B | ~1.5 GB | 3 GB | Very light; strong for its size |
| Qwen2.5-VL 7B (Q4) | ~4-5 GB | 6-8 GB | Best balance of quality and efficiency; outperforms Llama 3.2 Vision 11B on benchmarks |
| Llama 3.2 Vision 11B (Q4) | ~6-7 GB | 8 GB | Solid alternative; 128K context window |

#### Combined Approach (Recommended)

Use CLIP as the fast primary ranker (sub-second per image, runs on any GPU). Optionally layer a VLM on top for the final top-3 candidates to provide a more refined pick. The VLM is slower (a few seconds per image) but poster selection is not latency-critical.

### 7.3 CLIP Model Variants

| Model | Embedding Dim | VRAM Usage | Notes |
|---|---|---|---|
| ViT-B/32 | 512 | < 500 MB | Fastest; runs on anything including Intel Arc |
| ViT-B/16 | 512 | < 600 MB | Better quality than B/32, slightly slower |
| ViT-L/14 | 768 | < 1 GB | Best quality; still very modest resource usage |
| SigLIP (Google) | varies | < 1 GB | Improved CLIP variant; similar resource profile |

All CLIP variants are tiny compared to LLMs. Even the largest (ViT-L/14) uses well under 1 GB of VRAM. The bottleneck for hardware compatibility is the optional VLM, not CLIP.

### 7.4 Scoring Architecture: GATE then RANK

The pipeline makes two distinct kinds of decisions:

- **GATE** — absolute, hard, per-candidate. Fixed thresholds reject invalid or junk posters: resolution floor, aesthetic floor, off-style floor. Gated-out posters land in a visible bucket with their reason recorded.
- **RANK** — relative, soft, within-movie. Survivors are scored on a 9-dimensional feature vector (one embedding-derived scalar + 8 explicit scalars) and ranked. The best available wins, even if all candidates are mediocre. A low rank does NOT remove a candidate.

The embedding itself is reduced to one scalar (k-NN similarity to taste exemplars) — the raw 512-dim vector is not concatenated into the feature vector. This keeps the ranking head small, trainable on few labels, and interpretable.

Phase 0 uses hand-tuned positive weights (all features oriented higher=better, no mixed signs). Phase 1+ will train logistic regression or LightGBM on accumulated user feedback (approved vs overridden picks).

---

## 8. Database Design

### 8.1 Database Choice

SQLite — appropriate for the scale of a personal media library (hundreds to low thousands of items). No separate database container needed; the database is a single file stored in the config volume. Queries are sub-millisecond at this scale.

### 8.2 Schema

Single `media` table. No separate "complete" and "incomplete" tables — that would require DELETE + INSERT across tables when status changes (not atomic without transactions, data loss risk on crash, requires UNION for "show all" queries, duplicates schema). No boolean `has_poster` column — that's derived state computable from whether `poster_path` is null, and storing it separately creates a synchronization hazard for zero benefit.

Instead, the presence or absence of a value in `poster_path` is the completion status. NULL means incomplete, non-null means complete.

```sql
CREATE TABLE media (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    title           TEXT NOT NULL,
    tmdb_id         INTEGER UNIQUE,
    tvdb_id         INTEGER,
    imdb_id         TEXT,
    media_type      TEXT NOT NULL CHECK(media_type IN ('movie', 'show')),
    file_path       TEXT NOT NULL,
    identified_by   TEXT NOT NULL CHECK(identified_by IN ('radarr', 'sonarr', 'standalone')),
    radarr_id       INTEGER,            -- Radarr's internal ID (for API callbacks)
    sonarr_id       INTEGER,            -- Sonarr's internal ID (for API callbacks)
    poster_path     TEXT,               -- NULL = needs poster
    poster_source   TEXT,               -- which database the poster came from (tmdb, fanart, tvdb, tvmaze)
    poster_url      TEXT,               -- original source URL for reference
    ai_selected     BOOLEAN DEFAULT 0,  -- did AI pick this poster?
    embedding       BLOB,               -- CLIP embedding vector (for preference learning)
    sha256          TEXT,               -- SHA-256 hash of the selected poster file
    phash           TEXT,               -- Perceptual hash of the selected poster
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

### 8.3 Indexes

Standard index on `tmdb_id` (already UNIQUE, so automatically indexed).

Partial index for finding items that need posters:

```sql
CREATE INDEX idx_missing_posters ON media(id) WHERE poster_path IS NULL;
```

This index only contains rows where the poster is missing. As the library fills up and most items have posters, this index shrinks automatically. Scanning it is O(k) where k = number of incomplete items, not the total library size.

### 8.4 Big O Complexity

- B-tree index lookups (standard queries): O(log n)
- Full table scan (no index): O(n)
- Partial index scan (find incomplete items): O(k) where k << n in steady state
- At the scale of a personal media library, all queries are sub-millisecond

### 8.5 Candidate Images Table (Optional)

For caching fetched poster candidates and their hashes to avoid re-downloading on subsequent scans:

```sql
CREATE TABLE poster_candidates (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    media_id        INTEGER NOT NULL REFERENCES media(id),
    source          TEXT NOT NULL,       -- tmdb, fanart, tvdb, tvmaze
    source_url      TEXT NOT NULL,
    sha256          TEXT NOT NULL,
    phash           TEXT,
    width           INTEGER,
    height          INTEGER,
    embedding       BLOB,               -- CLIP embedding for this candidate
    similarity_score REAL,              -- cosine similarity to user preference
    fetched_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(media_id, sha256)
);
```

---

## 9. Poster Caching and Restoration

### 9.1 The Problem

Radarr (and Sonarr) delete and recreate the entire movie folder when upgrading media quality. Any poster artwork stored in that folder is destroyed. The user must manually re-select posters.

### 9.2 Cache Architecture

Maintain a local copy of every selected poster, keyed by TMDB ID (not folder name, because folders may change but TMDB IDs never will):

```
/config/cache/posters/
├── movies/
│   ├── 550/poster.jpg          # Fight Club
│   ├── 120/poster.jpg          # LOTR: Fellowship
│   └── 13475/poster.jpg        # Star Trek
└── shows/
    ├── 1399/poster.jpg         # Game of Thrones
    └── 63639/poster.jpg        # The Expanse
```

### 9.3 Storage Considerations

A typical movie poster at full resolution is 1-3 MB as JPEG. For a library of 1,000 movies, the total cache is 1-3 GB. This is negligible on a server with TB+ of media storage. There is no need to compress cached posters — the added code complexity and quality loss provide no practical benefit at this scale. Store the originals as-is.

### 9.4 Cache Population

When a poster is selected (either by AI or manually by the user):

1. Save the poster to the media folder (where Plex/Jellyfin expects it)
2. Copy the poster to the cache directory keyed by TMDB ID
3. Update the database row with the poster path, source, and hash information

### 9.5 Why Not Re-Download from the Source API?

Three reasons:

1. The user (or AI) already made a choice among potentially dozens of poster options. The specific URL of that choice must be remembered and the image preserved.
2. Source URLs can change — images are occasionally removed or replaced on TMDB/Fanart.tv.
3. If external APIs are down, restoration would fail. A local cache ensures zero dependency on external services for restoring existing artwork.

---

## 10. Radarr/Sonarr Integration

### 10.1 Webhook Integration (Real-Time)

Radarr and Sonarr both have a built-in Connect/Webhook system. In Radarr's settings under Connect, add a webhook pointing to Marquee's endpoint.

#### Marquee Endpoints

```
POST /api/webhooks/radarr
POST /api/webhooks/sonarr
```

#### Radarr Webhook Configuration

- **Event Triggers**: "On Movie File Upgraded", "On Movie Renamed"
- **URL**: `http://marquee:8080/api/webhooks/radarr` (or whatever hostname/port Marquee runs on)
- **Method**: POST

#### Radarr Webhook Payload (Example)

```json
{
  "eventType": "MovieFileUpgraded",
  "movie": {
    "id": 1,
    "title": "Fight Club",
    "tmdbId": 550,
    "folderPath": "/movies/Fight Club (1999)"
  },
  "movieFile": {
    "relativePath": "Fight Club (1999).mkv",
    "path": "/movies/Fight Club (1999)/Fight Club (1999).mkv"
  }
}
```

#### Restoration Logic

1. Receive webhook event
2. Extract `movie.tmdbId` and `movie.folderPath` from payload
3. Look up TMDB ID in cache directory
4. Copy cached poster to the new folder path
5. Update the database with the new file path
6. Log the restoration event

Sonarr works identically — equivalent webhook events for series and episode upgrades with TVDB/TMDB IDs in the payload. The payload structure differs slightly but contains the same essential information.

### 10.2 Periodic Filesystem Scan (Safety Net)

Webhooks can fail (app was down during upgrade, network timeout, etc.). As a safety net, run a periodic scan (configurable interval, default 15-30 minutes, or on-demand via the UI):

1. Walk the database, checking each `poster_path` for file existence on disk
2. If file is missing AND cached → restore from cache automatically, log the restoration
3. If file is missing AND NOT cached → mark as incomplete, queue for AI re-selection

This self-healing mechanism catches anything the webhook missed.

### 10.3 Filesystem Watcher (Optional Enhancement)

In addition to periodic scans, a real-time filesystem watcher using Python's `watchdog` library or Linux `inotify` can detect folder deletions/creations as they happen. This provides faster detection than periodic scans but is more complex to implement reliably with Docker volume mounts.

---

## 11. Web UI

### 11.1 Technology

A web-based frontend built with React, Vue, or plain HTML/JS. Served by the FastAPI backend.

### 11.2 Features

- **Library Overview**: Grid/list view of all media in the library with current poster thumbnails
- **Completion Status**: Visual indicator of which items have posters and which are missing
- **Poster Selection**: For any media item, display all fetched poster candidates in a grid, with the AI's pick highlighted
- **Manual Override**: Click any candidate to select it as the poster, overriding the AI's choice. This selection feeds back into the preference model.
- **Thumbs Up / Thumbs Down**: Simple feedback mechanism on AI-selected posters to improve future selections
- **Scan Trigger**: Button to trigger a manual library scan
- **Restoration Log**: View history of automatic poster restorations after Radarr/Sonarr upgrades
- **Settings**: Configure API keys, media library paths, scan intervals, AI model selection, webhook URLs

---

## 12. Docker Deployment

### 12.1 Container Architecture

```yaml
services:
  marquee:
    image: marquee:latest
    container_name: marquee
    ports:
      - "8080:8080"
    environment:
      # Artwork API Keys
      - TMDB_API_KEY=${TMDB_API_KEY}
      - FANART_API_KEY=${FANART_API_KEY}
      - TVDB_API_KEY=${TVDB_API_KEY}
      # Radarr/Sonarr Integration (preferred identification mode)
      - RADARR_URL=http://radarr:7878
      - RADARR_API_KEY=${RADARR_API_KEY}
      - SONARR_URL=http://sonarr:8989
      - SONARR_API_KEY=${SONARR_API_KEY}
      - SYNC_INTERVAL=15            # minutes, for *arr API sync
      # Standalone scan settings (fallback mode)
      - SCAN_INTERVAL=30            # minutes, for filesystem scan
      # AI settings
      - AI_MODEL=clip-vit-l-14      # or clip-vit-b-32, etc.
      - VLM_ENABLED=false           # enable optional VLM
      - VLM_MODEL=qwen2.5-vl-7b    # if VLM is enabled
    volumes:
      - ./config:/config                    # App config, database, poster cache
      - /path/to/movies:/movies:ro          # Media library (read-only)
      - /path/to/tvshows:/tvshows:ro        # Media library (read-only)
    deploy:
      resources:
        reservations:
          devices:
            - driver: nvidia
              count: 1
              capabilities: [compute]
    restart: unless-stopped
```

### 12.2 GPU Passthrough

- **NVIDIA GPUs** (RTX 3070, etc.): Use NVIDIA Container Toolkit (`nvidia-docker`). The `deploy.resources.reservations.devices` block in Docker Compose handles GPU passthrough.
- **Intel Arc GPUs** (A310, etc.): Pass through `/dev/dri` render node and use Intel's oneAPI/IPEX stack. CLIP and smaller VLMs work through this path since PyTorch supports Intel GPUs via IPEX.

```yaml
# Intel Arc GPU alternative
devices:
  - /dev/dri:/dev/dri
```

### 12.3 Volumes

| Volume | Purpose | Access |
|---|---|---|
| `/config` | Database, poster cache, app configuration | Read-write |
| `/movies` | Movie media library | Read-only |
| `/tvshows` | TV show media library | Read-only |

The `/config` volume stores the SQLite database, the poster cache directory, CLIP model files, and application settings. This volume must persist across container restarts.

Media library volumes are mounted read-only for safety — Marquee reads folder structures and poster files but writes posters directly to the media folders only when placing/restoring artwork (this requires the volume to be read-write in practice, or a separate write-enabled mount).

**Corrected**: Media volumes should be mounted read-write since Marquee needs to write poster files into them. Alternatively, Marquee can notify Plex/Jellyfin via API to use a poster URL directly, but filesystem-based poster placement is the most compatible approach.

### 12.4 Inference Options

Two approaches for running AI models:

1. **Direct PyTorch**: Include CLIP and optional VLM dependencies directly in the Marquee container. Simpler deployment, fewer moving parts.
2. **Ollama Sidecar**: Run Ollama as a separate container for model management (GPU allocation, model loading, API serving). Cleaner separation of concerns; useful if the user wants to share GPU resources with other LLM workloads.

For a v1, direct PyTorch in a single container is recommended for simplicity.

---

## 13. GPU and Model Compatibility

### 13.1 CLIP (Required) — ONNX Runtime with Multi-Provider Support

CLIP is the core AI component and runs on essentially any GPU (and even CPU, just slower). The current pipeline uses **CLIP ViT-B/32** exported to ONNX (~150 MB) with automatic provider selection:

| Priority | Provider | Hardware | Notes |
|---|---|---|---|
| 1 | CUDAExecutionProvider | NVIDIA GPU (RTX 3070+) | Requires onnxruntime-gpu |
| 2 | OpenVINOExecutionProvider | Intel iGPU (Arc, UHD) | Plex/QSV crowd hardware |
| 3 | CoreMLExecutionProvider | Apple Silicon (M-series) | Current dev platform |
| 4 | CPUExecutionProvider | Any CPU | Universal fallback |

The ONNX model is portable across all providers — only the session configuration changes. Providers not available on the current machine are automatically skipped (e.g., CUDA is not present on macOS).

| GPU | CLIP ViT-B/32 | PaddleOCR | Notes |
|---|---|---|---|
| RTX 3070 (8 GB) | CUDA (GPU) | GPU (paddle_dynamic) | Active deployment target |
| Intel Arc A310 (6 GB) | OpenVINO | CPU | Via IPEX or OpenVINO |
| RTX 3060 (12 GB) | CUDA (GPU) | GPU | — |
| Apple M-series | CoreML | CPU | Dev machine |
| CPU only | CPU | CPU | Viable for small libraries |

### 13.2 VLM (Optional Enhancement)

| GPU | Qwen2.5-VL 3B | Qwen2.5-VL 7B (Q4) | Llama 3.2 Vision 11B (Q4) |
|---|---|---|---|
| RTX 3070 (8 GB) | Yes | Yes (tight fit) | Marginal |
| Intel Arc A310 (6 GB) | Yes | Possible with aggressive quantization | No |
| RTX 3060 (12 GB) | Yes | Yes | Yes |
| RTX 3090/4090 (24 GB) | Yes | Yes | Yes, comfortably |

### 13.3 Multiple Model Support

Marquee should support configurable model selection via environment variable, allowing users to choose the model appropriate for their hardware:

- `AI_MODEL=clip-vit-b-32` — lightest, fastest, runs anywhere
- `AI_MODEL=clip-vit-l-14` — best CLIP quality, still very light
- `AI_MODEL=siglip` — Google's improved CLIP variant
- `VLM_MODEL=qwen2.5-vl-3b` — light VLM for weaker GPUs
- `VLM_MODEL=qwen2.5-vl-7b` — best VLM for 8 GB GPUs
- `VLM_MODEL=deepseek-janus-1.5b` — ultra-light VLM option

---

## 14. Complete Workflow

### 14.1 Initial Setup

1. User deploys Marquee via Docker Compose
2. User configures artwork API keys (TMDB, Fanart.tv, TheTVDB) as environment variables
3. User configures Radarr/Sonarr connection details (URL + API key) for *arr sync mode, or mounts media library volumes for standalone mode, or both
4. User configures Radarr/Sonarr webhooks pointing to Marquee's endpoints (Connect → Webhook)
5. User triggers initial library sync/scan from the web UI

### 14.2 Initial Library Import (Learning Phase)

#### Via Radarr/Sonarr Sync (Preferred)

1. Marquee calls `GET /api/v3/movie` on Radarr and `GET /api/v3/series` on Sonarr
2. For each movie/show, it receives pre-resolved TMDB ID, IMDB ID, TVDB ID, title, year, and folder path directly — no guessing needed
3. For Sonarr entries missing a TMDB ID, resolves via TMDB's `/find` endpoint using IMDB or TVDB ID
4. Checks for existing poster files in each media folder
5. For items WITH existing posters:
    - Computes CLIP embedding of the existing poster
    - Stores the embedding in the database
    - Copies the poster to the cache (keyed by TMDB ID)
    - This builds the initial preference profile
6. For items WITHOUT posters:
    - Records them in the database with NULL `poster_path`
    - Queues them for poster fetching and AI selection

#### Via Standalone Filesystem Scan (Fallback)

1. Marquee scans all configured media directories
2. For each movie/show, it parses filenames (via guessit) or NFO files to extract title/year/IDs
3. Resolves each title to a TMDB ID via the TMDB search API
4. Same poster existence check, CLIP embedding, and caching steps as above

### 14.3 Poster Selection (For Items Missing Posters)

1. Fetch all poster candidates from TMDB, Fanart.tv, TheTVDB, TVmaze
2. Stage 1 dedup: SHA-256 exact hash, discard byte-identical duplicates
3. OCR text filtering: reject text-heavy posters using PaddleOCR; emit title bbox + residual text boxes
4. Stage 2 dedup: pHash perceptual hash on OCR survivors, discard visual near-duplicates (Hamming distance < 6), keep higher resolution version
5. Extract 9-dimensional feature vector per candidate (CLIP k-NN style similarity, aesthetic quality, title colorfulness, text cleanliness, resolution, sharpness, face area, provenance, language match)
6. Hard gate: reject candidates below quality floors (resolution, aesthetic, off-style)
7. Rank survivors by learned weighted score (Phase 0: hand-tuned weights; Phase 1+: logistic/LightGBM trained on feedback)
8. Deploy top-ranked poster to the media folder AND the cache
9. Update database with poster path, source, embedding, and hashes

### 14.4 New Media Arrival

#### Via *arr Sync

1. Radarr/Sonarr webhook fires "On Movie Added" / "On Series Added" event, or periodic sync detects a new entry in the *arr API response
2. Marquee receives full metadata (TMDB ID, path, title) directly — no identification step needed
3. Proceeds to poster fetching, dedup, and AI selection (same as 14.3)

#### Via Standalone Scan

1. Filesystem watcher or periodic scan detects new media folder
2. Filename parsing via guessit + TMDB resolution
3. Same poster fetching, dedup, and AI selection flow as 14.3

### 14.5 Media Upgrade (Radarr/Sonarr)

1. Radarr/Sonarr upgrades a movie → deletes old folder → creates new folder → sends webhook to Marquee
2. Marquee receives webhook with TMDB ID and new folder path
3. Looks up TMDB ID in poster cache
4. Copies cached poster to new folder
5. Updates database with new file path
6. Logs the restoration

### 14.6 Periodic Self-Healing Scan

1. Every N minutes (configurable), scan the database
2. For each item with a non-null `poster_path`, verify the file exists on disk
3. If missing + cached → restore from cache, log it
4. If missing + not cached → mark as incomplete, queue for re-selection

### 14.7 User Override

1. User opens Marquee web UI, browses to a media item
2. Views all fetched poster candidates with AI's pick highlighted
3. Clicks a different poster to override
4. Marquee saves the new selection to the media folder and cache
5. Updates the preference model with this explicit feedback

---

## Appendix: Technology Stack Summary

| Component | Technology |
|---|---|
| Language | Python 3.11+ |
| Backend Framework | FastAPI |
| Database | SQLite |
| AI - Embeddings | CLIP ViT-B/32 via ONNX Runtime (multi-provider: CUDA/OpenVINO/CoreML/CPU) |
| AI - Aesthetic | LAION B/32 linear head (`nn.Linear(512,1)`) on CLIP embedding |
| AI - Face Detection | SCRFD ONNX (`scrfd_500m_bnkps.onnx`) |
| AI - VLM (optional) | Qwen2.5-VL / DeepSeek Janus / Llama 3.2 Vision (via Ollama or direct PyTorch) |
| OCR | PaddleOCR (PP-OCRv5_mobile_det, paddle_dynamic engine, GPU auto-detect) |
| Image Hashing | `imagehash` (pHash) + `hashlib` (SHA-256) |
| Filename Parsing | `guessit` |
| Taste Storage | NumPy .npz (k-NN over exemplars, no vector DB needed at current scale) |
| Image Processing | Pillow (PIL) + OpenCV |
| HTTP Client | `httpx` |
| Backend | FastAPI + SQLAlchemy 2.0 async + aiosqlite |
| Frontend | React / Vue / plain HTML+JS |
| Containerization | Docker + Docker Compose |
| GPU Support (NVIDIA) | ONNX Runtime CUDA + PaddlePaddle CUDA |
| GPU Support (Apple) | ONNX Runtime CoreML |
| GPU Support (Intel) | ONNX Runtime OpenVINO |
