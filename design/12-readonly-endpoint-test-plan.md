# Marquee Read-Only-Media API Endpoint Test Plan

**Status:** Draft plan for endpoint validation before the next build phase  
**Date:** 2026-06-16  
**Scope:** All *currently implemented* FastAPI endpoints, tested against the live Forge Marquee instance, except endpoints whose purpose is to directly mutate media-library files.

---

## 1. Goal

Run a rigorous live endpoint validation pass while the media library is mounted read-only.

The test run must prove:

1. The API server is healthy and connected to its prerequisites.
2. Every implemented endpoint that can operate with read-only access to media is exercised.
3. Endpoints that return media-derived data are verified against an independent source where practical: database rows, archived run JSON, `ffprobe`, filesystem metadata, or cross-endpoint consistency.
4. Direct media-file mutation endpoints are **not** executed.
5. Every request, response, header set, binary artifact, SSE transcript, and verification result is captured to disk.
6. Any endpoint-breaking bug is recorded in a dedicated bug log with repro details. The test agent must **not** attempt to fix bugs during this pass.

---

## 2. Sources Reviewed

Implementation inventory came from these files:

- `marquee/main.py` — router registration and `/health`
- `marquee/api/routes/config.py`
- `marquee/api/routes/feedback.py`
- `marquee/api/routes/letterbox.py`
- `marquee/api/routes/library.py`
- `marquee/api/routes/media_jobs.py`
- `marquee/api/routes/pipeline.py`
- `marquee/api/routes/subtitle_generators.py`
- `marquee/api/routes/subtitle_policies.py`
- `marquee/api/routes/subtitles.py`
- `marquee/api/routes/sync.py`
- `marquee/api/routes/system.py`
- `marquee/api/routes/taste.py`
- `marquee/api/routes/test_pipeline.py`
- `marquee/api/routes/webhooks.py`

Design references:

- `design/01-marquee-project-spec.md` — sync, webhooks, self-heal, restoration context
- `design/02-model-schema.md` — DB entities used for independent verification
- `design/03-config-and-paths.md` — path/config expectations
- `design/04-agent-build-instructions.md` — legacy test pipeline endpoint and run artifacts
- `design/09-feedback-loop-design.md` — production pipeline, feedback, taste/config endpoints
- `design/more-features/01-poster-restoration.md` — webhook/self-heal/write-path endpoints
- `design/more-features/02-taste-map-visualization.md` — taste map endpoints
- `design/more-features/03-subtitle-management.md` — subtitle inventory, policy, job, generator endpoints
- `design/more-features/04-letterbox-cropping.md` — letterbox detect/review/apply endpoints

---

## 3. Safety Rules for This Pass

### 3.1 Allowed

These are allowed because they do not directly write to media files:

- API reads and status checks.
- Database-only setup/cleanup when no endpoint exists to create a fixture.
- Syncing *arr metadata into Marquee's DB.
- Running poster pipeline endpoints; they write under project experiment/archive/cache paths, not into the media library unless feedback deploy is enabled.
- Running subtitle inventory/scan/inspect endpoints; they read media and write inventory/job DB rows.
- Running letterbox detection and preview endpoints; they read media, update detection DB rows, and may write preview cache files.
- Creating reversible DB-only test rows such as a temporary subtitle policy or synthetic letterbox state.
- Creating planned media jobs, then cancelling them, as long as they are never confirmed.

### 3.2 Disallowed

Do **not** run endpoints that intentionally write, restore, tag, remux, delete, or replace media-library files.

Specifically skipped in this pass:

| Endpoint | Reason |
|---|---|
| `POST /api/system/heal` | May restore missing poster files into movie folders. |
| `POST /api/letterbox/movies/{movie_id}/apply` | Applies MKV crop tags to media files. |
| `POST /api/letterbox/apply` | Batch applies MKV crop tags to media files. |
| `POST /api/letterbox/movies/{movie_id}/remove` | Removes MKV crop tags from media files. |
| `POST /api/letterbox/heal` | Re-applies crop tags that drifted off media files. |
| `POST /api/media-jobs/{job_id}/confirm` | Queues a planned media mutation for execution. |
| `POST /api/media-jobs/{job_id}/restore` | Restores backup/quarantined media sidecars. |
| `DELETE /api/media-jobs/{job_id}/backup` | Deletes tracked backup files. |
| `POST /api/subtitle-policies/{policy_id}/apply` | Creates confirmed removal jobs that may mutate media. |
| `POST /api/media-files/{media_file_id}/subtitle-generations` | Queues subtitle generation that writes subtitle output. |
| `POST /api/movies/{movie_id}/subtitle-generations` | Same as above, movie convenience route. |
| `POST /api/feedback` with `deploy=true` or omitted on approve/override | May deploy selected poster to the movie folder via `PosterService`. Test only with `deploy:false`. |
| Real Radarr `Download + isUpgrade=true` webhook for a tracked movie | Schedules poster restoration and subtitle scan. Use `Test`, ignored events, or untracked synthetic IDs only. |

### 3.3 Bug-handling rule

When a bug is encountered:

1. Record it in the dedicated bug log.
2. Capture all relevant request, response, stderr/stdout, server-visible details, selected fixture IDs, and expected-vs-actual notes.
3. Continue to the next independent endpoint when safe.
4. Do **not** patch code, restart services, change config, or alter infrastructure.
5. If the server becomes unhealthy, GPU hangs, or an infrastructure/tooling issue occurs, stop the run and notify Gautam instead of attempting recovery.

---

## 4. Output Capture Layout

The agent running the tests should create one timestamped run directory:

```bash
export BASE_URL="http://192.168.4.199:3165"
export TEST_RUN_ID="$(date -u +%Y%m%dT%H%M%SZ)"
export TEST_ROOT="/forge/Marquee/experiments/endpoint-tests/${TEST_RUN_ID}"
mkdir -p "$TEST_ROOT"/{requests,responses,headers,binaries,sse,state,verify}
```

Required files:

| File | Purpose |
|---|---|
| `$TEST_ROOT/full-transcript.log` | Everything printed by the test harness. |
| `$TEST_ROOT/requests.jsonl` | One JSON record per request: name, method, URL, body, timestamp. |
| `$TEST_ROOT/responses.jsonl` | One JSON record per response: status, duration, header path, body path, parsed summary. |
| `$TEST_ROOT/endpoint-test-summary.md` | Human-readable pass/fail/skip summary grouped by endpoint family. |
| `$TEST_ROOT/endpoint-test-bugs.md` | Dedicated bug log. Only endpoint-breaking bugs go here. |
| `$TEST_ROOT/fixture-selection.json` | Chosen movie IDs, media file IDs, run IDs, policy IDs, job IDs, track IDs, exemplar names. |
| `$TEST_ROOT/state/before.json` | Pre-test state snapshot. |
| `$TEST_ROOT/state/after.json` | Post-test state snapshot. |
| `$TEST_ROOT/state/media-stat-before.tsv` | `stat` data for every selected media file before endpoint tests. |
| `$TEST_ROOT/state/media-stat-after.tsv` | Same after tests; must match for read-only-media endpoints. |
| `$TEST_ROOT/sse/*.log` | Raw SSE streams for pipeline, letterbox, and media-job endpoints. |
| `$TEST_ROOT/binaries/*` | Downloaded poster images, taste exemplar images, subtitle downloads, and letterbox preview images. |

The harness should also tee stdout/stderr:

```bash
exec > >(tee -a "$TEST_ROOT/full-transcript.log") 2>&1
```

---

## 5. Request Wrapper Requirements

Every endpoint call should go through a wrapper that captures headers, body, status code, elapsed time, and curl errors.

Minimum behavior:

```bash
request_json() {
  local name="$1" method="$2" url="$3" body="${4:-}"
  local slug
  slug="$(printf '%s' "$name" | tr -cs 'A-Za-z0-9._-' '_' | sed 's/_$//')"
  local h="$TEST_ROOT/headers/${slug}.headers"
  local out="$TEST_ROOT/responses/${slug}.json"
  local meta="$TEST_ROOT/responses/${slug}.meta.json"

  printf '{"ts":"%s","name":"%s","method":"%s","url":"%s","body":%s}\n' \
    "$(date -u +%FT%TZ)" "$name" "$method" "$url" "${body:-null}" >> "$TEST_ROOT/requests.jsonl"

  if [ -n "$body" ]; then
    curl -sS --max-time 600 -D "$h" -o "$out" \
      -w '{"http_code":%{http_code},"time_total":%{time_total},"size_download":%{size_download}}\n' \
      -X "$method" "$BASE_URL$url" -H 'Content-Type: application/json' --data "$body" > "$meta"
  else
    curl -sS --max-time 600 -D "$h" -o "$out" \
      -w '{"http_code":%{http_code},"time_total":%{time_total},"size_download":%{size_download}}\n' \
      -X "$method" "$BASE_URL$url" > "$meta"
  fi

  jq -c --arg name "$name" --arg headers "$h" --arg body_path "$out" \
    '. + {name:$name, headers:$headers, body_path:$body_path}' "$meta" >> "$TEST_ROOT/responses.jsonl"
}
```

Binary endpoints should use the same pattern but write to `$TEST_ROOT/binaries/` and verify:

- HTTP status is 200.
- `Content-Type` is appropriate.
- Downloaded file size is non-zero.
- `file` command recognizes the artifact type.
- Hash is captured with `sha256sum`.

SSE endpoints should be captured with bounded timeouts:

```bash
curl -sS -N --max-time 900 "$BASE_URL/api/pipeline/runs/${RUN_ID}/events" \
  | tee "$TEST_ROOT/sse/pipeline-${RUN_ID}.log"
```

---

## 6. Pre-Test State Snapshot

Before endpoint testing, capture:

1. `git status --short`.
2. `/health`.
3. `/api/system/status`.
4. `/api/config/pipeline`.
5. Counts from the DB: movies, series, seasons, episodes, media files, subtitle inventories, letterbox states, pipeline runs, artwork events, media jobs, subtitle policies.
6. Hash/line count of feedback labels, if present.
7. Hashes of taste/profile artifacts, if present.
8. Hash/copy of `data/pipeline_overrides.json`, if present.
9. Mount/read-only state for the media roots.
10. `stat` records for every media file selected as a fixture.

The DB snapshot should be read-only. Use SQLAlchemy or sqlite directly, depending on the configured `DATABASE_URL`.

---

## 7. Fixture Discovery

Use endpoints first, manual DB setup only where no endpoint exists.

### 7.1 General fixtures

1. `MOVIE_PIPELINE_ID` — from `GET /api/library/movies?page_size=200`, first movie with `tmdb_id`.
2. `MOVIE_MEDIA_ID` and `MEDIA_FILE_ID` — first movie with `media_file_id`; if none, use `POST /api/movies/{movie_id}/subtitles/inspect` to create/link one.
3. `SERIES_ID` — first item from `GET /api/library/series?page_size=200`, if any.
4. `SEASON_SERIES_ID` — any series with seasons from `GET /api/library/series/{series_id}/seasons`.
5. `EPISODE_ID` — no list endpoint exists, so discover via read-only DB query against `episodes`.
6. `RUN_ID` — created by `POST /api/pipeline/movie/{MOVIE_PIPELINE_ID}/run`, or latest existing run if pipeline creation fails for an environmental reason.
7. `EXEMPLAR_NAME` — first point from `GET /api/taste/map`.
8. `TRACK_ID` — from subtitle inventory for `MEDIA_FILE_ID`; prefer an external text subtitle for preview/download, otherwise use an embedded subtitle to verify the non-previewable response.
9. `LETTERBOX_MOVIE_ID` — first ID in `GET /api/letterbox/movies/find-candidates` `detectable_movie_ids`, falling back to any returned movie with a file path.

### 7.2 Synthetic DB-only fixtures

Create temporary rows only for endpoint branches that would otherwise permanently change real DB state and have no endpoint undo.

Required synthetic fixtures:

| Fixture | Why |
|---|---|
| Temporary `Movie` + `LetterboxState` | Safely test `POST /api/letterbox/movies/{id}/ignore` without permanently marking a real title skipped. |
| Temporary `Movie` with `radarr_id` | Safely test Radarr `Rename` branch without changing a real movie path. |

