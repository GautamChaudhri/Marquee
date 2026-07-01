# 28 — OCR False-Positive / False-Negative Labeling & Log Capture

Debug-mode-only feature that lets the developer manually mark OCR false positives
and false negatives from the pipeline run results page. Each marking copies the
poster image and extracts per-poster diagnostic context (log lines, feature
vectors, OCR config at time of run) into a structured directory for later LLM
analysis aimed at tuning OCR pipeline settings.

## Problem

The OCR text gate rejects posters that are visually clean (false positives) and
passes posters that contain unwanted text (false negatives). Without a systematic
way to collect these cases along with their pipeline diagnostics, tuning OCR
thresholds and classifier rules is a blind process. The developer needs:

1. A way to mark any poster in the OCR-rejected tab as "false positive" (should
   have passed).
2. A way to mark any poster in the Ranked tab as "false negative" (should have
   been gated at OCR).
3. Each marking must capture: the poster image, all per-poster log lines from
   `pipeline.log`, the per-poster entry from `pipeline_run.json`, and the full
   OCR pipeline config at time of run — including the text-gate mode
   (title_only/textless/custom) and all classifier allow/deny toggles.
4. Collect everything into a structured, clearable directory for later batch
   analysis.
5. The feature must ONLY be available in debug mode — never in production.

## Root Cause Context

### Pipeline data flow (what exists, what's missing)

**Per-poster data in `pipeline_run.json`** (`CandidateScore.to_dict()`, `types.py:110-133`):
- `orig_filename`, `image_path`, `rejection_reason`, `gate_decision`,
  `gate_reason`, `raw_features`, `normalized_features`, `extended_features`,
  `typicality_detail`, `contributions`, `final_score`, `rank`, `stage_reached`,
  `dedup_kept`, stack fields.

**What is NOT in `pipeline_run.json`** — the OCR-detected text details:
- `detected_text`, `title_bbox`, `residual_boxes` — these live only in
  `OCRCandidateResult` (`types.py:25-31`) and are logged to `pipeline.log` at
  `runner.py:683-691` but **never serialized** into the archive JSON.
- The `CandidateScore` only stores `rejection_reason` from OCR — no text geometry.

**Config snapshot gap** — `pipeline_config.py:528-536` includes only 7 of ~20 OCR
knobs in the `snapshot()` `"ocr"` block. Missing are `OCR_TEXT_MODE`,
`OCR_ALLOW_TITLE/DIRECTOR/STUDIO/RATING/TAGLINE`, `OCR_CONFIDENCE_THRESHOLD`,
`OCR_STRIP_CONFIDENCE_THRESHOLD`, `OCR_BOTTOM_CONFIDENCE_THRESHOLD`,
`OCR_FUZZY_CUTOFF`, `OCR_TITLE_PROXIMITY_PIXELS`,
`OCR_RESIDUAL_SIGNIFICANT_AREA_FRACTION`, `OCR_RESIDUAL_SIGNIFICANT_WIDTH_FRACTION`,
and `OCR_ENHANCE_RETRY`. Without these, the "mode" and classifier rules at time of
run are invisible, making LLM analysis of false positives unreliable.

### Debug mode

`settings.DEBUG` (`config.py:31`) already gates:
- Auth bypass (`main.py:206-210` — docs URLs only in debug)
- Rate limit bypass (rate limiter skips when `settings.DEBUG`)
- CORS middleware (`main.py:224-231`)

The frontend has **no way to know** the current debug state. No endpoint exposes
this. The SSE comment in `$lib/sse.ts:3-4` references loopback/DEBUG for auth but
there's no programmatic flag.

### File lifecycle

`_copy_with_reason()` (`runner.py:180-186`) uses `shutil.copy2`, not `shutil.move`.
The original stays in `0-originals/`. On re-run, `_clear_generated_outputs()`
(`runner.py:150-155`) deletes `2-ocr-rejected/`, `gated/`, `ranked/`, etc. but
**keeps `0-originals/`**. So the poster is always findable from the originals
directory even after re-runs, as long as the run directory hasn't been manually
purged.

Poster serving: `GET /api/pipeline/runs/{run_id}/posters/{orig_filename}`
(`pipeline.py:146-177`) reads `image_path` from the archive JSON and serves it
with path confinement. If the image was deleted by a re-run, it returns 404.

## Architecture Options Considered

