# 28 — OCR False-Positive / False-Negative Label Capture Plan

**Status:** Ready to implement. Reconciled with the codebase on 2026-06-28.

## Scope

Ship one debug-only developer flow for the pipeline run results page:

- mark an OCR-rejected poster as a `false_positive`;
- mark a ranked poster as a `false_negative`;
- copy the poster image and capture the poster's archived diagnostics;
- clear all captured OCR labels from the Settings danger zone.

Keep it minimal:

- no batch-marking page;
- no database tables;
- no analysis tool;
- no production exposure.

## Corrections From The Research Doc

- `GET /api/settings` already exposes `app.debug` in `marquee/api/routes/settings.py`. We do **not** need a new debug-status backend endpoint.
- Active run artifacts live under `data/runs/archive/` and `data/runs/work/`. `marquee/experiments/runs/` is only a legacy allow-list in poster serving, not the primary runtime tree.
- `PipelineRun.output_dir` is already persisted in `marquee/models/pipeline_run.py` and set by `marquee/pipeline/run_manager.py`. That is the best source for `pipeline.log` and `0-originals/`, with `_sanitise_filename(title)` only as fallback.
- `frontend/src/lib/components/PosterCandidateTile.svelte` renders a root `<button>`. FP/FN controls cannot be nested inside it; they must be sibling overlay buttons in a wrapper.
- `marquee.api.results.find_candidate()` already exists and is sufficient. A linear scan over one run's candidate list is trivial here and better than introducing a second indexing path.
- `design/plans/` does not exist yet and must be created by this task.

## Chosen Approach

### Debug gating

Reuse `GET /api/settings` for frontend debug awareness and add a new dev-only router that is imported and registered only inside:

`marquee/main.py`

```python
if settings.DEBUG:
    from marquee.api.routes.dev_ocr_labels import router as dev_ocr_labels_router
    app.include_router(dev_ocr_labels_router)
```

This keeps the routes out of prod entirely: no handler, no OpenAPI entry, no accidental auth surface.

### Snapshot gating

Treat the expanded OCR snapshot as a hard prerequisite.

- Frontend: hide or disable FP/FN controls when the loaded run lacks the required OCR snapshot keys.
- Backend: reject capture for stale runs with a clear `409` or `422` explaining that the run must be re-run after the snapshot upgrade.

This satisfies the requirement that the feature cannot be used before `PipelineSettings.snapshot()` captures the full OCR context.

### Storage layout

Store captures under one debug-only root grouped by movie/run, not split into separate global false-positive and false-negative trees:

```text
data/debug/ocr-labels/
  {sanitized_title}__{run_id}/
    false_positive/
      {safe_poster_id}/
        poster.jpg
        capture.json
        log.txt
    false_negative/
      {safe_poster_id}/
        poster.jpg
        capture.json
        log.txt
```

Use full `run_id`, not `run_id[:8]`, to avoid collisions and keep the folder directly traceable back to `PipelineRun.run_id`.

Use a deterministic per-poster folder name such as:

`{Path(orig_filename).stem}__{sha1(orig_filename)[:8]}`

This stays idempotent for repeated clicks while avoiding awkward raw filename edge cases.

### Capture payload

Make each poster folder self-contained:

- `poster.<ext>`: copied with `shutil.copy2`;
- `log.txt`: exact per-poster log lines matching `file={orig_filename}`;
- `capture.json`: one JSON document containing:
  - label kind;
  - marked timestamp;
  - movie metadata;
  - run metadata;
  - full archived candidate entry;
  - full archived `config` snapshot;
  - `stage_timings_seconds`;
  - source path resolution details;
  - missing-artifact flags.

Duplicating the config snapshot per capture is acceptable here because the volume is tiny and the later LLM tooling benefits from self-contained folders.

### Clear behavior

Add one clear-all action only:

- no per-movie clear;
- no per-run clear;
- no inline clear button on the run page.

Put the action in the existing Settings danger zone, matching the deployed-poster reset pattern already used in:

`frontend/src/routes/settings/+page.svelte`

### Log extraction

Do **not** grep generic filename context windows. The runner already emits self-contained per-poster log lines:

- `STYLE FEATURES | file=...`
- `OCR | file=...`
- `FEATURES | file=...`
- `TYPICALITY | file=...`
- `GATE PASS | file=...`
- `GATE REJECT | file=...`
- `RANK DETAIL | file=...`
- `OUTPUT TOP | file=...`

