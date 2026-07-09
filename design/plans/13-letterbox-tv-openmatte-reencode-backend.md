# 13 — Letterbox TV: Open Matte/Pillarbox, Reencode, Batch Confidence, Resets — Backend

> **For the implementing agent:** Read `design/plans/04-television-backend.md` §0
> (ground rules) first — all of it applies: `ruff check marquee tests` only (never
> `ruff format`), failing-test baseline must not grow (known env failure on this box:
> `test_effective_ocr_workers_caps_cuda_unless_gpu_forced`), commit per phase.
> Decisions are final — no redesigning, no questions. **Verify in code** markers mean:
> read that module before writing; re-locate every line anchor in this doc before
> editing (do not trust line numbers blindly). The letterbox engine has its own
> concurrency rules — see the module docstrings in `marquee/media/letterbox_manager.py`
> and never run `binaries.run` on the event loop (existing `to_thread` / `gated`
> patterns). **This plan needs NO Alembic migration** — if you think you need one,
> you have misread a decision.
>
> **Timeline doc:** this plan shares ONE timeline with its frontend sibling:
> `design/plans/13-letterbox-tv-openmatte-reencode-timeline.md`. FIRST check whether it
> exists. Exists → read it, verify its claims against `git log` and the working tree,
> RESUME where it leaves off. Missing → create it before the first commit. After every
> commit append: completed (with hash), in progress, exact next steps, deviations from
> the plan and why, pending operator actions. Terse and factual.

**Goal:** (1) Classify episodes whose container AR proves no letterbox is possible as
**open matte** (wider than 16:9) / **pillarbox** (narrower than 16:9) — read-time, no
migration — and exclude them from all analysis and batch operations, with an explicit
per-episode/per-season escape hatch. (2) Mixed seasons (OM/PB + 16:9) auto-escalate to
per-episode scanning instead of triage; SD files always scan. (3) Warm a single preview
frame for scanned-clear episodes at detect time. (4) Confidence-filtered scoped apply
(default high) + scoped apply/revert as durable jobs. (5) The full TV reencode flow
(single episode + season/show batch, artifact fan-out, bulk replace-ready). (6) A TV
letterbox dev reset endpoint.

Prereqs: plans 10–12 merged (they are).

---

## 1. Locked decisions

