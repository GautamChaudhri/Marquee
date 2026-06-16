# 05 — Comprehensive Gauntlet Test Plan (write-enabled, two-pass)

**Status:** Execution-ready
**Scope:** Every implemented endpoint, exercised for real on writable lab media — including the media-mutating endpoints the read-only pass (`design/12`) skipped, plus the two newly implemented routes (`poster/restore`, library filters). **Movies only — no TV** this round.
**Model:** Extends `design/12-readonly-endpoint-test-plan.md` (same capture discipline, bug-log template, independent verification) with: real mutations, self-reversal, a **GPU pass then a CPU pass**, and **log-and-continue** on every error.

> Prerequisite gate: complete `04-gauntlet-readiness.md` (A–F) before starting. Apply `01`–`03` so code and docs match what this plan tests.

---

## 1. Goal

Prove that **every feature and endpoint** works end-to-end on writable media, on **both** compute paths (GPU and CPU), while:

1. Capturing every request, response, header, binary, SSE transcript, and verification result to disk.
2. Logging every error with full repro detail and **continuing** the run.
3. Mutating media safely and **returning it to baseline** after each mutation.
4. Recording GPU-vs-CPU behavior and timing deltas.

This is the last gate before the project moves forward; it is meant to find runtime bugs the read-only pass could not.

---

## 2. Two-pass structure

| Pass | Provider env | Restart | Covers |
|---|---|---|---|
| **A — GPU** | `EXECUTION_PROVIDER=auto` (CUDA), `OCR_DEVICE=auto` | start fresh | full matrix |
| reset | restore media to baseline (per `04` §C) | — | manifest re-verified |
| **B — CPU** | `EXECUTION_PROVIDER=cpu`, `OCR_DEVICE=cpu` | restart server | full matrix again |

Each pass gets its own timestamped root; a top-level run dir ties them together:

```bash
export BASE_URL="http://192.168.4.199:3165"     # or 127.0.0.1:3165 on the box
export GAUNTLET_ID="$(date -u +%Y%m%dT%H%M%SZ)"
export GAUNTLET_ROOT="/forge/Marquee/experiments/endpoint-tests/gauntlet-${GAUNTLET_ID}"
mkdir -p "$GAUNTLET_ROOT"/{passA-gpu,passB-cpu,shared}
# Per pass (run twice, PASS in {passA-gpu, passB-cpu}):
export TEST_ROOT="$GAUNTLET_ROOT/$PASS"
mkdir -p "$TEST_ROOT"/{requests,responses,headers,binaries,sse,state,verify,server-logs}
exec > >(tee -a "$TEST_ROOT/full-transcript.log") 2>&1
```

`shared/` holds the baseline manifest, fixture selection, and the cross-pass comparison (timings, bug rollup).

---

## 3. Safety & error policy

- **Allowed:** everything. This is the write-enabled pass. Mutations run on the lab copy only.
- **Error handling (log-and-continue):** on any unexpected status, timeout, hang, malformed body, or failed verification → write a bug entry (§10) with full artifacts, then **continue to the next independent step**. Do **not** stop the whole run.
- **Hard-pause conditions (only these):** server unreachable / 5xx storm on `/health`, or a GPU/ffmpeg hang that blocks a worker. Retry with backoff (0.5/2/5/15s); if still dead after backoff, record it, **skip the rest of that endpoint family**, and continue with families that don't depend on it. Note the pause in the summary.
- **No code/config/infra changes during the run.** If `04` prerequisites turn out unmet mid-run (e.g. worker not started), record it and continue where possible.
- **Reset discipline:** every mutation is paired with its reversal (§8) and a baseline re-check (§9.3). Drift → fallback re-copy (`04` §C.4).

---

## 4. Output capture (per pass)

Same files as `design/12` §4, plus mutation-specific additions:

| File | Purpose |
|---|---|
| `full-transcript.log` | tee of everything. |
| `requests.jsonl` / `responses.jsonl` | one record per request/response (name, method, url, body, status, time, paths). |
| `endpoint-test-summary.md` | pass/fail/skip per endpoint family, per pass. |
| `endpoint-test-bugs.md` | dedicated bug log (§10). |
| `fixture-selection.json` | resolved movie/media-file/run/job/policy/track/exemplar IDs. |
| `state/baseline-manifest.tsv` | `stat`+`sha256` for every fixture media file & sidecar (shared, captured once before Pass A). |
| `state/before.json` / `state/after.json` | DB + system snapshots. |
| `state/media-manifest-{stage}.tsv` | manifest re-capture after each mutation family. |
| `sse/*.log` | raw SSE for pipeline, letterbox batch, media-jobs. |
| `binaries/*` | posters, exemplar images, subtitle downloads, letterbox previews (+ `sha256`). |
| `server-logs/*` | server log snippets captured around each bug. |
| `verify/*` | ffprobe / mkvmerge JSON, DB query outputs, cross-endpoint checks. |

Use the `request_json` wrapper from `design/12` §5 verbatim (captures headers, body, status, time, curl errors). Binary + SSE handling per `design/12` §5.

---

## 5. Fixtures — the 23 lab movies

From `design/_temp-movie-selection-plan.md` (Option A trim: **dropped** Blade Runner 2049 `id=257` and Edge of Tomorrow `id=28`). Resolve each by `GET /api/library/movies?page_size=200` and record real IDs in `fixture-selection.json` (IDs below are from the planning snapshot — re-verify post-sync; they may shift).

| ID | Title | Res | Cont. | Subs | Codec | Audio | Letterbox | Primary variations exercised |
|---|---|---|---|---|---|---|---|---|
| 1 | 2001: A Space Odyssey | 4K | MKV | 9 | text+bitmap | eng | crop=(0,0) | forced sub; **false-positive letterbox** |
| 2 | A Quiet Place: Day One | 4K | MKV | 40 | text+bitmap | eng | detected | **massive lang diversity (policy stress)** |
| 3 | Alien: Romulus | 4K | MKV | 32 | text only | eng+tur | detected | 32 subs text-only; forced; dual audio |
| 5 | Arrival | 4K | MKV | 40 | **bitmap only** | 11 langs | crop=(276,276) | **11 audio tracks**; bitmap preview; large |
| 12 | Captain America: BNW | 4K | MKV | 38 | text+bitmap | eng | crop=(276,276) | 2 forced; SDH variants; letterbox apply |
| 15 | Color Out of Space | 4K | **MP4** | **0** | — | eng | detected | **MP4 + no subs (generation)** + letterbox |
| 25 | Dune: Part Two | 4K | MKV | 4 | text+bitmap | eng+ita | crop=(276,276) | large; dual audio; SDH; preview |
| 31 | Furiosa | 4K | MKV | 6 | text only | eng | detected | moderate text-only |
| 36 | Hot Fuzz | 4K | MKV | 6 | **bitmap only** | eng | crop=(263,263) | single-lang **bitmap preview**; apply |
| 52 | Moana 2 | 4K | MKV | 35 | text only | eng | detected | text-only, 35 langs |
| 61 | Reservoir Dogs | 4K | **MP4** | **0** | — | eng | detected | MP4 no subs (generation) + letterbox |
| 73 | Cabin in the Woods | 4K | **MP4** | **0** | — | eng | detected | MP4 no subs (generation) + letterbox |
| 148 | In the Mouth of Madness | 1080p | **MP4** | **0** | — | eng | not candidate | small MP4; **has external .srt (embed)** |
| 155 | Leave No Trace | 1080p | MKV | 5 | **ASS+SRT** | eng | not candidate | **ASS codec**; SDH; 1080p |
| 204 | The Game | 1080p | MKV | 2 | text only | eng | not candidate | ultra-minimal (2 subs); commentary audio |
| 219 | The Sixth Sense | 4K | **MP4** | 1 | **mov_text** | eng+spa | not candidate | **mov_text (MP4 sub adapter)**; dual audio |
| 224 | The Zone of Interest | 1080p | MKV | 23 | text only | **ger** | not candidate | **German audio**; non-eng |
| 314 | Knives Out | 4K | **MP4** | **0** | — | eng | not candidate | MP4 no subs (generation) |
| 338 | Catch Me If You Can | 1080p | MKV | 41 | text+bitmap | eng | not candidate | **41 subs (policy stress)**; SDH |
| 434 | Exit 8 | 1080p | MKV | 12 | text+bitmap | **jpn** | not candidate | **Japanese audio**; SDH |
| 435 | Lincoln | 1080p | MKV | **0** | — | eng | crop=(140,140) | **no embedded + external .srt (embed)**; letterbox apply |
| 439 | Burning | 1080p | MKV | 4 | text+bitmap | **kor** | not candidate | **Korean audio** + external .srt |
| 443 | The Borderlands | 1080p | MKV | 1 | text only | eng | crop=(0,0) | **false-positive letterbox**; single sub |

