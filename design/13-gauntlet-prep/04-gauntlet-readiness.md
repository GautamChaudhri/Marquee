# 04 — Gauntlet readiness checklist

**Type:** Operational gate (no code) — confirm all of this before running `05-gauntlet-test-plan.md`.

---

## A. Held-back endpoint inventory (all implemented — A verdict)

The read-only pass skipped these for safety. They are implemented and ready; the gauntlet exercises them for real. Handler locations for the test agent:

### Letterbox (`marquee/api/routes/letterbox.py` → `marquee/core/letterbox_service.py`, `marquee/media/letterbox_manager.py`)
| Endpoint | Handler | Mutates | Reverse |
|---|---|---|---|
| `POST /api/letterbox/movies/{id}/apply` | `letterbox.py:616` → `letterbox_service.apply` (`:172`) | writes MKV crop tags via `mkvpropedit` (verified re-read) | `remove` |
| `POST /api/letterbox/apply` (batch) | `letterbox.py:643` | batch of the above | `remove` per id |
| `POST /api/letterbox/movies/{id}/remove` | `letterbox.py:681` → `letterbox_service.remove` (`:231`) | deletes crop tags (idempotent) | re-`apply` |
| `POST /api/letterbox/heal` | `letterbox.py:704` → `letterbox_heal.py:35` | re-applies drifted tags | n/a (conservative) |

### Media jobs (`marquee/api/routes/media_jobs.py` → `marquee/core/media_jobs/manager.py`, `handlers.py`)
| Endpoint | Handler | Notes |
|---|---|---|
| `POST /api/media-jobs/{id}/confirm` | `media_jobs.py:48` | revalidates file signature, `planned→queued` |
| `POST /api/media-jobs/{id}/restore` | `media_jobs.py:151` → `subtitles/backup.py:restore_job_backup` | atomic copy backup→original |
| `DELETE /api/media-jobs/{id}/backup` | `media_jobs.py:162` → `subtitles/backup.py:delete_job_backup` | unlinks only the tracked backup |
| **Worker** | `manager.py:69–246`, started in `main.py` `lifespan` when `SUBTITLE_ENABLED=true` | serial, per-file locks, polls `queued` every 2s, `recover()` marks crashed `running`→`interrupted` |

Operations dispatched (`handlers.py:_HANDLERS`): `subtitle_scan`, `subtitle_remove`, `subtitle_embed`, `subtitle_metadata`, `subtitle_extract`, `subtitle_generate`, `subtitle_policy`, `subtitle_restore`. Backups: hardlink (fallback copy) into `.marquee/backups/{source_key}/{job_id}/` created **after** remux succeeds, **before** `os.replace` (`subtitles/mutation.py`).

### Other mutation endpoints
| Endpoint | Handler | Notes |
|---|---|---|
| `POST /api/subtitle-policies/{id}/apply` | `subtitle_policies.py:181` | creates `MediaBatch` + queued `subtitle_remove` jobs; skips review-required / hardlink-protected files |
| `POST /api/media-files/{id}/subtitle-generations` | `subtitle_generators.py:42` | **503 unless `SUBGEN_URL` set**; queues `subtitle_generate` |
| `POST /api/movies/{id}/subtitle-generations` | `subtitle_generators.py:62` | movie→media-file convenience wrapper |
| `POST /api/webhooks/subgen` | `webhooks.py:226` | optional completion callback (worker also reconciles via polling) |
| `POST /api/feedback` (`deploy=true`) | `feedback.py:185` → `PosterService.deploy` (`poster_service.py:116`) | atomic, path-validated write to movie folder + cache |
| `POST /api/system/heal` | `system.py:74` → `heal.py:28` | restores missing posters (cache→download) |
| Radarr `Download`+`isUpgrade` | `webhooks.py:206` → `_restore_after_upgrade` (`:83`), `_letterbox_stale_after_upgrade` (`:141`), `_schedule_subtitle_scan` (`:246`) | poster restore + letterbox re-detect + subtitle scan, all backgrounded |
| **NEW** `POST /api/movies/{id}/poster/restore` | (from `01-poster-restore-endpoint.md`) | manual restore; gauntlet covers force + non-force |

---

## B. Config prerequisites (assert in the pre-flight snapshot)

- [ ] `SUBTITLE_ENABLED=true` — **mandatory**. Without it the media-job worker never starts and every confirmed job hangs in `queued` forever. Confirm via `GET /api/system/status` (`media_jobs` block) and by checking a no-op job drains.
- [ ] `SUBGEN_URL` set and the external Subgen service reachable. If unset, `*/subtitle-generations` return `503` — record those as **expected-skip**, not bugs. Confirm via `GET /api/subtitle-generators` (provider health) and `GET /api/system/status/generators`.
- [ ] Media roots repointed to the writable lab volume and writable by the service user:
  - `/mnt/lab` owned/writable by the service user (the selection plan flags a `chown`/`chmod` blocker — resolve first).
  - `.env` `RADARR_MEDIA_PATH` / `SONARR_MEDIA_PATH` (and prefixes) point at the lab copy.
  - Re-sync after repoint (`POST /api/sync/all`) so DB `folder_path` / media-file paths resolve to lab files.