| # | Decision |
|---|---|
| D1 | **Read-time classification, no stored status, no migration.** The OM/PB bucket is derived at API-read/scan time from `Episode.video_width`/`video_height` via a new pure function; it applies only when the letterbox state has no detector truth (`state_has_detector_truth` false, or no state row at all). Detector truth always wins (D5). |
| D2 | **Two buckets, identical behavior.** `open_matte` = AR ≥ 1.80, `pillarbox` = AR < 1.70 — **HD only (height ≥ 720)**. Both are: skipped from ALL analysis (triage, exhaustive, **and force**) at show/library scope and by default at season scope; skipped by every batch apply/revert/reencode; excluded from dominant-AR and uniformity (not bar-bearing); verdict-neutral-clean (D7). AR band constants reuse `NATIVE_WIDE_AR`/`PILLARBOX_AR` from `marquee/media/probe.py` — do not redefine the numbers. |
| D3 | **SD rule.** height < 720 → always a scan candidate, never OM/PB: DVD-era storage dimensions (720×480, 720×576, 704×480…) are anamorphic and their pixel AR does not reflect display AR. The TV episode prefilter drops the height floor entirely (SD 16:9-ish, 4:3, and anamorphic files all get scanned; cropdetect measures storage pixels, which is exactly what a crop tag needs). The **movie** prefilter (`probe.prefilter_bucket`) keeps its 1080p floor and exact current behavior — implement the TV rules as new pure functions in `marquee/core/letterbox_prefilter.py`; movie call sites untouched. |
| D4 | **Escape hatch.** `TvDetectRequest` gains `include_open_matte: bool = False`, honored for **episode and season scopes only**. Show-scope detect (no `season_number`) and the library route never forward it (children are built with `include_open_matte=False` unconditionally). When set, OM/PB episodes in scope are scanned (combined with `force` from the frontend). |
| D5 | **Detector truth wins.** After an escape-hatch scan: bars found → `candidate` (normal workflow, badge gone); clean → `not_letterboxed` with the source-AR `aspect_label` (existing plan-12 R4 path). An OM/PB row is badged only while unscanned. |
| D6 | **Mixed-season auto-exhaustive.** In the default triage path of `detect_episode_batch_and_store`: if a season's episodes (the full ordered scope for that season, not just pending items) contain ≥ 1 OM/PB episode AND there are pending unscanned candidates, that season skips triage and scans all its pending groups individually. It never rescans rows with real verdicts and never rescans `sampled_clear` (those remain Exhaustive/Force territory, plan 12 R1). |
| D7 | **Rollups/verdicts.** `BUCKET_ORDER` gains `open_matte` and `pillarbox` (insert between `variable` and `ineligible`). Verdict rules in `_verdict_for_counts`: (new, first) non-empty `present ⊆ {open_matte, pillarbox, ineligible}` → `clean`; the existing unanalyzed check widens to `present ⊆ {unanalyzed, ineligible, open_matte, pillarbox}` → `unanalyzed`; `open_matte`/`pillarbox` are added to the clean/treated/needs_action subsets. `BAR_BEARING_BUCKETS` unchanged. Bucket delivery: `EpisodeLetterbox` gains an optional `bucket_override: str \| None = None` (wins in `__post_init__` when set); `_episode_letterbox_row` computes it (D1 guard included) and also sets the row's `aspect_label` to the source-AR label for OM/PB rows. |
| D8 | **Scanned-clear preview warm.** When a TV detect verdict is `not_letterboxed`, fire-and-forget warm ONE frame per linked episode: mode=`before`, crop 0/0, bright policy (`exact=False`), minute = first ok sample minute. New lightweight helper in `marquee/media/letterbox_preview.py` + a sibling of `_schedule_preview_warm` in the manager. Candidates keep the existing full warm; `sampled_clear` gets **no** warm (no detection ever decoded the file — the frontend triggers on-demand renders in the background instead). |
| D9 | **Confidence-filtered scoped apply.** `TvApplyRequest` gains `confidence_levels: list[str] \| None`. Server default (None) = `["high"]`. Allowed values `{"high","medium","low","variable","all"}`; a list containing `"all"` disables filtering; invalid values → 422. The filter applies to season/show scope only — `episode_id` scope stays unconditional. |
| D10 | **Scoped apply/revert become durable jobs.** New job types `letterbox_apply_tv_scope` and `letterbox_revert_tv_scope` (server-side handler registry, like `letterbox_detect_tv_scope`): payload `{series_id, season_number?, confidence_levels?}`, resources `{"media_write": 1}`, `subject_type="series"`, `subject_id=series_id` (so `_active_tv_job_ids` picks them up), per-file-group progress. `episode_id` scope stays inline (current behavior). New route `POST /tv/{series_id}/revert` with `{season_number?, episode_id?}`; the per-episode `/episodes/{id}/remove` route is unchanged. Revert targets only rows with applied crops; apply keeps its current predicates (status `candidate` + non-zero recommended crop) plus D9. Both skip OM/PB by construction (no candidate status). |
| D11 | **TV reencode — single episode.** `POST /tv/{series_id}/episodes/{episode_id}/reencode-plan` mirrors the movie plan flow: state must be `candidate` (reject `variable_unsafe` with the same 422 code) with a non-zero crop (body override allowed); resolve the shared file via the episode's active `MediaFile`; reuse `letterbox_reencode.build_plan` unchanged; MediaJob `request` carries `{series_id, episode_id, episode_ids (all episodes sharing the file), top, bottom, allow_cpu_fallback}`. Confirm stays `POST /api/media-jobs/{job_id}/confirm`; the letterbox confirm hook generalizes: when the confirmed job's media file maps to episodes, flip every linked `candidate` episode state → `tagged` (movie branch unchanged). |
| D12 | **Executor + artifact subject.** Artifact creation in `letterbox_reencode.py` stamps `media_type='episode'`, `episode_id` = lowest linked episode id, `movie_id=None` when the job's media file resolves to episodes. `replace_original` and `restore_original` fan their `LetterboxState` updates (status/provenance/crops — exact same field writes as today) out to **all** episodes linked to `artifact.media_file_id`, not a single row; movie path single-row as today. |
| D13 | **TV batch reencode.** `POST /tv/{series_id}/reencode` body `{season_number?: int, confidence_levels?: list[str] (D9 semantics, default ["high"]), settings: BatchReencodeSettings}` — season or whole-show scope only (cross-show batches do not exist). Eligible = status `candidate`, eligible file, non-zero crop, confidence in levels; dedupe by media file (one plan per physical file); per file: plan + auto-confirm (movie `/batch/reencode` pattern); response `{job_ids, count, skipped: [{episode_id, code, reason, sXXeYY}]}`. Wrap in a parent Job `letterbox_reencode_tv_batch` (`status="waiting_external"`, `subject_type="series"`, `subject_id=series_id`) with the per-file bridge Jobs as its children so the show page gets one progress bar (see Phase 5 for the bridge wiring). |
| D14 | **Per-artifact review stays; bulk replace-ready.** `POST /tv/{series_id}/reencode-artifacts/replace-ready` body `{season_number?: int}` — calls `letterbox_reencode.replace_original` for every artifact with status `candidate_ready` whose media file maps to the scope; returns `{replaced: n, failed: [{artifact_id, code, reason}]}`; runs inline with per-artifact error isolation. `GET /reencode-artifacts` gains `series_id` + `season_number` filters, and `artifact_to_dict` (or the route) adds display fields for TV rows: `series_id`, `series_title`, `episode_code` ("S01E03"). |
| D15 | **Dev reset.** `POST /api/letterbox/tv/dev/reset-all`: bulk-delete ALL `LetterboxState` and `LetterboxEvent` rows with `media_type='episode'`, purge all `episode-*` preview files + their bright-minute cache entries (new `purge_all_episode_previews()` in `letterbox_preview.py`). Returns `{states_deleted, events_deleted, previews_purged}`. Does NOT touch mkv tags on files, reencode artifacts, or movie rows. No UI. |
| D16 | **Movie regression guards.** Movie routes and behavior stay identical: the existing movie snapshot tests must keep passing untouched; the movie prefilter and `probe.prefilter_bucket` are not modified; every new query carries a `media_type` guard; literal routes register before parameterized siblings (`/tv/dev/reset-all` and `/tv/detect` before `/tv/{series_id}`), extend the route-order test. |