Capture exact matching lines for `file={orig_filename}` in order. This is cleaner than context-grep and avoids dragging unrelated posters into the artifact.

## Implementation Tasks

1. [ ] **(4 min)** Expand the archived OCR snapshot first.  
   Files: `marquee/core/pipeline_config.py`  
   Add the missing OCR keys to `PipelineSettings.snapshot()["ocr"]`: `OCR_TEXT_MODE`, all `OCR_ALLOW_*` toggles, confidence thresholds, fuzzy cutoff, title proximity, residual significance thresholds, and `OCR_ENHANCE_RETRY`.

2. [ ] **(3 min)** Lock the snapshot contract with a regression test.  
   Files: `tests/test_config_and_rescore.py`  
   Add a unit test that asserts the required OCR keys are present in `pipeline_settings.snapshot()["ocr"]`.

3. [ ] **(3 min)** Lock the existing debug flag contract instead of adding a new backend endpoint.  
   Files: `tests/test_frontend_gap_routes.py`  
   Extend the `/api/settings` test coverage to assert `body["app"]["debug"]` exists so the run page can depend on it safely.

4. [ ] **(4 min)** Add typed frontend access to runtime settings.  
   Files: `frontend/src/lib/api/system.ts`, `frontend/src/lib/api/types.ts`  
   Replace the current `any` return from `getSettings()` with a typed response that includes `app.debug`.

5. [ ] **(4 min)** Load `debugMode` alongside the run payload.  
   Files: `frontend/src/routes/pipeline/runs/[run_id]/+page.ts`  
   Fetch `getRunResults()` and `getSettings()` in parallel and return a plain `debugMode` boolean to the page data.

6. [ ] **(4 min)** Create the capture service module in the pipeline domain.  
   Files: `marquee/pipeline/ocr_label_capture.py`  
   Add:
   - a debug capture root helper using `settings.data_dir_path / "debug" / "ocr-labels"`;
   - a `_REQUIRED_OCR_SNAPSHOT_KEYS` constant;
   - a small enum or literal set for `false_positive` and `false_negative`.

7. [ ] **(5 min)** Implement run + candidate resolution using existing persisted data.  
   Files: `marquee/pipeline/ocr_label_capture.py`  
   Resolve artifacts in this order:
   - archive via `run_manager.load_archive(run.run_id, run.archive_path)`;
   - candidate via `marquee.api.results.find_candidate()`;
   - work dir via `run.output_dir`;
   - fallback work dir via `settings.runs_work_path / _sanitise_filename(archive["title"])`.

8. [ ] **(5 min)** Implement image, log, and capture-json extraction.  
   Files: `marquee/pipeline/ocr_label_capture.py`  
   Use:
   - primary image source: `{run.output_dir}/0-originals/{orig_filename}`;
   - fallback image source: archived `candidate["image_path"]`;
   - log source: `{run.output_dir}/pipeline.log`;
   - output: `poster.<ext>`, `log.txt`, `capture.json`.  
   If any artifact is missing, still write `capture.json` and mark the missing artifact explicitly instead of failing the whole capture.

9. [ ] **(4 min)** Make capture and clear idempotent and race-safe.  
   Files: `marquee/pipeline/ocr_label_capture.py`  
   Use deterministic folder names and a module-level lock so a clear request cannot delete a half-written capture. Keep the heavy filesystem work in synchronous helpers that the route layer can call via `asyncio.to_thread()`.

10. [ ] **(5 min)** Add the debug-only API router.  
    Files: `marquee/api/routes/dev_ocr_labels.py`  
    Add three endpoints under `/api/dev/ocr-labels`:
    - `POST /false-positive`
    - `POST /false-negative`
    - `POST /clear`  
    Keep routes thin: validate request, load `PipelineRun`, call the capture service in `asyncio.to_thread()`, return structured JSON.

11. [ ] **(3 min)** Register the router only when `settings.DEBUG` is true.  
    Files: `marquee/main.py`  
    Add the conditional import + `app.include_router()` near the router-registration block. Do not import the router at module top level.

12. [ ] **(5 min)** Add backend regression coverage for the new debug routes.  
    Files: `tests/test_dev_ocr_labels.py`  
    Cover:
    - successful false-positive capture with archive + log + originals present;
    - stale run rejected when required OCR snapshot keys are missing;
    - clear-all removes the debug capture root and recreates it cleanly;
    - prod absence: reload `marquee.main` with `settings.DEBUG = False` and assert the dev routes return `404`.

