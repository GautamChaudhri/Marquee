## 2026-07-04

- Completed: started Plan 08 from `0738001` (`add tv integration plans for audio subs and letterbox`); no prior timeline existed. Recorded baseline `pytest -q` on this machine: 722 passed, 27 failed in 56.67s. Failing tests: `tests/test_dev_ocr_labels.py` (9), `tests/test_frontend_gap_routes.py::test_media_job_snapshot_reflects_generic_job_failure`, `tests/test_jobs.py` (4), `tests/test_pipeline_revised.py::test_effective_ocr_workers_caps_cuda_unless_gpu_forced`, `tests/test_run_endpoints.py` (5), `tests/test_system_metrics.py::test_metrics_history_returns_points_rates_and_job_overlay`, `tests/test_taste_artifacts.py` (2), `tests/test_webhooks_heal.py` (2), `tests/test_worker_cancellation.py` (2).
- In progress: verifying Plan 08 code touchpoints before phase 0 schema work.
- Next steps: inspect models/sync/rollup/subtitle scan/Subgen/supervisor modules named in Plan 08; add the required movie snapshot test before shared subtitle-generation changes; implement phase 0 schema and tests.
- Deviations: baseline required sandbox escape because the suite needs the local PostgreSQL fixture; initial sandboxed run failed with `PermissionError: [Errno 1] Operation not permitted` while opening the DB socket.
- Pending operator actions: none yet.