## 2. Phase 1 — Classification + rollups

1. `marquee/core/letterbox_prefilter.py`:
   - `tv_dimension_class(width: int | None, height: int | None) -> str | None` — pure:
     `None` if dims missing/invalid or `height < 720`; `"open_matte"` if AR ≥
     `probe.NATIVE_WIDE_AR`; `"pillarbox"` if AR < `probe.PILLARBOX_AR`; else `None`.
   - Rework `prefilter_category_episode` to TV rules (D3): missing dims →
     `unknown_resolution`; `height < 720` → `("candidate", …reason "sd_assumed_candidate")`;
     else 16:9 band → candidate, wider → skip/`native_wide`, narrower →
     skip/`pillarbox_or_4_3`. Build the explanation dict in the same shape as
     `prefilter_category_for_dimensions` (**verify in code**: the dict keys, and that
     nothing movie-side calls `prefilter_category_episode`).
2. `marquee/core/letterbox_rollups.py`: `BUCKET_ORDER` + `EpisodeLetterbox.bucket_override`
   + `_verdict_for_counts` per D7. `episode_bucket()` itself is untouched.
3. `marquee/api/routes/letterbox.py` `_episode_letterbox_row` (~line 473): compute
   `dimension_class = tv_dimension_class(episode.video_width, episode.video_height)`;
   `bucket_override = dimension_class if not _state_has_detector_truth(state) else None`
   (**verify in code**: `state_has_detector_truth` import name in this module); pass to
   `EpisodeLetterbox`; matrix row `bucket` comes from the item as today; when the override
   is active, set the row/item `aspect_label` to
   `letterbox_detect.aspect_label(episode.video_width, episode.video_height)`.