| Option | Description | Verdict |
|--------|-------------|---------|
| **A: Post-hoc API endpoint** | `POST /api/dev/false-positive` called from run results page; backend reads archive JSON + log file + copies poster | ✅ **Best** — clean separation, works on any past run with archive, no pipeline coupling |
| **B: Inline marking during live run** | Button in live progress UI, data captured as posters flow through OCR stage | ❌ Too coupled to run lifecycle, live state disappears on page refresh |
| **C: Batch marking page** | Separate tool that lists all recent runs with poster grids for bulk marking | ❌ Overengineered for dev-only tool; ad-hoc marking from run results page is sufficient |

**Option A is the clear choice.** The user reviews run results and marks posters
immediately. The backend does all extraction work.

## Implementation Targets

### Target 1: Expose debug status to the frontend

**What:** Add a lightweight endpoint or embed the flag in an existing response so
the UI can conditionally show false-positive controls.

**Why:** The frontend currently has zero knowledge of `settings.DEBUG`. Without
this, false-positive UI elements either always render (bad) or rely on a hack.

**How — Option 1a (recommended):** Add `debug` field to `GET /api/config` (already
exists in `config.py` routes) or add `GET /api/status`:
```python
{"debug": settings.DEBUG}
```

**How — Option 1b:** Add `X-Marquee-Debug: true` response header globally when
`settings.DEBUG` is on (in `main.py` middleware). Frontend reads it from any
fetch call. Simpler but less explicit.

**How — Option 1c:** Embed in the run results payload (`RunResults.debug` field).
Only relevant to this one feature but couples it to the payload.

**Recommendation:** Option 1a — add to `GET /api/config` or create a dedicated
`GET /api/status` endpoint that also returns versions, health, etc. This is
reusable for other debug-only features in the future.

**Files:**
- `marquee/api/routes/config.py` — add `debug` field to config response, OR
- `marquee/api/routes/system.py` — add to existing system status endpoint
- `frontend/src/lib/api/config.ts` — add type if new field
- `frontend/src/routes/pipeline/runs/[run_id]/+page.svelte` — read debug flag

### Target 2: Expand OCR config snapshot

**What:** Add all missing OCR configuration knobs to `pipeline_config.py`'s
`snapshot()` method so future runs include full OCR context.

**Why:** Without this, false-positive logs don't capture the text-gate mode or
classifier rules that were active at the time of the run. The LLM analysis needs
to know "was this run in title_only mode with OCR_MAX_RESIDUAL_BOXES=0" to suggest
meaningful tweaks.

**Files:**
- `marquee/core/pipeline_config.py:528-536` — expand the `"ocr"` block in `snapshot()`

**Snapshot additions:**
```python
"ocr": {
    # existing
    "device": self.OCR_DEVICE,
    "workers": self.OCR_WORKERS,
    "detail_passes": self.OCR_DETAIL_PASSES,
    "max_residual_boxes": self.OCR_MAX_RESIDUAL_BOXES,
    "max_residual_area_fraction": self.OCR_MAX_RESIDUAL_AREA_FRACTION,
    "require_title": self.OCR_REQUIRE_TITLE,
    "accept_no_text_fallback": self.OCR_ACCEPT_NO_TEXT,
    # NEW
    "mode": self.OCR_TEXT_MODE,
    "allow_title": self.OCR_ALLOW_TITLE,
    "allow_director": self.OCR_ALLOW_DIRECTOR,
    "allow_studio": self.OCR_ALLOW_STUDIO,
    "allow_rating": self.OCR_ALLOW_RATING,
    "allow_tagline": self.OCR_ALLOW_TAGLINE,
    "confidence_threshold": self.OCR_CONFIDENCE_THRESHOLD,
    "strip_confidence_threshold": self.OCR_STRIP_CONFIDENCE_THRESHOLD,
    "bottom_confidence_threshold": self.OCR_BOTTOM_CONFIDENCE_THRESHOLD,
    "fuzzy_cutoff": self.OCR_FUZZY_CUTOFF,
    "title_proximity_pixels": self.OCR_TITLE_PROXIMITY_PIXELS,
    "residual_significant_area_fraction": self.OCR_RESIDUAL_SIGNIFICANT_AREA_FRACTION,
    "residual_significant_width_fraction": self.OCR_RESIDUAL_SIGNIFICANT_WIDTH_FRACTION,
    "enhance_retry": self.OCR_ENHANCE_RETRY,
},
```

### Target 3: False-positive/negative backend — data extraction & storage

**What:** Core module and API endpoints that receive a `(run_id, orig_filename)`
pair, extract all diagnostic data, copy the poster, and write a structured
directory.

**Why:** This is the core of the feature. The extraction must be efficient (no
O(n) scan of every candidate for every marking) and durable (handle missing files
gracefully).

