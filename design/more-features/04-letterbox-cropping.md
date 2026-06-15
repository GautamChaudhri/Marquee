# 13. Letterbox Cropping for Ultrawide Displays

## Automatic Detection and MKV Crop-Tag Application for Letterboxed Media

Marquee detects letterboxed media — content wider than 16:9 packed into a 16:9 container with black bars — and applies MKV pixel-crop tags so the Plex desktop app automatically crops the bars on ultrawide monitors. No re-encoding, no quality loss, no GPU required.

---

## Table of Contents

1. [Problem Statement](#1-problem-statement)
2. [Core Principles](#2-core-principles)
3. [Feature Overview](#3-feature-overview)
4. [Resolution-Based Pre-Filter](#4-resolution-based-pre-filter)
5. [Letterbox Detection Pipeline](#5-letterbox-detection-pipeline)
6. [Variable Aspect Ratio Handling](#6-variable-aspect-ratio-handling)
7. [Crop Tag Application](#7-crop-tag-application)
8. [Confidence Scoring and Inspection UI](#8-confidence-scoring-and-inspection-ui)
9. [Production-Grade Script Improvements](#9-production-grade-script-improvements)
10. [User Flows](#10-user-flows)
11. [Design Decisions and Trade-offs](#11-design-decisions-and-trade-offs)

---

## 1. Problem Statement

### The Ultrawide Experience

Many movies and TV shows are wider than the 16:9 aspect ratio (e.g., 2.39:1 scope films, 2.00:1 streaming originals). When these are distributed, they're typically encoded into a standard 16:9 container (1920×1080 or 3840×2160) with hardcoded black bars baked into the video stream above and below the image area.

On a 16:9 monitor or TV, this looks correct — the bars fill the screen. But on an ultrawide monitor (21:9, 32:9) or phone display, the player adds its own letterbox bars on the sides (or top/bottom depending on orientation), and the baked-in bars remain. The result is a tiny image floating in a sea of black: **bars on all four sides**.

### The Plex Crop Tag Solution

The Plex desktop app supports MKV pixel-crop metadata tags. When a file carries tags like `pixel-crop-top=140`, Plex automatically trims those pixels from the rendered frame — effectively cropping out the baked-in black bars. The result on an ultrawide display is a correctly scaled image that fills the screen width with no wasted space.

Critically, this is a **metadata-only operation**. The video stream is untouched. No re-encoding, no quality loss, no risk to the file. The tags take effect instantly and can be removed just as quickly.

### Why Automation Matters

Manually finding letterboxed files, detecting crop amounts, and applying tags is tedious. A library of 1,000+ movies might have 400+ scope films — each one a candidate. The detection itself requires frame extraction at multiple timestamps and intelligent consensus-building across samples. No existing tool in the *arr ecosystem handles this.

---

## 2. Core Principles

1. **No re-encoding, ever.** Crop tags are container metadata. Applying or removing them takes milliseconds and never touches the video or audio streams.

2. **Cheapest filter first.** Before extracting a single frame, Marquee eliminates non-candidates by inspecting encoded resolution. Only files that could plausibly be letterboxed proceed to the detection pipeline.

3. **Safe by default.** The detection pipeline never applies tags automatically — it reports findings with a confidence score. The user reviews and confirms.

4. **Detect before acting.** The pipeline samples multiple timestamps per file and builds a consensus. A single anomalous frame does not drive the decision.

5. **Handle variable aspect ratios.** Films that switch between ratios (IMAX sequences, streaming originals) are detected and handled safely: either skipped entirely or cropped to the most conservative ratio.

---

## 3. Feature Overview

Marquee's letterbox cropping feature operates in three stages:

| Stage | What it does | When it runs |
|---|---|---|
| **Pre-Filter** | Inspect encoded resolution to identify potential letterboxed files | On library scan, or on-demand |
| **Detection** | Extract sample frames, measure black bars, build consensus across timestamps | Triggered for pre-filter candidates only |
| **Tag Application** | Apply or remove MKV pixel-crop tags | Manual (review → confirm per file or batch) |

Supporting features: variable aspect ratio safety, confidence scoring, and an inspection UI that shows exactly what was found.

---

## 4. Resolution-Based Pre-Filter

### Concept

The detection pipeline is expensive — it extracts multiple full-resolution frames per file, converts them to images, and runs multi-pass trim analysis. Running it on every file in a library is unnecessary. The pre-filter eliminates non-candidates using only the file's encoded pixel dimensions, which Marquee already has from Radarr/Sonarr sync and ffprobe.

### The Filtering Logic

Every media file falls into one of three buckets based on its encoded video resolution:

**Skip — already cropped or native scope**

Files where the video stream is already wider than 16:9 (i.e., its encoded height is less than expected for the width):

- 1920×800, 1920×1040 (scope or 1.85:1 content encoded natively, no bars present)
- 3840×1600, 3840×2076 (4K scope, native)
- Any resolution where the aspect ratio of encoded pixels is ≥1.78:1

These files already display correctly on ultrawide. No crop needed.

**Skip — too small or already 4:3**

- 1280×720 and below (too low-resolution for meaningful crop; the black bars are small enough that cropping provides negligible benefit)
- 4:3 content like 640×480, 1440×1080 (not relevant for letterbox detection)

**Candidate — 16:9 container, content may be wider**

- 1920×1080 (the most common container for scope films)
- 3840×2160 (4K in 16:9 container)
- Any resolution where encoded pixel aspect ratio is exactly or nearly 16:9

These files are the ones that might have baked-in letterbox bars. They proceed to the detection pipeline.

### What This Saves

In a typical library of 1,000 movies:
- ~550 are already cropped, native scope, or low-resolution → skipped
- ~350 are 1080p or 2160p in a 16:9 container → candidates
- Of those candidates, ~250 actually have letterbox bars (the rest are native 16:9 content)

The pre-filter eliminates ~65% of the library without extracting a single frame.

---

## 5. Letterbox Detection Pipeline

### Concept

For each candidate file, Marquee extracts frames at strategic timestamps, measures black bars using multi-threshold trim analysis, and builds a consensus across all samples to determine the crop amount.

### Sampling Strategy

The pipeline uses different sampling strategies for movies and TV shows:

**Movie mode**: Samples every 5 minutes from 00:05:00 through 01:00:00 (12 frames total). Movies vary in runtime, but the first hour reliably captures the body of the film — avoiding credits sequences that may differ in aspect ratio — while also staying within reasonable bounds for short films.

**TV mode**: Samples at 5, 10, and 15 minutes (3 frames total). TV episodes are shorter and an episode's aspect ratio is typically consistent throughout.

### Frame Analysis Per Timestamp

For each sampled timestamp:

1. A single frame is extracted at that position using ffmpeg. The extraction is lossless at the frame level — the frame is a direct decode, not a re-encode.

2. The frame is analyzed using a trim-based approach. The algorithm crops inward from the edges, stripping solid or near-solid regions. To handle varying black-bar quality (pure black vs dark gray from some encoders), multiple tolerance thresholds are tried (typically 5%, 15%, 25%) and the results are compared.

3. Each tolerance yields a trimmed rectangle — the region of the frame that contains actual image content. The vertical black bar amount is the difference between the original frame height and the trimmed height, divided by 2 (assuming symmetric top/bottom bars — which is almost universally the case for scope films).

4. The three tolerance results are pooled, producing a weighted set of possible crop measurements for that timestamp.

### Consensus Building

Across all sampled timestamps in a file, the pipeline gathers every crop measurement and selects the most frequently occurring value as the **recommended crop**. This majority-vote approach handles:

- Frames where the scene is naturally dark and the trim algorithm over-crops (rare, but the consensus vote drowns out outliers)
- Title sequences or fade-to-black moments that yield spurious measurements
- Minor variations in bar thickness from compression artifacts

### Non-Letterbox Detection

If the consensus crop is zero pixels (or very near zero, e.g., ≤4px), the file is classified as **not letterboxed**. No tags are applied, and the file is removed from the candidate list. This is the script's existing fallback behavior and it works correctly.

---

## 6. Variable Aspect Ratio Handling

### The Problem

Some films and shows intentionally switch between aspect ratios:

- **IMAX sequences**: Christopher Nolan films (Interstellar, Dunkirk, Tenet, Oppenheimer) cut between 2.20:1 scope scenes and 1.43:1 or 1.90:1 IMAX scenes. On a 16:9 encode, the scope scenes have black bars and the IMAX scenes fill the frame.
- **Streaming originals**: Shows like The Expanse or For All Mankind switch between scope and 16:9 within the same episode.
- **Animated features**: Some use wider ratios for action sequences and 16:9 for dialogue.

If a single crop tag is applied based on the scope scenes, the IMAX/16:9 scenes will be incorrectly cropped — the top and bottom of those scenes would be sliced off.

### Detection Logic

The pipeline detects variable aspect ratios by examining the spread of crop measurements across samples:

**Case A: Some scenes are 16:9 (crop ≈ 0)**

If any sample returns a crop of approximately zero, that means some portion of the content fills the full 16:9 frame. Applying any crop would damage those scenes.

> **Rule**: File is flagged as **variable ratio — unsafe to crop**. No tags are applied. The inspection UI shows a warning explaining why.

**Case B: All scenes are letterboxed, but at different ratios**

If every sample has crop > 0, but the detected crop amounts vary (e.g., 140px on some samples, 80px on others), the content consistently has bars but switches between wider and narrower scope ratios.

> **Rule**: File is flagged as **variable ratio — safe to crop conservatively**. The recommended crop is the **minimum** detected across all samples (the narrower ratio, which preserves the most visible content). This ensures no scene gets its image area clipped.

**Case C: Consistent crop across all samples**

All samples agree on the same crop. Standard single-ratio scope film.

> **Rule**: Apply the consensus crop normally. High confidence.

### Edge Case: Opening/Closing Credits

Some films have credits sequences in 16:9 even though the body of the film is scope. This can produce a false positive for Case A (variable ratio — unsafe).

The sampling strategy mitigates this: movie sampling runs from 5 to 60 minutes, which omits the opening few minutes (where logo cards and credits often appear) and the closing credits. If a credit sequence at minute 3 is 16:9, the detector never sees it because sampling starts at minute 5.

For films with mid-roll interstitials or unique structures, the user can manually override the detection in the inspection UI.

---

## 7. Crop Tag Application

### What the Tags Do

MKV containers support four pixel-crop metadata tags on the video track:

- `pixel-crop-top` — pixels to trim from the top edge
- `pixel-crop-bottom` — pixels to trim from the bottom edge
- `pixel-crop-left` — pixels to trim from the left edge
- `pixel-crop-right` — pixels to trim from the right edge

For letterbox detection, only top and bottom are relevant. The tags are applied symmetrically (same value for top and bottom) since letterbox bars are almost always equal thickness.

When Plex Desktop reads an MKV file with these tags, it automatically crops the specified pixels from the rendered output. The player treats the effective display area as the trimmed region. External players (VLC, mpv) also respect these tags.

### Applying Tags

Tags are applied using mkvpropedit — a tool that edits MKV container metadata in-place without rewriting the file. The operation is near-instant (milliseconds, not seconds) because it only touches the header, not the data blocks.

### Removing Tags

Crop tags can be removed at any time with zero side effects — the video stream is identical. The removal is also instant via mkvpropedit with a delete operation. The inspection UI provides a "Remove Tags" action per-file.

### What Tags Don't Do

- Tags do not modify the video, audio, or subtitle streams.
- Tags do not affect playback on players that don't support MKV crop metadata (most notably, the Plex web client and mobile apps do not honor them — only the desktop app does).
- Tags do not change the file size meaningfully (a few bytes for the metadata entry).
- Tags are lost if the file is remuxed by another tool, unless that tool preserves track headers.

---

## 8. Confidence Scoring and Inspection UI

### Why Confidence Matters

The detection pipeline operates on samples and heuristics, not ground truth. A user reviewing results should know how reliable each recommendation is before applying it. The inspection UI surfaces this as a confidence tier.

### Confidence Tiers

| Tier | Criteria | What the UI Shows |
|---|---|---|
| **High** | All samples agree on the same crop ±2px. No zero-crop samples. | Green indicator. "All 12 frames agree: 140px crop." |
| **Medium** | Samples show 2–3 different crop values, all non-zero. Range is small (≤20px spread). | Yellow indicator. "Minor variation across frames. Recommended: 140px (minimum safe crop)." |
| **Low** | Wide spread in crop values (>20px), or some samples near zero. Variable ratio detected. | Orange indicator. "Aspect ratio varies. See details." Case A: "Unsafe — contains 16:9 scenes." Case B: "Safe but conservative — 80px crop (minimum across all ratios)." |
| **Not letterboxed** | Consensus crop ≈ 0. | Gray indicator. "No letterbox bars detected." |

### Inspection View

For a media item flagged as a potential candidate, the inspection view shows:

- **Original encoded resolution**: 1920×1080
- **Detected effective image area**: 1920×800 (all sampled frames)
- **Recommended crop**: 140px top, 140px bottom
- **Confidence tier**: High
- **Sample breakdown**: A small thumbnail grid or table showing each sampled timestamp's detected crop, so the user can visually verify
- **Actions**: Apply Tags, Ignore (mark as reviewed, skip), Remove Tags (if already tagged)

### Batch Review

When reviewing multiple files, the inspection view becomes a compact list:

```
The Matrix          1080p → 1920×800   140px crop   High   ✓
Interstellar        1080p → VARIES    UNSAFE       Low    ⚠️ (16:9 scenes)
Dune: Part Two      2160p → 3840×1606 157px crop   High   ✓
The Grand Budapest  1080p → VARIES    80px min     Medium ✓ (3 ratios)
Seinfeld S03E01     1080p → 1440×1080 N/A          None   — (pillarbox, not scope)
```

The user can select all "High" confidence items and apply tags in one batch action, then manually review the lower-confidence items.

---

## 9. Production-Grade Script Improvements

The existing detection script (`cct.sh`) works correctly for its current scope but should be refined for production use within Marquee. These are conceptual improvements, not implementation specifications:

### Performance

- **Single-pass frame extraction with thumbnail mode**: ffmpeg's thumbnail filter can extract representative frames in a single pass rather than seeking to each timestamp independently. For movie mode (12 timestamps), this reduces disk I/O significantly.
- **Parallel file processing**: The detection pipeline is CPU-bound (ffmpeg decode + image trim), not GPU-bound. Multiple files can be processed in parallel up to the CPU core count, dramatically reducing wall-clock time for batch detection.
- **Early termination**: If the first few sampled timestamps all return crop ≈ 0 with high confidence, the pipeline can terminate early and classify the file as not letterboxed.

### Accuracy

- **Color-aware trimming**: The current multi-fuzz approach handles dark gray bars well, but some encodes have letterbox bars with subtle color casts (blue-tinted blacks, crushed near-blacks). The trim algorithm should account for this by operating in a perceptual color space rather than raw RGB.
- **Edge frame exclusion**: Beyond just the credits window, the pipeline should check a frame at the very start (00:00:01) and very end of the file. If those frames show a different crop than the body, they're excluded from the consensus — preventing a studio logo or end card from skewing the result.

### Robustness

- **Corrupt frame handling**: If ffmpeg fails to extract a frame at a timestamp (corrupt GOP, broken file), the pipeline logs the failure for that timestamp and continues with the remaining samples rather than aborting the entire file.
- **Non-standard containers**: The pipeline currently targets MKV only (because only MKV supports crop tags). If a file is MP4 or AVI, it is skipped — but the user should see a note explaining why. A future enhancement could remux MP4→MKV losslessly, then apply tags (out of current scope).

---

## 10. User Flows

### Flow 1: Discovering Letterboxed Media

1. User navigates to the letterbox management view in Marquee.
2. The view shows three tabs: **Candidates** (files flagged by pre-filter), **Tagged** (files with active crop tags), **Skipped** (files reviewed and marked not letterboxed).
3. The Candidates tab shows a list of 350 movies, each with resolved resolution and a "Detect" button.
4. User clicks "Detect All." Marquee runs the detection pipeline across all candidates in parallel.
5. After processing, the list updates with confidence tiers and recommended crop values.
6. User sorts by "High confidence" and sees 230 movies with clean detections.

### Flow 2: Reviewing and Applying Tags

1. User reviews the High confidence items. Spots the ones they care about (or selects all).
2. Clicks "Apply Tags" on the selection.
3. Tags are applied instantly to each file via mkvpropedit.
4. Selected files move from Candidates to Tagged tab.
5. User spot-checks a few by opening Plex Desktop — the crop is active.

### Flow 3: Handling a Variable Ratio Film

1. Interstellar shows up in the detection results with a Low confidence flag: "Variable ratio detected — contains 16:9 scenes."
2. User clicks into the inspection view. Sees that 8 of 12 samples detected 140px crop (scope scenes), and 4 samples detected 0px crop (IMAX scenes).
3. The UI explains: "This film switches between scope and IMAX. Applying a 140px crop would cut off the top and bottom of the IMAX sequences."
4. User clicks "Ignore" — the file moves to Skipped and won't be re-flagged on future scans.

### Flow 4: Removing Tags

1. User decides they don't like the crop on a particular movie (or they're playing it on a 16:9 TV where the crop is irrelevant since Plex Desktop isn't the player).
2. Opens the Tagged tab, finds the movie, clicks "Remove Tags."
3. Tags are deleted instantly. File moves back to Candidates (or Skipped if the user chooses).
4. File plays with full 16:9 frame again.

---

## 11. Design Decisions and Trade-offs

### Pre-Filter by Resolution vs. Run on Everything

**Decision**: Pre-filter by encoded resolution before detection.

**Reasoning**: Detection is expensive — 12 full-resolution frame decodes + multi-pass image trim per file. On a CPU-only server without a discrete GPU, processing 1,000 movies could take hours. The pre-filter eliminates ~65% of files with a single metadata read, cutting total processing time proportionally. The trade-off is that a tiny number of edge cases might be missed (e.g., a scope film encoded at 1920×800 that for some reason still has thin bars), but these are rare and the user can manually trigger detection on any file.

### Detection Per-File vs. Per-Season

**Decision**: Detect per episode for TV shows, not per season.

**Reasoning**: While most TV seasons are consistent, some shows change aspect ratio between seasons, between episodes, or even within an episode. Per-episode detection is correct, and TV episodes are short (3 timestamps each), so the cost is manageable. A future optimization could detect per first episode and cascade the result across the season for shows that are known to be consistent.

### MKV-Only (No MP4)

**Decision**: Crop tag application only supports MKV files.

**Reasoning**: The MKV crop metadata standard is well-supported by Plex Desktop, VLC, and mpv. MP4 containers do not have an equivalent metadata field — the only way to "crop" an MP4 is to re-encode with different dimensions. Since the entire principle of this feature is no re-encoding, MP4 files are simply reported as not eligible with a clear explanation. Users with MP4 files can remux to MKV (lossless container swap, ~30 seconds) as a workaround.

### Tags vs. Re-Encode

**Decision**: Metadata tags only. Never re-encode.

**Reasoning**: Re-encoding to crop black bars would:
- Take 30–60 minutes per movie on a GPU, hours on CPU
- Reduce quality (generational loss)
- Require GPU hardware
- Be irreversible

Crop tags are instant, lossless, reversible, and require no GPU. The only downside is that not all players honor them — but since the target use case is specifically Plex Desktop on ultrawide, this is an acceptable constraint.

### Detection vs. User Curation

**Decision**: Automated detection with manual review, not fully automatic application.

**Reasoning**: Crop detection is a heuristic — it can be wrong. A fully automatic pipeline that silently modifies files would risk cropping content incorrectly on variable-ratio films. The confidence scoring system makes the review process fast (batch-apply all High confidence items in one click) while keeping the user in control for edge cases.

### Sampling Window (5–60 Minutes)

**Decision**: Movie sampling starts at 5 minutes and ends at 60 minutes.

**Reasoning**: Most films are longer than 60 minutes, and the first hour reliably captures the body of the work. Starting at 5 minutes avoids studio logos and opening credits, which are often in a different ratio. Ending at 60 minutes avoids closing credits. For films shorter than 60 minutes, the pipeline samples proportionally (e.g., a 90-second short film might sample at 30, 45, and 60 seconds). This is already handled by the existing script's duration check.

---

## Appendix: Relationship to Other Marquee Features

- **Library Sync (Phase 2)**: The resolution pre-filter uses metadata Marquee already has from Radarr/Sonarr sync. No additional scanning required.
- **Web UI (Phase 6)**: Letterbox management is a top-level section in the UI, alongside Poster Management and Subtitle Management.
- **Subtitle Management (Design 12)**: Both features share a pattern — inspect, preview, confirm, apply. They can share UI components and workflow conventions.
- **Radarr/Sonarr Upgrades**: When a movie is upgraded (better quality release), Marquee re-evaluates the resolution and re-runs the detection pipeline if the new file is a candidate. Previous crop tags are not persisted or re-applied — each upgrade gets a fresh detection pass.

---

# Part 2 — Implementation Plan

> This part turns the concept above into a buildable backend feature for the
> existing Marquee codebase (FastAPI + async SQLAlchemy + pydantic-settings).
> It picks an engine, settles the open choices, adds the data model + service +
> API the frontend will consume, and supplies a rewritten production-grade
> standalone script. Conventions and helpers are reused from the poster
> restoration feature (`design/more-features/01-poster-restoration.md`) and its
> code: `PosterService` (`marquee/core/poster_service.py`), the path guard
> `safe_translate_and_validate()` (`marquee/core/path_utils.py`), the
> `ArtworkEvent` audit pattern, the `RunManager` SSE/job pattern
> (`marquee/pipeline/run_manager.py`), and the system/heal routers.

## 12. Headline Decision: Replace ImageMagick `-trim` with ffmpeg `cropdetect`

The single most important change. The current script (`04-letterbox-script.sh`)
extracts a PNG per timestamp with ffmpeg, then runs ImageMagick `convert -fuzz
N% -trim` three times per frame and votes on the trimmed dimensions. This is
both **slower** and **less accurate** than the purpose-built tool that already
ships inside ffmpeg.

### 12.1 Why `cropdetect` is strictly better

**Accuracy.** `-trim` is a generic "strip solid edges" operation. It cannot
tell a letterbox bar from a genuinely dark frame, a black costume against a
black set, or a fade-to-black — it just crops whatever is uniform. A single
dark scene over-crops; a frame whose image content reaches the very edge
under-crops. ffmpeg's `cropdetect` filter is designed for exactly this job:
it samples luma against a black threshold and, crucially, **accumulates the
union of detected content across many consecutive frames within a window** so
that one anomalous dark frame cannot drive the result. As long as *any* frame
in the window has content reaching the true image extent, the converged crop
box is correct. That is precisely the failure mode (dark scenes) §5 worries
about — solved structurally instead of by majority vote over noisy per-frame
trims.

**Performance.** Per movie the old approach spawns ~48 processes (12 timestamps
× [1 ffmpeg + 3 `convert`]) and writes 12 full-resolution PNGs to disk.
`cropdetect` needs **one ffmpeg invocation per sampled window, no PNG, no
ImageMagick** — output is discarded to `-f null -` and we parse the filter's
stderr. Decode-only, no encode, no temp files. Combined with parallel file
processing this is roughly an order of magnitude less wall-clock and disk I/O.

**Free signals.** `cropdetect` reports the content rectangle `crop=W:H:X:Y`,
which gives us the `Y` offset for free — so we can detect and honor
**asymmetric** bars (top ≠ bottom) instead of assuming symmetry, and flag
off-center content as lower confidence.

### 12.2 How the engine works (per file)

For each sampled timestamp `T`:

```
ffmpeg -hide_banner -nostats -skip_frame nokey \
       -ss T -i FILE -an -sn \
       -t <window_seconds> \
       -vf cropdetect=limit=24:round=2:reset=1 \
       -f null - 2>&1
```

- `-ss T` **before** `-i` = fast input seek (no full decode to `T`).
- `-skip_frame nokey` decodes only keyframes inside the window — enough to
  detect bars, much cheaper.
- `cropdetect` prints lines to stderr ending in `crop=W:H:X:Y`. Within the
  window it *accumulates* (the content box only grows to encompass the largest
  content seen). We read the **final** `crop=` line of the window — that is the
  converged, dark-scene-immune verdict for timestamp `T`.
- `limit=24` is the black threshold on the 0–255 luma scale (handles slightly
  raised "dark gray" bars from cheap encoders); `round=2` forces even
  dimensions (codecs require even); tunable via config (§19).

This yields one `(W, H, X, Y)` measurement per timestamp. Then:

1. `full_h` = encoded height (from the pre-filter metadata or ffprobe).
2. For each timestamp: `top_bar = Y`, `bottom_bar = full_h − H − Y`.
   `bar = round((top_bar + bottom_bar) / 2)` for the symmetric summary; keep
   `top_bar`/`bottom_bar` for the asymmetric option.
3. Build the **consensus** across timestamps (§12.3).

### 12.3 Consensus → confidence, mapping cleanly onto §6 / §8

Let `bars = [bar_t for each sampled t]` (drop timestamps that failed to decode).

- **Not letterboxed** — `median(bars) ≤ NOISE_PX` (default 4): no tags, status
  `not_letterboxed`. (§5 fallback.)
- **Case A — variable, unsafe** (§6 A): at least one timestamp has `bar ≤
  NOISE_PX` *and* at least one has `bar > MIN_BAR_PX`. Some scenes fill the 16:9
  frame; any crop would clip them. Status `variable_unsafe`, no tags,
  confidence **Low**.
- **Case B — variable, safe-conservative** (§6 B): all `bar > MIN_BAR_PX` but
  spread `max(bars) − min(bars) > AGREE_PX`. Recommend the **minimum** bar (the
  narrowest scope ratio → preserves the most content, never clips). Confidence
  **Medium** if spread ≤ `MEDIUM_SPREAD_PX` (default 20), else **Low**.
- **Case C — consistent** (§6 C): spread ≤ `AGREE_PX` (default 2). Recommend the
  median. Confidence **High**.

Asymmetry: if `|median(top_bars) − median(bottom_bars)| > ASYM_PX` (default 2)
the bars are genuinely uneven; in `asymmetric` mode we recommend the per-edge
medians, otherwise we fall back to the symmetric `min`/`median` and drop one
confidence tier (the symmetry assumption is being violated). Off-center `X`
beyond a threshold downgrades confidence too (suggests pillarbox/odd encode,
not a clean scope letterbox).

This is a superset of §8's tier table and produces exactly the
`High/Medium/Low/None` tiers the inspection UI renders.

### 12.4 Verdict — `cropdetect` default, `trim` retained as a fallback

`cropdetect` is the **default** engine. The ImageMagick `-trim` method is
**kept as a selectable fallback** rather than deleted, behind a single config
switch `LETTERBOX_DETECT_METHOD ∈ {cropdetect, trim}` (§19). Rationale: `trim`
is the known-good path that produced the user's existing results, and a small
number of unusual encodes (e.g. very faint, color-cast bars that sit just under
`cropdetect`'s luma threshold) can read more cleanly under fuzzy trim. Keeping
both is cheap — they share the sampling schedule, the consensus/confidence math
(§12.3), and the `LetterboxState` shape; only the *per-window measurement* step
differs (parse `cropdetect` stderr vs. extract a frame and `convert -fuzz -trim`).

`marquee/media/letterbox_detect.py` exposes both as interchangeable
"measure one window → `(top_bar, bottom_bar)`" backends selected at runtime by
the config knob (and overridable per-request, so the inspection UI can offer a
"re-detect with the other method" action on a questionable result). The
standalone script (§23) mirrors this with a `--method cropdetect|trim` flag,
defaulting to `cropdetect`. Everything downstream of the per-window measurement
is identical regardless of method.

## 13. Engine Lives in Python, Not Bash (for the backend)

**Decision:** the backend detection/apply logic is a native Python module
(`marquee/media/`), not the backend shelling out to `04-letterbox-script.sh`.
Reasons: structured results (we need per-sample JSON, confidence, previews),
bounded parallelism tied to a job manager with SSE progress, graceful per-file
error handling, and — most importantly — every filesystem path must go through
`safe_translate_and_validate()` (§20) before any `mkvpropedit` write, which is
Python-side. The bash script (§23) is kept as a hardened standalone/manual tool
and as the reference for the exact ffmpeg/mkvtoolnix invocations.

## 14. Decisions on Open Points & New Features

### 14.1 Decisions

| Topic | Decision |
|---|---|
| Detection engine | ffmpeg `cropdetect` **default**, ImageMagick `-trim` retained as a config-selectable fallback (`LETTERBOX_DETECT_METHOD`, §12.4 / §19). Both feed the same consensus/confidence math; selectable per-request for a "re-detect with the other method" action. |
| Backend engine language | Native Python + `subprocess` (§13). |
| Phase-1 scope | **Movies only**, MKV only — matches `PosterService`, which is movie-only today (TV restoration is deferred there too). The engine itself is media-type agnostic; TV is a later phase that adds per-episode rows + the season cascade (§14.2). |
| Apply policy | **Never auto-apply by default.** Detection writes state + confidence; tags are applied only on explicit user confirm (single or batch). An opt-in `LETTERBOX_AUTO_APPLY_HIGH` knob exists for power users (§19) but defaults off, honoring §11 "Detection vs. User Curation". |
| Symmetric vs asymmetric | Symmetric by default (matches reality for ~all scope films and the player ecosystem); asymmetric application available via config/override when bars are genuinely uneven (§12.3). |
| Resolution for pre-filter | Persist `video_width`/`video_height`/`container` on `Movie` from Radarr `movieFile.mediaInfo` during sync (cheap, no decode); ffprobe on demand only when missing (§16, §21). |
| Re-detection on upgrade | Fresh detection pass, tags not re-applied automatically (§11). Webhook marks state stale and enqueues detection (§21). |

### 14.2 New features added (smoothing rough edges / things users will want)

1. **Before/after preview frames** (`GET …/preview`): extract one representative
   frame and render a small webp showing the proposed crop lines (and an
   after-crop thumbnail). Powers §8's "sample breakdown" grid without shipping
   full frames to the browser. Reuses PIL (already a dependency) and mirrors the
   taste-map thumbnail pattern.
2. **Persisted sample breakdown**: every timestamp's `(t, W, H, X, Y, bar,
   ok/error)` is stored as JSON so the inspection view and confidence are
   reproducible without re-running detection.
3. **Aspect-ratio labels**: derive a friendly label from the crop (`2.39:1`,
   `2.00:1`, `1.85:1`) for display next to the raw pixel crop.
4. **Reviewed / Ignored state**: `variable_unsafe` and user-skipped files are
   marked reviewed so library re-scans don't re-flag them (§10 Flow 3). The
   three UI tabs (Candidates / Tagged / Skipped) are just `status` filters.
5. **Tag-drift self-heal**: a periodic + on-demand scan that verifies tagged
   files still carry their crop tags (tags are lost when another tool remuxes,
   §7) and re-applies from stored state. Mirrors the poster self-heal loop.
6. **Batch detect with live progress (SSE)** and **batch apply / batch ignore**,
   so "Detect All" and "Apply all High-confidence" are one call each.
7. **Player-compatibility note** surfaced in the API (`honored_by:
   ["plex-desktop","vlc","mpv"]`, not honored by Plex web/mobile) so the UI can
   set expectations (§7).
8. **MP4/other containers** are reported as `ineligible` with a human reason
   ("crop tags require MKV; remux losslessly to enable") instead of silently
   vanishing (§9 robustness).
9. **Early termination**: if the first `EARLY_STOP_WINDOWS` sampled windows all
   read `bar ≤ NOISE_PX`, stop sampling and classify `not_letterboxed` (§9).
10. **TV season cascade (later phase)**: detect S0xE01, and if confidence is
    High, offer to cascade the same crop across visually-consistent episodes in
    the season (§ design decision "Detection Per-File vs. Per-Season").

## 15. Tech Stack & System Dependencies

- **ffmpeg / ffprobe** — frame-window decode + `cropdetect`; ffprobe for
  on-demand resolution/duration/container. (New external dependency for the
  service; nothing in `marquee/` shells out today — note in deploy docs.)
- **mkvtoolnix** — `mkvpropedit` (apply/remove crop tags in-place, milliseconds)
  and `mkvmerge -J` (robust **JSON** track-property read to detect existing
  tags, replacing fragile `mkvinfo` text-parsing; `mkvinfo` kept as a fallback).
- **Python stdlib `subprocess`** + `concurrent.futures` for a bounded worker
  pool (detection is CPU-bound, **no GPU** — so it does *not* contend with the
  poster pipeline's GPU lock, but parallelism is capped via config to avoid
  starving it of CPU/disk).
- **Pillow** (already present) — preview/thumbnail rendering.
- **ImageMagick `convert`** — *optional*, required only when
  `LETTERBOX_DETECT_METHOD="trim"` (the fallback engine, §12.4). The binary
  probe reports it; selecting `trim` without it returns a clear 503/error rather
  than failing mid-scan. `cropdetect` (default) needs no ImageMagick.
- No new Python package dependencies. A startup probe records which binaries are
  present and exposes it at `GET /api/letterbox/status` so the UI can show an
  actionable "install mkvtoolnix" banner instead of failing opaquely.

## 16. Data Model Changes

Additive migration (follow the established Alembic baseline-then-additive
pattern; `server_default` on every new NOT NULL column so it applies cleanly to
the populated live DB — same lesson as the poster work).

**`movies` — new columns** (pre-filter inputs; populated by sync §21):

```python
video_width:  Mapped[int | None]   = mapped_column(Integer, nullable=True)
video_height: Mapped[int | None]   = mapped_column(Integer, nullable=True)
container:    Mapped[str | None]   = mapped_column(String(16), nullable=True)  # "matroska", "mp4", ...
```

**`letterbox_state` — new table** (one row per movie; the engine is media-type
agnostic so a nullable `series_id`/`episode_id` can be added in the TV phase):

```python
class LetterboxState(Base, TimestampMixin):
    __tablename__ = "letterbox_state"
    id:              Mapped[int]  = mapped_column(Integer, primary_key=True)
    movie_id:        Mapped[int]  = mapped_column(ForeignKey("movies.id", ondelete="CASCADE"),
                                                  unique=True, index=True)
    status:          Mapped[str]  = mapped_column(String(24), nullable=False, server_default="'candidate'")
        # candidate | not_letterboxed | variable_unsafe | tagged | skipped | ineligible | errored
    confidence:      Mapped[str | None] = mapped_column(String(8))   # high | medium | low | none
    eligible:        Mapped[bool] = mapped_column(Boolean, server_default="1")   # MKV + writable + has video
    ineligible_reason: Mapped[str | None] = mapped_column(String(120))
    source_width:    Mapped[int | None]  = mapped_column(Integer)
    source_height:   Mapped[int | None]  = mapped_column(Integer)
    recommended_crop_top:    Mapped[int | None] = mapped_column(Integer)
    recommended_crop_bottom: Mapped[int | None] = mapped_column(Integer)
    aspect_label:    Mapped[str | None]  = mapped_column(String(12))  # "2.39:1"
    applied_crop_top:    Mapped[int | None] = mapped_column(Integer)  # NULL = no tags currently applied
    applied_crop_bottom: Mapped[int | None] = mapped_column(Integer)
    samples_json:    Mapped[str | None]  = mapped_column(Text)        # per-timestamp breakdown
    reviewed:        Mapped[bool] = mapped_column(Boolean, server_default="0")
    last_detected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_applied_at:  Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error:           Mapped[str | None]  = mapped_column(Text)
```

**Audit:** reuse the `ArtworkEvent` pattern but for letterbox actions. Cheapest
path that keeps separation: add a small `LetterboxEvent` table (`movie_id`,
`action` ∈ {detect, apply, remove, ignore, heal_reapply, error}, `detail` JSON,
`created_at`) — analogous to `artwork_events`. This keeps the poster audit log
clean and gives the UI a per-movie letterbox history.

## 17. Module Plan

```
marquee/media/                         (new package — media-file inspection/mutation)
  probe.py            ffprobe wrappers: dimensions, duration, container, video-track presence.
                      prefilter_bucket(width,height) -> "skip"|"candidate" per §4.
  letterbox_detect.py cropdetect invocation + parse, per-window measurement,
                      consensus → (status, confidence, crop, samples, aspect_label) per §12.3.
                      preview_frame(file, t, crop) -> webp bytes.
  binaries.py         resolve + cache availability of ffmpeg/ffprobe/mkvpropedit/mkvmerge;
                      thin checked-subprocess runner (timeout, returncode, captured stderr).

marquee/core/
  letterbox_service.py  LetterboxService singleton — THE single write path.
                        apply(db, movie, top, bottom): eligibility recheck → safe path validate
                        → mkvpropedit set → verify via mkvmerge -J → update LetterboxState +
                        LetterboxEvent (mirrors PosterService.deploy()).
                        remove(db, movie): mkvpropedit delete → verify → state/event.
                        Both are idempotent and atomic-at-the-metadata level.

marquee/media/letterbox_manager.py   LetterboxManager singleton (mirrors RunManager):
                        batch detect over a bounded ProcessPool/thread pool, per-job SSE
                        event buffer + replay, writes LetterboxState rows, DB job provenance.
                        CPU-bound → independent of the GPU lock; parallelism capped by config.

marquee/models/letterbox.py          LetterboxState, LetterboxEvent (+ exports in models/__init__.py).
marquee/api/routes/letterbox.py      router (prefix /api/letterbox) — §18.
marquee/config.py                    config additions — §19.
marquee/main.py                      register letterbox_router; optional tag-drift heal loop.
alembic/versions/…                   additive migration — §16.
tests/test_letterbox.py              consensus math, prefilter buckets, eligibility, parse of a
                                     captured cropdetect stderr fixture, endpoint control paths
                                     (no ffmpeg needed — feed canned stderr / fabricated state rows,
                                     same style as test_run_endpoints.py).
```

## 18. API Contracts

All under `/api/letterbox`. Shapes are designed so the frontend can build the
three-tab view, the inspection panel, and batch actions without post-processing.

| Method & path | Purpose | Notes |
|---|---|---|
| `GET /status` | Counts per tab, last scan time, **binary availability** | `{counts:{candidate,tagged,skipped,…}, binaries:{ffmpeg,mkvpropedit,…}, last_scan}` |
| `GET /candidates?status=&confidence=&sort=&page=&page_size=` | Paginated state list — drives all three tabs via `status` | Each row: movie id/title/year, status, confidence, source res, recommended crop, aspect_label, reviewed, applied flag |
| `GET /movies/{id}` | Full inspection detail | Includes `samples` breakdown, confidence rationale, `preview_urls`, eligibility/ineligible_reason, `honored_by` |
| `POST /movies/{id}/detect` | Detect one movie | 202 + job_id, or sync for a single file |
| `POST /detect` | Batch detect `{movie_ids?, all_candidates?}` | 202 + `job_id` |
| `GET /jobs/{job_id}/events` | **SSE** progress for a batch detect | Mirrors `GET /api/pipeline/runs/{id}/events` |
| `GET /movies/{id}/preview?t=<sec>&mode=before\|after` | Preview frame (webp) | `FileResponse`, path-confined like the poster route |
| `POST /movies/{id}/apply` | Apply tags `{top?, bottom?}` (override optional) | Validated write via `LetterboxService` |
| `POST /apply` | Batch apply `{movie_ids[], only_high?}` | |
| `POST /movies/{id}/remove` | Remove crop tags | |
| `POST /movies/{id}/ignore` | Mark reviewed → Skipped tab | |

Control paths: 404 unknown movie/job; 409 if a batch detect is already running
(reuse the "active job" pattern); 422 ineligible on apply (MKV-only / not
writable) with the reason string; 503 if required binaries are missing.

## 19. Config Additions (`marquee/config.py`)

```python
# Letterbox detection / application
LETTERBOX_ENABLED:        bool      = True
LETTERBOX_DETECT_METHOD:  str       = "cropdetect"   # "cropdetect" (default) | "trim" (ImageMagick fallback)
LETTERBOX_TRIM_FUZZ:      list[int] = [5, 15, 25]    # fuzz % tried in the trim fallback (needs ImageMagick `convert`)
LETTERBOX_FFMPEG:         str       = "ffmpeg"
LETTERBOX_FFPROBE:        str       = "ffprobe"
LETTERBOX_MKVPROPEDIT:    str       = "mkvpropedit"
LETTERBOX_MKVMERGE:       str       = "mkvmerge"
LETTERBOX_MOVIE_SAMPLES_MIN: int    = 5      # sample start (minutes)
LETTERBOX_MOVIE_SAMPLES_MAX: int    = 60     # sample end (minutes)
LETTERBOX_MOVIE_SAMPLE_STEP: int    = 5      # minutes between samples
LETTERBOX_TV_SAMPLES:     list[int] = [5, 10, 15]   # minutes
LETTERBOX_WINDOW_SECONDS: int       = 2      # cropdetect accumulation window per sample
LETTERBOX_CROPDETECT_LIMIT: int     = 24     # black luma threshold (0–255)
LETTERBOX_CROPDETECT_ROUND: int     = 2
LETTERBOX_NOISE_PX:       int       = 4      # ≤ this ⇒ "no bars"
LETTERBOX_MIN_BAR_PX:     int       = 8      # bar must exceed this to count as scope
LETTERBOX_AGREE_PX:       int       = 2      # spread for High confidence (Case C)
LETTERBOX_MEDIUM_SPREAD_PX: int     = 20     # Medium vs Low boundary (§8)
LETTERBOX_ASYM_PX:        int       = 2      # top/bottom asymmetry tolerance
LETTERBOX_EARLY_STOP_WINDOWS: int   = 3      # consecutive no-bar samples ⇒ stop early
LETTERBOX_MAX_PARALLEL:   int       = 0      # 0 = auto (cpu_count − 1)
LETTERBOX_AUTO_APPLY_HIGH: bool     = False  # opt-in: auto-apply High-confidence detections
LETTERBOX_ASYMMETRIC:     bool      = False  # honor uneven top/bottom bars
LETTERBOX_HEAL_ENABLED:   bool      = True   # periodic tag-drift verification
LETTERBOX_HEAL_INTERVAL_MINUTES: int = 360
```

Plus a `letterbox_preview_path` property under `DATA_DIR/cache/letterbox/` for
generated preview webps (mirrors `poster_cache_path`).

## 20. Eligibility & Safety — "only touch files we can"

Before any `mkvpropedit` write, a file must pass **all** of:

1. **Path is in-bounds** — `safe_translate_and_validate(path, source="radarr")`
   (null-byte reject, `..`/symlink collapse, must resolve inside a configured
   media root). This is the same guard the poster writer uses; it is mandatory
   here because we mutate the user's actual media files.
2. **Container is MKV** — extension `.mkv` *and* `mkvmerge -J` confirms a
   Matroska container with a video track. MP4/AVI → `ineligible` (clear reason),
   never touched.
3. **Regular file & writable** — `path.is_file()` and `os.access(path, os.W_OK)`;
   otherwise `ineligible` ("read-only").
4. **Has a video track** — from `mkvmerge -J`; audio-only/garbage → `ineligible`.

`mkvpropedit` edits header metadata in place (no rewrite), but we still: take a
per-file `asyncio.Lock` (no two writers on one file), capture the return code,
and **verify** the resulting tags by re-reading `mkvmerge -J`; only then update
`LetterboxState` and write the `LetterboxEvent`. Remove is the inverse and is a
no-op (idempotent) if no tags are present. Detection is read-only and never
writes to the media file.

## 21. Integration: Sync, Webhooks, Self-Heal

- **Sync (`marquee/core/sync_service.py`)**: when upserting a movie, read
  `data["movieFile"]["mediaInfo"]` → `width`/`height` and `data["movieFile"]`
  container/extension; populate the new `movies` columns. Free (already in the
  Radarr payload), and it lets the pre-filter (§4) run as a pure metadata query
  — no decode — to seed `letterbox_state` rows with `status=candidate` vs
  `skip`. ffprobe is the fallback only when `mediaInfo` is absent.
- **Webhooks (`marquee/api/routes/webhooks.py`)**: on Radarr `Download` +
  `isUpgrade`, in addition to poster restore, mark the movie's
  `letterbox_state.status` back to `candidate`/stale and enqueue a re-detect
  (fast-ACK background task, same pattern as `_restore_after_upgrade`). New file
  ⇒ tags are gone ⇒ fresh pass (§11).
- **Self-heal (`marquee/core/heal.py` sibling)**: a `letterbox_heal_scan()`
  (periodic via a lifespan loop like the poster heal, gated by
  `LETTERBOX_HEAL_ENABLED`) that, for every `tagged` row, re-reads `mkvmerge -J`
  and re-applies from stored state if the tags went missing (remux drift, §7).
  Exposed at `POST /api/letterbox/heal` and folded into `GET
  /api/system/status`.

## 22. Build Order & Verification

1. **Probe + prefilter + binaries** (`marquee/media/probe.py`, `binaries.py`) —
   unit-test `prefilter_bucket()` against §4's resolution table; no ffmpeg
   needed.
2. **Detection engine** (`letterbox_detect.py`) — parse a **captured**
   cropdetect stderr fixture into measurements; unit-test the §12.3 consensus →
   confidence mapping across Cases A/B/C and not-letterboxed. (Engine logic is
   fully testable from canned stderr — no media files in CI.)
3. **Data model + migration** (§16) — verify on a *copy* of the live DB first
   (the established practice), `server_default` on NOT NULL columns.
4. **LetterboxService** (apply/remove) — test eligibility gating + path
   validation with a fixture that neutralizes media roots (as
   `test_poster_service.py` does); mock `mkvpropedit`/`mkvmerge` runners.
5. **LetterboxManager + routes + SSE** — endpoint control paths (404/409/422/503)
   against fabricated `LetterboxState` rows, mirroring `test_run_endpoints.py`.
6. **Sync + webhook + heal integration** (§21).
7. **Previews** (`GET …/preview`) + the standalone script refresh (§23).
8. **Live smoke test** (manual, user-run on the GPU box): detect a known scope
   film (e.g. a 1920×1080 2.39:1 title) → expect High + ~140px; an IMAX/variable
   title (Interstellar) → expect `variable_unsafe`; apply → `mkvmerge -J` shows
   the crop; remove → gone; confirm crop in Plex Desktop on the ultrawide.

Gate at each step: `ruff check marquee tests` (the only lint gate) and `pytest`.

## 23. Rewritten Production-Grade Standalone Script

Drop-in replacement for `04-letterbox-script.sh`, kept for manual/standalone use
and as the canonical reference for the ffmpeg/mkvtoolnix invocations the Python
engine mirrors. Key changes vs. the original:

- **`cropdetect` by default** (§12) — faster, dark-scene-robust, no temp files,
  no ImageMagick dependency. The legacy PNG-extract + ImageMagick `-trim` path
  is retained behind `--method trim` for the rare faint/color-cast-bar encodes
  where it reads more cleanly; both feed the same consensus logic. (The listing
  below shows the `cropdetect` backend; the `trim` backend swaps only the
  `detect_window` body for the `ffmpeg -frames:v 1` + `convert -fuzz -trim`
  measurement.)
- **Dependency preflight** — verifies `ffmpeg`/`ffprobe`/`mkvpropedit`/`mkvmerge`
  exist before doing anything.
- **Per-file fault isolation** — a failed probe/decode/extract logs and
  `continue`s instead of aborting the whole batch (the original's `pipefail`
  would kill the run on one bad frame).
- **Eligibility gating** — only operates on regular, writable `.mkv` files with
  a video track; everything else is reported and skipped. Null-byte/odd names
  handled via `find -print0`.
- **Safe by default** — `--detect` *never* writes; applying tags is the separate
  explicit `--apply`. The old auto-apply `--movie`/`--tv` combos are gone.
- **Structured `--json` output** so the same script can back ad-hoc tooling.
- **Asymmetric-aware** — honors detected top/bottom independently (with a
  `--symmetric` override).
- **Variable-ratio detection** — reproduces Cases A/B/C and refuses to crop
  unsafe (16:9-containing) files.

```bash
#!/usr/bin/env bash
# letterbox.sh — detect & apply MKV pixel-crop tags for letterboxed media.
# Detection uses ffmpeg cropdetect (accurate, fast, no temp files). Safe by
# default: --detect never writes; --apply applies; --remove clears.
set -uo pipefail   # NOTE: no -e — we handle per-file errors and continue.

readonly NOISE_PX=4 MIN_BAR_PX=8 AGREE_PX=2 WINDOW=2 LIMIT=24 ROUND=2
SYMMETRIC=1; JSON=0; MODE=""; TARGET=""; APPLY_TOP=""; APPLY_BOTTOM=""
# Movie sampling: 5..60 by 5; TV: 5,10,15. Default movie; --tv switches.
SAMPLES=( $(seq 5 5 60) )

die(){ printf '❌ %s\n' "$*" >&2; exit 1; }
log(){ printf '%s\n' "$*" >&2; }

usage(){ cat >&2 <<EOF
Usage: $0 (--detect|--apply [--crop N]|--remove|--show) [--tv] [--json] [--symmetric] <file|dir>
  --detect     Detect letterbox crop (READ-ONLY); prints recommendation.
  --apply      Apply detected (or --crop N) pixel-crop tags to top & bottom.
  --remove     Remove any pixel-crop tags.
  --show       Show current pixel dimensions & crop tags.
  --tv         Use TV sampling (5,10,15 min) instead of movie sampling.
  --json       Machine-readable output.
  --symmetric  Force symmetric crop even if bars are uneven (default on).
EOF
exit 1; }

# ---- preflight: required tools -------------------------------------------
for bin in ffmpeg ffprobe mkvpropedit mkvmerge; do
  command -v "$bin" >/dev/null 2>&1 || die "missing required tool: $bin"
done

# ---- parse args -----------------------------------------------------------
while [[ $# -gt 0 ]]; do case "$1" in
  --detect) MODE=detect;;  --apply) MODE=apply;;  --remove) MODE=remove;;
  --show) MODE=show;;      --tv) SAMPLES=(5 10 15);;  --json) JSON=1;;
  --symmetric) SYMMETRIC=1;;
  --crop) shift; [[ "${1:-}" =~ ^[0-9]+$ ]] || die "--crop needs a number"; APPLY_TOP="$1"; APPLY_BOTTOM="$1";;
  -h|--help) usage;;
  -*) die "unknown option: $1";;
  *) [[ -z "$TARGET" ]] && TARGET="$1" || die "multiple targets";;
esac; shift; done
[[ -n "$MODE" && -n "$TARGET" ]] || usage

# ---- build file list (NUL-safe; mkv only) --------------------------------
FILES=()
if [[ -d "$TARGET" ]]; then
  while IFS= read -r -d '' f; do FILES+=("$f"); done \
    < <(find "$TARGET" -type f -iname '*.mkv' -print0)
elif [[ -f "$TARGET" ]]; then FILES=("$TARGET")
else die "'$TARGET' is not a file or directory"; fi
(( ${#FILES[@]} )) || die "no .mkv files found under '$TARGET'"

# ---- eligibility: regular, writable, mkv w/ video track ------------------
eligible(){ # $1=file -> 0 ok / prints reason on stderr if not
  local f="$1"
  [[ "${f,,}" == *.mkv ]] || { log "skip (not mkv): $f"; return 1; }
  [[ -f "$f" && -w "$f" ]] || { log "skip (missing/read-only): $f"; return 1; }
  mkvmerge -J "$f" 2>/dev/null | grep -q '"type": *"video"' \
    || { log "skip (no video track): $f"; return 1; }
}

probe_height(){ ffprobe -v error -select_streams v:0 \
  -show_entries stream=height -of csv=p=0 "$1" 2>/dev/null | tr -cd '0-9'; }
probe_duration(){ ffprobe -v error -show_entries format=duration \
  -of default=nokey=1:noprint_wrappers=1 "$1" 2>/dev/null | cut -d. -f1; }

# One cropdetect window at minute M -> echoes "TOP BOTTOM" bar px, or nothing.
detect_window(){ # $1=file $2=minuteM $3=full_height
  local f="$1" m="$2" fh="$3" ts line crop W H X Y
  ts=$(printf '%02d:%02d:00' $((m/60)) $((m%60)))
  # Fast input-seek, keyframes only, short accumulation window; parse last crop=.
  line=$(ffmpeg -hide_banner -nostats -skip_frame nokey -ss "$ts" -i "$f" \
                -an -sn -t "$WINDOW" \
                -vf "cropdetect=limit=${LIMIT}:round=${ROUND}:reset=1" \
                -f null - 2>&1 | grep -oE 'crop=[0-9]+:[0-9]+:[0-9]+:[0-9]+' | tail -n1) || return 1
  [[ -n "$line" ]] || return 1
  crop="${line#crop=}"; IFS=: read -r W H X Y <<<"$crop"
  echo "$Y $(( fh - H - Y ))"
}

analyze(){ # $1=file -> sets globals: STATUS CONF REC_TOP REC_BOTTOM ASPECT
  local f="$1" fh; fh=$(probe_height "$f")
  [[ "$fh" =~ ^[0-9]+$ ]] || { STATUS=errored; CONF=none; return 1; }
  local dur; dur=$(probe_duration "$f"); [[ "$dur" =~ ^[0-9]+$ ]] || dur=0
  local tops=() bots=() bars=() zero=0 nonzero=0 noprog=0
  for m in "${SAMPLES[@]}"; do
    (( dur>0 && m*60>dur )) && continue
    read -r t b < <(detect_window "$f" "$m" "$fh") || { log "   (decode fail @ ${m}m)"; continue; }
    [[ -z "${t:-}" ]] && continue
    local bar=$(( (t + b) / 2 ))
    tops+=("$t"); bots+=("$b"); bars+=("$bar")
    if (( bar <= NOISE_PX )); then zero=1; noprog=$((noprog+1)); else nonzero=1; noprog=0; fi
    (( noprog>=3 )) && break   # early stop on repeated no-bar windows
  done
  (( ${#bars[@]} )) || { STATUS=errored; CONF=none; return 1; }
  # min / max / median of bars
  local sorted; sorted=$(printf '%s\n' "${bars[@]}" | sort -n)
  local mn mx med n; mapfile -t S <<<"$sorted"; n=${#S[@]}
  mn=${S[0]}; mx=${S[n-1]}; med=${S[n/2]}
  if (( med <= NOISE_PX )); then STATUS=not_letterboxed; CONF=none; REC_TOP=0; REC_BOTTOM=0
  elif (( zero && nonzero )); then STATUS=variable_unsafe; CONF=low; REC_TOP=0; REC_BOTTOM=0
  elif (( mx - mn > AGREE_PX )); then
    STATUS=tagged_candidate; REC_TOP=$mn; REC_BOTTOM=$mn       # conservative = min bar
    (( mx-mn <= 20 )) && CONF=medium || CONF=low
  else
    STATUS=tagged_candidate; CONF=high; REC_TOP=$med; REC_BOTTOM=$med
  fi
  # asymmetry: honor uneven bars unless --symmetric
  if (( ! SYMMETRIC && STATUS == 0 )); then :; fi   # (full asym handled in Python engine)
  ASPECT=$(awk -v h="$fh" -v c="$REC_TOP" 'BEGIN{ if(h-2*c>0) printf "%.2f:1",(16.0/9.0)*h/(h-2*c); else print "?"}')
}

apply_tags(){ # $1 file $2 top $3 bottom
  mkvpropedit "$1" --edit track:v1 \
    --set pixel-crop-top="$2" --set pixel-crop-bottom="$3" \
    --set pixel-crop-left=0 --set pixel-crop-right=0 >/dev/null \
    && mkvmerge -J "$1" >/dev/null 2>&1   # verify readable afterwards
}
remove_tags(){ mkvpropedit "$1" --edit track:v1 \
    --delete pixel-crop-top --delete pixel-crop-bottom \
    --delete pixel-crop-left --delete pixel-crop-right >/dev/null 2>&1 || true; }

for f in "${FILES[@]}"; do
  eligible "$f" || continue
  case "$MODE" in
    show) log "🔎 $f"; mkvmerge -J "$f" | grep -E '"(pixel_dimensions|display_dimensions)"' || true;;
    remove) log "🧹 $f"; remove_tags "$f"; log "   ✅ tags cleared";;
    detect|apply)
      log "🔍 $f"; analyze "$f" || { log "   ⚠️ detect failed"; continue; }
      log "   status=$STATUS confidence=$CONF crop(top/bottom)=${REC_TOP:-0}/${REC_BOTTOM:-0} (${ASPECT:-?})"
      (( JSON )) && printf '{"file":"%s","status":"%s","confidence":"%s","top":%s,"bottom":%s,"aspect":"%s"}\n' \
                    "$f" "$STATUS" "$CONF" "${REC_TOP:-0}" "${REC_BOTTOM:-0}" "${ASPECT:-?}"
      if [[ "$MODE" == apply ]]; then
        local top="${APPLY_TOP:-$REC_TOP}" bot="${APPLY_BOTTOM:-$REC_BOTTOM}"
        if [[ "$STATUS" == variable_unsafe ]]; then log "   ⛔ unsafe (contains 16:9 scenes) — not applied"; continue; fi
        if (( ${top:-0} <= NOISE_PX )); then log "   ℹ️ not letterboxed — nothing to apply"; continue; fi
        apply_tags "$f" "$top" "$bot" && log "   ✅ applied ${top}/${bot}px" || log "   ❌ mkvpropedit failed"
      fi;;
  esac
done
```

> The Python engine (§17) issues the identical `ffmpeg … cropdetect` and
> `mkvpropedit`/`mkvmerge -J` calls via `subprocess`, but adds the path guard,
> per-file locks, structured `LetterboxState` persistence, previews, SSE batch
> progress, and the asymmetric-crop refinement that the bash version stubs out.

## Appendix B — End-to-End Walkthrough

1. **Sync** pulls Radarr `movieFile.mediaInfo` → `movies.video_width/height/
   container`. The pre-filter (§4) buckets each movie purely from metadata and
   seeds `letterbox_state` (`candidate` vs skipped). No decode yet.
2. User opens the Letterbox view → `GET /api/letterbox/candidates?status=candidate`.
   The UI also calls `GET /status`; if `binaries.mkvpropedit` is false it shows
   an install banner.
3. User hits **Detect All** → `POST /api/letterbox/detect {all_candidates:true}`
   → 202 + `job_id`. UI subscribes to `GET /jobs/{job_id}/events` (SSE) and
   watches the bar fill. `LetterboxManager` runs `cropdetect` across candidates
   in a CPU-bound pool (capped by `LETTERBOX_MAX_PARALLEL`), writing each
   `LetterboxState` as it finishes.
4. Rows update with `status`/`confidence`/`recommended_crop`/`aspect_label`.
   Interstellar lands `variable_unsafe` (Case A); The Matrix lands `high`/140px.
5. User inspects The Matrix → `GET /api/letterbox/movies/{id}` returns the sample
   breakdown + `preview_urls`; the UI shows before/after frames from
   `GET …/preview?mode=before|after`.
6. User selects all High and **Apply** → `POST /api/letterbox/apply
   {only_high:true}`. `LetterboxService` re-checks eligibility, validates each
   path, runs `mkvpropedit`, verifies via `mkvmerge -J`, flips rows to `tagged`,
   writes `LetterboxEvent`s. Files move to the Tagged tab.
7. Later, Radarr upgrades The Matrix → webhook flips it back to `candidate` and
   enqueues re-detection (new file, tags gone). The tag-drift heal scan catches
   any `tagged` file silently remuxed by another tool and re-applies from state.
8. On an ultrawide, Plex Desktop now renders The Matrix edge-to-edge; `--remove`
   / `POST …/remove` reverts instantly with zero quality cost.

---

# Part 3 — Build Status (Phase 1)

Backend implemented and unit-tested (no media binaries needed in CI — the
engine is exercised from captured `cropdetect` stderr + fabricated state). 36
new tests; full suite 223 passing; `ruff check` clean.

**Shipped**

- `marquee/media/` — `binaries.py` (lazy, cached binary resolution + availability
  probe + checked runner), `probe.py` (ffprobe + the §4 pre-filter, pure),
  `letterbox_detect.py` (both `cropdetect` and `trim` backends + §12.3 consensus),
  `letterbox_manager.py` (batch jobs, bounded CPU pool, SSE), `letterbox_preview.py`
  (before/after webp frames).
- `marquee/core/letterbox_service.py` — the single validated write path
  (eligibility §20 → `mkvpropedit` → verify → `LetterboxState` + `LetterboxEvent`).
- `marquee/core/letterbox_heal.py` — tag-drift scan (periodic + `POST /api/letterbox/heal`).
- `marquee/models/letterbox.py` — `LetterboxState` + `LetterboxEvent`; new
  `movies.video_width/height/container` columns.
- `marquee/api/routes/letterbox.py` — the 12 endpoints in §18, registered in `main.py`.
- Alembic migration `fd3dee2ea0da` (down-rev `14e34b6bd956`), verified up→down→up
  on a throwaway DB. **Not yet applied to the live DB** — run `alembic upgrade head`
  when ready (live DB is currently at `14e34b6bd956`).
- Sync captures Radarr `movieFile.mediaInfo` resolution; the upgrade webhook
  re-queues detection (clears stale applied-crop, flips row to `candidate`).
- Config knobs §19; periodic heal loop wired into the lifespan.
- Standalone script: `04-letterbox-script-v2.sh` (both backends, safe-by-default,
  `bash -n` clean). The original `04-letterbox-script.sh` is left untouched.

**Decisions honored:** `cropdetect` default + `trim` fallback (`LETTERBOX_DETECT_METHOD`);
movies-only MKV; manual-confirm apply (`LETTERBOX_AUTO_APPLY_HIGH=False`).

**⚠️ Two items to verify on the GPU box (coded defensively, not yet run against real binaries):**

1. **`cropdetect` option spelling.** We emit `cropdetect=limit=24:round=2:reset=1`
   with a fallback that retries without `reset` if the build rejects it. Confirm
   the installed ffmpeg accepts it and that detection returns sane crops on a
   known scope film.
2. **`mkvmerge -J` crop surfacing.** `read_applied_crop` scans track `properties`
   for crop keys; if this build doesn't expose them it returns "unknown", so the
   DB stays the source of truth for applied crop and the **tag-drift heal can't
   verify drift** (it logs `unverifiable` and leaves files alone). If drift
   detection matters, confirm the property name or switch to an `mkvinfo`
   text-parse fallback.

**Not in Phase 1 (as planned):** TV per-episode + season cascade; auto-apply;
the asymmetric default (available via `LETTERBOX_ASYMMETRIC`).