4. `/summary` TV section: the funnel/verdict builders consume `EpisodeLetterbox` items
   (**verify in code**: `letterbox.py` ~830-930) — confirm the new buckets flow into
   `tv` verdict counts without touching movie code; OM/PB must NOT enter `tv_aspects`
   (they are not bar-bearing — the existing filter already guarantees it; add a test).

**Tests** (`tests/test_letterbox_rollups.py`, `tests/test_letterbox_tv_api.py`):
classifier boundaries (AR 1.699/1.70/1.79/1.80 at 1080p; heights 719/720; 720×480 and
720×576 → None + category candidate; missing dims → None + unknown); verdict table:
`{open_matte}`→clean, `{open_matte, pillarbox}`→clean, `{open_matte, unanalyzed}`→unanalyzed,
`{open_matte, clear}`→clean, `{open_matte, candidate}`→needs_action; OM/PB never affect
dominant label/uniformity; API: matrix row for a 1920×800 episode without state →
`bucket="open_matte"`, `aspect_label="2.40:1"`; same episode with a `candidate` state →
`bucket="candidate"` (truth wins); summary excludes OM/PB from `tv.aspect_distribution`.

## 3. Phase 2 — Scan semantics (manager + detect routes)

1. `marquee/media/letterbox_manager.py` `detect_episode_batch_and_store` gains
   `include_open_matte: bool = False`. In the per-episode loop (current skip block at
   ~lines 913-924, **verify in code**):
   - `if tv_dimension_class(episode.video_width, episode.video_height) and not include_open_matte: continue`
     — **before** the force check, i.e. OM/PB are skipped *even when force is set*,
     with a short comment saying exactly that.
   - The existing detector-truth skip (plan 12 R1) and prefilter-category check stay;
     note the category check now passes SD episodes (D3).
2. Mixed-season auto-exhaustive (D6): while iterating `ordered_episodes`, record per
   season whether any episode has a non-None `tv_dimension_class`. In the triage branch
   (after `seasons` is built, ~line 964), seasons flagged mixed skip triage:
   `touched_states.extend(await scan_groups(season_groups))` and `continue`.
3. `marquee/api/routes/letterbox.py`: `TvDetectRequest` gains
   `include_open_matte: bool = False`; `_tv_scope_child` gains and forwards it in the
   payload. Forwarding rules (D4): episode-scope child → `body.include_open_matte`;
   season-scope child → `body.include_open_matte`; show-scope children (one per season)
   → `False` always; `TvLibraryDetectRequest` does NOT gain the field.
4. `marquee/core/jobs/builtin_handlers.py` `letterbox_detect_tv_scope` handler
   (**verify in code**: ~line 338): pass `include_open_matte` from the payload through
   to the manager (default False for old payloads).

**Tests** (`tests/test_letterbox.py`, reuse the `_seed_tv_detect_scope` + monkeypatch
pattern near the triage/escalation/force tests): mixed season (one 1920×800 + rest
1920×1080 unscanned) default-detect scans every candidate individually and creates zero
`sampled_clear` rows; all-16:9 season still triages; OM/PB episode is not scanned under
`exhaustive=True, force=True` without the include flag, and IS scanned with
`include_open_matte=True` at season and episode scope; SD (720×480) episode is scanned;
`tests/test_letterbox_tv_api.py`: child payload carries `include_open_matte` per the
forwarding rules (episode/season true when requested; show/library children always false).

## 4. Phase 3 — Scanned-clear preview warm

1. `marquee/media/letterbox_preview.py`: `warm_clear_preview(source, *, subject_key,
   minute, candidate_minutes)` — purge the subject's previews, then one
   `generate_preview(mode="before", crop 0/0, exact=False, force=True)`.
2. `marquee/media/letterbox_manager.py`: sibling of `_schedule_preview_warm`
   (**verify in code**: its threading pattern at ~line 61) scheduling the above; in
   `detect_episode_group_and_store`'s post-commit loop (~768-784), extend the existing
   `if state.status == "candidate"` warm with an `elif state.status == "not_letterboxed"`
   branch using the first ok sample minute (fallback 5).

**Tests**: unit — a `not_letterboxed` group result schedules exactly one warm per linked
episode with mode before/minute of first ok sample (monkeypatch the scheduler hook, as
the existing candidate-warm tests do; **verify in code**: how those tests stub it).

