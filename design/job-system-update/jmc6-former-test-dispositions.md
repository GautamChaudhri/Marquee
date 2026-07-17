# JMC6 — Former Failing-Test Disposition Ledger

Authoritative reconciliation of the reset-window **31-failure** inventory
([`job-system-miscellaneous-reset-window-fixes.md` §"Current failing-test
inventory"](job-system-miscellaneous-reset-window-fixes.md)) with the JMC6C
fresh full-suite result. Created in JMC6C Phase C0; dispositions verified in
Phase C1. Every original entry ends `fixed`, `replaced`, or
`removed-obsolete/deferred`. No agent/model attribution appears here.

**Audit universe (C06):** the original 31 remain the ledger even though JMC5C
reduced the live failing set to 17. The 17 retained failures are frozen in
`tests/fixtures/jmc6a/retained_backend_failures.txt`.

**C0 baseline (owned disposable PostgreSQL `127.0.0.1:55450/marquee_test`,
Python 3.13.14, pgqueuer 1.1.1):** `1270 passed, 17 failed, 2 warnings` in
~90 s. Failure membership is byte-identical to the frozen JMC6A/JMC6B
inventory; 0 skips, 0 xfail/xpass; the 2 warnings are the same
`umap n_jobs` `UserWarning` (third-party, informational).

Verification command (every run):
```
DB_URL=postgresql+asyncpg://marquee:***@127.0.0.1:55450/marquee_test DEBUG=true \
  .venv/bin/python -m pytest -q -rsxXf -p no:cacheprovider
```

---

## Part A — the 17 retained failures (live at C0, resolved in C1)

### A1. Development OCR labels — `tests/test_dev_ocr_labels.py` (9)

| Original / current node ID | Result |
|---|---|
| `test_capture_false_rejection_uses_this_runs_log` | fixed |
| `test_capture_synthesises_from_archive_when_log_missing` | fixed |
| `test_capture_ignores_stale_pipeline_log_from_another_run` | fixed |
| `test_capture_false_acceptance_with_nan_features` | fixed |
| `test_capture_flags_missing_image_and_ocr_diagnostics` | fixed |
| `test_capture_includes_full_ocr_trace` | fixed |
| `test_capture_flags_stale_null_ocr_read` | fixed |
| `test_capture_does_not_flag_pre_ocr_reject` | fixed |
| `test_list_run_labels_returns_persisted_filenames` | fixed |

- **Invariant:** `POST /api/dev/ocr-labels/{false-rejection,false-acceptance}`
  captures a current run's archive diagnostics (image/log/OCR read) for
  training-data curation and returns 200.
- **Canonical owner:** `marquee/api/routes/dev_ocr_labels.py` +
  `marquee/pipeline/ocr_label_capture.py::capture_ocr_label`.
- **Classification:** stale test fixture (OCR-config **schema drift**). Product
  is correct: `capture_ocr_label` raises 409 ("predates the OCR snapshot
  upgrade; re-run it first") when `missing_ocr_snapshot_keys(archive)` is
  non-empty. `REQUIRED_OCR_SNAPSHOT_KEYS` includes `allow_billing`
  (`pipeline_config.py:593` `"allow_billing": self.OCR_ALLOW_BILLING`), but the
  test fixture `_full_ocr_snapshot()` omitted it, so every run archive looked
  stale → 409 before any real assertion ran. The paired **passing** test
  `test_capture_rejects_stale_snapshot` (its own minimal snapshot) proves the
  409 gate is intended — so `_full_ocr_snapshot()` must be the *complete*
  current snapshot.
- **Disposition:** `fixed` — add the missing `allow_billing` key to
  `_full_ocr_snapshot()`. No assertion weakened; label/diagnostic evidence and
  archive/current-run isolation preserved (C07).

### A2. Effective OCR worker policy — `tests/test_pipeline_revised.py` (1)

| Original node ID | Replacement node ID | Result |
|---|---|---|
| `test_effective_ocr_workers_caps_cuda_unless_gpu_forced` | `test_effective_ocr_workers_honors_config_else_hardware_default` | replaced |

- **Invariant:** `hardware.effective_ocr_workers()` resolves the PaddleOCR pool
  size from the configured `OCR_WORKERS`, falling back to the hardware-tier
  default when unset.
- **Canonical owner:** `marquee/ml/hardware.py::effective_ocr_workers` /
  `_auto_ocr_workers` / `detect_hardware`.
- **Classification:** stale expectation + host non-determinism. The current
  contract honors an explicit `OCR_WORKERS` (`return min(configured, 16)`)
  regardless of `OCR_DEVICE`/tier; the fallback `detect_hardware().ocr_workers`
  is `min(3, cpu//4)` on CUDA. The old test asserted a device-conditional cap
  that no longer exists and compared against the host's live `os.cpu_count()`
  (fails on any multi-core box: `assert 10 == 3` here). No product defect — an
  explicit operator setting must not be silently reduced.
- **Disposition:** `replaced` — deterministic test of the actual contract
  (mocks `os.cpu_count()`; asserts configured>0 → `min(cfg,16)`, configured==0
  → hardware default, and the 16 upper bound), covering CPU/CUDA/forced modes
  (C07).

### A3. Run/retrain endpoints — `tests/test_run_endpoints.py` (4)

| Original node ID | Replacement node IDs | Result |
|---|---|---|
| `test_run_conflict_returns_409` | `test_jmc4a_submission.py`, `test_jmc4a_batch_coordination.py::test_*idempoten*` | removed-obsolete |
| `test_run_refused_during_rebuild` | `test_jmc3a_*`/`test_job_*` execution-class + advisory-lock gates | removed-obsolete |
| `test_retrain_refused_during_run` | `test_jmc4a_submission.py` (idempotency) | removed-obsolete |
| `test_cancel_retrain_endpoint_requests_process_stop` | `test_job_commands.py`, `test_pgqueuer_delivery.py` (canonical cancel + process death) | removed-obsolete |

- **Invariant (retained, moved):** concurrent/duplicate poster-analysis or
  taste-rebuild requests are rejected as a conflict; a rebuild cancel proves
  process stop.
- **Canonical owner:** `marquee/api/routes/pipeline.py::run_pipeline`
  (202; enqueues canonical `poster_pipeline`; 409 only on
  `IdempotencyConflictError`), `marquee/api/routes/taste.py::retrain_taste`
  (202; enqueues `taste_rebuild`) and `::cancel_retrain_taste`
  (`job_control.cancel` on the canonical `Job`).
- **Classification:** obsolete custom-runtime expectation. The endpoints no
  longer consult the in-process `run_manager` GPU/`_active_run_id` busy-check or
  terminate an in-process `_active_rebuild_process`; conflict is canonical
  idempotency and cancellation is the durable fenced lifecycle. The old tests
  drove the removed busy-check (got 400 "no downloaded file" / 400 instead of
  the legacy 409) or the removed in-process cancel (retrain now enqueues a real
  ticket → `UndefinedTableError` in a schema without PgQueuer installed).
- **Disposition:** `removed-obsolete` — delete the four tests; valid behavior
  is canonically covered by the replacement node IDs above. The still-present
  run/RunState/`release-gpu`/rebuild-progress tests in this file remain green
  (their code is live: `run_manager.load_archive` feeds
  `ocr_label_capture`, `/api/system/release-gpu` and `/api/taste/status`'s
  `rebuild` field still use the in-process helpers) and are untouched.

### A4. Taste artifacts — `tests/test_taste_artifacts.py` (2)

| Original node ID | Result |
|---|---|
| `test_heads_endpoint_backfills_active_head` | fixed |
| `test_taste_status_normalizes_duplicate_active_profiles` | fixed |

- **`test_heads_endpoint_backfills_active_head`**
  - **Invariant:** `GET /api/taste/heads` backfills an active learned-head
    artifact snapshot and links its training-sample movies.
  - **Classification:** **product defect** (dangling evidence FK) + test
    isolation leak. `artifact_registry._head_movies` resolved sample movie IDs
    from `feedback_store.read_all` but inserted **non-null unresolved
    `movie_id`s** into `artifact_snapshot_movies`, violating
    `artifact_snapshot_movies_movie_id_fkey` when the referenced live movie was
    absent (`Key (movie_id)=(406) is not present in table "movies"`). This
    contradicts the durable-history rule that historical evidence must survive a
    retired/absent live projection (`ArtifactSnapshotMovie.movie_id` is
    nullable `SET NULL`). Separately, the movies-namespace `read_all` reads the
    non-isolated `FEEDBACK_LABELS_PATH` (`data/feedback/labels.jsonl`, operator
    data with 119 `406` references), so the test also read operator data.
  - **Disposition:** `fixed` — product: `_head_movies` (and, for parity,
    `_resolve_profile_entries`) now **null the `movie_id` while keeping the
    title/year/tmdb snapshot** when the live movie row is absent, so the
    artifact snapshot never carries a dangling FK. Test: `managed_head` isolates
    `FEEDBACK_LABELS_PATH` to a `tmp_path`, making the test hermetic (touches no
    operator data). Strengthens evidence-linkage/durable-history coverage (C05).

- **`test_taste_status_normalizes_duplicate_active_profiles`**
  - **Invariant:** `GET /api/taste/status` normalizes a duplicate-active
    anomaly to exactly one active `ArtifactSnapshot` per kind
    (`artifact_registry._normalize_active_rows` keeps the newest, archives the
    rest).
  - **Classification:** stale test fixture (**schema drift**). The unique
    constraint `uq_artifact_snapshot_kind_storage_path` was added to the
    canonical schema; the test built its second active row with the *same*
    `(kind, storage_path)` as the backfilled one, so the setup insert now hits a
    `UniqueViolationError` before the endpoint runs.
  - **Disposition:** `fixed` — the duplicate row uses a distinct
    `storage_path`/`active_path`/`sha256`, so two active rows can coexist and
    the normalization invariant (`active_count == 1` after `GET /status`) is
    proven against real product code, not the constraint.

### A5. Whisper catalog — `tests/test_whisper_catalog.py` (1)

| Original node ID | Result |
|---|---|
| `test_gpu_recommendation_prefers_turbo_on_8gb` | fixed |

- **Invariant:** on an 8 GB GPU, the transcribe recommendation prefers
  `large-v3-turbo`.
- **Canonical owner:** `marquee/core/subtitles/whisper_catalog.py::recommend` /
  `per_model_verdicts` (fully deterministic from an explicit `HardwareSnapshot`
  — no host dependency).
- **Classification:** stale assertion (**catalog retune**). The recommendation
  is still correctly `large-v3-turbo` (asserted, passes); the failure is a
  secondary check that `large-v3`'s verdict is `fits_int8`/`too_big`. With the
  current catalog, `large-v3` fp16 (4.7 GB) fits the 8 GB budget (`8 − 1.5`
  reserve = 6.5 GB) → verdict `fits_fp16`. Turbo is still preferred for speed.
- **Disposition:** `fixed` — assert the current catalog policy
  (`large-v3` `fits_fp16` while the recommendation still prefers turbo),
  proving the intended hardware/model policy against explicit inputs (C07).

---

## Part B — the 14 originals resolved during JMC1–JMC5C

Reconciled against the current tree (test presence via `grep -rl "def <name>"
tests/`) and the passing C0 baseline.

### B1. Removed as obsolete/deferred (8)

| Original node ID (file) | Reason | Result |
|---|---|---|
| `test_subtitle_scan_all_raises_when_registry_event_is_set` (`test_cooperative_cancellation.py`) | in-process scan-all cancel; `subtitle_scan_all` is now a reserved disabled ticketless parent; canonical cancellation covered by `test_pgqueuer_delivery.py`/`test_job_commands.py` | removed-obsolete |
| `test_media_job_snapshot_reflects_generic_job_failure` (`test_frontend_gap_routes.py`) | `MediaJob` lifecycle deleted (JMC5); canonical snapshot covered by `test_jobs_api.py`/`test_job_presenters.py` | removed-obsolete |
| `test_subtitle_scan_all_handler` (`test_frontend_gap_routes.py`) | inline scan-all handler retired; reserved disabled type | removed-obsolete |
| `test_create_and_run_never_retries_even_for_retryable_types` (`test_jobs.py`) | `create_and_run` inline executor deleted (JMC5); replaced by `test_pgqueuer_delivery.py` retry classification | removed-obsolete |
| `test_cancelling_parent_batch_cascades_and_preserves_completed_children` (`test_jobs.py`) | custom batch manager deleted; replaced by `test_jmc4a_batch_coordination.py` | removed-obsolete |
| `test_cancelled_before_execution_updates_parent_to_terminal` (`test_jobs.py`) | custom batch manager deleted; replaced by `test_jmc4a_batch_coordination.py` | removed-obsolete |
| `test_webhook_upgrade_restores_poster` (`test_webhooks_heal.py`) | webhooks **deferred** (C04); route-level webhook tests removed; no webhook family certified | removed-deferred |
| `test_webhook_upgrade_noop_when_poster_survives` (`test_webhooks_heal.py`) | webhooks **deferred** (C04) | removed-deferred |

`tests/test_jobs.py` and `tests/test_webhooks_heal.py` no longer exist.

### B2. Fixed — present and passing at C0 (6)

| Original / current node ID (file) | Fix landed in | Result |
|---|---|---|
| `test_scan_library_subtitles_endpoint` (`test_frontend_gap_routes.py`) | scan-library request-contract drift (`force` query→canonical body) | fixed |
| `test_tv_dev_reset_all_deletes_episode_rows_and_previews_only` (`test_letterbox_tv_api.py`) | letterbox reset nullable-column defect | fixed |
| `test_events_404_for_unknown_run` (`test_run_endpoints.py`) | run-events 404 path retained/canonical | fixed |
| `test_movie_media_replacement_resets_letterbox_state_on_signature_mismatch` (`test_sync.py`) | letterbox state reset on signature mismatch | fixed |
| `test_episode_media_replacement_resets_stale_letterbox_state_and_links` (`test_sync.py`) | letterbox state reset + links | fixed |
| `test_metrics_history_returns_points_rates_and_job_overlay` (`test_system_metrics.py`) | metrics-history contract migrated (canonical job overlay) | fixed |

---

## Summary

| Disposition | Count |
|---|---|
| fixed | 9 (OCR labels) + 2 (taste) + 1 (whisper) + 6 (JMC1–5C) = **18** |
| replaced | 1 (OCR workers) |
| removed-obsolete | 4 (run endpoints) + 6 (JMC1–5C) = **10** |
| removed-deferred (webhooks) | **2** |
| **Total original 31** | **31** |

Every original failing test has a final disposition. Replacement node IDs are
recorded for removed/rewritten behavior.

## C1 verification (2026-07-16)

- Product fix: `artifact_registry._head_movies` / `_resolve_profile_entries` now
  null an unresolvable `movie_id` while keeping the title snapshot.
- Test edits: `_full_ocr_snapshot()` gains `allow_billing`; OCR-workers test
  rewritten deterministically (`test_effective_ocr_workers_honors_config_else_hardware_default`);
  whisper `large-v3` verdict corrected to `fits_fp16`; `managed_head` isolates
  `FEEDBACK_LABELS_PATH` and seeds an absent-movie label (durable-linkage
  coverage); duplicate-active fixture uses a distinct `storage_path` and reads
  status via a column query; four obsolete run/retrain tests removed.
- **Full backend suite (owned `:55450/marquee_test`): `1283 passed, 0 failed,
  2 warnings` in ~95 s** (13 former failures fixed/replaced in place + 4
  obsolete removed; `1270 + 13 = 1283`). 0 skips, 0 xfail/xpass; the 2 warnings
  are the pre-existing third-party `umap n_jobs` `UserWarning`.
- Ruff clean; OpenAPI deterministic 3.1.0 / **202 paths** (no contract change);
  `svelte-check` **0 errors / 0 warnings**; frontend **108 unit tests** pass.
- Every original 31 disposition is verified. C1 gate met.

## C5 final verification (2026-07-17)

- The complete backend suite was rerun after C4 on the owned disposable PostgreSQL target:
  **1283 passed, 0 failed, 0 skipped, 0 xfail/xpass, 2 warnings in 93.21s**.
- All 31 ledger entries and their replacement links remain present. No test was quarantined,
  blanket-xfailed, ignored, weakened, or deleted solely to obtain green.
- The guarded fresh reset, PgQueuer durable verification, Alembic/model equivalence, OpenAPI/type
  drift, Ruff, frontend unit/check/lint/build, Playwright/axe, workflow lint, and static absence
  gates pass. The disposition ledger is final for `jmc6c-complete`.
