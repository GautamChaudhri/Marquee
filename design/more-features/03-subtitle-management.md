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

External subtitle files (`.srt`, `.ass`) sitting alongside media files are fragile. When Radarr/Sonarr upgrades a movie, the new file lands in a new folder or with a different filename, and the external subtitle no longer matches. By embedding the subtitle directly into the container, it travels with the current media file through ordinary moves and renames.

Embedding protects a subtitle from ordinary folder moves and renames, but not
from a Radarr/Sonarr upgrade that replaces the media file itself. Marquee can
optionally keep a small managed copy of subtitles it embeds and re-embed them
into a replacement file after an upgrade.

### How It Works (Conceptual)

1. User selects one or more external subtitle tracks from the inspection table.
2. User clicks "Embed."
3. Marquee remuxes the container, adding the selected subtitle files as new tracks inside the container. Video and audio are stream-copied.
4. After successful embedding, the original external file is optionally deleted (user's choice — default: keep).

### What Changes

- The subtitle moves from "External SRT on disk" to "Embedded text track inside the container."
- Player subtitle menus now show it as an internal track alongside any original embedded tracks.
- It survives folder moves and renames because it lives inside the file.
- With managed-subtitle restoration enabled, Marquee can restore it after a
  media-file replacement.
- MKV text embedding preserves the subtitle content. MP4 SRT embedding converts
  it to `mov_text`, so the representation changes even though video and audio
  remain untouched.

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
- Exposes `/batch` for path-based generation, `/status` for service
  status/version, OpenAI-compatible upload endpoints, and `/docs`.
- Can notify Marquee through a configured completion webhook.

Marquee treats Subgen as a configured external service, like Radarr or Sonarr.

### Generation Workflow

1. **Trigger**: From a media item's subtitle page, the user clicks "Generate Subtitles."
2. **Configuration panel**:
   - **Language**: Auto-detect, or force a specific language (e.g., Japanese, French).
   - **Generator profile**: Shows the configured provider, mode, and model
     label. These are read-only unless the provider supports per-request
     changes.
   - **Output**: External (`.srt` next to the media file) or Embedded (directly remuxed into the container).
3. **Marquee calls Subgen's `/batch` endpoint** with the translated media file
   path and supported options.
4. **Progress feedback**: Marquee shows durable stages such as queued,
   provider-running, validating, and embedding. It uses the Subgen completion
   webhook plus filesystem reconciliation rather than inventing a percentage.
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
- **Managed location**: The original is retained under a hidden
  `.marquee/backups/` directory at the containing media root, outside the
  Radarr/Sonarr-managed title folder.
- **Space awareness**: Marquee warns if the backup would exceed available disk space.
- **No automatic cleanup**: Backups are never deleted automatically unless the
  user later enables a retention policy.
- **Restore**: Marquee provides a tracked restore action and re-probes the file
  after restoration.

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
2. User wants it embedded for cleaner playback and enables managed restoration
   so Marquee can re-embed it after a future upgrade.
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

**Reasoning**: Subgen has its own model lifecycle, GPU management, queueing, and Docker deployment story. Embedding Whisper directly into Marquee would duplicate all of that and tie Marquee's release cycle to Subgen's. The HTTP boundary is clean: Marquee sends a path and supported options, Subgen does the heavy lifting, and Marquee reconciles completion through a callback and output inspection.

### Batch Queue vs Parallel Processing

**Decision**: Sequential queue for remux operations.

**Reasoning**: Remuxing is I/O-bound — the bottleneck is the disk, not the CPU. Running multiple remuxes in parallel on spinning rust (which many media servers use) would thrash the drive and make all operations slower than running them one at a time. Generation concurrency is bounded by Marquee and the configured Subgen service rather than assumed to be unlimited.

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

---

# Part 2 - Implementation-Ready Backend Design

**Status:** Proposed for discussion before build
**Date:** 2026-06-15
**Scope:** Backend foundation and API contracts for the future frontend
**Primary code references:** `marquee/models/`, `marquee/core/sync_service.py`,
`marquee/core/path_utils.py`, `marquee/api/routes/webhooks.py`,
`marquee/pipeline/run_manager.py`, `marquee/main.py`, and `alembic/`

This part turns the conceptual feature above into two concrete backend
workstreams:

1. **Subtitle inventory and mutation:** inspect, classify, remove, embed,
   extract, edit metadata, apply policies, batch work, backup, and restore.
2. **Subtitle generation:** submit work to a configured generation provider
   such as Subgen, track it durably, validate the result, and optionally embed
   it through the same mutation path.

The frontend should be a thin client. It should render server-provided
capabilities, warnings, previews, progress, and history. It must never construct
FFmpeg/MKVToolNix commands, infer whether an operation is safe, or implement
language-policy rules.

## Part 2 Table of Contents

16. [Corrections and Decisions](#16-corrections-and-decisions-that-supersede-part-1)
17. [Product Behavior Worth Adding](#17-product-behavior-worth-adding)
18. [Technology Stack](#18-technology-stack)
19. [Shared Media-File Foundation](#19-shared-media-file-foundation)
20. [Subtitle Inventory Model](#20-subtitle-inventory-model)
21. [Policies](#21-policies)
22. [Durable Plans, Jobs, and Batches](#22-durable-plans-jobs-and-batches)
23. [Mutation Planning and Transaction](#23-mutation-planning-and-transaction)
24. [Generation Integration](#24-generation-integration)
25. [API Surface](#25-api-surface)
26. [Webhook Changes](#26-webhook-changes)
27. [Configuration Knobs](#27-configuration-knobs)
28. [File and Module Plan](#28-file-and-module-plan)
29. [Migration Plan](#29-migration-plan)
30. [Testing Strategy](#30-testing-strategy)
31. [Build Order](#31-build-order)
32. [Backend-Ready Acceptance Criteria](#32-backend-ready-acceptance-criteria)
33. [External Contract References](#33-external-contract-references)

## 16. Corrections and Decisions That Supersede Part 1

### 16.1 Media files are the unit of work

Subtitle operations modify a physical media file, not a Movie or Episode row.
This matters because:

- a movie can receive a replacement file after a Radarr upgrade;
- multiple Sonarr Episode rows can point to one multi-episode file;
- jobs must lock and fingerprint the exact file they will mutate;
- future features such as letterbox detection and HDR inspection need the same
  file-level identity.

Marquee therefore needs a first-class `MediaFile` model before subtitle work is
built. Convenience routes may still begin at a movie or episode, but they must
resolve to a `media_file_id` before planning an operation.

### 16.2 "Lossless" has a precise meaning

For removal and embedding, video and audio elementary streams are copied with
no re-encoding. The container itself is rewritten, so byte-for-byte identity of
the whole media file, muxer metadata, or stream ordering is not promised.

SRT-to-MP4 embedding transcodes the subtitle stream to `mov_text`; video and
audio remain stream-copied, but the subtitle representation is changed. ASS
styling cannot be preserved in MP4 timed text.

### 16.3 A dry-run is a persisted, expiring plan

A preview cannot remain valid forever. The file may be replaced by Radarr,
renamed, or modified between preview and confirmation.

Every manual mutation uses:

1. `plan` - inspect the current file and persist the exact proposed change;
2. `confirm` - verify the plan has not expired and the file signature still
   matches;
3. `execute` - queue the operation and stream progress.

Manual mutations always require confirmation. Explicitly enabled automatic
policies are the only exception; they still produce the same plan and audit
record internally.

### 16.4 Subgen capabilities must not be invented by Marquee

The current Subgen contract was reviewed on 2026-06-15. Important constraints:

- `POST /batch` accepts a file or directory path and an optional forced
  language.
- `/status` reports service status/version, not per-file percentage progress.
- Whisper model and several behavior choices are service-level configuration,
  not reliably selectable per `/batch` request.
- Subgen can send a completion webhook with the source video path and generated
  subtitle path.
- OpenAI-compatible upload endpoints exist, but their `model` parameter is
  accepted and ignored in favor of the configured Subgen model.

The frontend must therefore choose from capabilities reported by Marquee. It
must not show a per-request model picker unless a future provider adapter
actually supports one. Marquee owns durable job state; provider progress is
best-effort and stage-based rather than a fabricated percentage.

### 16.5 Generated "embedded" output is a two-stage operation

Subgen produces an external subtitle. If the user requests embedded output,
Marquee first validates the generated file, then runs the normal embed
transaction. Generation and remuxing remain separate stages in one parent job.

### 16.6 Container conversion is deferred

"Convert to MKV" is useful but changes the media filename and extension, can
break torrent hardlinks, and requires stronger Radarr/Sonarr coordination than
subtitle-only remuxing. The first implementation reports it as a recommendation
but does not perform it. It can be added later as a separate media-conversion
feature using the shared job framework.

### 16.7 Backups are managed and restorable

The original `filename.ext.bak` proposal is dropped. Loose backup files next to
media can confuse scanners and provide no reliable cleanup or restore workflow.

Backups are tracked in the database and stored under a hidden directory on the
same filesystem as the source but outside the Radarr/Sonarr-managed title
folder, for example:

```
/movies/.marquee/backups/<source_key>/<job_id>/Movie.mkv
```

The containing configured media root is selected automatically. This keeps the
backup available if Radarr replaces the whole movie folder. Marquee exposes
restore and delete actions. Automatic deletion is disabled by default; an
optional retention policy may be added after the core flow is trusted.

### 16.8 Hardlinks are protected by default

Many Radarr/Sonarr libraries use hardlinks for torrent seeding. Replacing a
hardlinked media file breaks the link and can unexpectedly double disk usage.
If `st_nlink > 1`, mutation plans are blocked by default and return a clear
warning. The user may explicitly enable `allow_break` globally or confirm an
individual override.

### 16.9 Automatic filtering is conservative

Unknown-language tracks, forced tracks, and commentary tracks need separate
semantics. A whitelist must not blindly delete `und` tracks, and a forced track
must not count as full-dialogue coverage.

Defaults:

- keep unknown-language tracks and flag them for review;
- protect forced tracks;
- protect the only full-dialogue subtitle track;
- exclude commentary and forced-only tracks from "full coverage";
- never auto-delete external subtitle files;
- skip hardlinked or unstable files;
- run in audit-only mode until the user explicitly enables mutation.

## 17. Product Behavior Worth Adding

These additions materially improve the feature without turning Marquee into a
subtitle-download service.

### 17.1 Coverage, not just track count

Each media file receives a server-computed coverage summary:

- audio languages;
- full-dialogue subtitle languages;
- forced-only languages;
- SDH/closed-caption availability;
- commentary tracks;
- external versus embedded coverage;
- missing preferred-language coverage;
- unknown or conflicting language metadata.

This supports useful frontend filters such as:

- no English full-dialogue subtitles;
- forced English only, with no full English track;
- external subtitles but no embedded subtitles;
- unknown-language tracks needing review;
- more than N subtitle tracks;
- generated subtitles present;
- policy violation count.

### 17.2 Track metadata editing

Users should be able to correct:

- language;
- title/name;
- default flag;
- forced flag;
- hearing-impaired/SDH flag where the container supports it.

The first implementation uses the normal remux transaction for consistency and
atomic replacement. A later MKV-only optimization may use `mkvpropedit` for
header-only edits after it has its own recovery tests.

### 17.3 Extraction and text preview

Users often need to inspect a suspicious embedded subtitle before deleting it.
Add:

- extract an embedded track to an external file;
- download an external or extracted track through a safe API;
- preview the first N text cues for SRT/ASS/SSA/WebVTT;
- report "bitmap subtitle - text preview unavailable" for PGS/VobSub;
- optionally shift text subtitles by a constant offset later using the same
  parser layer.

Extraction is non-destructive and does not require confirmation unless it would
overwrite an existing file.

### 17.4 Quarantine instead of immediate sidecar deletion

Manual external-file removal defaults to moving the file into
`<media-root>/.marquee/trash/<source_key>/<job_id>/`, not unlinking it.
IDX/SUB pairs move together. The history endpoint offers restore and
permanent-delete actions.

### 17.5 Provenance

Generated and embedded tracks carry a title such as:

```
English - generated by Subgen (large-v3-turbo)
```

The database records provider, model label, mode, source audio stream,
language, creation time, and parent job. The model label is informational and
comes from configured provider capabilities, not from an ignored request
parameter.

### 17.6 Duplicate and conflict warnings

Before embedding or generating, Marquee warns when an equivalent track appears
to exist:

- same normalized language and role;
- same external file hash;
- same generated provenance;
- full-dialogue coverage already present;
- output filename collision.

The user can still proceed with an explicit `allow_duplicate` option.

### 17.7 Managed subtitle restoration after upgrades

Embedding alone does not survive replacement of the media file. When a user
embeds an external or generated subtitle, Marquee offers "manage across
upgrades" (default on for generated subtitles, off for arbitrary manual
embeds).

For managed tracks:

- cache the exact source subtitle bytes under
  `data/cache/subtitles/<asset_id>/`;
- store language, role, title, flags, provider provenance, and content hash;
- bind the asset to its logical movie or episode, not only the current
  MediaFile;
- after a Radarr/Sonarr replacement, compare the new inventory to managed
  assets;
- create a normal, auditable re-embed plan for missing managed tracks;
- default to automatic restore only when the user enabled it for that asset or
  policy.

This is the subtitle equivalent of `PosterService` restoration, but it still
uses the same mutation queue, hardlink checks, and post-write validation as any
other embed.

## 18. Technology Stack

### 18.1 Required system tools

| Tool | Role |
|---|---|
| `ffprobe` | Canonical JSON probe for container, streams, dispositions, duration, tags, and audio languages |
| `ffmpeg` | MP4/MOV remuxing, subtitle conversion to `mov_text`, extraction, validation helpers, and optional audio extraction |
| `mkvmerge` | MKV mutation with Matroska-native handling of tracks, attachments, chapters, tags, languages, and flags |

Use MKVToolNix for MKV writes and FFmpeg for MP4/MOV writes. This is more
reliable than forcing every container through one muxer. `ffprobe` remains the
common read model so API responses are container-independent.

The Docker image in `docker/Dockerfile` must install `ffmpeg` and
`mkvtoolnix`. Bare-metal startup reports missing tools as degraded
capabilities; read-only library and poster features still work.

All commands use `asyncio.create_subprocess_exec()` with an argument list.
Never invoke a shell and never interpolate paths into command strings.

### 18.2 Python dependencies

| Package | Role |
|---|---|
| existing `httpx` | Subgen/provider calls |
| existing SQLAlchemy + Alembic | Inventory, plans, jobs, policies, events, and backups |
| `pysubs2` | Parse, validate, preview, normalize, convert, and later shift text subtitle cues |
| `langcodes` | Normalize ISO-639 and BCP-47 language tags and provide display names |
| `charset-normalizer` | Detect legacy text encodings before preview or normalization |

No Celery, Redis, RabbitMQ, or separate worker service is required for v1. A
single persisted SQLite queue and an asyncio worker fit Marquee's personal
library scale and deployment model.

### 18.3 Provider abstraction

Define a small protocol in `marquee/core/subtitles/generators/base.py`:

```python
class SubtitleGenerator(Protocol):
    async def health(self) -> GeneratorHealth: ...
    def capabilities(self) -> GeneratorCapabilities: ...
    async def submit(self, request: GenerationRequest) -> ProviderSubmission: ...
    async def reconcile(self, job: MediaJob) -> ProviderState: ...
```

The first adapter is `SubgenPathGenerator`. The API and database must not use
Subgen-specific fields outside the adapter so another OpenAI-compatible or
local generator can be added later.

## 19. Shared Media-File Foundation

### 19.1 New `media_files` table

Add `marquee/models/media_file.py`:

| Column | Type | Purpose |
|---|---|---|
| `id` | INTEGER PK | Stable Marquee file ID used by jobs and the frontend |
| `source` | VARCHAR(20) | `radarr`, `sonarr`, or future `standalone` |
| `source_key` | VARCHAR(100) UNIQUE | e.g. `radarr:movie-file:1234` or `sonarr:episode-file:5678` |
| `source_file_id` | INTEGER NULL | Native Radarr/Sonarr file ID |
| `movie_id` | FK movies.id NULL | Set for movie files |
| `path` | TEXT | Path in the source application's namespace |
| `relative_path` | TEXT NULL | Diagnostic/source metadata |
| `size_bytes` | BIGINT NULL | Last source-reported size |
| `container` | VARCHAR(20) NULL | Last probed container |
| `is_active` | BOOL | Current file versus replaced historical file |
| `last_seen_at` | TIMESTAMP | Last successful sync |
| `last_resolved_path` | TEXT NULL | Diagnostic only; never trusted without revalidation |
| `created_at` / `updated_at` | TIMESTAMP | Standard timestamps |

Do not persist a local path as authoritative. Path mappings can change. Every
filesystem operation resolves `MediaFile.path` through
`safe_translate_and_validate()` using its source.

### 19.2 Episode association table

Add `episode_media_files`:

| Column | Type | Purpose |
|---|---|---|
| `episode_id` | FK episodes.id | One logical episode |
| `media_file_id` | FK media_files.id | Physical file containing it |

Primary key: `(episode_id, media_file_id)`.

This correctly represents double episodes where several Episode rows share one
Sonarr episode-file ID.

### 19.3 Sync changes

Update `marquee/core/sync_service.py`:

- Radarr: capture `movieFile.id`, full path, relative path, and size; upsert one
  active `MediaFile` per movie.
- Sonarr: upsert each `episodeFile.id` once, then associate every Episode whose
  `episodeFileId` points to it.
- mark replaced files inactive instead of immediately deleting history needed
  by jobs/events;
- continue updating `Movie.movie_file_path` and
  `Episode.episode_file_path` during a compatibility period;
- deduplicate current legacy Episode rows that reference the same path.

Add `source_file_id` to the Radarr/Sonarr webhook payload models so a webhook can
target the exact file without waiting for the next full sync.

### 19.4 Shared resolver

Add `marquee/core/media_files.py`:

```python
async def resolve_media_file(db, media_file_id: int) -> ResolvedMediaFile:
    """Load the row, translate its source path, validate the root, and stat it."""
```

`ResolvedMediaFile` contains the database ID, source, validated `Path`, stat
data, owner entities, and a signature. Every subtitle, letterbox, and future
media operation should use this boundary.

## 20. Subtitle Inventory Model

### 20.1 `subtitle_inventories`

One current inventory per `media_file_id`:

| Column | Type |
|---|---|
| `id` | INTEGER PK |
| `media_file_id` | FK UNIQUE |
| `file_signature` | VARCHAR(128) |
| `container` | VARCHAR(20) |
| `duration_seconds` | REAL NULL |
| `audio_streams_json` | JSON |
| `chapters_count` | INTEGER |
| `attachments_count` | INTEGER |
| `coverage_json` | JSON |
| `probe_tool_versions_json` | JSON |
| `scanned_at` | TIMESTAMP |
| `error` | TEXT NULL |

The signature is based on resolved path, size, `mtime_ns`, and a cheap
first/last-block hash. It is not a content hash of a multi-gigabyte file. Its
purpose is stale-plan detection.

### 20.2 `subtitle_tracks`

Rows are versioned by their parent inventory and replaced transactionally on a
rescan:

| Column | Type | Notes |
|---|---|---|
| `id` | VARCHAR(32) PK | UUID used by API requests |
| `inventory_id` | FK | Cascade delete |
| `source` | VARCHAR(10) | `embedded` or `external` |
| `stream_index` | INTEGER NULL | FFprobe stream index |
| `tool_track_id` | INTEGER NULL | MKVToolNix track ID for MKV plans |
| `external_path` | TEXT NULL | Validated path; never accepted from client input |
| `paired_path` | TEXT NULL | IDX/SUB partner |
| `codec` | VARCHAR(40) |
| `kind` | VARCHAR(20) | `text`, `bitmap`, `teletext`, `unknown` |
| `language_raw` | VARCHAR(40) NULL |
| `language_tag` | VARCHAR(40) | Normalized BCP-47 or `und` |
| `language_source` | VARCHAR(20) | metadata, filename, user, unknown |
| `title` | TEXT NULL |
| `is_default` | BOOL |
| `is_forced` | BOOL |
| `is_sdh` | BOOL |
| `is_commentary` | BOOL |
| `is_generated` | BOOL |
| `size_bytes` | BIGINT NULL |
| `content_sha256` | VARCHAR(64) NULL | External files and extracted text when cheap |
| `metadata_json` | JSON |

Track IDs are inventory-scoped. After a remux, stream indexes may change and a
new inventory receives new track IDs. A plan always stores the inventory ID and
file signature that its selected tracks came from.

### 20.3 Managed subtitle assets

Add `managed_subtitle_assets`:

| Column | Purpose |
|---|---|
| `id` | UUID |
| `cache_path` | Exact cached source subtitle |
| `content_sha256` | Duplicate detection and integrity |
| `language_tag`, `title`, `kind` | Track identity |
| `is_default`, `is_forced`, `is_sdh`, `is_commentary` | Desired flags |
| `source` | `external`, `generated`, or `extracted` |
| `provenance_json` | Provider/model/job/original path metadata |
| `restore_on_replacement` | Whether webhook restore may run automatically |
| `active` | Disabled assets remain in history |
| timestamps | Creation and last successful restore |

Add `managed_subtitle_bindings` with `asset_id`, `owner_type`
(`movie` or `episode`), and `owner_id`. Application validation ensures the
owner exists. A single asset may bind to multiple Episode rows for a
multi-episode file.

The cached source is small and lives under Marquee's data directory, not the
media root. The cache never stores full media containers.

### 20.4 External discovery rules

Add `marquee/core/subtitles/external.py`:

- supported initially: `.srt`, `.ass`, `.ssa`, `.vtt`, `.sub`, `.idx`, `.sup`;
- require the subtitle basename to match the video basename exactly or continue
  with recognized dot-separated metadata tokens;
- never include a subtitle matching another video in the same folder;
- treat `.idx` + `.sub` as one logical VobSub track;
- parse language, `forced`, `sdh`, `cc`, `commentary`, and `subgen` tokens;
- use metadata when available, filename inference second, and `und` otherwise;
- keep the raw path server-side and expose only IDs plus display-safe relative
  names to the frontend.

### 20.5 Inventory cache behavior

`GET` inventory behavior:

1. stat the file;
2. return the cached inventory when the cheap signature still matches;
3. otherwise return `stale: true` and either:
   - refresh inline for a single-file detail request when expected to be fast;
   - enqueue a scan for bulk/library requests.

All successful mutations and generation completions trigger an immediate
rescan before the job is marked complete.

## 21. Policies

### 21.1 Tables

Add `subtitle_policies`:

| Column | Purpose |
|---|---|
| `id`, `name`, `enabled`, `revision` | Identity and stale-plan tracking |
| `mode` | `allowlist` or `blocklist` |
| `languages_json` | Normalized language tags |
| `unknown_action` | `keep`, `review`, or `remove`; default `keep` |
| `protect_forced` | default `true` |
| `protect_default` | default `true` |
| `protect_last_full_dialogue` | default `true` |
| `include_external` | default `false` |
| `auto_apply` | default `false` |
| `audit_only` | default `true` |
| `hardlink_action` | `block` or `allow_break`; default `block` |
| `backup_mode` | `none` or `keep_original`; default `none` |
| `created_at`, `updated_at` | timestamps |

Add `subtitle_policy_bindings` with `policy_id`, `scope_type`, and optional
`scope_id`. Resolution order:

1. media-file/item override;
2. series binding;
3. movie or TV library binding;
4. global binding.

The first UI only needs global, Movies, and TV bindings, but the schema should
support per-series rules without a migration.

### 21.2 Policy evaluator

Add `marquee/core/subtitles/policy.py`:

```python
def evaluate_policy(
    inventory: SubtitleInventorySnapshot,
    policy: SubtitlePolicySnapshot,
) -> PolicyEvaluation:
    ...
```

The result contains selected removals, protected tracks, review-required
tracks, coverage before/after, warnings, and human-readable reasons. It is pure
logic with no filesystem access and should receive extensive unit tests.

### 21.3 Import timing

Radarr/Sonarr Download webhooks schedule an audit after a configurable delay
(default proposal: 120 seconds) and a file-stability check. This allows import
scripts and Bazarr to finish. Repeated webhooks for the same source file and
signature collapse through an idempotency key.

Automatic mutation only occurs when:

- a matching policy has `enabled=true`, `auto_apply=true`, and
  `audit_only=false`;
- the file is stable and writable;
- the file is not hardlinked unless policy explicitly allows breaking it;
- no active media job already owns the file;
- the evaluation has no `review_required` result.

## 22. Durable Plans, Jobs, and Batches

The current `RunManager` in `marquee/pipeline/run_manager.py` is a useful SSE
pattern, but it is deliberately in-memory and serializes poster inference. It
must not be reused as the persistence layer for media mutation.

### 22.1 Tables

Add generic tables so later media-file features can reuse them.

**`media_batches`**

- `batch_id` UUID;
- operation, status, requested count, completed count, failed count;
- request/summary JSON;
- paused/cancel-requested flags;
- timestamps.

**`media_jobs`**

- `job_id` UUID;
- optional `batch_id`;
- `media_file_id`;
- `operation`:
  `subtitle_scan`, `subtitle_remove`, `subtitle_embed`,
  `subtitle_extract`, `subtitle_metadata`, `subtitle_policy`,
  `subtitle_generate`, `subtitle_restore`;
- `status`:
  `planned`, `queued`, `running`, `succeeded`, `failed`, `cancelled`,
  `interrupted`;
- `stage` and progress counters;
- `trigger`: `manual`, `batch`, `policy`, `webhook`;
- `request_json`, `plan_json`, `result_json`, `error_json`;
- `input_signature`, `plan_expires_at`, `confirmed_at`;
- `idempotency_key` UNIQUE NULL;
- `cancel_requested`;
- timestamps and attempt count.

**`media_job_events`**

- monotonically increasing event ID;
- job ID;
- stage/state/message/progress JSON;
- created timestamp.

**`media_backups`**

- backup ID, job ID, media-file ID;
- original path and backup path;
- original signature and size;
- status: available, restored, deleted, missing;
- timestamps.

### 22.2 Worker

Add `marquee/core/media_jobs/manager.py` and start one worker from
`marquee/main.py` lifespan:

- scans are allowed limited configurable concurrency;
- media mutations use a global semaphore of 1 by default;
- a per-`media_file_id` lock prevents conflicting scan/remux/generation
  post-processing;
- generation submission concurrency is configurable but bounded;
- jobs emit persisted events and mirror them to live SSE subscribers;
- batch pause prevents new child jobs from starting; the current subprocess is
  allowed to finish unless explicitly cancelled.

### 22.3 Restart recovery

At startup:

- stale `running` remux jobs become `interrupted`;
- leftover `.partial` files are inventoried and removed only when linked to a
  known job;
- an interrupted remux may be requeued only when the original still exists and
  matches the saved input signature;
- generation jobs in provider-wait stages run reconciliation against callback
  data and expected output files;
- no job is reported as successful until post-operation probe validation has
  passed.

### 22.4 SSE

`GET /api/media-jobs/{job_id}/events` replays persisted events, then switches to
live events, using the same response pattern as
`GET /api/pipeline/runs/{run_id}/events`.

Example event:

```json
{
  "job_id": "7a...",
  "stage": "remux",
  "state": "progress",
  "done": 48,
  "total": 100,
  "message": "Writing replacement container"
}
```

## 23. Mutation Planning and Transaction

### 23.1 Plan response

`POST /api/media-files/{media_file_id}/subtitle-plans` accepts an operation and
server-issued track IDs. It returns:

```json
{
  "job_id": "7a...",
  "status": "planned",
  "expires_at": "2026-06-15T22:15:00Z",
  "before": {"tracks": [], "coverage": {}},
  "after": {"tracks": [], "coverage": {}},
  "actions": [],
  "warnings": [
    {"code": "forced_track_selected", "requires_override": true}
  ],
  "capabilities": {"can_execute": true},
  "storage": {
    "source_bytes": 18200000000,
    "estimated_temp_bytes": 18170000000,
    "free_bytes": 42000000000,
    "backup_requested": false
  },
  "confirmation_required": true
}
```

The API never returns a shell command as something the client can edit. It may
return a sanitized diagnostic command preview for advanced users.

### 23.2 Preflight

Immediately before execution:

1. resolve and validate the path;
2. require a regular resolved target; allow a symlink only when its resolved
   target remains inside an allowed media root;
3. compare the saved file signature;
4. verify size/mtime stability across a configurable window;
5. check writability and same-filesystem temp location;
6. check free space for the complete replacement plus margin;
7. detect hardlinks;
8. acquire the file lock;
9. refresh the inventory if anything changed.

Failure returns a structured error code such as `plan_stale`,
`insufficient_space`, `hardlink_protected`, `file_unstable`, or
`path_not_writable`.

### 23.3 Temporary output

Write beside the source so final replacement uses the same filesystem:

```
.<original-name>.marquee.<job-id>.partial.<extension>
```

Never write directly over the input file.

### 23.4 Container adapters

Add:

```
marquee/core/subtitles/
    probe.py
    external.py
    languages.py
    capabilities.py
    policy.py
    service.py
    adapters/
        base.py
        matroska.py
        mp4.py
```

`MatroskaAdapter` uses `mkvmerge` track IDs from the saved inventory.
`Mp4Adapter` uses explicit FFmpeg mapping, stream copy, metadata/chapter
mapping, and `mov_text` only when conversion is required.

Unsupported containers are read-only. The capability response explains why
and recommends keeping the subtitle external.

### 23.5 Validation

Probe the temporary output before replacement and verify:

- at least one video stream still exists;
- video/audio stream counts and codecs match the input;
- duration is within a small tolerance;
- chapters and attachments were preserved where the adapter promises support;
- removed tracks are absent;
- kept tracks remain;
- embedded tracks have the requested language/title/dispositions;
- the output is non-empty and readable through a second probe.

Small integration fixtures additionally compare elementary-stream hashes before
and after remuxing. Production does not hash entire movie streams because that
would double I/O for every operation.

### 23.6 Backup and replace

When backup is requested:

1. create the tracked hidden backup directory;
2. prefer a hardlink from original to backup on the same filesystem;
3. fall back to copy + fsync when hardlinking is unavailable;
4. atomically `os.replace()` the validated temporary file over the original.

Without backup, step 4 is sufficient. Copy mode/ownership when permitted, keep
the new modification time so media scanners can detect the change, fsync the
file, and best-effort fsync the parent directory.

If replacement succeeds but the database update fails, startup reconciliation
uses the job ID, temporary/backup paths, and a fresh probe to repair the job
record.

### 23.7 Post-operation coordination

After success:

- rescan subtitle inventory;
- update `MediaFile` size/container/last-seen metadata;
- append an audit event with before/after snapshots and tool versions;
- ask Radarr or Sonarr to rescan the affected movie/series through their
  command API;
- expose a media-server refresh hook later, but do not make it a v1 dependency.

## 24. Generation Integration

### 24.1 Configuration

Add validated settings to `marquee/config.py`:

| Setting | Purpose |
|---|---|
| `SUBGEN_URL` | Base URL; unset means generation disabled |
| `SUBGEN_PROFILE_NAME` | Frontend display name |
| `SUBGEN_MODEL_LABEL` | Informational configured model label |
| `SUBGEN_MODE` | `transcribe` or `translate`, matching the service configuration |
| `SUBGEN_LOCAL_PATH_PREFIX` | Prefix as Marquee sees it |
| `SUBGEN_REMOTE_PATH_PREFIX` | Prefix as Subgen sees it |
| `SUBGEN_CALLBACK_TOKEN` | Token included in completion callback URL |
| `SUBGEN_TIMEOUT_MINUTES` | Reconciliation timeout |
| `SUBGEN_POLL_SECONDS` | Output reconciliation interval |
| `SUBTITLE_IMPORT_DELAY_SECONDS` | Webhook policy delay |

Do not expose the callback token in `GET` config responses.

For multiple model/mode combinations, run multiple named Subgen instances in a
later phase and represent them as generator profiles. The API shape below is
already plural so this does not require a frontend rewrite.

### 24.2 Generator capability endpoint

`GET /api/subtitle-generators`:

```json
{
  "generators": [
    {
      "id": "subgen-default",
      "name": "Subgen - fast transcription",
      "healthy": true,
      "provider": "subgen",
      "mode": "transcribe",
      "model_label": "large-v3-turbo",
      "supports_language_hint": true,
      "supports_per_request_model": false,
      "supports_percent_progress": false,
      "transport": "shared_path"
    }
  ]
}
```

### 24.3 Generation plan

Request fields:

- generator ID;
- source audio stream ID;
- language hint or auto;
- output language derived from provider mode;
- output: external or embedded;
- target filename policy;
- title/default/forced/SDH flags for the generated track;
- duplicate/overwrite behavior;
- whether to keep the generated external file after embedding.

The plan warns when:

- full-dialogue coverage already exists;
- only forced coverage exists;
- selected audio is commentary or descriptive audio;
- the provider cannot see the translated path;
- the output filename exists;
- the provider profile mode does not match the requested result.

### 24.4 Submission and completion

For the first `SubgenPathGenerator`:

1. translate the validated local media path into Subgen's namespace;
2. call `POST /batch` for that exact file;
3. store provider submission time and expected output directory;
4. wait for `POST /api/webhooks/subgen?token=...` or periodic reconciliation;
5. map the callback's remote paths back to validated local paths;
6. correlate by media-file ID, source signature, and active generation job;
7. validate the generated subtitle;
8. normalize/rename it if configured;
9. optionally embed through the mutation adapter;
10. rescan and complete the parent job.

The completion webhook is an optimization, not the sole source of truth.
Subgen does not send it for every skip/error path, so timeout and filesystem
reconciliation remain required.

### 24.5 Generated subtitle validation

For text outputs:

- decode safely and normalize to UTF-8;
- parse through `pysubs2`;
- require at least one non-empty event;
- require monotonic, non-negative cue timing;
- reject cues far beyond media duration;
- record cue count and first/last timestamp;
- preserve the unmodified provider output in job diagnostics when
  normalization fails.

Do not claim generated subtitles are correct merely because they parse. The UI
should label them "generated, not reviewed" until the user marks them good.

## 25. API Surface

### 25.1 Library and inventory

| Method | Endpoint | Purpose |
|---|---|---|
| GET | `/api/library/movies` | Implement existing stub; include current media-file ID and subtitle summary |
| GET | `/api/library/movies/{movie_id}` | Movie detail plus media-file linkage |
| GET | `/api/library/series` | Implement existing stub with aggregate subtitle coverage |
| GET | `/api/library/series/{series_id}` | Series detail |
| GET | `/api/library/series/{series_id}/seasons` | Existing stub plus coverage |
| GET | `/api/library/episodes/{episode_id}` | Episode detail plus shared media-file ID |
| GET | `/api/media-files/{id}/subtitles` | Inventory, coverage, capabilities, available actions |
| POST | `/api/media-files/{id}/subtitles/scan` | Queue forced refresh |
| GET | `/api/media-files/{id}/subtitles/{track_id}/preview` | Text cue preview or bitmap limitation |
| GET | `/api/media-files/{id}/subtitles/{track_id}/download` | Safe external/extracted file response |

Library list filters are server-side query parameters, not frontend scans:
`language`, `missing_language`, `source`, `forced_only`, `generated`,
`policy_violation`, `min_tracks`, and `inventory_state`.

### 25.2 Plans and jobs

| Method | Endpoint | Purpose |
|---|---|---|
| POST | `/api/media-files/{id}/subtitle-plans` | Create removal/embed/extract/metadata/policy/generation plan |
| POST | `/api/media-jobs/{job_id}/confirm` | Revalidate and queue a manual plan |
| GET | `/api/media-jobs/{job_id}` | Current state, preview, result, errors |
| GET | `/api/media-jobs/{job_id}/events` | Persisted + live SSE |
| POST | `/api/media-jobs/{job_id}/cancel` | Request cancellation |
| GET | `/api/media-jobs` | Filterable history |
| POST | `/api/media-jobs/{job_id}/restore` | Restore backup/quarantined files where available |
| DELETE | `/api/media-jobs/{job_id}/backup` | Explicit permanent cleanup |

### 25.3 Batches and policies

| Method | Endpoint | Purpose |
|---|---|---|
| POST | `/api/subtitle-batches/plan` | Build per-file child plans and aggregate warnings |
| POST | `/api/subtitle-batches/{id}/confirm` | Queue all still-valid children |
| GET | `/api/subtitle-batches/{id}` | Aggregate and per-item progress |
| POST | `/api/subtitle-batches/{id}/pause` | Stop claiming new children |
| POST | `/api/subtitle-batches/{id}/resume` | Resume |
| POST | `/api/subtitle-batches/{id}/cancel` | Cancel queued children and request active cancellation |
| GET/POST | `/api/subtitle-policies` | List/create |
| GET/PUT/DELETE | `/api/subtitle-policies/{id}` | Manage policy |
| POST | `/api/subtitle-policies/{id}/audit` | Dry-run against a filtered selection |
| POST | `/api/subtitle-policies/{id}/apply` | Create a confirmed batch from an audit |

### 25.4 Providers and webhooks

| Method | Endpoint | Purpose |
|---|---|---|
| GET | `/api/subtitle-generators` | Provider health and real capabilities |
| POST | `/api/webhooks/subgen` | Completion callback with token validation |
| GET | `/api/system/status` | Add tool versions, queue health, active job, and generator health |

All mutation responses use stable machine-readable error/warning codes plus
plain-language text so the frontend does not need its own decision dictionary.

## 26. Webhook Changes

### 26.1 Radarr

Extend `marquee/api/routes/webhooks.py` payloads to include `movieFile.id`,
`movieFile.path`, and `movieFile.relativePath`.

For every Download:

- upsert the MediaFile immediately;
- retain the existing poster-restoration behavior for upgrades;
- schedule a delayed subtitle inventory/policy job for new files and upgrades;
- use `radarr:<movie_file_id>:<file_signature>:subtitle-policy` as the
  idempotency key.

### 26.2 Sonarr

Create real Sonarr payload models rather than reusing `RadarrWebhookPayload`.
Parse `series`, `episodes`, and `episodeFile`. Upsert the physical MediaFile,
associate all episode IDs in the payload, then schedule one subtitle job for
the shared file.

### 26.3 Fast acknowledgement

Webhooks still return quickly. They only perform validation, database upsert,
and durable job creation in the request. No remux, probe, or provider call runs
inside the webhook request or an untracked `asyncio.create_task()`.

## 27. Configuration Knobs

Add a dedicated `SubtitleSettings` singleton in
`marquee/core/subtitles/config.py`, following the validated
`PipelineSettings` pattern:

| Knob | Default |
|---|---|
| `SUBTITLE_ENABLED` | `true` |
| `SUBTITLE_SCAN_CONCURRENCY` | `2` |
| `SUBTITLE_MUTATION_CONCURRENCY` | `1` |
| `SUBTITLE_GENERATION_CONCURRENCY` | `1` |
| `SUBTITLE_PLAN_TTL_MINUTES` | `15` |
| `SUBTITLE_FILE_STABILITY_SECONDS` | `10` |
| `SUBTITLE_IMPORT_DELAY_SECONDS` | `120` |
| `SUBTITLE_TEMP_SPACE_MARGIN_PERCENT` | `5` |
| `SUBTITLE_HARDLINK_POLICY` | `block` |
| `SUBTITLE_BACKUP_MODE` | `none` |
| `SUBTITLE_EXTERNAL_DELETE_MODE` | `quarantine` |
| `SUBTITLE_UNKNOWN_LANGUAGE_ACTION` | `keep` |
| `SUBTITLE_PROTECT_FORCED` | `true` |
| `SUBTITLE_PROTECT_LAST_FULL_DIALOGUE` | `true` |
| `SUBTITLE_NORMALIZE_TEXT_UTF8` | `true` |
| `SUBTITLE_JOB_EVENT_RETENTION_DAYS` | `30` |

Settings that affect automatic policy behavior should also be represented in
the policy revision saved into every plan. Runtime UI changes can follow the
existing `/api/config/pipeline` override pattern later; policy CRUD covers the
important frontend knobs first.

## 28. File and Module Plan

### New models

```
marquee/models/media_file.py
marquee/models/subtitle_inventory.py
marquee/models/managed_subtitle.py
marquee/models/subtitle_policy.py
marquee/models/media_job.py
marquee/models/media_backup.py
```

Update `marquee/models/__init__.py` and test cleanup fixtures.

### New core modules

```
marquee/core/media_files.py
marquee/core/media_jobs/
    __init__.py
    manager.py
    events.py
    recovery.py
marquee/core/subtitles/
    __init__.py
    config.py
    types.py
    languages.py
    external.py
    probe.py
    capabilities.py
    policy.py
    service.py
    validation.py
    adapters/
        __init__.py
        base.py
        matroska.py
        mp4.py
    generators/
        __init__.py
        base.py
        subgen.py
```

### New routes

```
marquee/api/routes/media_files.py
marquee/api/routes/subtitles.py
marquee/api/routes/media_jobs.py
marquee/api/routes/subtitle_policies.py
marquee/api/routes/subtitle_generators.py
```

Extend:

- `marquee/api/routes/library.py`;
- `marquee/api/routes/webhooks.py`;
- `marquee/api/routes/system.py`;
- `marquee/api/deps.py`;
- `marquee/main.py`;
- `marquee/core/sync_service.py`;
- Radarr/Sonarr clients with command/rescan helpers;
- `docker/Dockerfile`, `docker/docker-compose.yml`, `pyproject.toml`;
- Alembic migrations.

## 29. Migration Plan

Use one foundation migration followed by feature migrations:

1. **Media-file foundation**
   - create `media_files` and `episode_media_files`;
   - backfill movie files from folder + relative path;
   - deduplicate episode paths into physical file rows;
   - leave legacy path columns in place.
2. **Inventory**
   - create inventories/tracks, managed assets/bindings, and indexes.
3. **Jobs**
   - create batches/jobs/events/backups.
4. **Policies**
   - create policies/bindings and a disabled audit-only default policy.

Backfill source file IDs on the next Radarr/Sonarr sync. Legacy path-derived
source keys remain valid until replaced or merged. Do not drop
`movie_file_path` or `episode_file_path` in the first subtitle release.

Migration verification must run `alembic upgrade head` on a copy of the current
database, then perform a real sync and confirm that no Movie/Episode rows or
poster state changed.

## 30. Testing Strategy

### 30.1 Pure unit tests

- language normalization and filename token parsing;
- external subtitle matching and IDX/SUB pairing;
- coverage classification;
- policy allowlist/blocklist behavior;
- unknown/forced/default/only-full-dialogue protection;
- plan expiry and signature mismatch;
- disk-space and hardlink decisions;
- capability matrix;
- Subgen path translation and callback correlation;
- managed subtitle binding, cache integrity, and replacement matching;
- job state transitions and batch pause/resume;
- structured API error codes.

### 30.2 Tool integration fixtures

Generate tiny media fixtures during tests with FFmpeg:

- MKV with one video, one audio, SRT, ASS, and attachment;
- MKV with PGS/VobSub fixture where licensing permits a test artifact;
- MP4 with `mov_text`;
- multi-audio and multi-language file;
- external SRT/ASS/VTT and IDX/SUB pairs.

Mark tests skipped with a clear reason when tools are absent. Docker CI should
run the complete set.

Verify:

- ffprobe inventory shape;
- MKV removal preserves video/audio and attachments;
- MP4 removal preserves video/audio;
- SRT embed into MKV;
- SRT-to-`mov_text` embed into MP4;
- forced/default/language metadata;
- extraction round trip;
- output validation rejects a corrupt partial;
- elementary stream hashes match before/after for fixtures;
- backup restore returns the exact original fixture hash.

### 30.3 Queue and recovery tests

- two jobs for one media file cannot run concurrently;
- unrelated scan jobs can use configured concurrency;
- running job becomes interrupted on simulated restart;
- unchanged original permits safe retry;
- changed original invalidates retry;
- persisted SSE events replay after manager recreation;
- callback before/after polling completes the same job exactly once;
- duplicate Radarr/Sonarr webhooks create one policy job.
- replacement webhook creates at most one managed-subtitle restore job.

### 30.4 API tests

Use the existing `ASGITransport` style:

- library inventory and filters;
- plan -> confirm -> success;
- stale plan returns `409 plan_stale`;
- warning override requirements;
- batch mixed success/failure;
- quarantine/restore;
- policy audit versus apply;
- generator capability response;
- invalid Subgen callback token;
- path traversal attempts never reach subprocess invocation.

No normal unit test requires OCR, CLIP, or downloaded ML models.

## 31. Build Order

### Phase 0 - Media-file foundation and read-only library

1. Add `MediaFile` models/migration and sync upserts.
2. Implement the existing library route stubs.
3. Add the shared resolver and path tests.
4. Add system-tool detection and `/api/system/status` capabilities.

**Exit:** every current movie and episode resolves to the correct physical
media-file ID; multi-episode files are represented once.

### Phase 1 - Read-only subtitle inventory

1. Implement probe/language/external discovery.
2. Persist inventories and tracks.
3. Add inventory, preview, download, coverage, and library filters.
4. Scan on demand and after sync without mutating files.

**Exit:** the future frontend can browse the whole library and accurately answer
what subtitle coverage exists without running local logic.

### Phase 2 - Durable plan/job framework

1. Add plans, jobs, events, batches, SSE, worker, and recovery.
2. Add file locks, signatures, stability, free-space, hardlink, and
   cancellation preflight.
3. Implement scan jobs through the new manager first.

**Exit:** restart-safe background work and progress exist before any destructive
operation is enabled.

### Phase 3 - Single-file mutations

1. MKV and MP4 adapters.
2. Remove, embed, metadata edit, extract.
3. Managed-subtitle cache and logical-owner bindings.
4. Validation, atomic replacement, backup, quarantine, restore.
5. Radarr/Sonarr rescan commands and full audit history.

**Exit:** all manual mutations use plan -> confirm -> execute and can be safely
reviewed or restored.

### Phase 4 - Policies, batches, and webhooks

1. Policy CRUD/evaluator/audit.
2. Batch plan/confirm/pause/resume.
3. Radarr/Sonarr Download upsert, managed-subtitle restoration, and delayed
   audit.
4. Opt-in automatic policy mutation and managed-track re-embedding.

**Exit:** library-wide cleanup is restart-safe, observable, and conservative.

### Phase 5 - Subgen generation

1. Generator protocol and capability endpoint.
2. Subgen path adapter and callback endpoint.
3. Reconciliation, generated-file validation, provenance.
4. Optional embed post-processing through Phase 3.

**Exit:** generation works without pretending Subgen offers per-job model or
percentage controls that it does not have.

### Phase 6 - Quality-of-life follow-ups

- text timing offset tool;
- compare two text tracks;
- duplicate subtitle detection beyond file hashes;
- backup retention/cleanup UI;
- multiple named generation profiles;
- explicit media-container conversion;
- media-server refresh adapters;
- optional MKV header-only metadata edits.

## 32. Backend-Ready Acceptance Criteria

The backend is ready for frontend work when:

1. every API response uses stable IDs and includes human-readable labels;
2. library endpoints can filter by subtitle coverage from persisted data;
3. the server returns per-track available actions and reasons for disabled
   actions;
4. every mutation has a before/after plan and stale-file protection;
5. jobs, events, batches, and history survive a server restart;
6. hardlinks, free space, unstable files, forced tracks, and unknown languages
   are handled explicitly;
7. successful remuxes pass post-write validation before replacement;
8. backups/quarantined sidecars can be restored through the API;
9. managed subtitle tracks can be restored after a media-file replacement;
10. Radarr/Sonarr receive a rescan request after media mutation;
11. generator controls reflect actual provider capabilities;
12. OpenAPI schemas are complete enough to generate frontend TypeScript types;
13. the full non-ML test suite and tool integration suite pass in Docker.

## 33. External Contract References

- Subgen repository and current API behavior:
  `https://github.com/McCloudS/subgen`
- FFmpeg stream mapping, metadata, stream-copy, and disposition behavior:
  `https://ffmpeg.org/ffmpeg.html`
- MKVToolNix `mkvmerge` track selection, language, ordering, and forced-display
  options: `https://mkvtoolnix.download/doc/mkvmerge.html`

These contracts should be checked again immediately before implementation,
especially Subgen, because it is an actively changing external service.
