# 10 — Letterbox: TV Support + Landing: Backend

> **For the implementing agent:** Read `design/plans/04-television-backend.md`
> §0 (ground rules) first — all of it applies: `ruff check marquee tests`
> only, Alembic verified offline only (operator applies), failing-test
> baseline must not grow, commit per phase. Decisions are final. **Verify in
> code** markers mean: read that module before writing. The letterbox
> engine has its own concurrency rules — see the module docstrings in
> `marquee/media/letterbox_manager.py` and never run `binaries.run` on the
> event loop (existing `to_thread` patterns).

**Goal:** (1) Extend letterbox detection/state to TV episodes with a
cost-tiered analysis funnel (resolution prefilter → season-sample triage →
quick pass → thorough escalation, runtime-proportional timestamps).
(2) Show/season rollups + a season × episode matrix API mirroring the HDR
pattern. (3) A `/api/letterbox/summary` landing payload for movies + TV.
(4) **Reencode provenance**: a durable "was letterboxed, de-letterboxed by
reencode" record with signature-guarded clearing when a genuinely new file
replaces it. TV reencode itself is **deferred** (like episode DoVi
conversion) until the movie reencode path is validated on real files.

Prereqs: plans 04 + 06 merged (Episode has `video_width`/`video_height`;
`EpisodeMediaFile` associations exist; `tv_queries` predicates exist).

---

## 1. Locked decisions

