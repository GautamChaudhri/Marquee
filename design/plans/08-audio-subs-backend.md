# 08 — Audio & Subtitles Management: Backend

> **For the implementing agent:** Read `design/plans/04-television-backend.md`
> §0 (ground rules) first — every rule there applies verbatim: `ruff check
> marquee tests` is the only lint gate (never `ruff format`), Alembic
> migrations are verified offline with `alembic upgrade head --sql` and NEVER
> applied to the live PostgreSQL DB (the operator applies them), the
> pre-existing failing-test baseline must not grow, commit per phase with
> short lowercase imperative messages. All decisions below are final — do not
> redesign. Where a step says **verify in code**, read the referenced module
> before writing; do not trust this doc over the code.

**Goal:** (1) TV audio/subtitle coverage: capture per-episode audio/subtitle
language truth from Sonarr at sync time, add a pure rollup engine
(episode → season → show, compliance × uniformity, mirroring
`marquee/core/hdr_rollups.py`), deep probe scans on demand + nightly. (2) A
landing summary API for the new `/audio-subs` landing page. (3) **Fix the
Subgen integration** (the current client does not match Subgen's real API)
and add **embedded Subgen**: Marquee supervises a vendored Subgen child
process, with a hardware-aware Whisper model picker.

Prereq: plan 06 backend is merged (Episode already has `hdr_type_raw`,
`video_width`, `video_height`; sync already parses episodefile `mediaInfo`).

---

## 1. Locked decisions

| # | Decision |
|---|---|
| A1 | **Two-tier coverage truth.** Tier 1 (sync, free): Sonarr episodefile `mediaInfo.audioLanguages` + `mediaInfo.subtitles` → new `Episode.audio_languages_json` / `Episode.subtitle_languages_json` (normalized tag lists). Tier 2 (probe): the existing `SubtitleInventory` ffprobe scan, per media file, run on demand (show/season scope) or by the nightly job. Tier 2 always wins when present. Rollups never trigger probes. |
| A2 | Rollup engine is a **pure module** `marquee/core/audio_subs_rollups.py` — same shape/style as `hdr_rollups.py`. Specials (season 0) are excluded from show rollups but still reported per-season. Episode statuses: `ok`, `audio_gap`, `subtitle_gap`, `both_gap`, `unknown` (no tier-1 or tier-2 data). Uniformity: `uniform` / `uniform_by_season` / `mixed` over the status axis. |
| A3 | Preferred languages remain the global `SubtitleSettings` lists (shared + optional audio/sub split), persisted via the existing `data/subtitle_overrides.json` mechanism. `Series` gains the same two nullable override columns `preferred_audio_languages_json` / `preferred_subtitle_languages_json` that `Movie` already has (series-level only; applies to all seasons/episodes). |
| A4 | **Subgen client rewrite**: `/batch` is `POST` with **query params** `directory` (pipe-separated paths; single files are valid) and `forceLanguage` — not a JSON body. All existing Subgen touchpoints are updated to the real API (D-phase 4). |
| A5 | Two generation paths: **primary** = `/batch` (path-based, uses Subgen's internal dedup queue, `forceLanguage` selects the source audio language); **advanced** = `/asr` (Marquee extracts the exact audio stream with ffmpeg, uploads it, gets the SRT back synchronously; supports per-request `task=transcribe|translate` and exact stream-index selection). |
| A6 | **Embedded Subgen** (`SUBGEN_DEPLOYMENT=embedded`): a pinned upstream `subgen.py` vendored under `marquee/vendor/subgen/`, spawned and supervised the same way `WorkerSupervisor` runs the worker/scheduler, configured entirely from Marquee settings, stdout/stderr tailed for status. External mode (`SUBGEN_DEPLOYMENT=external` + `SUBGEN_URL`) keeps working; `disabled` = feature off. Existing installs with only `SUBGEN_URL` set must resolve to `external` (back-compat default logic). |
| A7 | **Model picker**: curated dropdown (§6 table) + a `custom` option accepting any HuggingFace CT2 repo id or absolute local path, passed verbatim as `WHISPER_MODEL` (faster-whisper accepts names, HF repo ids, and paths natively). Hardware-aware recommendation + auto-select on first enable; models whose int8 footprint exceeds the budget are marked `too_big`. Device dropdown: `cpu` + one entry per NVIDIA GPU (multi-GPU via `CUDA_VISIBLE_DEVICES` on the child env — Subgen itself has no device-index knob). |
| A8 | Generation jobs on the embedded deployment hold the `gpu` resource for their duration so Whisper never collides with the poster pipeline / NVENC on the shared card. **Verify in code** how `subtitle_generate` media jobs surface in the durable platform (`marquee/core/jobs/legacy_media.py`) and attach the reservation at that layer. External deployment: `network_external` as today. |
| A9 | Nightly deep scan: a scheduler-owned recurring job (`audio_subs_deep_scan`) gated on `AUDIO_SUBS_DEEP_SCAN_ENABLED` (default False) + `AUDIO_SUBS_DEEP_SCAN_HOUR` (default 3, local time), scanning inventories for media files with missing/stale tier-2 data (movies + TV), oldest-first, bounded by `AUDIO_SUBS_DEEP_SCAN_BATCH` (default 200 files/night). Mirror the existing scheduler patterns in `marquee/core/jobs/scheduler.py`. |
| A10 | Landing summary endpoint computes read-time in Python from DB rows (same style as `hdr_index`/`/api/hdr/summary`); no materialized aggregates. |
| A11 | Movie-side subtitle endpoints and the existing movie inventory flow are **behavior-unchanged** except for the Subgen client fix; snapshot-test the movie library list response shape before/after. |
| A12 | The "Test generation" probe uses Subgen's `POST /detect-language` with a bundled ~10 s public-domain English speech clip committed at `marquee/assets/subgen_probe.mp3` (≤ 200 KB). It returns the detected language — a real end-to-end inference proof. |
| A13 | Subgen's completion webhook (`POST /api/webhooks/subgen`, token-authed — exists but currently only logs) is wired to resolve waiting generation jobs early and trigger an inventory rescan of the produced file. Polling reconciliation stays as the fallback (webhook is an optimization, never the sole truth). |
| A14 | Output reconciliation implements Subgen's **real naming**: `{base}[.subgen][.model].{lang}.srt`, language code per `SUBTITLE_LANGUAGE_NAMING_TYPE` (upstream default `ISO_639_2_B`, e.g. `eng`). The `.subgen` filename marker sets `SubtitleTrack.is_generated=True` at scan time. |

---

## 2. Phase 0 — Schema

One Alembic migration (offline-verified only):

1. `episodes`: add `audio_languages_json` (JSON, nullable), `subtitle_languages_json` (JSON, nullable). NULL = unknown (pre-sync); `[]` = mediaInfo present but no languages reported.
2. `series`: add `preferred_audio_languages_json` (JSON, nullable), `preferred_subtitle_languages_json` (JSON, nullable) — copy the exact column style from `Movie` (**verify in code**: `marquee/models/movie.py`).
3. No new tables. `SubtitleInventory`/`SubtitleTrack` are already file-keyed and TV-ready (`MediaFile` rows for Sonarr episode files + `EpisodeMediaFile` associations already exist from sync).

## 3. Phase 1 — Sync capture (tier 1)

In `_sync_episodes` (`marquee/core/sync_service.py`), where plan 06 already
reads each episodefile's `mediaInfo` for HDR/resolution:

1. Parse `mediaInfo.audioLanguages` and `mediaInfo.subtitles`. **Verify the
   real payload shape on the box** — Sonarr v4 emits strings like
   `"eng / jpn"` or `"eng/jpn"`; split on `/`, strip, drop empties, then
   normalize each through `marquee.core.subtitles.languages.normalize()` and
   store the deduplicated ordered tag list.
2. Multi-episode files: every `Episode` row sharing the file gets the same
   lists (same pattern as the plan 06 HDR fan-out).
3. File removed → reset both columns to NULL (mirror how `hdr_type_raw` is
   cleared).
4. Movies: no change (movies get tier-2 truth from the existing inventory
   scan; the movie landing tiles read coverage from inventories, falling back
   to "unknown" where no inventory exists yet).

## 4. Phase 2 — Rollup engine (pure)

`marquee/core/audio_subs_rollups.py`, mirroring `hdr_rollups.py` exactly in
style (dataclass inputs, pure functions, no DB imports):

```python
@dataclass(frozen=True)
class EpisodeCoverage:
    episode_id: int
    season_number: int
    audio_languages: list[str] | None      # tier-1 or tier-2 (tier-2 wins)
    subtitle_languages: list[str] | None   # full-dialogue langs when tier-2
    tier: str                              # "none" | "synced" | "probed"

def episode_status(cov, preferred_audio, preferred_subs) -> str: ...
def season_rollup(episodes, preferred_audio, preferred_subs) -> dict: ...
def show_rollup(seasons) -> dict: ...
```

Rules (documented in the module docstring):
- `unknown` when `tier == "none"`; unknown episodes are excluded from the
  gap verdict but counted (`unknown_count`) — same convention as HDR.
- A language matches via `languages.same_language()` (never string equality).
- Tier-2 subtitle truth = the inventory coverage's `full_dialogue_languages`
  (forced-only tracks don't satisfy a preferred-subtitle requirement);
  tier-1 subtitle truth = the synced list as-is (best effort, no
  full-dialogue distinction — this asymmetry is intentional and documented).
- Season rollup: status counts, missing-language union, **dub coverage**
  (`audio_ok_count / counted`), uniformity over episode statuses.
- Show rollup: worst-status verdict (`ok` / `gaps` / `none_met`),
  uniformity (`uniform` / `uniform_by_season` / `mixed`), specials excluded.
- Also compute per-scope `forced_coverage` and `sdh_coverage` counts from
  tier-2 data when present (`forced_only_languages` / `sdh_languages` in the
  coverage summaries) — surfaced on the landing page.

Resolve effective preferred languages per series: series override columns →
global settings (reuse `effective_preferred_languages()`).

## 5. Phase 3 — Deep scan (tier 2) + nightly job

1. Extend the existing library-scan flow (**verify in code**: `POST
   /api/subtitles/scan-library` in `marquee/api/routes/subtitles.py` and
   whatever job/manager it drives) with a scope: `{"scope": "movies" |
   "tv" | "series", "series_id": ..., "season_number": ...}`. TV scope
   resolves media files via `EpisodeMediaFile`, honoring the eligibility
   predicates in `marquee/core/tv_queries.py`.
2. Nightly `audio_subs_deep_scan` per A9: pick files with no inventory or a
   stale `file_signature` (**verify in code** how staleness is checked in
   the scan service), oldest `scanned_at` first, cap at
   `AUDIO_SUBS_DEEP_SCAN_BATCH`.
3. New settings on `SubtitleSettings` (persisted through the existing
   overrides mechanism): `AUDIO_SUBS_DEEP_SCAN_ENABLED` (False),
   `AUDIO_SUBS_DEEP_SCAN_HOUR` (3), `AUDIO_SUBS_DEEP_SCAN_BATCH` (200).

## 6. Phase 4 — Subgen: corrected client, both paths, embedded mode

### 6.1 Client fix (all deployments)

Rewrite `SubgenPathGenerator.submit()` (`marquee/core/subtitles/generators/subgen.py`):

- `POST {SUBGEN_URL}/batch` with **query params**: `directory` = the remote
  path (pipe-join for multi-file submits), `forceLanguage` = the language
  hint if given. No JSON body.
- Multi-file submits (season/show generation) pipe-join explicit file paths
  from the DB — never submit a folder (folders would sweep extras/samples).
- Reconciliation: replace `expected_output_srt()` with the real naming rule
  (A14). Add settings mirroring the naming knobs so external deployments can
  be described: `SUBGEN_NAMING_TYPE` (default `ISO_639_2_B`),
  `SUBGEN_NAME_INCLUDES_SUBGEN` (True), `SUBGEN_NAME_INCLUDES_MODEL` (False).
  Keep a stem-glob fallback, but log when the exact-name prediction missed.
- Health: parse `/status`'s version string (`"Subgen X, stable-ts Y,
  faster-whisper Z (Docker|Standalone)"`) into structured fields. Delete the
  `"cpu" in version` device guess in `generation.list_generators()` — device
  and model come from embedded config (or from the external-descriptor
  settings), never from string sniffing.
- Document (module docstring) the two upstream gotchas: `SKIP_STARTUP_SCAN=
  True` on the Subgen side makes `/batch` a silent no-op, and Subgen's
  `SKIP_IF_*` envs can silently drop submissions. Pre-check Marquee-side:
  before submitting, if the target already has subtitles in the requested
  language, surface "already covered — Subgen would likely skip this" in the
  job result instead of a blind timeout.

### 6.2 `/asr` advanced path

New method on the generator (used when the request asks for exact-track or
per-request translate):

1. Resolve the chosen audio stream (`stream_index` from the request, or the
   first stream matching the language hint via the inventory's
   `audio_streams_json`).
2. `ffmpeg -map 0:a:{n} -ac 1 -ar 16000` → temp WAV in the scratchpad-style
   temp dir the mutation pipeline already uses (**verify in code**:
   `SUBTITLE_MUTATION_TEMP_DIR` handling).
3. `POST /asr` multipart upload: `audio_file`, query `task=transcribe|
   translate`, `language`, `output=srt`, `encode=false`. The response streams
   the SRT body; Marquee writes the sidecar itself using **Marquee's**
   naming (`{base}.{tag}[.forced].srt` conventions — **verify in code** how
   external sidecars are named/paired in `marquee/core/subtitles/external.py`)
   and marks the track generated.
4. Translate reality (document in code + API): Whisper translates **to
   English only**; `large-v3-turbo` and the distil models cannot translate
   at all. Reject invalid combos with a 422 and a plain message.

### 6.3 Vendored embedded Subgen

1. Vendor upstream at a pinned commit: `marquee/vendor/subgen/subgen.py`,
   `language_code.py`, upstream `LICENSE` (MIT), and a `VERSION` file
   recording the commit SHA. Derive the runtime deps from the upstream
   `requirements.txt` **at that same commit** into a new pyproject extras
   group `subgen` (heavy: faster-whisper/ctranslate2, stable-ts; keep out of
   `dev`). Never enable upstream self-update (we don't vendor `launcher.py`).
2. `marquee/core/subtitles/embedded_subgen.py`: builds the child env from
   settings and returns the spawn spec. Env it must set:
   `WHISPER_MODEL`, `TRANSCRIBE_DEVICE` (`cuda`|`cpu`), `COMPUTE_TYPE`,
   `WHISPER_THREADS`, `CONCURRENT_TRANSCRIPTIONS`, `CLEAR_VRAM_ON_COMPLETE=
   True`, `MODEL_PATH` (default `data/subgen/models`), `WEBHOOK_PORT`
   (=`SUBGEN_EMBEDDED_PORT`, default 9000), `TRANSCRIBE_OR_TRANSLATE`,
   `WEBHOOK_URL_COMPLETED=http://127.0.0.1:{marquee_port}/api/webhooks/subgen?token={SUBGEN_CALLBACK_TOKEN}`
   (auto-generate the token if unset), `SKIP_STARTUP_SCAN=False`, `MONITOR=
   False`, `UPDATE=False`, `USE_PATH_MAPPING=False`, and
   `CUDA_VISIBLE_DEVICES={index}` when a specific GPU is chosen. Embedded
   mode forces `SUBGEN_URL=http://127.0.0.1:{port}` internally.
3. Supervision: extend `WorkerSupervisor` (`marquee/core/jobs/supervisor.py`,
   **verify in code** before touching — group-kill semantics must include the
   new child) to optionally spawn the Subgen child when
   `SUBGEN_DEPLOYMENT=embedded`. Restart-on-crash with backoff, same policy
   as the worker child.
4. Log tailing: capture the child's stdout/stderr into a bounded in-memory
   ring buffer (e.g. last 500 lines) + parse best-effort status lines:
   `Jobs: N processing, M queued` (queue depth), model-load and download
   lines, per-file start/complete lines. Expose via the status endpoint
   (§6.5). Parsing is best-effort by design — never fail on unmatched lines.
5. Settings (SubtitleSettings, overrides-persistable): `SUBGEN_DEPLOYMENT`
   (`external` when `SUBGEN_URL` set, else `disabled`), `SUBGEN_EMBEDDED_PORT`
   (9000), `SUBGEN_WHISPER_MODEL` (empty = use recommendation),
   `SUBGEN_TRANSCRIBE_DEVICE` (`auto`), `SUBGEN_GPU_INDEX` (None),
   `SUBGEN_COMPUTE_TYPE` (`auto`), `SUBGEN_CONCURRENT_TRANSCRIPTIONS` (1),
   `SUBGEN_WHISPER_THREADS` (0 = auto), `SUBGEN_MODEL_PATH`
   (`data/subgen/models`).

### 6.4 Model catalog + hardware recommendation

Pure module `marquee/core/subtitles/whisper_catalog.py`:

| id | params | ~VRAM fp16 | ~VRAM int8 | multilingual | can translate | notes |
|---|---|---|---|---|---|---|
| `tiny` | 39M | 0.5 GB | 0.3 GB | yes | yes | last resort |
| `base` | 74M | 0.7 GB | 0.4 GB | yes | yes | weak CPUs |
| `small` | 244M | 1.2 GB | 0.8 GB (~1.5 GB RAM on CPU) | yes | yes | CPU default |
| `medium` | 769M | 2.6 GB | 1.6 GB | yes | yes | legacy mid-size |
| `distil-large-v3` | 756M | 1.9 GB | 1.2 GB | **English only** | no | fastest for English libraries |
| `large-v3-turbo` | 809M | 1.9 GB | 1.3 GB | yes | **no** | best speed/accuracy; default GPU pick |
| `large-v3` | 1550M | 4.7 GB | 3.0 GB | yes | yes | max accuracy |
| `custom` | — | — | — | — | — | free-text HF CT2 repo id or local path |

(The 4.7/3.0 GB figures come from the faster-whisper README benchmarks;
the rest are parameter-scaled estimates — label them "approx" in API output.)

Recommendation function `recommend(hw, mode) -> Recommendation`:
- Hardware snapshot: enumerate NVIDIA GPUs via the pynvml plumbing already
  in `marquee/core/system_metrics.py` (**extend it** from index-0-only to
  `nvmlDeviceGetCount()` enumeration: index, name, vram_total, vram_free),
  plus `cpu_count` and total RAM (psutil, already used there).
- Device: the NVIDIA GPU with the most VRAM → `cuda` (+index); none → `cpu`.
  If lspci-style detection is unavailable just report "no CUDA GPU — using
  CPU; integrated GPUs are not usable (CTranslate2 supports CPU and CUDA
  only)" as the `device_note`.
- Budget = `vram_total − 1.5 GB` headroom (shared card: CLIP/DINO/OCR are
  process-resident; the `gpu` reservation serializes jobs but not residency).
- Per-model verdicts: `fits_fp16` / `fits_int8` (footprint ≤ budget) /
  `too_big`. On CPU: `too_big` when int8 RAM footprint > 50% of system RAM;
  `large-v3` additionally `impractical_cpu`. In translate mode,
  `large-v3-turbo` and `distil-large-v3` get `unavailable_translate`.
- Pick: GPU + transcribe → largest of (`large-v3-turbo` fits → it, else
  `distil-large-v3` if preferred subs are English-only, else `small`);
  GPU + translate → `large-v3` if fits else `medium` else `small`;
  CPU → `small` (`base` when < 4 cores). Compute type: `float16` when
  fits_fp16 on GPU else `int8`; `int8` on CPU. Return the reason string.
- Unit-test the verdict matrix with fake hardware snapshots (8 GB 3070,
  24 GB 4090, 4 GB laptop, CPU-only 8 GB RAM).

### 6.5 Subgen management API (`marquee/api/routes/subtitle_generators.py`)

| Route | Behavior |
|---|---|
| `GET /api/subtitle-generators` | Fixed-up version of today's list: real health, structured versions, deployment mode, configured model/device/compute, embedded child state (`running`/`starting`/`crashed`/`disabled`), parsed queue depth + last activity line, last webhook heartbeat. |
| `GET /api/subtitle-generators/subgen/hardware` | Hardware snapshot + per-model verdicts + the recommendation (§6.4). |
| `PUT /api/subtitle-generators/subgen/settings` | Validated update of the §6.3.5 settings via the overrides store; when embedded config changed → restart the child. 422 on invalid combos (translate + turbo/distil, custom empty, GPU index out of range). |
| `POST /api/subtitle-generators/subgen/restart` | Embedded only; supervised restart. |
| `GET /api/subtitle-generators/subgen/logs?tail=200` | Ring-buffer tail (embedded only). |
| `POST /api/subtitle-generators/subgen/test` | The A12 probe: uploads `marquee/assets/subgen_probe.mp3` to `/detect-language`, returns `{ok, detected_language, latency_ms}`. Works for external deployments too. |

Generation submit extensions: the existing
`POST /api/media-files/{id}/subtitle-generations` gains optional
`stream_index` + `task` (routing to `/asr` when either is set);
new `POST /api/audio-subs/tv/{series_id}/generate` (body:
`season_number?`, `language_hint?`, `output`, `advanced?: {stream policy}`)
creating a batch parent with per-file children — copy the dovi batch
parent/children pattern (**verify in code**: `create_batch` usage in
`marquee/api/routes/hdr.py`).

## 7. Phase 5 — Landing + TV API (`marquee/api/routes/audio_subs.py`, prefix `/api/audio-subs`)

Register literal routes before any parameterized siblings (same trap as
plan 06 — test it).

| Route | Payload (summary) |
|---|---|
| `GET /api/audio-subs/summary` | `movies`: {total, audio_ok/gap, subtitle_ok/gap, both_gap, unknown, forced_coverage, sdh_coverage, unknown_language_tracks, generated_tracks}; `tv`: same counts at episode level + show-status counts (ok/gaps/none_met/unknown) + uniformity counts + dub-coverage highlights (top N seasons lacking preferred audio); `preferred`: effective global lists; `policies`: active policy count + last audit summary (**verify in code**: what `subtitle_policies.py` exposes); `generator`: the `GET /api/subtitle-generators` card payload inlined; `deep_scan`: enabled/hour/last-run/pending-file count. |
| `GET /api/audio-subs/tv` | Show list, movie-library-compatible envelope (copy the plan 06 `/api/hdr/tv` envelope): per-show rollup, missing-language union, dub coverage, uniformity, episode fraction text, active job ids. Query filters: `status`, `uniformity`, `missing_language`, `q`, sort by title/status/coverage. Eligibility per `tv_queries.series_visible()`. |
| `GET /api/audio-subs/tv/{series_id}` | Header rollup + per-season rollups + episode matrix: `{episode_id, sXXeYY, title, audio_languages, subtitle_languages, forced/sdh flags (tier-2 only), tier, status, media_file_id}`. Include per-season and per-show active scan/generation job ids. |
| `POST /api/audio-subs/tv/{series_id}/deep-scan` | Body `{season_number?}` → enqueue tier-2 scans for the scope; returns job summary. |
| `POST /api/audio-subs/deep-scan` | Body `{scope: "movies"\|"tv"\|"all"}` — library-wide, rate-limited like other batch endpoints (`enforce_rate_limit`, DEBUG skips). |
| `PUT /api/audio-subs/preferences` | Global preferred lists (shared/audio/sub) → overrides store. (The landing edits move here; the old settings path, if any, keeps working — **verify in code** where the audio-subs page saves today and keep that route functional.) |
| `PUT /api/audio-subs/tv/{series_id}/preferences` | Series-level override columns (A3); null clears. |

NaN sanitation is not expected here (no float features), but coverage JSON
passes through — keep the plan 06 convention of sanitizing any float fields
echoed from stored JSON.

## 8. Tests

- Rollup engine: pure unit tests (status matrix, uniformity, specials
  exclusion, tier-2-wins, same_language matching, dub coverage).
- Whisper catalog: recommendation matrix per §6.4.
- Sync: episodefile fixture with `audioLanguages`/`subtitles` strings →
  normalized lists; multi-episode fan-out; clear-on-removal.
- Subgen client: `submit()` builds the correct query URL (respx/httpx mock);
  naming prediction for `ISO_639_2_B` + `.subgen` marker; `/asr` invalid
  combos → 422.
- Route-order test for `/api/audio-subs/*`; movie library list snapshot (A11).
- Embedded supervisor: spawn-spec env assembly unit test (no real child in CI).

## 9. Out of scope

Frontend (plan 09). Letterbox (plans 10/11). Native faster-whisper generator
(future; the `SubtitleGenerator` protocol already permits it). Non-English
translation targets (Whisper limitation). Per-episode policy bindings UI.

## 10. Operator notes (surface in the final report)

New migration to apply; library sync required before TV tiles populate
(tier-1 fields NULL until then). `pip install -e ".[subgen]"` needed for
embedded mode; first embedded start downloads the model to
`data/subgen/models` (size varies by model). The user's Subgen Docker
container stays retired — embedded replaces it on this box.
