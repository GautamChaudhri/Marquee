# Marquee — API Reconciliation (real backend ⟷ mockup)

> **Source of truth for the frontend.** The handoff's "API Contract" (`MARQUEE_HANDOFF.md` §4–§6) is an _idealized_ surface invented by the design tool — it does **not** match the running FastAPI app. This doc maps every mockup screen to the routes that actually exist, and flags the gaps that need a backend change or a frontend workaround.
>
> Rule: **never code a screen against a handoff route verbatim.** Look it up here first.
>
> Verified against `marquee/api/` on 2026-06-17 (branch `frontend`).

---

## 1. Access model (real)

Auth is **arr-style `X-Api-Key`**, enforced by a global `require_api_key` dependency (`marquee/api/auth.py`). There is **no** `/login`, no `/api/auth/me`, no session cookie yet. Order of checks:

1. `DEBUG=true` → auth fully disabled (use this for local dev).
2. Health probes are exempt.
3. `AUTH_ALLOW_LOCAL` + loopback origin → exempt.
4. Otherwise: key required via `X-Api-Key` header (also accepts `Authorization` / query). Wrong/missing → `401`; brute-force lockout → `429`; key not configured → `503`.
5. `auth.py:119` has a TODO: _"Frontend phase will accept a valid httpOnly session cookie here."_ — cookie auth is **not built**.

**Frontend decision (chosen): SvelteKit server-side proxy.** The SvelteKit Node server holds `API_KEY` and injects `X-Api-Key` on every forwarded call. The browser never sees the key; no login UI needed yet.

```
browser ──same-origin──> SvelteKit server (+server.ts / load) ──X-Api-Key──> FastAPI :3165
```