- [ ] Required binaries present (`GET /api/system/status.tools` / `GET /api/letterbox/status.binaries`): `ffmpeg`, `ffprobe`, `mkvpropedit`, `mkvmerge`. `503` from letterbox/subtitle routes when missing is expected, not a bug — but for a real gauntlet they should all be present.
- [ ] Webhook auth: if `WEBHOOK_TOKEN` is set, the gauntlet must include `?token=...`; if `WEBHOOK_DRY_RUN=true`, note that webhook restores won't touch the filesystem.

---

## C. Reset-between-passes strategy (critical for the two-pass design)

The gauntlet mutates media in Pass A (GPU) and must start Pass B (CPU) from a clean baseline.

1. **Baseline manifest** — before Pass A, capture `stat -c '%n\t%s\t%Y\t%i\t%h\t%a'` **and** `sha256sum` for every fixture media file (and every sidecar `.srt`). Save to `state/baseline-manifest.tsv`.
2. **Self-reversing pairs** — author every mutation test as mutate → verify-changed → reverse → verify-baseline:
   - letterbox `apply` → `mkvmerge -J` shows crop tags → `remove` → tags gone, file back to baseline-equivalent.
   - subtitle `remove`/`metadata`/`embed` job → ffprobe shows the change → `media-jobs/{id}/restore` (backup) → ffprobe + sha256 back to baseline → `DELETE .../backup`.
   - policy `apply` → jobs run → verify → restore each job's backup.
   - feedback `deploy=true` → poster file present → restore prior / `system/heal` → baseline.
   - manual `poster/restore` → leaves the deployed poster; if a test deleted it first, that's the restore.
3. **Drift guard** — after Pass A reversals, diff the live manifest against `baseline-manifest.tsv`. Note that MKV tag round-trips may change file size/mtime/sha even after `remove` (mkvpropedit rewrites). Treat **media content** equivalence (track layout via ffprobe/mkvmerge), not byte-identical sha, as the baseline criterion for tag operations; require byte-identical sha for subtitle backup/restore.
4. **Fallback reset** — for any title where reversal left unexpected drift, re-copy from the read-only source (`cp -a /mnt/PLUNDER/.../<title> /mnt/lab/Movies/<title>`) before Pass B and log it. Keep this scriptable; it is the safety net, not the primary path.

---

## D. GPU → CPU toggle

Both knobs are read at **startup**, so the two passes are separated by a server restart.

| Pass | `EXECUTION_PROVIDER` (ONNX: CLIP/DINO/aesthetic/face) | `OCR_DEVICE` (PaddleOCR) | Restart |
|---|---|---|---|
| A (GPU) | `auto` (resolves CUDA on the RTX 3070) | `auto` (GPU when Paddle-CUDA present) | start once |
| B (CPU) | `cpu` | `cpu` | restart with these set |

- Only **inference** endpoints differ across passes: pipeline run, legacy `test/pipeline`, taste `retrain`, taste `map` rebuild. Mutation endpoints (ffmpeg/mkvpropedit/mkvmerge) are provider-independent — but per the user, run the **full** suite in both passes anyway, so the matrices are identical and any provider-coupled surprise surfaces.
- Capture per-stage timings both passes; the GPU-vs-CPU delta on pipeline stages is a deliverable.
- Confirm the active provider after each restart via `GET /api/system/status` (OCR status block: `device`, `effective_workers`, `paddle_cuda_available`) and the pipeline run's `config_snapshot`.

---

## E. Memory & stability notes

- VM is **16–24 GB RAM with ballooning enabled**. The CPU pass runs CLIP/DINO/aesthetic on CPU + OCR worker pool on CPU — RAM-bound, not VRAM-bound. Sample available memory + process RSS at each pipeline stage and log a line on pressure (do **not** hard-stop; log and continue, per the error policy).
- GPU pass: watch VRAM across the 23-movie sweep to confirm the `f924490` fix holds (no monotonic growth). The OCR worker cap should keep Paddle contexts bounded.
- Error policy for the whole gauntlet: any endpoint error → full capture → **continue**. Only a dead/unreachable server or a hard GPU hang pauses the *current family* (with retry/backoff) while other families proceed. Nothing is silently skipped; every skip is recorded with its reason.

---

## F. Go/No-go

Proceed to `05` only when A–E are all green: worker running, SUBGEN status known, lab media writable + synced, binaries present, baseline manifest captured, and the restart procedure for Pass B is scripted.