### Endpoint family → fixtures (drive the matrices)

- **Library + filters:** all 23 (browse, detail, pagination); filters exercised against varied coverage (eng-only `204`, 30+ langs `2`/`52`/`338`, no-inventory until inspected, external-present `148`/`435`/`439`, generated after a generation test).
- **Pipeline (poster):** diverse poster styles — `1`, `5`, `25`, `36`, `52`, `31` minimum; extend to all 23 if time permits for the GPU/CPU timing sweep.
- **Subtitle inspect/inventory/scan:** all 23 (every embedded/external combination).
- **Subtitle preview — text:** `2`, `3`, `204`; **ASS:** `155`; **mov_text:** `219`. **bitmap (limitation msg):** `5`, `36`.
- **Subtitle download (external):** `148`, `435`, `439` (have external `.srt`); 404-path on a movie with none.
- **Subtitle plan/confirm/execute — remove:** `2` (40), `338` (41), `12` (38). **embed:** `435`, `148` (external→embed). **metadata:** `204`. **extract:** `5`/`36` (bitmap extract to `.sup`).
- **Subtitle generation (needs `SUBGEN_URL`):** `15`, `61`, `73`, `314`, `435` (no embedded subs). Non-eng generation target: `224` (ger), `434` (jpn), `439` (kor).
- **Subtitle policies (audit/apply):** blacklist/whitelist on `2` + `338` + `52`.
- **Letterbox detect/preview/apply/remove/heal:** detect varied crops `435`(140), `25`/`5`/`12`(276), `36`(263); preview large crops `25`, `36`; **apply** `435`, `5`, `12`; **false-positive** `1`, `443` (crop 0,0); not-candidate skip `204`, `224`.
- **Feedback deploy / poster restore:** any pipeline-run movie (e.g. `1`, `25`, `52`).
- **Webhook upgrade (real):** one lab movie with a `radarr_id` (synthetic or a lab title) — `435` or a synthetic.
- **Multi-audio:** `5`(11), `3`/`25`/`219`(2). **MP4 adapter:** `15`,`61`,`73`,`148`,`219`,`314`.

---

## 6. Pre-test snapshot (before Pass A)

Per `design/12` §6, plus:

1. `git status --short`; `/health`; `/api/system/status` (+ `media_jobs`, `tools`, OCR-status block); `/api/system/status/generators`; `/api/config/pipeline`.
2. DB counts: movies, series, seasons, episodes, media files, subtitle inventories, letterbox states, pipeline runs, artwork events, media jobs, media batches, media backups, subtitle policies.
3. Confirm prerequisites from `04` §B (worker running, SUBGEN status, lab writable, binaries).
4. **Baseline manifest** (`shared/state/baseline-manifest.tsv`): `stat` + `sha256` for every fixture media file and every sidecar `.srt`.
5. Hashes of taste/profile artifacts and `data/pipeline_overrides.json` if present.
6. Record active provider (must be GPU for Pass A).

---

## 7. Mutation reversal contract (applies throughout §8)

Every mutating step is authored as: **mutate → verify-changed → reverse → verify-baseline**. Reversal map:

| Mutation | Verify-changed | Reverse | Verify-baseline |
|---|---|---|---|
| letterbox `apply` | `mkvmerge -J` shows crop tags; `letterbox_state` = `tagged` | `remove` | tags gone; ffprobe track layout == baseline (sha may differ — content-equivalent) |
| subtitle remove/metadata/embed (job) | ffprobe stream delta; job `succeeded` | `POST /api/media-jobs/{id}/restore` | ffprobe + **sha256 == baseline**; then `DELETE /api/media-jobs/{id}/backup` |
| policy `apply` | per-file jobs `succeeded`; tracks removed | restore each job backup | sha256 == baseline |
| feedback `deploy=true` | poster file present + cache meta | restore prior poster / `system/heal` | poster path back to prior |
| `poster/restore` (manual) | poster file (re)created | n/a (this *is* a restore) | poster present |
| webhook upgrade (real) | restore scheduled; artwork/letterbox/subtitle effects | revert synthetic row / restore | baseline |

If any reverse leaves drift beyond the documented tolerance, log it and re-copy the title from the read-only source before Pass B (`04` §C.4).

---

## 8. Endpoint test matrix (full)

Run **every** sub-section in **both** passes. Verification columns abbreviated; default verification = status as documented + independent cross-check (DB/ffprobe/mkvmerge/stat/sha256) + no 5xx.

### 8.1 Health / system / config
- `GET /health` (first & last) — `ok`/`degraded`; `database` matches connectivity.
- `GET /api/system/status` — `cache`, `heal`, `letterbox_heal`, `webhook`, `tools`, `media_jobs`, **OCR status block** (`device`, `configured_workers`, `effective_workers`, `paddle_cuda_available`, `workers`); media-job counts == DB grouped counts; OCR `device` matches the pass (gpu/auto in A, cpu in B).
- `GET /api/system/status/generators` and `GET /api/subtitle-generators` — agree on provider health.
- `GET /api/config/pipeline` — `values/defaults/overrides/restart_required`.
- `PUT /api/config/pipeline` — three negative subtests (empty→400, unknown key→400, restart-required key→400). One **positive** safe-key update is allowed this pass (writable), then revert it and re-hash `data/pipeline_overrides.json`.
- `POST /api/system/heal` — **now run it.** Delete one deployed poster on disk first, heal, confirm `restored>=1` and a `heal_restore` artwork event; re-verify the poster file exists.

### 8.2 Sync & library (+ new filters)
- `POST /api/sync/all` — once early; report counts; **media manifest unchanged** by sync.
- `GET /api/library/movies` — default, `page=1&page_size=1`, invalid `page_size=999`→422.
- **Filters (new):** `language=eng`, `missing_language=fre`, `source=external`, `forced_only=true`, `generated=true`, `min_tracks=2`, `inventory_state=scanned`, `inventory_state=unscanned`; combined `language=eng&min_tracks=2` + pagination; **`policy_violation=true`→400**, **`inventory_state=stale`→400**, `source=bogus`→422. Each filtered result spot-checked against DB coverage.
- `GET /api/library/movies/{id}` (valid + missing→404); `subtitle_coverage` matches latest inventory.
- `GET /api/library/series`, `/series/{id}`, `/series/{id}/seasons`, `/episodes/{id}` — basic shape + 404s (no TV mutation; read-only checks fine).

### 8.3 Pipeline / runs / posters / rescore
- `POST /api/pipeline/movie/{id}/run` for the pipeline fixture set; immediately fire a second concurrent run → expect `409` with active run id; subsequent runs wait for GPU/CPU idle.
- `GET /api/pipeline/runs/{run_id}/events` — SSE to `event: done`; stage names per design 09.
- `GET /api/pipeline/runs/{run_id}` — `movie/status/auto_pick/ranked/rejected/counts/stage_timings_s/config_snapshot`; archive JSON matches; **record `stage_timings_s`** for the GPU/CPU comparison; `config_snapshot` provider matches the pass.
- `GET /api/pipeline/runs/{run_id}/posters/{orig_filename}` — download auto-pick + one ranked/rejected; invalid filename→404; binaries non-zero + `file`-recognized + sha256.
- `POST /api/pipeline/runs/{run_id}/rescore` — `{}` and one safe weight/gate override; sorted by `final_score`; subset of archived ranked.
- `GET /api/movies/{id}/runs` — new run appears, newest first.
- `GET /api/movies/{id}/artwork-events` — matches DB.
- `POST /api/test/pipeline/movie/{id}` — once per pass when idle; output under `experiments/runs/`.

