## 2026-07-04

- Completed: started Plan 08 from `0738001` (`add tv integration plans for audio subs and letterbox`); no prior timeline existed. Recorded baseline `pytest -q` on this machine: 722 passed, 27 failed in 56.67s. Failing tests: `tests/test_dev_ocr_labels.py` (9), `tests/test_frontend_gap_routes.py::test_media_job_snapshot_reflects_generic_job_failure`, `tests/test_jobs.py` (4), `tests/test_pipeline_revised.py::test_effective_ocr_workers_caps_cuda_unless_gpu_forced`, `tests/test_run_endpoints.py` (5), `tests/test_system_metrics.py::test_metrics_history_returns_points_rates_and_job_overlay`, `tests/test_taste_artifacts.py` (2), `tests/test_webhooks_heal.py` (2), `tests/test_worker_cancellation.py` (2).
- In progress: verifying Plan 08 code touchpoints before phase 0 schema work.
- Next steps: inspect models/sync/rollup/subtitle scan/Subgen/supervisor modules named in Plan 08; add the required movie snapshot test before shared subtitle-generation changes; implement phase 0 schema and tests.
- Deviations: baseline required sandbox escape because the suite needs the local PostgreSQL fixture; initial sandboxed run failed with `PermissionError: [Errno 1] Operation not permitted` while opening the DB socket.
- Pending operator actions: none yet.

- Completed: phase 0 schema + guards in `6cb41ce` (`add audio subs schema`). Added episode tier-1 audio/subtitle JSON columns to the ORM + Alembic, added series-level preferred audio/subtitle override columns, added phase-0 ORM smoke tests, added the required movie library subtitle-shape snapshot test before shared Subgen work, and removed one unrelated dead import so `ruff check marquee tests` is clean. Verification: `alembic upgrade head --sql` rendered cleanly; focused tests passed (`tests/test_audio_subs_schema.py`, `tests/test_library_enrichment.py`); full `pytest -q` now 725 passed / 27 failed, so the pre-existing failure set did not grow.
- In progress: phase 1 sync capture for episode tier-1 audio/subtitle languages.
- Next steps: update `_sync_episodes` to parse Sonarr `mediaInfo.audioLanguages` and `mediaInfo.subtitles`, fan out shared file truth across multi-episode files, clear the columns on file removal, and add sync tests for normalization and clear-on-removal.
- Deviations: none from Plan 08 in phase 0.
- Pending operator actions: none yet.

- Completed: phase 1 Sonarr tier-1 capture in `3cbd488` (`capture episode audio subs`). `_sync_episodes` now normalizes `mediaInfo.audioLanguages` and `mediaInfo.subtitles` into ordered deduped tag lists, shares them across multi-episode files, stores `[]` when `mediaInfo` exists but reports no languages, and resets both columns to `NULL` when the file is removed. Added focused sync tests covering normalization, fan-out, empty-report semantics, and clear-on-removal.
- In progress: phase 2 pure audio/subs rollup engine.
- Next steps: build `marquee/core/audio_subs_rollups.py` mirroring `hdr_rollups.py`; add pure unit tests for status, tier precedence, specials exclusion, same-language matching, uniformity, and dub coverage; then wire preferred-language resolution for series/global settings.
- Deviations: none from Plan 08 in phase 1.
- Pending operator actions: none yet.

- Completed: phase 2 pure rollup core in `8142e2b` (`add audio subs rollups`). Added `marquee/core/audio_subs_rollups.py` with tier-2-wins episode resolution, per-episode status classification, season/show rollups, specials exclusion, same-language matching, missing-language unions, dub coverage, and forced/SDH counters. Added pure unit coverage for the status matrix, tier precedence, uniformity modes, specials exclusion, and none-met/unknown show verdicts.
- In progress: phase 3 deep scan scope expansion + nightly scheduling.
- Next steps: extend subtitle scan-all to accept movie/TV scopes, resolve TV files through `EpisodeMediaFile` with `tv_queries`, add the nightly `audio_subs_deep_scan` scheduler path and settings, and add tests around scoped scan selection and stale-inventory picking.
- Deviations: none from Plan 08 in phase 2.
- Pending operator actions: none yet.

- Completed: phase 3 scan scope + nightly scheduling in `0d883b3` (`add audio subs deep scan`). Added persisted deep-scan settings, extended `POST /api/subtitles/scan-library` to accept scoped bodies (`movies` / `tv` / `series`), taught `subtitle_scan_all` to queue missing or stale inventories instead of only missing ones, added the scheduled `audio_subs_deep_scan` job with nightly local-hour scheduling, and added focused tests for scope filtering, stale selection, batch limiting, and schedule enablement.
- In progress: phase 4 Subgen client correction and embedded-mode plumbing.
- Next steps: rewrite the Subgen `/batch` client to query params + real naming, add the `/asr` path, then vendor and supervise embedded Subgen with the hardware/model recommendation plumbing before touching the management API.
- Deviations: none from Plan 08 in phase 3.
- Pending operator actions: none yet.

- Completed: phase 4 Subgen correction + embedded plumbing is implemented in the working tree (commit pending). Added vendored Subgen sources pinned at `e68db4a0d77f8e5ba73e8b96e2be3c512f2aa6a3` with upstream LICENSE/VERSION and a new `subgen` extras group; rewrote the generator to use real `/batch` query params, exact upstream filename prediction, `/asr` upload mode, `/detect-language` probe support, structured `/status` parsing, and webhook-assisted reconciliation; added embedded Subgen spawn-spec helpers, supervisor child/log plumbing, Whisper hardware/model recommendation helpers, GPU inventory enumeration, generation resource split (`gpu` embedded vs `network_external` external), and Subgen management routes/settings updates. Added focused tests for generator request shape, naming, invalid translate combos, whisper recommendation matrices, and embedded env assembly. Verification available in-sandbox: `ruff check marquee tests` clean; `python -m py_compile` clean for all touched phase-4 modules.
- In progress: phase 5 `/api/audio-subs` landing + TV routes is also implemented in the working tree (commit pending). Added `/api/audio-subs` summary/list/detail/deep-scan/preferences/generate routes, registered the router in `main.py`, and verified route registration plus importability with an app import probe; lint and `py_compile` remain clean after the phase-5 wiring.
- Next steps: add/finish the phase-5 API tests, run the full suite if PostgreSQL socket access can be approved again, then stage and commit phases 4 and 5 separately once `.git` writes are available.
- Deviations: could not rerun pytest in this sandbox after phase 4 because the session-wide PostgreSQL fixture needs socket access and the escalated retry was rejected by the platform approval quota; no broader workaround was attempted. Also, `marquee/assets/subgen_probe.mp3` is currently a locally generated 10 s placeholder MP3 (valid file for route wiring, not yet the intended public-domain English speech sample) because outbound fetches are blocked in-sandbox.
- Pending operator actions: if full pytest verification is required before handoff, rerun with local PostgreSQL socket access outside the sandbox or after approval quota resets.