Cleanup synthetic rows at the end and record before/after DB counts.

---

## 8. Endpoint Test Matrix

### 8.1 Health, system, config

| Endpoint | Test | Verification |
|---|---|---|
| `GET /health` | Call first and last. | Status is `ok` or documented `degraded`; `database` key matches DB connectivity check. |
| `GET /api/system/status` | Call before/after. | Contains `cache`, `heal`, `letterbox_heal`, `webhook`, `tools`, `media_jobs`; media-job counts match DB grouped counts. |
| `GET /api/system/status/generators` | Call once. | Response shape matches `{"generators": [...]}`; compare with `GET /api/subtitle-generators`. |
| `GET /api/config/pipeline` | Call before config tests. | Contains `values`, `defaults`, `overrides`, `restart_required`; every restart-required key is a known pipeline setting. |
| `PUT /api/config/pipeline` | Run three subtests: empty body -> 400, unknown key -> 400, restart-required key -> 400. Then run one reversible positive update using a non-restart knob and restore the original overrides file. | Validation errors are machine-readable enough to explain the failure. Positive update appears in `GET /api/config/pipeline`, then restored state exactly matches the pre-test hash. |
| `POST /api/system/heal` | **Skip.** | Direct poster restore risk. |

### 8.2 Sync and library

| Endpoint | Test | Verification |
|---|---|---|
| `POST /api/sync/all` | Run once early unless rate limit is active. If 429, record rate-limit behavior and use existing DB data. | Report has `status`, `duration_seconds`, movie/series/season/episode created/updated/error counts. Compare DB counts before/after and verify no media file stats changed. |
| `GET /api/library/movies` | Test default page, `page=1&page_size=1`, and invalid `page_size=999`. | Pagination metadata matches item count; invalid query returns 422; each returned `media_file_id` exists in DB when non-null. |
| `GET /api/library/movies/{movie_id}` | Use `MOVIE_MEDIA_ID`; also test missing ID. | Detail matches list item and DB row; `subtitle_coverage` matches latest `SubtitleInventory.coverage_json` when present; missing ID returns 404. |
| `GET /api/library/series` | Test default and small page. | Count matches DB; item schema includes `id`, `title`, `year`, `season_count`. |
| `GET /api/library/series/{series_id}` | Use `SERIES_ID`; also missing ID. | Detail matches DB; missing ID returns 404. |
| `GET /api/library/series/{series_id}/seasons` | Use `SEASON_SERIES_ID`. | Returned seasons are ordered by `season_number` and match DB. |
| `GET /api/library/episodes/{episode_id}` | Use DB-discovered `EPISODE_ID`; also missing ID. | Detail matches DB and `episode_media_files` association. |

### 8.3 Production pipeline, run archive, posters, rescore

| Endpoint | Test | Verification |
|---|---|---|
| `POST /api/pipeline/movie/{movie_id}/run` | Start a production run for `MOVIE_PIPELINE_ID`. Immediately try a second run to exercise 409 conflict while active. | First call returns 202 with `run_id`, `events_url`, `results_url`; second call returns 409 with active run ID. No media file stats change. |
| `GET /api/pipeline/runs/{run_id}/events` | Connect immediately after starting the run and save raw SSE. | SSE contains stage events and ends with `event: done`; stage names align with design 09. |
| `GET /api/pipeline/runs/{run_id}` | Poll until terminal. | Payload contains `movie`, `status`, `auto_pick`, `ranked`, `rejected`, `counts`, `stage_timings_s`, `config_snapshot`; archive JSON on disk matches returned candidate names/counts. |
| `GET /api/pipeline/runs/{run_id}/posters/{orig_filename}` | Download auto-pick poster and one rejected/ranked poster if available. Also test invalid filename. | File is image, non-zero, path-confined by archive record; invalid candidate returns 404. |
| `POST /api/pipeline/runs/{run_id}/rescore` | Test `{}` and one safe weight/gate override. | Ranked output sorted by `final_score`; candidate set is subset of archived ranked candidates; `gated_out` has valid reasons; no images/inference rerun. |
| `GET /api/movies/{movie_id}/runs` | Call before/after production run. | New `run_id` appears after run, ordered newest first. |
| `GET /api/movies/{movie_id}/artwork-events` | Use same movie. | Events match `artwork_events` DB rows. Empty history is acceptable. |
| `POST /api/test/pipeline/movie/{movie_id}` | Run once after production run is complete and GPU is idle. | Response paths exist: `pipeline_log`, `pipeline_run_json`, `output_dir`; JSON counts match response; output remains under `experiments/runs/`; no media stats change. |