## 5. Phase 4 — Scoped apply/revert jobs + confidence filter

1. `TvApplyRequest` gains `confidence_levels` (D9); validation helper shared with D13.
2. Extract the current inline scope-apply body (route ~1540-1589) into a manager/service
   function that also reports progress; register handlers `letterbox_apply_tv_scope` and
   `letterbox_revert_tv_scope` in `marquee/core/jobs/builtin_handlers.py` (**verify in
   code**: registration + progress-update pattern of existing handlers; per-file-group
   `{done, total}` progress so the frontend bar moves).
3. Route behavior: `/tv/{series_id}/apply` — `episode_id` scope unchanged (inline);
   season/show scope now `job_manager.create(...)` → 202 + `job_summary` (job payload
   carries the validated confidence levels). New route `POST /tv/{series_id}/revert`
   `{season_number?, episode_id?}` — episode inline (reuse `remove_episode_group` via the
   group scoper), scoped → job. Revert scope = rows with non-null applied crops; per-file
   dedupe; OM/PB untouched by construction.
4. Rate limiting: none needed (mutating jobs are idempotent per state; mirror existing
   apply routes which are unlimited).

**Tests** (`tests/test_letterbox_tv_api.py` + `tests/test_letterbox.py`): default apply
skips a `medium` candidate and applies the `high` one; `confidence_levels=["all"]` applies
both; `["bogus"]` → 422; episode scope applies a `low` candidate; scoped apply/revert
return a job summary whose payload has the levels; revert job untags only applied rows
(fan-out across a shared file); handler-level test driving the new handlers end-to-end
against SQLite fixtures with a monkeypatched `letterbox_service`.

## 6. Phase 5 — TV reencode

1. **Plan route** (D11): generalize `_create_reencode_plan_job` (route module ~1723) —
   split subject resolution (movie vs episode-group) from the shared core
   (build_plan → supersede → `media_job_manager.create_job`). Episode variant resolves
   the group via the episode's active media file (reuse `_tv_scope_rows` +
   `_scope_episode_group`), state checks per D11, request payload per D11.
   **Verify in code**: `ensure_media_file_for_movie` has an episode-side equivalent or
   the media file id comes straight from the scope rows.
2. **Confirm hook** (route ~1846-1856): after queueing, when
   `MediaFile.movie_id is None`, resolve linked episodes via `EpisodeMediaFile` and flip
   every `candidate` episode state → `tagged` (movie branch byte-identical).
3. **Executor/artifact** (D12): `marquee/core/letterbox_reencode.py` artifact creation
   (~1016-1018): derive subject from the resolved media file — episodes linked → media_type
   `'episode'` + lowest `episode_id`; else movie as today. **Verify in code**:
   `ResolvedMediaFile` fields and what `resolved.movie_id` is for episode files.
4. **Fan-out** (D12): `replace_original` (~1421-1456) and `restore_original` (~1488-1506):
   when `artifact.media_type == 'episode'`, select ALL states for episodes joined through
   `EpisodeMediaFile` on `artifact.media_file_id` and apply the same field updates to each.
5. **Batch route** (D13): follow `/batch/reencode` (~1878-1912): iterate deduped file
   groups, per group plan+`_confirm_media_job_plan`, HTTPException → skipped entry.
   Parent Job: create `letterbox_reencode_tv_batch` with `status="waiting_external"`
   (the `apply_batch` pattern ~1626-1683); attach each file's **bridge Job** as a child.
   **Verify in code**: where the generic bridge Job for a media job is created
   (`_generic_media_job_bridge` looks it up by `payload.media_job_id`; find the creation
   site — likely `media_job_manager`/`legacy_media` — and thread `parent_id`/
   `correlation_id` through it; if creation happens before the parent exists, set
   `parent_id` on the bridge rows after creating the parent). The parent must complete
   via the normal children-completion path — do NOT write a polling handler (single
   durable worker: a waiting handler would deadlock its own children).
6. **Bulk replace-ready** (D14) + artifact list filters/labels (D14). Series/season →
   media-file scoping goes through `EpisodeMediaFile` joins; keep the movie filter
   behavior identical.