### 8.4 Feedback (now with real deploy) + manual restore
- `POST /api/feedback` `approve` with **`deploy:true`** on a completed run → poster deployed to the lab folder; verify file on disk + cache meta + `artwork_events action=deploy`; `labels_written` per design; then `POST /api/feedback/undo` and restore prior poster; manifest back to baseline.
- `override` (non-auto pick) with `deploy:true` → verify deploy + `labels_written=2` when auto≠pick; undo + restore.
- `reject_all` → `labels_written=1`, no exemplar added; undo.
- Negative: bad run id→404, unknown action→400, override w/o `selected_filename`→400 (not 500).
- **`POST /api/movies/{id}/poster/restore` (new):** delete deployed poster → restore (force=false) → `restored:true source:cache`; call again with poster present + force=false → `restored:false reason:already_present`; force=true → overwrites; movie-never-had-poster → non-5xx; confirm artwork events.

### 8.5 Taste
- `GET /api/taste/status` (before/after feedback).
- `GET /api/taste/map?recompute=false` (rebuild via `POST /api/taste/map/rebuild` if 404, poll).
- `POST /api/taste/map/candidates` (completed run) — names ⊆ ranked.
- `GET /api/taste/exemplars/{name}/image` (`size=thumb|full`; `../bad`→400); `/neighbors` (unknown→404).
- `POST /api/taste/retrain` — run late, GPU idle (Pass A) and CPU (Pass B); `started|already_running|409`; poll status; compare artifact hashes; record provider/timing.