### 8.4 Feedback loop

Use the completed `RUN_ID`. Always send `deploy:false` for `approve` and `override`.

| Endpoint/action | Test | Verification |
|---|---|---|
| `POST /api/feedback` action `approve` | Approve auto-pick with `deploy:false`, then undo. | `labels_written=1`; `deployed_to` is null; run reviewed marker flips true then false after undo; labels/profile deltas are explained and undone. |
| `POST /api/feedback` action `override` | Pick a non-auto ranked candidate when available, with `deploy:false`, then undo. | `labels_written=2` when auto differs from pick; selected filename exists in run archive; no media file stats change. |
| `POST /api/feedback` action `reject_all` | Reject auto-pick, then undo. | `labels_written=1`; no exemplar added; undo removes exactly that event's labels. |
| `POST /api/feedback` negative cases | Bad run ID, unknown action, override without `selected_filename`. | Expected 404/400 responses, not 500. |
| `POST /api/feedback/undo` | Undo each event immediately after verification; also test unknown event ID. | Known event removes labels and clears reviewed marker; unknown returns 404. |

### 8.5 Taste profile and taste map

| Endpoint | Test | Verification |
|---|---|---|
| `GET /api/taste/status` | Call before and after feedback tests. | Label counts change only during active feedback test and return after undo; exemplar/head status is internally consistent. |
| `GET /api/taste/map` | Call with `recompute=false`. If 404 because map artifact is missing, run `POST /api/taste/map/rebuild` and poll. | Map has finite coordinates; point count is plausible relative to exemplar count; clusters reference valid point IDs/names. |
| `POST /api/taste/map/rebuild` | Run only if map missing or as a late artifact test after pipeline/feedback tests. | Returns 202; subsequent `GET /api/taste/map` eventually succeeds or records rebuild error in bug log. |
| `POST /api/taste/map/candidates` | Use completed `RUN_ID`. | Returned candidate names are subset of ranked run candidates with cached embeddings; coordinates finite. |
| `GET /api/taste/exemplars/{name}/image` | Use `EXEMPLAR_NAME`, test `size=thumb` and `size=full` when available; also invalid `../bad`. | Image response non-zero; invalid name returns 400. |
| `GET /api/taste/exemplars/{name}/neighbors` | Use `EXEMPLAR_NAME`; also unknown name. | Neighbor list sorted/valid; unknown returns 404. |
| `POST /api/taste/retrain` | Run late, only when GPU is idle and after artifact hashes are captured. | Acceptable outputs: `started`, `already_running`, or 409 GPU busy. If started, poll `/api/taste/status` until rebuild stops; compare artifact hashes and record changes. |

### 8.6 Letterbox detection/review