**Files:**
- `marquee/core/false_positive.py` — **NEW** — extraction + storage logic
- `marquee/api/routes/dev_tools.py` — **NEW** — debug-only API endpoints
- `marquee/main.py` — register dev tools router (with DEBUG guard)

**Directory structure (per-movie subdirectories — recommended):**

```
data/false_positives/ocr/
├── {sanitized_movie_title}__{run_id[:8]}/
│   ├── {orig_filename}                         # poster copy
│   ├── {orig_filename}.log.txt                 # relevant log excerpt
│   ├── {orig_filename}.pipeline_entry.json     # candidate dict from archive
│   ├── run_config.json                         # full config snapshot
│   └── marking_metadata.json                   # movie, run, timestamp, mode

data/false_negatives/ocr/                       # same structure
```

**Why per-movie subdirectories over flat:** When the LLM analyzes false positives,
it needs to compare all rejected posters for one movie against the survivors that
passed. Per-movie grouping makes this natural — the LLM can see "for Movie X,
these 3 posters were rejected (false positives) and these 2 passed that
shouldn't have (false negatives)". Flat storage mixes everything and loses the
within-movie relationship. Also, per-movie directories make it easy to:
- Delete all markings for one movie
- Process one movie at a time in the LLM analysis
- Avoid filename collisions from different movies

**What each marking captures:**

1. **Poster image** — `shutil.copy2` from the originals directory
   (`data/runs/work/{movie}/0-originals/{orig_filename}`) into the false-positive
   directory. If the originals directory is gone, try the path recorded in the
   archive JSON's `image_path`. If both fail, record the absence in metadata.

2. **Log excerpt** (`{filename}.log.txt`) — grep `pipeline.log` for all lines
   containing the original filename. This captures OCR details (detected text,
   title bbox, residual box count), style feature lines, gate decisions, and any
   error lines. Include 2-3 lines of context before/after for readability.

3. **Per-poster pipeline entry** (`{filename}.pipeline_entry.json`) — the full
   `candidate` dict from `pipeline_run.json` for that poster, extracted by
   matching `orig_filename`. Contains features, gate decisions, rejection
   reasons, contributions.