### 8.6 Letterbox (full mutation cycle)
- `GET /api/letterbox/status` — `enabled/method/counts/binaries/honored_by/not_honored_by/batch_active`.
- `GET /api/letterbox/movies/find-candidates` (default + `include_skipped=true&include_analyzed=true`).
- `GET /api/letterbox/candidates` — filters/sort/pagination + invalid→422.
- `GET /api/letterbox/movies/{id}` — samples parse; `preview_urls` use `minute=`.
- `POST /api/letterbox/movies/{id}/detect` (sync, 200) — `435`,`25`,`36`; source dims match ffprobe; false-positive `1`,`443` → crop (0,0)/low confidence handled.
- `POST /api/letterbox/detect` (batch, 202+job_id) — small explicit `movie_ids`; `GET /api/letterbox/jobs/{job_id}/events` SSE total == request count, ends cleanly.
- `GET /api/letterbox/movies/{id}/preview?minute=&mode=before|after` — WebP non-zero; under preview cache; media unchanged.
- **`POST /api/letterbox/movies/{id}/apply`** (`435`,`5`,`12`) — `mkvmerge -J` confirms crop tags; state `tagged`; **then `POST /.../remove`** → tags gone; ffprobe layout == baseline.
- **`POST /api/letterbox/apply`** (batch, `only_high`) — aggregate result; reverse each.
- **`POST /api/letterbox/heal`** — after an apply, simulate drift if feasible; confirm conservative re-apply counts; reverse.
- `POST /api/letterbox/movies/{id}/ignore` — on a **synthetic** movie/state (don't skip a real title); cleanup.
- `422` ineligible on apply for a not-writable / non-MKV target (e.g. an MP4 fixture) — confirm reason string, not 500.

### 8.7 Subtitles (full mutation cycle)
- `POST /api/movies/{id}/subtitles/inspect` — all 23; stream counts == ffprobe.
- `GET /api/media-files/{id}/subtitles` (cached + `force=true`); `POST /api/media-files/{id}/subtitles/scan` (inline, returns inventory — **no job/SSE**, per reconciled doc).
- `GET .../subtitles/{track_id}/preview` — text (`2`,`3`,`204`), ASS (`155`), mov_text (`219`) → cues; bitmap (`5`,`36`) → documented non-previewable JSON (not 500).
- `GET .../subtitles/{track_id}/download` — external (`148`,`435`,`439`) → non-zero, path in same media dir; no-external movie → 404 only.
- `POST /api/media-files/{id}/subtitle-plans` — create plans: remove (`2`,`338`,`12`), embed (`435`,`148`), metadata (`204`), extract (`5`/`36`). Each returns `job_id`, `status=planned`, before/after/capabilities/storage; media unchanged at plan time.
- `GET /api/media-jobs` (filters by status/operation) + `GET /api/media-jobs/{id}` + `GET /api/media-jobs/{id}/events` (SSE).
- **`POST /api/media-jobs/{id}/confirm`** → worker runs → job `succeeded`; ffprobe shows the change; **`POST /api/media-jobs/{id}/restore`** (backup) → ffprobe + sha256 == baseline; **`DELETE /api/media-jobs/{id}/backup`** → backup gone, live media intact.
- `POST /api/media-jobs/{id}/cancel` — cancel a planned/queued job; verify no mutation started.
- **Subtitle generation** (`15`,`61`,`73`,`314`,`435`; non-eng `224`/`434`/`439`): if `SUBGEN_URL` set → `POST /api/media-files/{id}/subtitle-generations` and `/api/movies/{id}/subtitle-generations` → job runs → new track appears → **remove generated track / restore** to baseline. If `SUBGEN_URL` unset → expect `503`, record as expected-skip.
- **Subtitle policies:** `GET/POST /api/subtitle-policies` (create disabled test policy named with `GAUNTLET_ID`); `GET/PUT /{id}`; `POST /{id}/audit` against `[2, 338]` (dry-run; removals/protected/warnings consistent); **`POST /{id}/apply`** (real) on a movie with embedded subs → jobs run → verify → **restore each job backup** → baseline; `DELETE /{id}` → 404 after.

### 8.8 Media-jobs lifecycle edge cases
- Confirm expiry path (planned job past `plan_expires_at`) → confirm rejected.
- Signature revalidation: mutate file underneath a planned job (touch via a separate safe op) → confirm refuses on changed signature.
- `recover()`: not directly triggerable without a crash; verify no `running` jobs are orphaned at snapshot time.

### 8.9 Webhooks
- `POST /api/webhooks/radarr` `{"eventType":"Test"}` → friendly 200; `system/status.webhook` updates.
- `Rename` using a **synthetic** DB movie with temp `radarr_id` → only synthetic `folder_path` changes; cleanup.
- **Real `Download`+`isUpgrade=true`** on a lab movie with `radarr_id` (or synthetic pointing at a lab copy): confirm `restore_scheduled`; verify the three background effects (poster restore attempt, letterbox state flipped to `candidate`, `subtitle_scan` job created). Reverse: restore prior poster/state.
- ignored event + untracked `Download` upgrade → documented `ignored`/`restore_scheduled` without touching unrelated fixtures.
- `POST /api/webhooks/sonarr` `Test` + one ignored event → 200 + state update (no TV mutation).
- `POST /api/webhooks/subgen` harmless callback → `{ok:true}`; if `SUBGEN_CALLBACK_TOKEN` set, omitting it → 401.

---

## 9. Verification strategy

### 9.1 Cross-endpoint consistency
Movie detail == list for same id; run history includes created run; run results == archive JSON; poster download paths from archive (not input); taste candidates ⊆ ranked; inventory agrees across inspect/get/scan; media-job list/get/events agree on transitions; `system/status` media-job counts == DB grouped counts; letterbox `find-candidates` IDs ⊆ returned movies; filter results == DB coverage predicate.

### 9.2 Independent verification
Read-only DB queries; `ffprobe` (stream counts/dims/lang); `mkvmerge -J` (crop tags); `stat`+`sha256` (manifests); archive `pipeline_run.json`; `data/pipeline_overrides.json` hash; feedback label line counts + event IDs.

### 9.3 Media mutation check (inverted vs design/12)
For each mutation: assert the media **changed as expected** (ffprobe/mkvmerge delta), then after reversal assert it **returned to baseline** (sha256 for subtitle backup/restore; content-equivalence for MKV tag round-trips). Re-capture `state/media-manifest-{family}.tsv` after each mutation family and diff against baseline. Any unexplained drift in a non-targeted file = **blocker** bug.

### 9.4 Cross-pass comparison (`shared/`)
After both passes: diff `endpoint-test-summary.md`; tabulate pipeline `stage_timings_s` GPU vs CPU; note any endpoint that passed in one pass and failed in the other (provider-coupled bug); confirm baseline restored between passes.

---

## 10. Bug log format

Per `design/12` §10 template, plus a **Pass** field (`A-gpu` | `B-cpu`) and a **Reversal** field (did baseline restore succeed?). Endpoint-breaking bugs only; deliberate negative-path results (e.g. 404 for missing id, 400 for `policy_violation`, 503 when `SUBGEN_URL` unset) are passes, not bugs.

```markdown
## BUG-### — short title
**Pass:** A-gpu | B-cpu
**Endpoint:** `METHOD /path`
**Fixture:** movie/media/run/job IDs
**When:** UTC timestamp
**Severity:** blocker | high | medium | low
**Expected:** ...
**Actual:** ...
**Repro:** exact curl / harness request name
**Reversal:** baseline restored? (y/n + manifest diff path)
**Artifacts:** response/headers/SSE/binary/server-log paths
**Notes:** design/code refs
```

---

## 11. Execution order (per pass)

1. (Pass A only) capture baseline manifest + pre-test snapshot.
2. Health/system/config (incl. positive config update + revert; **run `system/heal`**).
3. Sync + library + **filters**.
4. Fixture discovery + synthetic DB-only setup (letterbox ignore movie, webhook rename movie).
5. Pipeline runs + SSE + results + posters + rescore + run history.
6. Legacy `test/pipeline`.
7. Feedback approve/override/reject_all **with deploy:true** + undo/restore; **manual poster/restore**.
8. Taste status/map/candidates/exemplars; **retrain** (idle).
9. Letterbox status/prefilter/detect/batch/SSE/preview → **apply→remove→heal**; false-positive; synthetic ignore.
10. Subtitle inspect/inventory/scan/preview/download → **plan→confirm→execute→restore→delete-backup**; **generation** (or expected-skip); **policy CRUD→audit→apply→restore**.
11. Media-jobs lifecycle edge cases.
12. Webhooks (incl. **real upgrade**).
13. Post-test snapshot + manifest diff; restore any drift.
14. Write per-pass `endpoint-test-summary.md` + `endpoint-test-bugs.md`.
15. (Between passes) reset media to baseline; restart server with CPU env; re-verify provider; repeat 2–14.
16. (After Pass B) write `shared/` cross-pass comparison.

---

## 12. Pass criteria

- Every endpoint exercised in **both** passes (or recorded as expected-skip with reason).
- No unexpected 5xx; no hang beyond timeout without a logged bug.
- Every mutation verified-changed then **returned to baseline**; no drift in non-targeted media.
- All binaries non-empty + type-correct; all SSE streams complete or produce a reproducible bug.
- Pipeline/test-pipeline artifacts exist and agree with responses; `stage_timings_s` captured for GPU and CPU.
- Feedback/labels clean after undo; config overrides reverted; synthetic fixtures + test policies cleaned up.
- VRAM stable across the GPU pipeline sweep; RAM pressure logged (not fatal) in the CPU pass.
- Cross-pass comparison written; provider-coupled discrepancies flagged.

---

## 13. Endpoint count

`design/12` counted **69** implemented endpoints. This plan covers all 69 **as real executions** (no media-mutation exclusions) plus:
- **+1** `POST /api/movies/{id}/poster/restore` (new, `01`).
- library filters add query-param coverage to existing `GET /api/library/movies` (not a new endpoint).

Target: **70** endpoints, each exercised twice (GPU + CPU). Reconcile the exact number against the live route table at run time and record it in `endpoint-test-summary.md`.