| Endpoint | Test | Verification |
|---|---|---|
| `GET /api/letterbox/status` | Call first. | Contains `enabled`, `method`, `counts`, `binaries`, `honored_by`, `not_honored_by`, `batch_active`; binary availability matches `GET /api/system/status`. |
| `GET /api/letterbox/movies/find-candidates` | Test default and `include_skipped=true&include_analyzed=true`. | Counts add up; candidate/unknown/detectable IDs are subsets of returned movie IDs; DB `letterbox_state` prefilter rows match response after call. |
| `GET /api/letterbox/candidates` | Test default, status filter, confidence filter, sort variants, invalid pagination. | Pagination correct; filters match item fields; invalid pagination returns 422. |
| `GET /api/letterbox/movies/{movie_id}` | Use a movie with existing state, or after detect. | Samples parse as JSON; preview URLs use implemented `minute` query; detail matches DB state. |
| `POST /api/letterbox/movies/{movie_id}/detect` | Use one detectable candidate. | Requires ffmpeg. Response updates DB state; source dimensions match `ffprobe`; status/confidence/crop values are plausible and documented. |
| `POST /api/letterbox/detect` | Use a small explicit `movie_ids` list, not `all_candidates=true` for the first pass. | Returns 202 with `job_id`; no giant batch accidentally starts. |
| `GET /api/letterbox/jobs/{job_id}/events` | Connect immediately after batch detect. | SSE progress total matches request movie count and ends cleanly. |
| `GET /api/letterbox/movies/{movie_id}/preview` | Fetch `mode=before` and `mode=after` using `preview_minute` from detail. | WebP image non-zero, generated under preview cache, no media stats changed. |
| `POST /api/letterbox/movies/{movie_id}/ignore` | Use synthetic temporary movie/state, not a real title. | State changes to `skipped`, `reviewed=true`; cleanup deletes synthetic rows. |
| `POST /api/letterbox/movies/{movie_id}/apply` | **Skip.** | Direct MKV tag write. |
| `POST /api/letterbox/apply` | **Skip.** | Direct MKV tag write. |
| `POST /api/letterbox/movies/{movie_id}/remove` | **Skip.** | Direct MKV tag write. |
| `POST /api/letterbox/heal` | **Skip.** | Direct MKV tag re-apply risk. |

### 8.7 Subtitle inventory, plans, jobs, policies, generators

| Endpoint | Test | Verification |
|---|---|---|
| `POST /api/movies/{movie_id}/subtitles/inspect` | Use `MOVIE_MEDIA_ID`. | Creates/uses media-file row, scans with ffprobe, returns inventory; compare subtitle/audio stream counts with independent `ffprobe`. |
| `GET /api/media-files/{media_file_id}/subtitles` | Test cached call and `force=true`. | Cached and forced results are consistent; coverage equals DB `SubtitleInventory.coverage_json`. |
| `POST /api/media-files/{media_file_id}/subtitles/scan` | Force a fresh inline scan. | Inventory timestamp/id updates as expected; stream counts still match ffprobe. |
| `GET /api/media-files/{media_file_id}/subtitles/{track_id}/preview` | Use external text track if available; otherwise embedded/bitmap track. | External text returns cue preview; embedded/bitmap returns documented non-previewable JSON instead of 500. |
| `GET /api/media-files/{media_file_id}/subtitles/{track_id}/download` | Use external track if available. If no external track exists, run only 404 negative case. | Downloaded subtitle is non-zero and path is in same media directory; no file writes. |
| `POST /api/media-files/{media_file_id}/subtitle-plans` | Create a safe planned job, preferably `subtitle_metadata` with no actual confirm. | Returns `job_id`, `status=planned`, before/after/capabilities/storage; no media file stats change. |
| `GET /api/media-jobs` | Test default and filters by `status`/`operation`. | Created plan appears; filters agree with DB. |
| `GET /api/media-jobs/{job_id}` | Use planned job ID. | Job payload matches plan response. |
| `GET /api/media-jobs/{job_id}/events` | Call before/after cancel. | SSE replays persisted events and eventually returns done for terminal status. |
| `POST /api/media-jobs/{job_id}/cancel` | Cancel the planned job. | Status becomes cancelled/cancel requested; no worker media mutation is started. |
| `POST /api/media-jobs/{job_id}/confirm` | **Skip.** | Would queue mutation execution. |
| `POST /api/media-jobs/{job_id}/restore` | **Skip.** | Restores media backup/sidecar files. |
| `DELETE /api/media-jobs/{job_id}/backup` | **Skip.** | Deletes backup files. |
| `GET /api/subtitle-policies` | Call before/after CRUD. | Count changes only around test policy and returns after delete. |
| `POST /api/subtitle-policies` | Create disabled test policy named with run ID. | Response has ID/revision and matches DB. |
| `GET /api/subtitle-policies/{policy_id}` | Fetch created policy. | Matches create response. |
| `PUT /api/subtitle-policies/{policy_id}` | Update languages/name. | Revision increments; fields updated. |
| `POST /api/subtitle-policies/{policy_id}/audit` | Audit against `[MOVIE_MEDIA_ID]`. | Dry-run only; removals/protected/warnings consistent with inventory and policy rules. |
| `DELETE /api/subtitle-policies/{policy_id}` | Delete test policy. | Subsequent get returns 404; count returns to baseline. |
| `POST /api/subtitle-policies/{policy_id}/apply` | **Skip.** | Queues subtitle removal jobs. |
| `GET /api/subtitle-generators` | Call and compare with `/api/system/status/generators`. | Same provider health/capability information. |
| `POST /api/media-files/{media_file_id}/subtitle-generations` | **Skip.** | Queues subtitle output write. |
| `POST /api/movies/{movie_id}/subtitle-generations` | **Skip.** | Queues subtitle output write. |