4. **Run config** (`run_config.json`) — the `config` block from
   `pipeline_run.json`. One copy per movie subdirectory (shared across all
   markings for that run — don't duplicate for every poster).

5. **Marking metadata** (`marking_metadata.json`):
   ```json
   {
     "movie_id": 42,
     "movie_title": "Dune",
     "tmdb_id": 438631,
     "run_id": "abc123...",
     "orig_filename": "gnb54xyz.jpg",
     "marked_at": "2026-06-28T12:30:00Z",
     "marked_by": "developer",
     "rejection_reason": "no_title",
     "stage_reached": "ocr",
     "pipeline_ocr_mode": "title_only"
   }
   ```

**Clear mechanism:**
- `DELETE /api/dev/false-positives?scope=all|movie` — delete all or a specific
  movie's subdirectory. Quick, simple.
- Could also be a button on the Settings page in the danger zone.

### Target 4: API endpoints (debug-only)

**What:** Three endpoints under a debug-guarded router.

**Files:**
- `marquee/api/routes/dev_tools.py` — **NEW**

**Endpoints:**

| Method | Path | Body | Response |
|--------|------|------|----------|
| `POST` | `/api/dev/false-positive` | `{run_id, orig_filename}` | `{status, path, image_copied, metadata}` |
| `POST` | `/api/dev/false-negative` | `{run_id, orig_filename}` | `{status, path, image_copied, metadata}` |
| `DELETE` | `/api/dev/false-positives` | `{scope: "all" \| "movie", movie_title?}` | `{deleted: N, paths: [...]}` |

**Registration in `main.py`:**
```python
if settings.DEBUG:
    from marquee.api.routes.dev_tools import router as dev_tools_router
    app.include_router(dev_tools_router)
```

The `if settings.DEBUG` guard at import+register time (NOT per-request) is the
cleanest approach — in production, the routes simply don't exist and FastAPI
returns 404. This is better than per-request guards because:
- No risk of the guard being accidentally removed in a refactor
- Routes are completely absent from the OpenAPI schema in prod
- Matches the existing pattern (`docs_url=None` when not DEBUG)

### Target 5: Run originals directory lookup

**What:** Reliable lookup of the poster file from the run's working directory,
falling back through available sources.

**Why:** The `image_path` in the archive JSON may point to a subdirectory
(`2-ocr-rejected/`, `gated/`, `ranked/`) that no longer exists after re-run. But
`0-originals/` is preserved across re-runs and `_copy_with_reason` uses
`shutil.copy2` (not move), so the original is always there. We need a reliable
resolution strategy.

**Resolution order in `false_positive.py`:**
1. Try `data/runs/work/{movie_title}/0-originals/{orig_filename}` (always best)
2. Try the `image_path` from `pipeline_run.json` (may be stale/rejected-dir)
3. If neither exists, proceed without the image — log the absence in metadata

**Run title lookup:** The `pipeline_run.json` contains `"title"` field. Use
`_sanitise_filename(title)` (from `runner.py`) to reconstruct the working
directory path. This is the same sanitization used when the run directory was
created.

### Target 6: Frontend — conditional UI in run results page

**What:** Add small "FP" (false positive) and "FN" (false negative) buttons to
poster tiles in the pipeline run results page, only when debug mode is active.

**Files:**
- `frontend/src/lib/api/pipeline.ts` — add API functions
- `frontend/src/lib/api/types.ts` — add response types
- `frontend/src/lib/api/config.ts` — add debug field
- `frontend/src/routes/pipeline/runs/[run_id]/+page.svelte` — add FP/FN buttons

**UI placement:**

1. **OCR tab** (`activeStage === 'ocr'`): Each `PosterCandidateTile` gets a small
   "FP" button overlaid on the bottom-right corner. Click → calls
   `POST /api/dev/false-positive` with `{run_id, orig_filename}` → toast success.

2. **Ranked tab** (`activeStage === 'ranked'`): Each `PosterCandidateTile` gets a
   small "FN" button overlaid on the bottom-right corner. Click → calls
   `POST /api/dev/false-negative` with `{run_id, orig_filename}` → toast success.

3. **Flat view expanded stacks**: Same buttons on individual tiles inside expanded
   stacks.

**Conditional rendering:**
```svelte
{#if debugMode}
  <button class="fp-btn" onclick={() => markFalsePositive(c.orig_filename)}>FP</button>
{/if}
```

The `debugMode` flag is loaded from `GET /api/config` or `GET /api/status` in the
page's load function.

**Visual design:**
- Small pill-shaped button, positioned bottom-right of the poster tile
- "FP" label with a subtle amber/warning color (orange-tint)
- "FN" label with a subtle red/danger color (red-tint)
- Opacity ~60%, full opacity on hover
- These are dev-only tools — don't make them prominent

### Target 7: Clear mechanism

**What:** Way to clear the false-positive directories from the UI.

**Why:** After analyzing the collected false positives and applying OCR tweaks,
the developer needs to start fresh and collect new ones. A simple clear action
prevents stale data accumulation.

**Files:**
- `marquee/api/routes/dev_tools.py` — `DELETE /api/dev/false-positives`
- `frontend/src/routes/settings/+page.svelte` or the pipeline page — clear button

**Options for clear UI:**
- **Option A:** Add a "Clear false positives" button to the Settings page danger
  zone (follows the existing poster-deploy-reset pattern)
- **Option B:** Add a small clear button on the pipeline run results page near the
  FP/FN buttons
- **Option C:** Both — Settings for bulk clear, inline for per-movie clear

**Recommendation:** Option A for now — Settings page danger zone is the
established pattern for destructive maintenance actions. Option C can be added
later if needed.

### Target 8: OCRAnalysisReport generator (future — Phase 2)

**What (not in this doc's scope, but noted for future):** An LLM-powered script
that reads the `data/false_positives/ocr/` directory, analyzes each poster +
log + config, and produces a report with recommended OCR pipeline knob changes.

**Why:** This is the ultimate goal of collecting false positives — the data
collection is Phase 1, the analysis is Phase 2. This should be its own tool/endpoint.

**Analysis input per marking:**
- The poster image (for visual inspection by the LLM)
- The log excerpt (detected text, OCR reason, feature values)
- The pipeline entry (full feature vector, gate decisions)
- The config snapshot (OCR settings at time of run)
- The metadata (movie context, timestamp)

**Expected output:**
- "For movie Dune, 3 posters were falsely rejected because `OCR_MAX_RESIDUAL_BOXES=0`
  is too strict — all 3 have 1-2 studio/tagline boxes that should be ignored.
  Recommendation: set `OCR_MAX_RESIDUAL_BOXES=1` or enable `OCR_ALLOW_STUDIO=true`."
- "For movie Interstellar, 1 poster passed OCR but contains large billing text
  at bottom. `OCR_RESIDUAL_SIGNIFICANT_AREA_FRACTION=0.005` is too loose for this
  poster's 2000×3000 resolution. Recommendation: lower to 0.002 or add bottom-zone
  exclusion."
- Aggregate statistics: "Across 15 marked false positives, the most common rejection
  reason was `no_title` (67%), followed by `residual_limit` (27%)…"

This would live in a new file like `marquee/ml/ocr_analyzer.py` or be a standalone
script.

## File Change Summary

| # | What | Where | Priority |
|---|------|-------|----------|
| 1 | Expose debug status to frontend | `marquee/api/routes/config.py` or `system.py` | High |
| 2 | Expand OCR config snapshot | `marquee/core/pipeline_config.py:528-536` | High |
| 3 | Core extraction + storage module | `marquee/core/false_positive.py` (NEW) | High |
| 4 | Dev-only API endpoints | `marquee/api/routes/dev_tools.py` (NEW) | High |
| 5 | Register dev routes (DEBUG guard) | `marquee/main.py:322` | High |
| 6 | Frontend API functions + types | `frontend/src/lib/api/pipeline.ts`, `types.ts`, `config.ts` | High |
| 7 | FP/FN buttons in run results page | `frontend/src/routes/pipeline/runs/[run_id]/+page.svelte` | High |
| 8 | Clear mechanism (Settings danger zone) | `marquee/api/routes/dev_tools.py`, `frontend/src/routes/settings/+page.svelte` | Medium |
| 9 | OCRAnalysisReport generator | `marquee/ml/ocr_analyzer.py` (NEW, Phase 2) | Low |

## Key Decisions & Rationale

### Per-movie subdirectories > flat directory

Analysis is per-movie. The LLM needs to see "all posters for Dune — which ones
were falsely rejected and why." Storing `{movie}__{run_id}/poster.jpg` groups
everything naturally. Also prevents filename collisions (different movies can
have posters with the same TMDB filename).

### Config snapshot expansion is mandatory for Target 3

Without `OCR_TEXT_MODE` in the snapshot, you can't tell if a false positive was
rejected under `title_only` mode (title not found) or `custom` mode (director
text denied). The analysis is meaningless without this context. This is a
**blocking prerequisite** — all future runs need the expanded snapshot.

### DEBUG guard at route registration, not per-endpoint

Putting the router registration behind `if settings.DEBUG: app.include_router(...)`
in `main.py` is simpler and safer than per-endpoint guards. The endpoint simply
doesn't exist in prod. This also keeps the OpenAPI schema clean in prod.

### Copy poster from originals, not rejected directory

`0-originals/` survives re-runs; `2-ocr-rejected/` doesn't. Always resolve from
originals first. The `_copy_with_reason` function uses `shutil.copy2` (not
`shutil.move`), so the original is always present as long as the run directory
exists.

### OCR log extraction from `pipeline.log` (not archive JSON)

The archive JSON doesn't contain OCR-detected text details (`detected_text`,
`title_bbox`, `residual_boxes`). These are only in `pipeline.log`. The extraction
must grep the log file for all lines containing the poster's filename.

## References

- `marquee/config.py:31` — `settings.DEBUG`
- `marquee/main.py:206-211` — DEBUG condition for docs URLs
- `marquee/pipeline/runner.py:180-186` — `_copy_with_reason` (shutil.copy2)
- `marquee/pipeline/runner.py:150-155` — `_clear_generated_outputs`
- `marquee/pipeline/runner.py:668-705` — OCR stage in `run_sync_stages`
- `marquee/pipeline/runner.py:683-691` — OCR per-poster log line
- `marquee/pipeline/runner.py:286-315` — `build_run_payload`
- `marquee/pipeline/types.py:25-31` — `OCRCandidateResult` dataclass
- `marquee/pipeline/types.py:77-133` — `CandidateScore` + `to_dict()`
- `marquee/core/pipeline_config.py:504-554` — `PipelineSettings.snapshot()`
- `marquee/core/pipeline_config.py:311-370` — OCR config knobs
- `marquee/core/pipeline_config.py:528-536` — OCR snapshot section (needs expansion)
- `marquee/api/results.py:83-84` — `poster_url()` function
- `marquee/api/results.py:132-152` — `_candidate_view()` function
- `marquee/api/routes/pipeline.py:146-177` — poster serving endpoint
- `frontend/src/routes/pipeline/runs/[run_id]/+page.svelte:194-198` — `currentPosters` derived
- `frontend/src/routes/pipeline/runs/[run_id]/+page.svelte:588` — OCR tab hint text
- `frontend/src/routes/pipeline/runs/[run_id]/+page.svelte:654-663` — rejected tab poster grid
- `frontend/src/lib/api/types.ts:495-519` — `CandidateView` interface
- `frontend/src/lib/api/types.ts:540-562` — `RunResults` interface