| # | Decision |
|---|---|
| L1 | **Subject pattern** on `LetterboxState` + `LetterboxEvent`: add `media_type` (String(10), server_default `'movie'`), make `movie_id` nullable, add nullable `episode_id` FK (unique on `LetterboxState`), check constraint `(media_type='movie' AND movie_id IS NOT NULL AND episode_id IS NULL) OR (media_type='episode' AND episode_id IS NOT NULL AND movie_id IS NULL)` — copy the exact style plan 06 used for `DoviState` (**verify in code**: `marquee/models/dovi.py`). Every existing movie-scoped letterbox query gains a `media_type='movie'` guard — this is the top regression risk; grep every `LetterboxState`/`LetterboxEvent` query. |
| L2 | **State is per-episode; work is per-file.** Detection and tag apply/remove run once per physical `MediaFile` and fan results out to every `Episode` row sharing it (multi-episode files). The apply path resolves the file via `EpisodeMediaFile` and Sonarr path translation (`translate_sonarr_path`), reusing `LetterboxService` with a generalized subject (movie or episode-set). |
| L3 | **Analysis funnel for TV**, cheapest first: (1) resolution prefilter per episode from `Episode.video_width/height` (reuse `letterbox_prefilter.py` categories); (2) **season-sample triage**: quick-scan 2–3 representative episodes per season (first/middle/last downloaded, distinct media files); all clean → every unscanned episode in the season gets status `sampled_clear` (no per-episode scan); any bar found → the whole season escalates to per-episode scanning; (3) per-episode **quick pass** = `LETTERBOX_TV_QUICK_WINDOWS` (default 3) sample windows; any bar → immediate **thorough pass** = `LETTERBOX_TV_THOROUGH_WINDOWS` (default 8). The existing early-stop (`LETTERBOX_EARLY_STOP_WINDOWS`) keeps working within a pass. |
| L4 | **Runtime-proportional timestamps** for TV: new pure `sample_offsets_proportional(duration_s, count, head_pct, tail_pct)` in `letterbox_detect.py` — usable span is `[LETTERBOX_TV_HEAD_SKIP_PCT, 100−LETTERBOX_TV_TAIL_SKIP_PCT]` percent of runtime (defaults 12 / 12), `count` evenly spaced points within it. Works for any episode length (20/40/60+ min). When duration is unknown, ffprobe it (the detect path already probes — **verify in code**); the legacy fixed `LETTERBOX_TV_SAMPLES` config is removed. Movie scheduling (`sample_minutes`) is untouched. |
| L5 | **Exhaustive mode**: batch-detect endpoints accept `exhaustive: bool` (default False). Exhaustive skips triage (2) and scans every downloaded episode with the quick→thorough flow. Offered per-show and library-wide. |
| L6 | **Re-run at any scope**: detect endpoints accept episode / season / show scope and a `force: bool` that ignores `reviewed`/existing verdicts (so a user who spots one odd episode can rescan it, its season, or the show). Re-running a season in force mode uses per-episode scanning, not triage. |
| L7 | **New statuses** (TV + movies where noted): `sampled_clear` (season triage verdict, episode not individually scanned) and `reencoded` (terminal; L8). The full status vocabulary and which transitions are legal must be documented in the `LetterboxState` docstring. `sampled_clear` episodes are re-scannable at any time (L6) and count as "clear" in rollups but are visually distinct. |
| L8 | **Reencode provenance** on `LetterboxState`: `resolved_by` (String(16), e.g. `'reencode'`), `resolved_at`, `original_crop_top`, `original_crop_bottom`, `original_aspect_label`. Stamped (with status → `reencoded`) at the moment `replace-original` succeeds (**verify in code**: the replace flow in `letterbox_reencode.py` / the artifact routes). Migration backfills provenance for movies from existing `LetterboxReencodeArtifact` rows with status `replaced`/`kept` (crop + aspect from the artifact row; `resolved_at` from its `updated_at`). |
| L9 | **Signature-guarded clearing on file replacement**: in sync, when the active `MediaFile` for a movie/episode changes (new `source_key` → old row deactivated — this hook point already exists, **verify in code**: `_upsert_movie_media_file` / `_upsert_episode_media_files`), and the subject's `LetterboxState.resolved_by == 'reencode'`: compute the new file's cheap signature (reuse the `file_signature` scheme from the subtitle inventory — path+size+mtime+edge-block hash, **verify in code**: where it's implemented) and compare against the newest artifact's `candidate_signature` for that subject. Match → our own reencode output re-imported under a new file id: keep provenance, update the artifact's `media_file_id`. Mismatch → genuinely new file: clear provenance fields, reset status to `prefilter_candidate`, clear applied/recommended crops, log a `LetterboxEvent(action='reset', source='sync', detail=…)`. For subjects **without** reencode provenance, a file replacement also resets stale detection state (recommended crop, samples, status → `prefilter_candidate`) — a new file's bars are unknown. |
| L10 | `LetterboxReencodeArtifact` gains the same subject generalization (`media_type` default `'movie'`, nullable `movie_id`, nullable `episode_id`) so the schema is TV-ready, but the TV reencode planner/executor is **out of scope** (deferred until movie reencode is validated — see `design/timeline.md` "Needs validation"). |
| L11 | Rollups are a pure module `marquee/core/letterbox_rollups.py` mirroring `hdr_rollups.py`. Episode buckets: `clear` (verified not_letterboxed) · `sampled_clear` · `candidate` (letterboxed, untreated) · `tagged` · `reencoded` · `variable` · `ineligible` · `error` · `unanalyzed`. Season/show rollups: bucket counts, dominant aspect label, compliance verdict (`clean` / `treated` / `needs_action` / `mixed` / `unanalyzed`), uniformity (`uniform` / `uniform_by_season` / `mixed`), specials excluded from show verdicts (consistent with HDR/audio-subs). |
| L12 | Movie letterbox endpoints stay behavior-identical (except the shared L8/L9 improvements); snapshot-test `/api/letterbox/status` and `/api/letterbox/candidates` responses before/after. Literal new routes (`/summary`, `/tv`, …) register before any parameterized siblings — add the route-order test. |
| L13 | TV detection jobs run through the **existing letterbox job manager** (`marquee/media/letterbox_manager.py`), not a new system — same worker-pool, NVDEC, and DB-throttling rules. Batch parents report per-episode progress the same way movie batches report per-movie progress (**verify in code** the progress/events contract the frontend already consumes at `/api/letterbox/jobs/{job_id}/events`). |

## 2. Phase 0 — Schema

One migration: L1 columns/constraints on `letterbox_state` + `letterbox_events`;
L7/L8 status + provenance columns; L10 artifact generalization; backfill
`media_type='movie'` everywhere; L8 provenance backfill from artifacts.
Offline-verify only.

## 3. Phase 1 — Detection engine changes (`marquee/media/letterbox_detect.py` + manager)

1. `sample_offsets_proportional()` (L4) + config: `LETTERBOX_TV_QUICK_WINDOWS=3`,
   `LETTERBOX_TV_THOROUGH_WINDOWS=8`, `LETTERBOX_TV_HEAD_SKIP_PCT=12`,
   `LETTERBOX_TV_TAIL_SKIP_PCT=12`, `LETTERBOX_TV_SEASON_SAMPLE_EPISODES=3`.
   Remove `LETTERBOX_TV_SAMPLES`.
2. Quick→thorough escalation wrapper for a single file: run quick pass;
   if any sample shows a real bar (existing `LETTERBOX_MIN_BAR_PX`
   semantics), rerun thorough and use only the thorough result for the
   verdict (confidence math unchanged — it just sees more samples).
3. Season triage orchestration in the manager: representative-episode
   selection (first/middle/last *downloaded* episodes, distinct media
   files, skip specials for triage — specials are always scanned
   individually when requested), fan-out of `sampled_clear`, escalation.
4. Per-file dedupe: a batch over a season groups episodes by
   `media_file_id`; one detection per file, results written to all its
   episode states.
5. Episode prefilter: extend `letterbox_prefilter.py` with an
   episode-driven variant using `Episode.video_width/height`; run it during
   the batch (not sync) exactly like the movie flow does today
   (**verify in code**: when/where `refresh_letterbox_prefilter_for_movie`
   is called).

## 4. Phase 2 — Service + sync

1. Generalize `LetterboxService` subject handling (movie | episode-set via
   shared file) — path resolution for Sonarr episodes goes through
   `safe_translate_and_validate(source="sonarr")`; eligibility rules
   unchanged (MKV, writable, video track).
2. Bulk season apply/remove: apply the recommended crop to every `tagged`-
   eligible candidate episode in a season in one endpoint call (per-file
   dedupe, per-file locks as today).
3. Sync hook per L9 (movies **and** episodes) + heal: extend
   `letterbox_heal.py` to TV (re-apply tags after upgrades when state says
   applied but file lacks tags — same drift rules; **verify in code** the
   movie heal flow first).

## 5. Phase 3 — Rollups + API (`marquee/api/routes/letterbox.py`)

| Route | Payload / behavior |
|---|---|
| `GET /api/letterbox/summary` | `movies` + `tv` sections: workflow funnel counts (map existing statuses to the UI tabs: Candidates / Staging(tagged-pending-confirm) / Preview / Processed — **verify in code** how the frontend tab keys map to statuses today and mirror that mapping server-side); verdict breakdown (clear / sampled_clear / letterboxed-untreated / tagged / reencoded / variable / ineligible / unanalyzed); aspect-ratio distribution from `aspect_label`; reencode stats (count, `space_reclaimed_bytes` = Σ(original−candidate) over `replaced` artifacts, artifacts awaiting decision, saved originals on disk); analyzed-coverage percent per library. |
| `GET /api/letterbox/tv` | Show list, HDR-style envelope: rollup verdict, uniformity, bucket counts + fraction, dominant AR, active job ids. Filters: `verdict`, `uniformity`, `has_candidates`, `q`; query-string driven. Eligibility via `tv_queries`. |
| `GET /api/letterbox/tv/{series_id}` | Header rollup + season rollups + episode matrix `{episode_id, sXXeYY, title, bucket, status, confidence, aspect_label, recommended/applied crop, resolution, media_file_id, eligible, reviewed}` + active jobs. |
| `POST /api/letterbox/tv/{series_id}/detect` | Body `{season_number?, episode_id?, exhaustive?: bool, force?: bool}` (L5/L6) → letterbox-manager batch job. |
| `POST /api/letterbox/tv/detect` | Library-wide TV batch `{exhaustive?: bool}`; rate-limited like the movie batch. |
| `POST /api/letterbox/tv/{series_id}/apply` | Body `{season_number?, episode_id?}` bulk apply (L2/§4.2); per-episode remove/ignore/mark-not-letterboxed mirror the movie routes. |
| Movie routes | Unchanged (L12). |

## 6. Tests

Pure: proportional offsets (20/40/65-min durations, head/tail skip, short-
clip clamp), triage episode selection, escalation logic, rollup buckets/
uniformity/specials, funnel mapping. DB (SQLite fixtures): subject
constraint, provenance backfill, L9 signature guard (matching vs new
signature), multi-episode fan-out, bulk apply dedupe. Snapshot: movie
status/candidates routes; route-order test.

## 7. Out of scope

TV reencode execution (deferred — schema only, L10). Frontend (plan 11).
Movie sampling changes. Webhook-driven TV letterbox reactions.

## 8. Operator notes

New migration; TV letterbox rows appear only after a batch detect is run
(nothing auto-scans). First TV batch on a big library should use default
(triage) mode; exhaustive is available per-show afterwards. cropdetect/
mkvpropedit binary checks are unchanged from the movie feature.
