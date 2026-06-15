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