### 8.8 Webhooks

| Endpoint | Test | Verification |
|---|---|---|
| `POST /api/webhooks/radarr` | Test `{"eventType":"Test"}`. | Returns friendly 200 body; `/api/system/status.webhook` updates. |
| `POST /api/webhooks/radarr` | Test `Rename` using a synthetic DB movie with temporary `radarr_id`. | Only synthetic row `folder_path` changes; cleanup removes it. |
| `POST /api/webhooks/radarr` | Test ignored event and untracked `Download` upgrade payload. | Returns documented `ignored` or `restore_scheduled` without touching any selected media file. Do not use a tracked real movie for upgrade. |
| `POST /api/webhooks/sonarr` | Test `{"eventType":"Test"}` and one ignored event. | 200 body and webhook state update. |
| `POST /api/webhooks/subgen` | Send harmless callback payload. | Returns `{"ok": true}` and updates webhook state; token handling returns 401 if configured and omitted. |

---

## 9. Correctness Verification Strategy

### 9.1 Cross-endpoint consistency

- Movie detail must match movie list for the same ID.
- Run history must include the run created by the pipeline endpoint.
- Run results candidate names must match the run archive JSON.
- Poster download endpoints must serve files whose paths are recorded in the archive, not path input.
- Taste candidate overlay names must be a subset of ranked run candidates.
- Subtitle inventory returned by inspect, get, and scan should agree after a forced scan.
- Media-job list/get/events must agree on status transitions.
- System status media-job counts must match DB grouped counts.

### 9.2 Independent verification

Use non-endpoint verification where practical:

- DB read-only queries for counts, IDs, policies, jobs, runs, events, letterbox states, subtitle inventories.
- `ffprobe` for media stream counts, dimensions, and subtitle/audio stream metadata.
- `stat` before/after for selected media files.
- `sha256sum` for downloaded binary responses.
- Archive JSON and `pipeline_run.json` for pipeline outputs.
- Saved `data/pipeline_overrides.json` hash before/after config update/restore.
- Feedback labels file line counts and event IDs before/after feedback/undo.

### 9.3 Media immutability check

For every media path selected as a fixture:

```bash
stat -c '%n	%s	%Y	%i	%h	%a' "$path"
```

Save before and after. Any change in size, mtime, inode, link count, or mode for a selected media file is a blocker and must be logged as a bug unless the file was outside the tested fixture set and conclusively unrelated.

---

## 10. Dedicated Bug Log Format

Every bug entry in `$TEST_ROOT/endpoint-test-bugs.md` should use this template:

```markdown
## BUG-### — short title

**Endpoint:** `METHOD /path`  
**Fixture:** movie/media/run/job/etc. IDs  
**When:** UTC timestamp  
**Severity:** blocker | high | medium | low  
**Expected:** design/code expectation  
**Actual:** status/body/timeout/hang/invalid artifact  
**Repro:** exact curl command or harness request name  
**Artifacts:** response path, headers path, SSE log, binary path, server log snippet if available  
**Notes:** relevant design/code file references
```

Do not put expected negative-path results in the bug log. For example, a deliberate missing ID returning 404 is a pass, not a bug.

---

