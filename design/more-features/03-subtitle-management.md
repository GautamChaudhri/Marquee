# 12. Subtitle Management

## AI-Assisted Subtitle Inspection, Cleanup, and Generation

Marquee's subtitle management layer gives users full control over subtitle tracks across their media library — viewing, removing unwanted tracks, embedding external subtitles, and generating new ones via Subgen — all without touching video or audio quality.

---

## Table of Contents

1. [Problem Statement](#1-problem-statement)
2. [Core Principles](#2-core-principles)
3. [Feature Overview](#3-feature-overview)
4. [Subtitle Track Inspection](#4-subtitle-track-inspection)
5. [Track Removal](#5-track-removal)
6. [Auto-Language Filtering](#6-auto-language-filtering)
7. [External-to-Embedded Conversion](#7-external-to-embedded-conversion)
8. [Subgen Integration](#8-subgen-integration)
9. [Batch Operations](#9-batch-operations)
10. [Dry-Run Preview](#10-dry-run-preview)
11. [Container-Aware Handling](#11-container-aware-handling)
12. [Forced Subtitle Handling](#12-forced-subtitle-handling)
13. [Original File Backup](#13-original-file-backup)
14. [User Flows](#14-user-flows)
15. [Design Decisions and Trade-offs](#15-design-decisions-and-trade-offs)

---

## 1. Problem Statement

### Current State

Media files accumulate subtitle tracks from many sources — embedded tracks bundled in the original release, external SRT files downloaded by Bazarr, legacy tracks in languages the user doesn't speak. Over time, the library fills with subtitle clutter:

- **Unwanted language tracks** bloat files and clutter player subtitle menus. A movie might carry 15 embedded subtitle tracks in languages the user will never select.
- **Out-of-sync or wrong subtitles** — an embedded track was timed for a different release, or an external SRT was downloaded for the wrong episode.
- **External subtitle sprawl** — `.srt` and `.sub` files litter media folders. They're fragile: a Radarr/Sonarr upgrade that moves the media file leaves the orphaned subtitle behind, or the new folder lacks subtitles entirely.
- **Missing subtitles** — some media simply has no subtitle coverage, and manually hunting down or generating subtitles for each one is tedious.

### Why It Matters

Subtitles aren't just a convenience — for many users they're essential (hearing accessibility, noisy environments, non-native-language content). But the current ecosystem treats subtitle management as an afterthought:

- **Players show all tracks equally** — no way to hide the clutter of 12 unwanted language tracks without remuxing.
- **Bazarr handles download but not cleanup** — it adds, never removes.
- **No tool bridges the gap between inspection, removal, generation, and embedding** — users cobble together MKVToolNix GUIs, ffmpeg one-liners, and Subgen CLI flags.

---

## 2. Core Principles

1. **Never touch video or audio.** All subtitle operations use remuxing (stream copy), not re-encoding. Video and audio streams pass through bit-for-bit. The operation costs seconds per file, not minutes, and requires no GPU.

2. **Make the invisible visible.** Before the user can manage subtitles, they need to see what exists. Every track — embedded and external — is surfaced with its language, codec, forced status, and whether it's embedded or loose on disk.

3. **Manual and automatic modes.** The user can hand-pick tracks per movie, or define language policies (blacklist/whitelist) that apply automatically when new media enters the library.

4. **Subgen as a first-class tool.** Subtitle generation isn't a separate workflow — it's woven into the same UI. When a track is wrong or missing, generate a new one in-place.

5. **Safety by default.** Every destructive operation (removal, embedding that overwrites) shows a dry-run preview first. Original files can be backed up before modification.

---

## 3. Feature Overview

Marquee's subtitle management spans six interconnected capabilities:

| Capability | What it does | Triggers |
|---|---|---|
| **Inspection** | View all subtitle tracks for a media item (embedded + external), with language, codec, type, size | Manual browse |
| **Removal** | Strip unwanted subtitle tracks from the container via remuxing | Manual selection, or auto-policy on library add |
| **Embedding** | Take an external subtitle file and embed it into the media container via remuxing | Manual selection |
| **Generation** | Trigger Subgen to transcribe audio into a new `.srt`, then optionally embed it | Manual, from the subtitle UI |
| **Batch operations** | Apply inspection, removal, embedding, or generation across multiple media items at once | Manual bulk selection |
| **Auto-language filtering** | Blacklist or whitelist languages; on new media addition, automatically strip disallowed tracks | Webhook-driven (Radarr/Sonarr add) |

Supporting features: dry-run preview, container-aware command selection, forced subtitle flagging, and optional original-file backup.

---

## 4. Subtitle Track Inspection

### What the User Sees

For a given media item, Marquee displays all subtitle tracks — both embedded within the container and external files sitting alongside it — in a unified table:

| # | Source | Language | Codec | Type | Forced | Size |
|---|---|---|---|---|---|---|
| 1 | Embedded | English | PGS | bitmap | No | 12.3 MB |
| 2 | Embedded | French | PGS | bitmap | No | 10.1 MB |
| 3 | Embedded | Spanish | PGS | bitmap | No | 9.8 MB |
| 4 | External | English | SRT | text | No | 48 KB |
| 5 | External | English (SDH) | SRT | text | No | 52 KB |

**Embedded tracks** are read from the container's metadata via ffprobe — no extraction needed. The UI distinguishes them with a container icon and shows the codec (PGS, SRT, ASS, VobSub, mov_text).

**External tracks** are discovered by scanning the media file's directory for matching subtitle files (`.srt`, `.ass`, `.sub`, `.vtt`, `.idx`). The UI shows the file path and flags whether the filename suggests SDH, forced, or commentary.

### How Discovery Works

- On viewing a media item's subtitle page, Marquee runs `ffprobe` against the file to enumerate embedded streams.
- Simultaneously, it scans the file's directory for external subtitle files that match by filename convention (e.g., `Movie.Name.2024.eng.srt`).
- Results are merged into a single view, sorted by language then source (embedded first).

This is a read-only operation — nothing is modified.

---

## 5. Track Removal

### Concept

Removing an unwanted subtitle track means stripping it from the container so it no longer appears in player subtitle menus or consumes disk space. This is done via **remuxing** — the container is rewritten with the selected tracks omitted, while video and audio streams are copied bit-for-bit.

### How It Works (Conceptual)

1. User selects one or more tracks to remove from the inspection table.
2. Marquee shows a **dry-run preview** (see §10) confirming which tracks will be removed.
3. On confirmation, the file is remuxed: a new container is written with only the kept tracks, then atomically replaces the original.
4. The operation takes ~15–30 seconds for a typical movie (pure I/O, no encoding).

### Edge Cases

- **Last subtitle track**: If the user tries to remove every subtitle track, Marquee warns but allows it. Some users want no subtitles at all.
- **External tracks**: "Removing" an external subtitle means deleting the `.srt` file from disk — no remuxing needed. Marquee asks for confirmation before file deletion.
- **Forced tracks**: Tracks flagged as forced are visually distinguished in the inspection table so the user doesn't accidentally remove the one subtitle track that provides foreign-language translations.

---

## 6. Auto-Language Filtering

### Concept

Instead of manually cleaning every movie, the user defines a language policy once. When new media enters the library (via Radarr/Sonarr webhook or manual scan), Marquee automatically strips disallowed subtitle tracks.

### Two Modes

**Blacklist mode**: "Remove these languages."
> *Example: Remove all Arabic, Hindi, and Turkish subtitle tracks. Keep everything else.*

**Whitelist mode**: "Keep only these languages."
> *Example: Keep only English and Japanese subtitles. Strip everything else.*

The user picks one mode per library section (movies vs TV) or globally.

### When It Runs

The auto-filter fires on:
- **Radarr/Sonarr webhook** — when a new movie or episode is added and imported, the webhook triggers the filter after a short delay (giving the file time to finish writing and any post-import scripts to run).
- **Manual library scan** — when the user triggers a library-wide subtitle audit.
- **On-demand** — the user can apply their language policy to an existing item or selection without waiting for a webhook.

### Safety

- **No silent mutations**: The auto-filter logs every action. The user can see a history of what was removed from which file and when.
- **Opt-in per library**: Auto-filtering is disabled by default. The user explicitly enables it and chooses their mode/languages.
- **Dry-run audit mode**: Before enabling auto-filter, the user can run a "what would this do to my entire library?" audit to see how many tracks would be stripped.

---

## 7. External-to-Embedded Conversion

### Concept

External subtitle files (`.srt`, `.ass`) sitting alongside media files are fragile. When Radarr/Sonarr upgrades a movie, the new file lands in a new folder or with a different filename, and the external subtitle no longer matches. By embedding the subtitle directly into the container, it travels with the media file forever.

### How It Works (Conceptual)

1. User selects one or more external subtitle tracks from the inspection table.
2. User clicks "Embed."
3. Marquee remuxes the container, adding the selected subtitle files as new tracks inside the container. Video and audio are stream-copied.
4. After successful embedding, the original external file is optionally deleted (user's choice — default: keep).

### What Changes

- The subtitle moves from "External SRT on disk" to "Embedded text track inside the container."
- Player subtitle menus now show it as an internal track alongside any original embedded tracks.
- It survives media upgrades, folder moves, and renames because it lives inside the file.
- The operation is lossless — the subtitle data is identical, just repackaged.

### Format Compatibility

Different container formats support different subtitle codecs:

| Container | Can embed SRT | Can embed ASS | Can embed PGS | Can embed VobSub |
|---|---|---|---|---|
| MKV | ✅ | ✅ | ✅ | ✅ |
| MP4 | ⚠️ (as mov_text) | ❌ | ❌ | ❌ |
| AVI | ❌ | ❌ | ❌ | ❌ |

Marquee detects the container format and warns if the external subtitle format can't be embedded — suggesting conversion (SRT → mov_text for MP4) or recommending MKV as a target.

---

## 8. Subgen Integration

### Concept

When subtitles are missing, wrong, or out-of-sync, Marquee integrates with Subgen — a self-hosted AI subtitle generator powered by Whisper — to create new, accurately-timed subtitles from the media's audio track. This is exposed directly in the Marquee UI: no switching to another tool, no curl commands, no CLI flags to memorize.

### Subgen at a Glance

Subgen is a FastAPI service that:
- Transcribes audio from media files using Whisper (via faster-whisper + stable-ts).
- Outputs `.srt` subtitle files with word-level timestamps.
- Supports GPU (CUDA) and CPU modes.
- Can transcribe in the source language or translate to English.
- Exposes `/batch` (process a file or folder), `/status` (check progress), and `/docs` (Swagger UI).

Marquee treats Subgen as a configured external service, like Radarr or Sonarr.

### Generation Workflow

1. **Trigger**: From a media item's subtitle page, the user clicks "Generate Subtitles."
2. **Configuration panel**:
   - **Language**: Auto-detect, or force a specific language (e.g., Japanese, French).
   - **Mode**: Transcribe (keep original language) or Translate (convert to English).
   - **Whisper model**: Small / Medium / Large-v3 / Large-v3-turbo (model choice affects speed vs accuracy; GPU availability is detected and shown).
   - **Output**: External (`.srt` next to the media file) or Embedded (directly remuxed into the container).
3. **Marquee calls Subgen's `/batch` endpoint** with the media file path and selected options.
4. **Progress feedback**: Subgen reports progress; Marquee polls `/status` and shows a progress bar in the UI.
5. **Completion**: 
   - If "External" was selected, the `.srt` file is written next to the media file. The inspection table refreshes to show it.
   - If "Embedded" was selected, Marquee takes the generated `.srt` and remuxes it into the container, then removes the temporary external file.

### When to Use Subgen vs Traditional Download

| Situation | Best approach |
|---|---|
| No subtitles exist anywhere for this media | Subgen generation |
| Embedded subtitles are for the wrong release (mistimed) | Remove the bad track, then Subgen |
| Existing subtitles are correct but in a language the user wants to remove | Auto-language filter |
| Correct external subtitles exist but should be embedded | External-to-embedded conversion |
| Subtitles exist on OpenSubtitles / other providers | Bazarr handles this already; no Marquee involvement needed |

Marquee doesn't compete with Bazarr — it complements it. Bazarr handles traditional subtitle download; Marquee handles inspection, cleanup, embedding, and AI generation for the gaps Bazarr can't fill.

---

## 9. Batch Operations

### Concept

Managing subtitles one movie at a time doesn't scale. Marquee supports batch operations across multiple media items — entire seasons, collections, or filtered library subsets.

### Supported Batch Actions

1. **Batch inspection** — View subtitle summaries for multiple items side-by-side (e.g., "these 12 movies have French tracks").
2. **Batch language cleanup** — Apply the configured language policy (blacklist or whitelist) to a selection all at once.
3. **Batch embed** — Select external subtitles across multiple media items and embed them in one operation.
4. **Batch generate** — Trigger Subgen for multiple items that have no subtitle coverage.

### Queueing

Batch operations are queued rather than run in parallel. Remuxing is I/O-bound — running multiple remuxes simultaneously on the same disk would thrash it. The queue:

- Processes files one at a time.
- Shows progress: "Processing 7 of 24 (The Matrix)…"
- Can be paused and resumed.
- Survives server restarts (queue state is persisted).

### Selection

The user selects media items through the library grid using checkboxes, or by predefined filters:
- All movies in a collection.
- An entire TV season.
- "Everything without English subtitles."
- "Everything with more than 5 subtitle tracks."

---

## 10. Dry-Run Preview

### Concept

Before any modification touches a file, Marquee shows exactly what will happen — a before/after comparison of the subtitle track listing. The user confirms or cancels.

### What the Preview Shows

For a removal operation:

```
Before (4 tracks)                    After (2 tracks)
─────────────────────────────────────────────────────
✅ KEPT  #1 English PGS 12.3 MB     #1 English PGS 12.3 MB
❌ REM   #2 French PGS 10.1 MB      —
✅ KEPT  #3 Spanish PGS 9.8 MB      #2 Spanish PGS 9.8 MB
❌ REM   #4 External English SRT     —
─────────────────────────────────────────────────────
Disk savings: ~10.1 MB (embedded tracks) + 48 KB (external file)
File will be remuxed: yes
Original backup: no
```

For an embedding operation, the preview shows the new track that will appear after remuxing.

For a Subgen generation, the preview shows the estimated processing time, the model being used, and where the output will land.

### Confirm Step

Every modification requires explicit confirmation. The dry-run is not skippable — it's a safety gate, not a preference.

---

## 11. Container-Aware Handling

### Concept

MKV and MP4 containers have fundamentally different subtitle capabilities. Marquee knows which container a file uses and adapts its behavior accordingly.

### MKV (Matroska)

- **Native support** for SRT, ASS/SSA, PGS, VobSub, and many others.
- All operations (remove, embed, forced flagging) work without caveats.
- Preferred container for subtitle management. Marquee recommends MKV for users who want full subtitle flexibility.

### MP4

- **Only mov_text** (MP4 timed text) is widely supported as an embedded subtitle codec.
- SRT can be converted to mov_text during embedding (lossless text conversion).
- ASS/PGS/VobSub cannot be embedded into MP4 without re-encoding — Marquee warns and blocks these operations, suggesting the user either keep the subtitle external or remux to MKV first.
- Removing tracks works fine regardless of codec (it's just dropping a stream from the container).

### What the User Sees

The container format is shown in the media item header. The UI disables or warns about operations that aren't possible for the current container:
- "Embed ASS subtitle" is grayed out on an MP4 file with a tooltip explaining why.
- A "Convert to MKV" action is offered as a path forward (another remux — lossless, just changing the container).

---

## 12. Forced Subtitle Handling

### What Are Forced Subtitles?

Forced subtitles are tracks that should always display, regardless of the player's subtitle setting. They're used for:
- Foreign-language dialogue in an otherwise English movie (e.g., the Sicilian scenes in *The Godfather*).
- Translated signs, text messages, or location cards.
- Alien/constructed language translations.

### How Marquee Handles Them

- **Detection**: ffprobe reports the "forced" flag on embedded tracks. External filenames containing `.forced.` or `.forced-` are also recognized.
- **Visual distinction**: Forced tracks are highlighted in the inspection table (e.g., a lock icon or different row color).
- **Removal protection**: When the user selects tracks to remove, forced tracks show an extra "Are you sure?" confirmation. Batch language filters **never remove forced tracks** unless the user explicitly overrides this (opt-in checkbox).
- **Embedding with forced flag**: When embedding a subtitle (from external or from Subgen), the user can toggle a "Mark as forced" checkbox. The remux sets the forced flag on the new track so players treat it accordingly.

### The "Foreign Audio Only" Use Case

Subgen can generate subtitles for the entire movie, but sometimes the user only wants subtitles for the foreign-language portions. Marquee doesn't automate this (it's a complex editorial task), but:
- The user can generate full subtitles via Subgen, download the `.srt`, manually edit it to keep only the foreign portions, then use Marquee's embed feature with the "forced" flag.
- Marquee's inspection view shows whether existing forced tracks exist, so the user knows which movies have this gap.

---

## 13. Original File Backup

### Concept

Remuxing is safe and well-tested, but some users want the safety net of a backup before any modification. Marquee offers an optional backup mode.

### How It Works

- **Toggle per operation**: The dry-run preview includes a "Keep original file as backup" checkbox. Default: off.
- **Backup naming**: The original file is renamed to `filename.ext.bak` (e.g., `Movie.2024.mkv.bak`). It stays in the same directory.
- **Space awareness**: Marquee warns if the backup would exceed available disk space.
- **No automatic cleanup**: Backups are never deleted automatically — they accumulate until the user cleans them up manually, or Marquee could offer a "delete all backups older than N days" housekeeping function later.
- **Restore**: If something went wrong, the user can rename `.bak` back to the original filename manually. Marquee doesn't provide a one-click restore (a future enhancement).

### When to Use Backup

- First time using the subtitle management feature (trust-building).
- Files that are hard to replace (rare releases, personal rips).
- Batch operations on large selections (safety net for bulk changes).

For daily use after the user trusts the tool, backup can stay off to avoid doubling disk usage.

---

## 14. User Flows

### Flow 1: Cleaning Up a Single Movie

1. User opens *The Matrix* in Marquee's library view.
2. User clicks "Subtitle Tracks" to see the inspection table.
3. Table shows 14 tracks: English, French, Spanish, German, Italian, Portuguese, Russian, Arabic, Hindi, Turkish, Dutch, Swedish, Danish, Norwegian — plus 2 external SRT files.
4. User selects the 11 non-English, non-Spanish embedded tracks for removal.
5. Dry-run preview confirms: 11 tracks removed, ~95 MB saved, English and Spanish kept.
6. User confirms. File remuxes in ~22 seconds.
7. Inspection table refreshes showing 3 embedded tracks + 2 external.

### Flow 2: Setting Up Auto-Language Filtering

1. User navigates to Settings → Subtitle Management.
2. Chooses "Whitelist mode" for movies.
3. Enters: English, Japanese.
4. Enables "Apply to new media automatically."
5. Runs a "Library audit" to see what would happen to existing files. Audit shows 847 tracks across 312 movies would be stripped.
6. User decides to apply retroactively. Selects all 312 movies, clicks "Apply Language Policy."
7. Batch queue processes overnight. User checks the history log to confirm.

### Flow 3: Fixing Wrong Subtitles with Subgen

1. User watches an episode of a show and notices subtitles are 3 seconds early — clearly for a different release.
2. Opens the episode in Marquee, goes to Subtitle Tracks.
3. Sees one embedded English SRT track. Inspects it — confirmed, wrong timing.
4. User selects the bad track, clicks "Remove." Confirms in dry-run preview.
5. Track is gone. Inspection table now shows zero subtitle tracks.
6. User clicks "Generate Subtitles."
7. Chooses: Language = Auto-detect, Mode = Transcribe, Model = Medium, Output = Embedded.
8. Subgen processes the episode (~2 minutes for a 45-min episode on GPU).
9. New embedded English SRT track appears in the inspection table. Timing is correct.

### Flow 4: Embedding External Subtitles

1. User's library has *Spirited Away* with an external `Spirited.Away.2001.eng.srt` downloaded by Bazarr.
2. User wants it embedded so it survives upgrades.
3. Opens the movie's subtitle page. Sees the external SRT listed.
4. Clicks "Embed" on the external track.
5. Dry-run shows: container is MKV, SRT can be embedded natively, new track will appear as embedded.
6. User confirms. Remux completes in ~10 seconds.
7. User chooses "Delete external file after embedding." The `.srt` is removed from disk.

---

## 15. Design Decisions and Trade-offs

### Remuxing vs. In-Place Editing

**Decision**: Full remux (write a new file, atomically replace).

**Reasoning**: MKV and MP4 containers don't support in-place track removal. The alternative — using MKVToolNix's header editor to disable tracks without removing them — leaves the data in the file, consuming space. Full remux is faster for the user's end goal (actually recovering disk space and cleaning the player menu) and the I/O cost is negligible on modern storage.

### Subgen as External Service vs Embedded

**Decision**: Subgen runs as a separate service, called over HTTP.

**Reasoning**: Subgen has its own model lifecycle, GPU management, queueing, and Docker deployment story. Embedding Whisper directly into Marquee would duplicate all of that and tie Marquee's release cycle to Subgen's. The HTTP boundary is clean: Marquee sends a path and options, Subgen does the heavy lifting, Marquee polls for completion.

### Batch Queue vs Parallel Processing

**Decision**: Sequential queue for remux operations.

**Reasoning**: Remuxing is I/O-bound — the bottleneck is the disk, not the CPU. Running multiple remuxes in parallel on spinning rust (which many media servers use) would thrash the drive and make all operations slower than running them one at a time. Subgen generation IS parallelizable (GPU-bound), so Marquee can send multiple generation requests to Subgen concurrently.

### No Subtitle Download (Bazarr Stays)

**Decision**: Marquee does not download subtitles from OpenSubtitles, Podnapisi, etc.

**Reasoning**: Bazarr already does this extremely well. Marquee's subtitle management complements Bazarr by handling what Bazarr doesn't: inspection, removal, embedding, and AI generation. Users keep Bazarr for traditional subtitle acquisition and use Marquee for everything else. This avoids duplicating a mature ecosystem and keeps Marquee focused.

### File Modification Tracking

**Decision**: All modifications are logged with file path, timestamp, operation, and before/after track listings.

**Reasoning**: When the user wonders "why is my Spanish track gone?" or "when did I embed that SRT?", the log answers it. This is essential for trust in an automated system that modifies media files. The log is stored in Marquee's database and viewable in the UI.

---

## Appendix: Relationship to Other Marquee Features

- **Poster Caching (Phase 5)**: Subtitle management and poster caching share a pattern — both modify the media folder after a Radarr/Sonarr upgrade. The same webhook that triggers poster restoration can trigger auto-language filtering.
- **Web UI (Phase 6)**: Subtitle management is a first-class tab in the media detail view, alongside poster candidates.
- **Sync Service**: The library scanner that discovers media files can also report subtitle coverage stats (tracks per file, languages present, missing coverage).
