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