## 11. Current Design Drift Notes Found During Planning

These are not test failures for this endpoint pass. They are now tracked in `design/todos.md` under **API design drift follow-ups**. The test plan below is intentionally aligned to the current implementation so the run does not fail just because the design docs are stale.

| Drift | Current implementation | Test-plan disposition |
|---|---|---|
| `design/more-features/01-poster-restoration.md` mentions `POST /api/movies/{movie_id}/poster/restore`, but no such route exists. | No route is registered in `marquee/main.py` / `marquee/api/routes/pipeline.py`. | Do not test this endpoint. It is not counted in the 69 implemented endpoints. |
| `design/more-features/03-subtitle-management.md` lists dedicated `/api/subtitle-batches/...` endpoints. | Current code has no dedicated subtitle-batch routes; policy apply creates `MediaBatch` records internally. | Do not test `/api/subtitle-batches/...` endpoints. Test only implemented media-job and subtitle-policy routes. |
| Subtitle design says `POST /api/media-files/{id}/subtitles/scan` queues a forced refresh. | Current route performs an inline forced scan and returns the inventory directly. | Test it as an inline scan. Do not expect `202`, `job_id`, or SSE. |
| Subtitle design mentions server-side library filters (`language`, `missing_language`, etc.). | Current `library.py` list endpoints expose pagination only. | Test pagination and invalid pagination only. Do not send unimplemented filter query params as acceptance tests. |
| Letterbox design table says preview uses `t=<sec>`. | Current route uses `minute=<int>` plus `mode=before\|after`. | Test with `minute=` from the movie detail payload's `preview_minute`. Do not send `t=` as a positive-path test. |
| Letterbox design allows `POST /api/letterbox/movies/{id}/detect` to return `202 + job_id` or sync. | Current single-movie detect is synchronous and returns the updated letterbox state directly. | Test it as synchronous. Use `POST /api/letterbox/detect` for the async `202 + job_id` batch/SSE path. |

---

## 12. Recommended Execution Order

1. Create `$TEST_ROOT` and enable full transcript capture.
2. Capture pre-test state and media stats.
3. Health/system/config read checks.
4. Sync and library checks.
5. Fixture discovery and synthetic DB-only setup.
6. Production pipeline run + SSE + results + poster + rescore checks.
7. Legacy test pipeline endpoint check.
8. Feedback approve/override/reject_all with `deploy:false`, undoing each event immediately.
9. Taste status/map/exemplar/candidate overlay checks.
10. Letterbox status/prefilter/detect/batch/SSE/preview checks.
11. Subtitle inspect/inventory/scan/preview/download/plan checks.
12. Media-job get/list/events/cancel checks using the planned job.
13. Subtitle policy CRUD/audit checks.
14. Webhook safe-branch checks.
15. Optional late artifact-heavy tests: taste map rebuild and taste retrain, only if GPU idle and Gautam wants those artifacts exercised now.
16. Capture post-test state and media stats.
17. Compare before/after snapshots.
18. Write `endpoint-test-summary.md` and final `endpoint-test-bugs.md`.

---

## 13. Pass Criteria

A pass means:

- Every included endpoint was exercised or explicitly skipped with a documented prerequisite reason.
- Every direct media mutation endpoint was skipped.
- Selected media-file stats are unchanged before/after.
- No unexpected 5xx responses occurred.
- No endpoint hangs beyond its timeout.
- All binary responses are non-empty and type-correct.
- All SSE streams either complete normally or produce a documented, reproducible bug.
- Pipeline/test-pipeline artifacts exist and agree with endpoint responses.
- Feedback tests leave labels/reviewed state clean after undo.
- Config update test restores the original overrides file.
- Synthetic DB fixtures and test policies/jobs are cleaned up or explicitly recorded if cleanup fails.

---

## 14. Endpoint Count

Static route inventory found **69 currently implemented endpoints**, including `/health`. The plan above covers all 69 as either:

- safe to test under read-only-media constraints,
- safe only with `deploy:false` or synthetic fixtures,
- safe as a setup/DB-only endpoint with cleanup, or
- intentionally skipped because it directly mutates media-library files.