- Put `MARQUEE_API_URL` + `MARQUEE_API_KEY` in server-only env (`$env/static/private`).
- All `fetch` to the backend happens in `load()` / `+server.ts`, never in browser code.
- SSE is the exception (browser `EventSource` can't set headers) — see §4.

---

## 2. Real route inventory

Grouped by router. ✅ = usable as-is · ⚠️ = shape differs from mockup · ❌ = mockup assumes it but it doesn't exist.

### System & ops — `/api/system`

| Method | Path                                                                                                                    | Returns / notes                                                                                                                                                                    |
| ------ | ----------------------------------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| GET    | `/api/system/status`                                                                                                    | ⚠️ `{ cache{posters,bytes}, configuration, heal, tools, jobs{phase:count}, ocr, worker_supervisor }` — operational state; detailed telemetry is under `/metrics` and `/operations` |
| GET    | `/api/system/status/generators`                                                                                         | subtitle-provider health                                                                                                                                                           |
| POST   | `/api/system/heal`                                                                                                      | submit a canonical poster-heal batch; attempt-owned processes release GPU resources at their tracked boundary                                                                      |
| POST   | `/api/system/backup` · GET `/api/system/backups` · POST `/api/system/restore` (202) · DELETE `/api/system/backups/{id}` | DB backups                                                                                                                                                                         |
| ❌     | `cpu / gpu / ram / disk / uptime / workers` live metrics                                                                | **No backend source exists** (see Gap G1)                                                                                                                                          |

### Library — `/api/library`

| Method | Path                                                                               | Returns                                                                                                                                                                                 |
| ------ | ---------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| GET    | `/api/library/movies?page&page_size`                                               | `{ total, page, page_size, items:[{ id, title, year, tmdb_id, container, media_file_id, subtitle_coverage }] }` — **no filters**, **no poster/hdr/res/genre/letterbox fields** (Gap G2) |
| GET    | `/api/library/movies/{id}`                                                         | above + `genres, media_file_path`                                                                                                                                                       |
| GET    | `/api/library/series` · `/series/{id}` · `/series/{id}/seasons` · `/episodes/{id}` | series tree                                                                                                                                                                             |

### Poster pipeline — `/api/pipeline` + `/api/movies` + `/api/feedback`

| Method | Path                                                        | Returns / notes                                                                                                                                |
| ------ | ----------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------- |
| POST   | `/api/pipeline/movie/{id}/run`                              | **202**, async. Returns `run_id` + `events_url`                                                                                                |
| GET    | `/api/pipeline/runs/{run_id}`                               | full result: `{ run_id, status, reviewed, scorer, auto_pick, ranked[], rejected{gate,ocr,dedup,errored}, rejection_summary, stage_timings_s }` |
| GET    | `/api/pipeline/runs/{run_id}/events`                        | **SSE** for a live run                                                                                                                         |
| GET    | `/api/pipeline/runs/{run_id}/posters/{orig_filename}`       | candidate image bytes                                                                                                                          |
| POST   | `/api/pipeline/runs/{run_id}/rescore`                       | re-rank with `{weights?, gates?}`                                                                                                              |
| GET    | `/api/movies/{id}/runs` · `/api/movies/{id}/artwork-events` | run history / activity                                                                                                                         |
| POST   | `/api/feedback`                                             | approve flow — body `{ run_id, action:'approve'\|'override'\|'reject_all', selected_filename?, deploy? }`                                      |
| POST   | `/api/feedback/undo`                                        | `{ event_id }`                                                                                                                                 |

Candidate view shape (in `ranked[]` / `auto_pick`): `{ orig_filename, rank, score, poster_url, raw_features, normalized_features, stage_reached }`. `auto_pick` also has `explanations`.

### Taste — `/api/taste`

| GET `/status` · POST `/retrain` (202) · POST `/retrain/cancel` · GET `/map` · POST `/map/rebuild` (202) · GET `/exemplars/{name}/neighbors` |

### Letterbox — `/api/letterbox`

| Method | Path                                                    | Notes                                                                                                  |
| ------ | ------------------------------------------------------- | ------------------------------------------------------------------------------------------------------ |
| GET    | `/status`                                               | `{ enabled, method, counts{status:n}, binaries, honored_by, not_honored_by, last_scan, batch_active }` |
| GET    | `/candidates?status&confidence&sort&page&page_size`     | paginated state joined to movie — **drives all trays**                                                 |
| GET    | `/movies/find-candidates`                               | resolution pre-classify (no ffmpeg)                                                                    |
| GET    | `/movies/{id}` · `/movies/{id}/preview?minute=`         | detail / before-after frame                                                                            |
| POST   | `/movies/{id}/detect` (sync) · `/detect` (202 batch)    | frame analysis                                                                                         |
| POST   | `/movies/{id}/apply` · `/apply` (batch)                 | apply crop                                                                                             |
| POST   | `/movies/{id}/remove` · `/movies/{id}/ignore` · `/heal` | remove tag / skip / fix drift                                                                          |
| GET    | `/jobs/{job_id}/events`                                 | **SSE** for a batch job                                                                                |

Mockup verbs `analyze/fix/confirm/skip` → real verbs `detect/apply/remove/ignore`. Trays come from `counts`/`status`, not 5 separate endpoints.

### Subtitles

| Method       | Path                                                                                                                                    | Notes                                                     |
| ------------ | --------------------------------------------------------------------------------------------------------------------------------------- | --------------------------------------------------------- |
| GET          | `/api/media-files/{id}/subtitles`                                                                                                       | track inventory (keyed by **media_file_id**, not film id) |
| POST         | `/api/media-files/{id}/subtitles/scan`                                                                                                  | **inline** forced rescan (not a queued job)               |
| GET          | `/api/media-files/{id}/subtitles/{track}/preview` · `/download`                                                                         |                                                           |
| POST         | `/api/media-files/{id}/subtitle-plans` (201)                                                                                            | removal plan                                              |
| POST         | `/api/movies/{id}/subtitles/inspect`                                                                                                    |                                                           |
| — policies   | `/api/subtitle-policies` CRUD + `/{id}/audit` + `/{id}/apply` (202)                                                                     |
| — generation | GET `/api/subtitle-generators`; POST `/api/media-files/{id}/subtitle-generations` (202) / `/api/movies/{id}/subtitle-generations` (202) |

### Jobs — `/api/media-jobs`

| GET `""?status&operation&limit` → `{ jobs:[…] }` · GET `/{id}` · GET `/{id}/events` (**SSE**) · POST `/{id}/confirm` · POST `/{id}/cancel` · POST `/{id}/restore` · DELETE `/{id}/backup` |

### Other

- `/api/config/pipeline` GET/PUT — pipeline knobs (hot-update).
- `/api/sync/all` POST — trigger Radarr/Sonarr sync.
- `/api/webhooks/{radarr,sonarr,subgen}` — inbound only, not for the UI.
- `/api/test/pipeline/movie/{id}` — dev harness.
- ❌ **No `/api/activity` feed**, ❌ **no `/api/settings`**, ❌ **no `/api/hdr`** — see gaps.

---

## 3. Mockup screen → real endpoint map

| Mockup screen                   | Handoff assumed                          | Reality                                                                         | Status                   |
| ------------------------------- | ---------------------------------------- | ------------------------------------------------------------------------------- | ------------------------ |
| **Dashboard** CPU/GPU/RAM cards | `GET /api/system` + `system.metrics` SSE | `/api/system/status` has **no hardware metrics**                                | ❌ **G1**                |
| Dashboard triage / health       | `/api/system` aggregates                 | derive from `/library`, `/letterbox/status`, `/media-jobs`, `subtitle_coverage` | ⚠️ aggregate client-side |
| Dashboard live jobs             | global `/api/events` SSE                 | `/api/media-jobs` list + per-job `/events`                                      | ⚠️                       |
| **Films / Shows list**          | `/api/films`, rich `Film`                | `/api/library/movies` (sparse, no filters)                                      | ⚠️ **G2**                |
| Film detail · Poster tab        | `/api/pipeline/{film}/candidates`        | run-based: `/pipeline/movie/{id}/run` → `/runs/{run_id}`                        | ⚠️ **G3**                |
| Film detail · Subtitles tab     | `/api/subtitles/inventory/{film}`        | `/api/media-files/{mfid}/subtitles` (needs media_file_id)                       | ⚠️                       |
| Film detail · Letterbox tab     | `/api/letterbox/{film}/…`                | `/api/letterbox/movies/{id}/…`                                                  | ✅ rename                |
| Film detail · Activity tab      | per-film activity                        | `/api/movies/{id}/artwork-events`                                               | ✅                       |
| **Review overlay**              | survivors/rejected + approve/reject      | `/runs/{id}` (`ranked`/`rejected`) + `/api/feedback`                            | ⚠️ **G3**                |
| Rejected grouping               | `OCR / Style / pHash`                    | real buckets `gate / ocr / dedup / errored`                                     | ⚠️                       |
| **Pipeline queue**              | `GET /api/pipeline` pending/decided      | no such list; runs are per-movie                                                | ❌ **G4**                |
| **Taste map**                   | `/api/taste` active publication          | `/api/taste/map` + active-profile exemplar neighbors                            | ✅ canonical authority   |
| **HDR page**                    | `/api/hdr` + distribution                | **does not exist**; HDR not in any read model                                   | ❌ **G5**                |
| **Subtitles** (4 tabs)          | `/api/subtitles/*`                       | media-file + policies + generators routes                                       | ⚠️ remap                 |
| **Letterbox** (5 trays)         | 5 endpoints                              | `/letterbox/candidates` + `/status.counts`                                      | ⚠️                       |
| **Activity feed**               | `/api/activity`                          | **does not exist** (only per-movie artwork-events)                              | ❌ **G6**                |
| **Settings**                    | `/api/settings`                          | only `/api/config/pipeline` (pipeline knobs, not arr URLs/keys)                 | ❌ **G7**                |
| **Real-time**                   | one global `/api/events`                 | **per-run / per-job SSE only**                                                  | ⚠️ **G8**                |
| **Auth**                        | cookie/session, `/login`                 | `X-Api-Key` + SvelteKit proxy                                                   | ⚠️                       |

---

## 4. Real-time / SSE model

There is **no global event firehose**. SSE is scoped to a single run or job, and you only open a stream when you start that work:

- `GET /api/pipeline/runs/{run_id}/events` — one live pipeline run.
- `GET /api/letterbox/jobs/{job_id}/events` — one batch letterbox job.
- `GET /api/media-jobs/{job_id}/events` — one subtitle/media job.

System metrics & job lists are **polled GETs**, not pushed. Plan: poll `/api/system/status` and `/api/media-jobs` on an interval (e.g. 3–5 s); open an `EventSource` only for an in-flight run/job the user is watching.

> `EventSource` can't send headers, so SSE either runs in `DEBUG`/loopback-exempt locally, or the proxy forwards the stream and appends the key (`proxy_buffering off` in nginx, per handoff §10).

---

## 5. Backend gaps — decide before building each slice

These are the deltas where the UI wants data the backend doesn't return. Each needs an explicit decision: **(A)** add it to the backend, or **(B)** drop/restyle the UI element.

| #      | Gap                                                                                  | Affects                            | Suggested call                                                                                                                               |
| ------ | ------------------------------------------------------------------------------------ | ---------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------- |
| **G1** | No CPU/GPU/RAM/disk/uptime telemetry                                                 | Dashboard hero cards               | **Add** `psutil` + `nvidia-smi`/NVML to `/api/system/status` (small, high visual payoff)                                                     |
| **G2** | Library list lacks poster-status, HDR, resolution, genre, letterbox, subtitle-status | Films/Shows list, dashboard triage | **Add** an enriched list (join poster state + media_file resolution/HDR + letterbox state) — otherwise the table degrades to title/year only |
| **G3** | Pipeline is run-based & async, not a static candidates GET                           | Poster tab, Review overlay         | **Adopt the run model in the UI**: trigger run → watch SSE → render `/runs/{id}`. Cache last run per movie                                   |
| **G4** | No pending/decided review-queue list                                                 | Pipeline page                      | **Add** a queue endpoint (movies with un-reviewed latest run) or build from `/api/movies/{id}/runs`                                          |
| **G5** | No HDR read model anywhere                                                           | HDR page, badges                   | **Defer HDR page** until backend exposes HDR (likely from media_file probe). Don't build against a phantom route                             |
| **G6** | No global activity feed                                                              | Activity page                      | **Add** `/api/activity` (union of artwork-events + job events) or defer the page                                                             |
| **G7** | `/api/settings` (arr URLs/keys, languages) absent; only pipeline knobs exist         | Settings page                      | **Add** a settings read/write, or scope the page to `/api/config/pipeline` for v1                                                            |
| **G8** | No global SSE                                                                        | Live everything                    | Use per-run/per-job SSE + polling (§4). Don't build a global event bus client                                                                |

**Build-order implication:** slices that are ✅/⚠️ today (Films library, Letterbox, Subtitles, Taste, Pipeline/Review) can proceed now. Slices gated on ❌ backend work (HDR, Activity, full Settings, Dashboard hero metrics) should either get a small backend PR first or ship as "v1 partial." Start with the green ones to validate the stack, and batch the backend additions (G1, G2, G6, G7) into one focused PR when you reach them.

---

## 6. Frontend client conventions

- One typed module per router under `src/lib/api/` (`system.ts`, `library.ts`, `pipeline.ts`, …). Each function returns a typed result and is called only from `load()` / `+server.ts`.
- Mirror real field names (`orig_filename`, `media_file_id`, `subtitle_coverage`) in TS types — do **not** rename to the mockup's invented fields. Map to display models in the component layer.
- Every list endpoint is `{ total, page, page_size, items }` — build one `Paginated<T>` helper.
- Run/job flows return `{ ...id, events_url }` on 202 — standardize a "start → subscribe → poll fallback" helper.
- Keep a `mock/` adapter behind the same interface so screens build before a backend gap is closed.

---

_Companion to `MARQUEE_HANDOFF.md` (design system, components) and `Marquee.dc.html` (visual reference only — proprietary runtime + synthetic data, not portable)._