**Tests** (`tests/test_letterbox_reencode.py`, `tests/test_letterbox_tv_api.py`):
episode plan route 201 + request payload shape (monkeypatch `build_plan` — the existing
movie plan tests show the pattern, **verify in code**); plan rejects variable_unsafe /
zero-crop / missing file with the movie's error codes; confirm flips all shared-file
episode candidates → tagged; artifact stamped `media_type='episode'` + lowest episode id;
`replace_original` fans provenance to every linked episode state and `restore_original`
clears them all; batch dedupes a two-episode shared file into one plan, filters by
confidence, returns skipped with codes, parents the bridge jobs (children_total == plans);
replace-ready replaces only `candidate_ready` in scope and isolates per-artifact failures;
artifact list filters by series/season and emits `series_title`/`episode_code`.

## 7. Phase 6 — Dev reset

`POST /api/letterbox/tv/dev/reset-all` + `purge_all_episode_previews()` per D15.
Register the literal route before `/tv/{series_id}` (extend the route-order test).

**Tests**: seeds movie + episode states/events → endpoint deletes only episode rows,
returns counts, movie rows untouched; preview purge unlinks `episode-*` files only
(tmp-dir fixture) and clears `episode-` bright-minute cache keys.

## 8. API contract summary (frontend reads this; route code is source of truth)

| Route | Change |
|---|---|
| `GET /api/letterbox/tv` | Rollup `bucket_counts` gain `open_matte`, `pillarbox`. |
| `GET /api/letterbox/tv/{series_id}` | Same + matrix rows may have `bucket: "open_matte"\|"pillarbox"` with source-AR `aspect_label`. |
| `POST /api/letterbox/tv/{series_id}/detect` | Body + `include_open_matte?: bool` (episode/season scopes). |
| `POST /api/letterbox/tv/{series_id}/apply` | Body + `confidence_levels?: string[]` (default `["high"]`, `"all"` disables). Episode scope → inline result (unchanged shape); season/show scope → **202 job summary**. |
| `POST /api/letterbox/tv/{series_id}/revert` | NEW. `{season_number?, episode_id?}` — episode inline `{removed, path, episode_ids}`; scoped → 202 job summary. |
| `POST /api/letterbox/tv/{series_id}/episodes/{episode_id}/reencode-plan` | NEW. Movie plan-response shape + `job_id`/`status`/`expires_at`. |
| `POST /api/letterbox/tv/{series_id}/reencode` | NEW. `{season_number?, confidence_levels?, settings}` → `{job_ids, count, skipped[], parent_job_id}`. |
| `POST /api/letterbox/tv/{series_id}/reencode-artifacts/replace-ready` | NEW. `{season_number?}` → `{replaced, failed[]}`. |
| `GET /api/letterbox/reencode-artifacts` | + `series_id`, `season_number` query params; TV rows carry `series_id`, `series_title`, `episode_code`. |
| `POST /api/letterbox/tv/dev/reset-all` | NEW. → `{states_deleted, events_deleted, previews_purged}`. |
| Job types | NEW: `letterbox_apply_tv_scope`, `letterbox_revert_tv_scope`, `letterbox_reencode_tv_batch` (parent). Media-job op `letterbox_reencode` reused for episodes. |
| Movie routes | Unchanged (D16). |

## 9. Out of scope

Frontend (the 13-frontend doc). Movie-side behavior changes of any kind. Sync-time
episode prefilter writes. Auto-scan scheduling for TV letterbox. Cross-show reencode
batches. Migrations (none). Artifact-page/global-review redesign beyond the list filters.

## 10. Operator notes

- No migration. New job types appear in the jobs UI once the frontend registers labels.
- `POST /api/letterbox/tv/dev/reset-all` wipes TV letterbox rows/previews only — mkv
  tags on files and reencode artifacts survive; a rescan of an already-reencoded file
  simply comes back clear (its provenance rows are gone — acceptable for a dev reset).
- First detect after this plan on a library with mixed seasons scans more episodes than
  triage used to (that is the point); OM/PB seasons drop out entirely, usually a net win.
- SD content now scans; a pure-4:3 SD season costs ~3 sampled episodes via triage.
- TV reencodes write candidate files next to originals like movies — watch disk space;
  artifacts await review unless bulk replace-ready is used.