13. [ ] **(4 min)** Add frontend API helpers and types for OCR label capture.  
    Files: `frontend/src/lib/api/pipeline.ts`, `frontend/src/lib/api/types.ts`  
    Add request/response types and helpers for:
    - `markOcrFalsePositive()`
    - `markOcrFalseNegative()`
    - `clearOcrLabels()`

14. [ ] **(5 min)** Add run-page gating for debug mode and snapshot completeness.  
    Files: `frontend/src/routes/pipeline/runs/[run_id]/+page.svelte`  
    Add Svelte 5 derived state for:
    - `debugMode` from `data.debugMode`;
    - `hasFullOcrSnapshot` from `results.config_snapshot?.ocr`;
    - a per-poster busy map using immutable reassignment, not in-place mutation.  
    If `debugMode` is true but the run snapshot is stale, show a small dev-only hint telling the developer to re-run after the snapshot upgrade.

15. [ ] **(5 min)** Add wrapper-based FP/FN overlay buttons without nesting buttons.  
    Files: `frontend/src/routes/pipeline/runs/[run_id]/+page.svelte`  
    Wrap each `PosterCandidateTile` render site in a relative container and place the dev action button as a sibling overlay. Apply it in:
    - OCR rejected grid;
    - ranked sectioned grid;
    - ranked expanded-stack tiles.  
    Do **not** render an FN button on collapsed `PosterStack` cards; the user must expand the stack to label a specific variant.

16. [ ] **(4 min)** Add toast and disable behavior for mark actions.  
    Files: `frontend/src/routes/pipeline/runs/[run_id]/+page.svelte`  
    On click:
    - disable only that poster's FP/FN button while the request is in flight;
    - toast success on capture;
    - toast a warning if the backend reports missing image/log artifacts;
    - keep the button available even when the run is already `reviewed`, because label capture is diagnostic, not taste feedback.

17. [ ] **(4 min)** Add the clear-all danger-zone card.  
    Files: `frontend/src/routes/settings/+page.svelte`  
    Reuse the existing `ConfirmDialog` pattern and only render the OCR-label clear action when `data.settings.app.debug` is true.

18. [ ] **(3 min)** Add a short settings-page clear handler.  
    Files: `frontend/src/routes/settings/+page.svelte`  
    Call `clearOcrLabels(fetch)`, toast the deleted counts, and close the dialog. Keep it separate from the deployed-poster reset dialog state.

## Notes For The Implementer

- Keep the route layer async and thin. All disk work should run off the event loop.
- Prefer `run.output_dir` over title reconstruction whenever it exists.
- Import `_sanitise_filename` from `marquee.pipeline.runner`, matching the existing pattern already used by `run_manager.py`, `batch_runner.py`, and `api/routes/test_pipeline.py`.
- Reuse `marquee.api.results.find_candidate()` instead of re-implementing candidate lookup.
- Return idempotent results from the mark endpoints so a double-click does not create duplicate folders.
- Keep the debug capture root out of `data/feedback/` and `data/training/`; this is developer instrumentation, not user feedback data.

## Verification Commands

Run all of these after implementation:

```bash
rtk pytest tests/test_config_and_rescore.py tests/test_frontend_gap_routes.py tests/test_dev_ocr_labels.py -q
rtk pytest tests/test_run_endpoints.py -q
rtk ruff check marquee tests
rtk npm --prefix frontend run check
rtk npm --prefix frontend run lint
```

Manual smoke check in debug mode:

```bash
rtk uvicorn marquee.main:app --reload
rtk find data/debug/ocr-labels -maxdepth 4 -type f | rtk sort
```

Manual prod-absence check:

```bash
env DEBUG=false rtk uvicorn marquee.main:app --port 3166
rtk curl -i -X POST http://127.0.0.1:3166/api/dev/ocr-labels/false-positive \
  -H 'content-type: application/json' \
  -d '{"run_id":"missing","orig_filename":"missing.jpg"}'
```

Then confirm:

- the run page shows FP/FN only in debug mode;
- OCR-tab FP works on an OCR reject;
- ranked-tab FN works on an accepted poster;
- each capture writes `poster.*`, `log.txt`, and `capture.json`;
- the Settings danger-zone clear action removes everything under `data/debug/ocr-labels/`;
- `POST /api/dev/ocr-labels/*` returns `404` when the app is started with `DEBUG=false`.
