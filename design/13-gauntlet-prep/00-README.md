# 13 — Gauntlet Prep

**Date:** 2026-06-16
**Status:** Implementation specs ready for coding agents
**Purpose:** Get Marquee fully ready for the **gauntlet test** — one rigorous live pass that exercises *every* endpoint and feature, including the media-mutating ones the read-only pass (`design/12-readonly-endpoint-test-plan.md`) deliberately skipped.

This folder closes the gap between "read-only validated" and "everything validated." It contains the fixes for the 6 API design-drift items, an operational readiness checklist, and the comprehensive write-enabled gauntlet test plan.

---

## How this folder is meant to be used

Each numbered file below is a **self-contained brief for a coding/test agent**. An agent should be able to open one file and execute it without re-deriving context. Read order:

| File | Type | What an agent does with it |
|---|---|---|
| `00-README.md` | index | Orientation (this file). |
| `01-poster-restore-endpoint.md` | **code** | Implement `POST /api/movies/{id}/poster/restore`. |
| `02-library-filters.md` | **code** | Implement server-side library list filters. |
| `03-doc-reconciliation.md` | **docs** | Apply 4 surgical design-doc edits + bookkeeping. |
| `04-gauntlet-readiness.md` | checklist | Pre-flight gate before the gauntlet runs. |
| `05-gauntlet-test-plan.md` | test plan | Execute the full two-pass (GPU→CPU) gauntlet. |

Apply `01`–`03` first (they make code/docs consistent), confirm `04`, then run `05`.

---

## Background findings (why this folder exists)

### The 6 design-drift items (`design/todos.md` → "API design drift follow-ups")

| # | Item | Verdict | Fix file |
|---|---|---|---|
| 1 | `POST /api/movies/{id}/poster/restore` documented (01-poster-restoration §14) but not implemented | **Real gap → implement** | `01` |
| 2 | `/api/subtitle-batches/...` documented (03-subtitle-management §25.3) but never built | Doc stale → annotate | `03` |
| 3 | Subtitle `scan` documented as "queue forced refresh" but code is inline | Doc stale → reword | `03` |
| 4 | Library list filters documented (§25.1) but only pagination exists | **Real gap → implement** | `02` |
| 5 | Letterbox preview documented as `?t=<sec>` but code uses `?minute=<int>` | Doc stale → reword | `03` |
| 6 | Letterbox single-detect documented as "202 or sync" but code is sync-only | Doc stale → clarify | `03` |

Two are genuine feature gaps (1, 4); four are stale docs where the **code is already the sensible design** (2, 3, 5, 6).

### Held-back endpoint readiness (IMPORTANT)

The read-only pass skipped every media-mutating endpoint for safety. Investigation found **all of them are already fully implemented** — none are stubs or broken:

- Letterbox `apply` / `apply` (batch) / `remove` / `heal` — `marquee/api/routes/letterbox.py` → `marquee/core/letterbox_service.py`, `marquee/media/letterbox_manager.py` (mkvpropedit, verified writes).
- Media-jobs `confirm` / `restore` / `backup` DELETE + the **durable worker** — `marquee/api/routes/media_jobs.py` → `marquee/core/media_jobs/manager.py` (started in `lifespan`, restart-safe, serial, per-file locks, hardlink backups, atomic `os.replace`).
- Subtitle-policy `apply` — `marquee/api/routes/subtitle_policies.py` (creates queued `subtitle_remove` jobs).
- Subtitle generation — `marquee/api/routes/subtitle_generators.py` (end-to-end; **requires `SUBGEN_URL`**).
- Feedback `deploy=true` — `marquee/api/routes/feedback.py` → `PosterService.deploy()` (atomic, path-validated).
- System `heal` — `marquee/api/routes/system.py` → `marquee/core/heal.py`.
- Radarr upgrade webhook — `marquee/api/routes/webhooks.py` (poster restore + letterbox stale + subtitle scan, all backgrounded).

**Consequence:** readiness work is *config prerequisites + the two feature gaps*, not patching broken code. The gauntlet itself is what will surface deeper runtime bugs. Full per-family inventory with file:line is in `04-gauntlet-readiness.md`.

### VRAM leak fix — verified

Commit `f924490` ("fix ocr gpu worker memory handling") is **correct and complete**:
- `effective_ocr_workers()` (`marquee/ml/hardware.py`) caps OCR worker count on the CUDA tier unless `OCR_DEVICE=gpu` is explicitly set — stops stale `OCR_WORKERS=N` from spawning N Paddle GPU contexts and stranding VRAM.
- Runtime device resolution (`_resolve_ocr_device`, `paddle_cuda_available`) + PID-tracked worker lifecycle (`_register_worker`/`active_worker_status`) in `marquee/pipeline/ocr_filter.py`.
- `OCR_DEVICE` config knob (`marquee/core/pipeline_config.py:202`, validated to `auto|cpu|gpu`).
- OCR status surfaced on `GET /api/system/status`.
- Test coverage included (worker cap, device resolution, forced-shutdown surfacing, status shape).

No further action needed; the gauntlet should simply confirm the OCR-status shape and watch for VRAM growth across the 23-movie pipeline sweep.

---

## Environmental prerequisites (assert before the gauntlet)

- `SUBTITLE_ENABLED=true` — else the media-job worker never starts and confirmed jobs hang forever.
- `SUBGEN_URL` set + reachable — else `*/subtitle-generations` return 503 (record as expected-skip, not a bug).
- Media roots repointed to the writable lab volume (`/mnt/lab`) and writable by the service user; re-sync run after repoint.

See `04-gauntlet-readiness.md` for the full checklist.
