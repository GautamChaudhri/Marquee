# Marquee — Next Steps Handoff

**Status as of 2026-06-16:** Phase-1 security hardening is complete and merged into
`feature-add`. The backend is feature-complete (v2 gauntlet passed). This document is a
self-contained brief for the next agent session(s) to execute the remaining work queue.

---

## What was just completed (do NOT redo)

**Phase-1 security hardening** — all of the following are already implemented and tested:

- `marquee/api/auth.py` — global `require_api_key` FastAPI dependency (Bearer /
  `X-Api-Key` / `?apikey=`; constant-time compare; `DEBUG=true` bypasses; `/health` and
  `/api/webhooks/subgen` exempt; loopback bypass via `AUTH_ALLOW_LOCAL`; fail-closed 503
  when key unset and not in debug)
- Global dep wired into `FastAPI(dependencies=[Depends(require_api_key)])` in `marquee/main.py`
- Docs locked to debug: `docs_url/redoc_url/openapi_url` are `None` unless `DEBUG=true`
- Baseline security headers middleware: `X-Content-Type-Options`, `X-Frame-Options`, `Referrer-Policy`
- Per-endpoint rate limits on expensive ops (pipeline run, letterbox detect/batch, taste
  retrain/map rebuild) via `marquee/api/deps.py:enforce_rate_limit`; skipped in debug
- `marquee/media/binaries.py:safe_media_path()` — rejects non-absolute paths; applied to
  all mkvmerge/mkvpropedit/mp4box/mkvextract call sites
- End-of-options `--` separator added where file is trailing positional (mkvmerge `-J`,
  `build_remove` in matroska adapter)
- `tests/conftest.py` autouse fixture sets `settings.DEBUG=True` session-wide so all
  existing tests pass unchanged
- `tests/test_auth.py` — 10 test cases covering all auth paths
- `.env.example` updated with Security/Auth section; `SECURITY.md` added
- Old `WEBHOOK_TOKEN` bespoke auth removed from `webhooks.py` (global dep covers it now)

Config knobs added to `marquee/config.py` (all read from `.env`):
```
API_KEY=                        # static key; generate with: python -c "import secrets; print(secrets.token_urlsafe(32))"
AUTH_ALLOW_LOCAL=true           # loopback skips key check
RATE_PIPELINE_RUN_SECONDS=15
RATE_LETTERBOX_DETECT_SECONDS=20
RATE_LETTERBOX_BATCH_SECONDS=300
RATE_TASTE_RETRAIN_SECONDS=60
RATE_TASTE_MAP_REBUILD_SECONDS=300
```

---

## Immediate tasks (close the loop on security — ~30 min)

### 1. Update the gauntlet harness to send the API key

The gauntlet test suite at `design/13-gauntlet-prep/` makes HTTP calls to the running
server. When the server runs with `DEBUG=false` and `API_KEY` set, every request needs
the key or it gets a 401. The harness currently sends no auth header.

**What to do:** Find where the gauntlet scripts build their `httpx` / `requests` / `curl`
calls and add `X-Api-Key: <key>` (or `?apikey=<key>`) to all of them. The key value
should come from an env var (e.g. `MARQUEE_API_KEY`) so it isn't hardcoded.

### 2. Set API_KEY in .env and update webhook URLs

In `/forge/Marquee/.env` (do NOT read or commit this file — just instruct the user):
- Generate a key: `python -c "import secrets; print(secrets.token_urlsafe(32))"`
- Add `API_KEY=<generated-key>` to `.env`
- In Radarr: Settings → Connect → Marquee webhook URL → append `?apikey=<key>`
- In Sonarr: same

The server currently starts with the warning:
```
WARNING: No API_KEY set and DEBUG is off — protected endpoints will return 503.
```
Once `API_KEY` is set in `.env` and `DEBUG=false`, that warning disappears and the API
is locked.

---

## Engineering hygiene track (~1–2 days)

### 3. Fix 4 pre-existing test failures

These existed before the security work and were confirmed pre-existing by stash-testing:

| Test | Failure | Root cause |
|------|---------|------------|
| `test_letterbox.py::test_find_candidate_movies_prefilters_radarr_resolutions` | `[6,1,3] != [1,3,6]` | Query returns title-order, test expects id-order |
| `test_path_utils.py::test_logs_warning_when_prefix_mismatches` | `AttributeError` | Unknown — investigate |
| `test_taste_map.py` (2 tests) | Tests expect `pca`, code picks `umap` | UMAP is installed so the auto-selector uses it; tests don't account for this |

Run with: `cd /forge/Marquee && source .venv/bin/activate && pytest --tb=short`

### 4. pip-audit / dependency vulnerability scan

```bash
pip install pip-audit
pip-audit --requirement requirements.txt   # or: pip-audit -r pyproject.toml
```

Fix any HIGH or CRITICAL findings. Add `pip-audit` to CI when CI is set up.

### 5. CI setup (GitHub Actions)

Create `.github/workflows/ci.yml` with:
- Trigger: push + PR to `main`
- Steps: `pip install -e ".[dev]"` → `ruff check marquee tests` → `pytest`
- Skip ML-heavy tests in CI (no GPU): the existing `pytest.ini` / conftest skip markers
  should handle this; verify.
- Optionally: `pip-audit` step as a separate job

### 6. Database backup strategy

**Superseded by `design/14-internal-backup-strategy.md`** — which defines a full
internal backup system (DB snapshot + state archive, REST API, rotation, restore).

The three options originally listed here (cron + sqlite3 `.backup`, WAL checkpoint +
rsync, Litestream) are analyzed and rejected in that document. Summary of why:
- Cron + `.backup`: DB-only, requires external scheduling, no REST API
- WAL checkpoint + rsync: requires NAS/secondary target, not point-in-time atomic
- Litestream: solves a different problem (disaster recovery), DB-only, external sidecar

The chosen approach: in-process `VACUUM INTO` for DB snapshot + gzipped tarball for
managed non-DB state, stored as per-backup directories under `data/backups/`, with
rotation and REST API.

---

## Frontend phase — SvelteKit (the big one, ~weeks)

The chosen stack: **SvelteKit**, served same-origin from the FastAPI process, with
**httpOnly session cookie + CSRF** for human login. Machine/webhook callers continue
using the static API key.

### Architecture decision: same-origin serving

SvelteKit builds to static files. Serve them from FastAPI using `StaticFiles` mount:

```python
# marquee/main.py (add after routers)
from fastapi.staticfiles import StaticFiles
app.mount("/", StaticFiles(directory="frontend/build", html=True), name="frontend")
```

The SvelteKit app is built separately (`npm run build` in `frontend/`) and the output
goes to `frontend/build/`. The API stays at `/api/...` and the UI catches everything
else. Same origin = no CORS issues for cookie-based auth.

### Auth extension point (already designed)

`marquee/api/auth.py` has a comment marking where to add session cookie acceptance:

```python
# extension point: accept session cookie here in frontend phase
```

The plan:
1. Add `POST /api/auth/login` endpoint — accepts `{username, password}`, validates
   against a hashed credential in `.env` or a config file, sets an `httpOnly; SameSite=Strict`
   session cookie containing a signed token (use `itsdangerous.TimestampSigner` or JWT)
2. Add `POST /api/auth/logout` endpoint — clears the cookie
3. In `require_api_key`, before the 401 branch, check for a valid session cookie
4. Add CSRF: SvelteKit sends a `X-CSRF-Token` header; FastAPI validates it matches a
   value stored in a separate non-httpOnly cookie (double-submit cookie pattern)

### SvelteKit project setup

```bash
cd /forge/Marquee
npm create svelte@latest frontend   # choose: SvelteKit app, TypeScript, ESLint+Prettier
cd frontend && npm install
npm run dev    # dev server at :5173 (proxied to FastAPI at :3165 in dev)
```

Configure `svelte.config.js` to use the static adapter for the production build:
```js
import adapter from '@sveltejs/adapter-static';
```

In dev, use `vite.config.ts` proxy to forward `/api` requests to FastAPI:
```ts
server: { proxy: { '/api': 'http://localhost:3165' } }
```

### UI reference

A frontend mockup already exists — ask the user for its location or check `design/` for
any mockup files. The app is image-heavy (movie posters), so the Immich visual pattern
is the closest reference: grid layout, large thumbnails, sidebar filters.

Key UI surfaces to build:
1. Login page (sets the httpOnly session cookie)
2. Library view — movie/series grid with poster thumbnails
3. Pipeline run page — trigger pipeline for a movie, see live SSE progress
4. Letterbox manager — batch detect, review candidates, apply/skip crops
5. Subtitle manager — per-file subtitle inventory, job queue, policy editor
6. Taste editor — upload exemplars, trigger retrain, view k-NN map
7. Config page — wraps `GET/PUT /api/config/pipeline`
8. System page — health, version, connected clients

---

## Deferred security items (low urgency, track separately)

### TLS via tailscale serve (easy, ~5 min)

```bash
tailscale serve https / http://localhost:3165
```

This puts Marquee behind Tailscale's HTTPS reverse proxy. The app stays HTTP internally;
external access (from other tailnet devices) is HTTPS. No cert management needed.

Only do this after verifying the app works correctly behind a proxy (check that
`request.client.host` still resolves correctly for the `AUTH_ALLOW_LOCAL` loopback check
— behind a proxy you may need `X-Forwarded-For` trust config).

### allow_pickle hardening

The old `allow_pickle=True` artifact risk is closed by keeping `.npz` files but normalizing all
live Marquee runtime artifacts to pure numeric/Unicode arrays and `genres_json` rows. Runtime
loads now use `allow_pickle=False`, and known live legacy artifacts auto-migrate in place on
startup or first load. Old `taste_map_history/` snapshots are intentionally left as legacy
archives and should not block the app.

### Full CSP

A `Content-Security-Policy` header with `script-src 'self'` etc. requires knowing the
exact asset origins (SvelteKit chunk hashes, any CDN). Defer until the SvelteKit build
is stable, then add it to the `security_headers` middleware in `marquee/main.py`.

---

## Key files for orientation

| File | Purpose |
|------|---------|
| `marquee/api/auth.py` | Auth dependency — start here for any auth changes |
| `marquee/config.py` | All env-var settings including `API_KEY`, rate-limit knobs |
| `marquee/main.py` | App wiring: global dep, middleware, router includes |
| `marquee/api/deps.py` | `enforce_rate_limit` helper used by expensive endpoints |
| `marquee/media/binaries.py` | `safe_media_path()` + all binary execution |
| `tests/test_auth.py` | Auth test suite |
| `tests/conftest.py` | `_auth_disabled_in_tests` autouse fixture (sets DEBUG=True) |
| `design/10-security-and-hardening.md` | Full threat model and hardening reference |
| `SECURITY.md` | Operator-facing security guide |

## Running the test suite

```bash
cd /forge/Marquee
source .venv/bin/activate
ruff check marquee tests        # must be green
pytest                          # 252 pass, 4 pre-existing failures (see §3 above)
```

The 4 pre-existing failures are known and documented above. Any new failures introduced
by your changes are regressions.

---

## Phase 1 close-out audit (2026-06-17)

### Actual status of 11-next-steps items

| Item | State | Notes |
|------|-------|-------|
| 1. Gauntlet harness sends key | ✅ Done | `experiments/gauntlet_runner.py` now wraps the real runner and sends `X-Api-Key` from `MARQUEE_API_KEY` |
| 2. `API_KEY` in `.env` + webhook URLs | ⚠️ Your action | `.env` is present; cannot read/confirm |
| 3. Fix 4 pre-existing test failures | ⚠️ Verify | Run `pytest` to confirm |
| 4. pip-audit | ⚠️ Verify | No lockfile evidence yet |
| 5. CI (GitHub Actions) | ✅ Done | `.github/workflows/ci.yml` exists |
| 6. Backup strategy | ✅ Done | `marquee/core/backup.py` + `marquee/api/routes/backup.py` + design 14 |
| Pickle hardening | ✅ Done | `marquee/ml/artifact_codec.py` — all live reads use `allow_pickle=False`; `=True` only inside the legacy migration reader |
| Docker | ✅ Already scaffolded | `docker/Dockerfile` + `docker/docker-compose.yml` with cpu/nvidia/intel profiles |

---

## Big Task: API security audit & hardening

### Already correct (do NOT re-do)

- **No endpoint serializes the secret-bearing `settings`.** Radarr/Sonarr URLs+keys,
  TMDB/Fanart/TVDB tokens live in `Settings` (`marquee/config.py`) and are **never** returned
  from any route. `GET /api/config/pipeline` only dumps `PipelineSettings` (a separate model:
  gate thresholds, scorer weights, model paths). The one `settings.RADARR_URL` reference in
  routes (`sync.py:53`) is a log string, not a response. This is exactly the Huntarr mistake
  avoided — a `curl` to any endpoint cannot leak a connected-service URL or key.

- **Global auth on every router** — `dependencies=[Depends(require_api_key)]` on the `FastAPI`
  app (`main.py:248`), inherited by all 18 routers. Exempt set is **exact-match** (`/health`,
  `/api/webhooks/subgen`), so no prefix-bypass attack. `subgen` has its own
  `SUBGEN_CALLBACK_TOKEN`. Constant-time compare. Fail-closed 503 when key is unset. Clean.

- **File-serving endpoints are traversal-safe.** Every `FileResponse` resolves from a *recorded
  database record* (not the user-supplied string) and confines with `resolve()` + `startswith`:
  - Pipeline posters → `_EXPERIMENTS_DATA` (`pipeline.py:168`)
  - Letterbox previews → `letterbox_preview_path` (`letterbox.py:609`)
  - Subtitle downloads → media file's own directory (`subtitles.py:128`)
  - Taste exemplars → `_safe_exemplar_name()` sanitizer (`taste.py:478`)

### Things to implement before frontend

**1. Global exception handler** — two spots return raw exception strings today:
- `PUT /api/config/pipeline` → `detail=f"Invalid configuration: {exc}"` (`config.py:103`)
- Unknown-key echo returns field names (`config.py:86`)

These leak internal structure, not secrets — low severity, but a `@app.exception_handler(Exception)`
that logs full detail server-side and returns `{"detail":"Internal error"}` makes this airtight
and gives one logging chokepoint. Add it to `marquee/main.py`.

**2. Strip `?apikey=` from uvicorn access logs** — `log_requests` middleware logs
`request.url.path` only (not query string), which is correct. But **uvicorn's own access logger**
logs the full URL including `?apikey=`. Deploy with `--no-access-log` (the custom middleware
already covers it) or the key ends up in your log files.

**3. Request body size cap** — no limit today; a multi-GB POST to any endpoint is an easy DoS.
Add a small middleware rejecting `Content-Length > 10_485_760` (10 MB). Explicitly deferred from
Phase 1, now due.

```python
# In marquee/main.py — add before the router includes
@app.middleware("http")
async def limit_body_size(request: Request, call_next):
    max_bytes = 10 * 1024 * 1024  # 10 MB
    content_length = request.headers.get("content-length")
    if content_length and int(content_length) > max_bytes:
        return Response(status_code=413, content='{"detail":"Request body too large"}',
                        media_type="application/json")
    return await call_next(request)
```

**4. CORS lockdown for cookie phase** — current `CORSMiddleware` uses `allow_methods=["*"]`
and `allow_headers=["*"]`. When httpOnly-cookie login is added, browsers reject `*` with
credentials. Plan: gate CORS on `DEBUG` for dev server only; same-origin serving in prod means
CORS is irrelevant there. Add `allow_credentials=True` and narrow `allow_methods` when enabling
cookie auth.

**5. Verify subtitle preview path confinement** — `GET .../subtitles/{track_id}/preview` calls
`service.text_preview(track.external_path)` (`subtitles.py:99`). The download path re-verifies
against the media file's directory; confirm `text_preview` does the same or refuses non-`data/`
paths.

**6. Brute-force throttle on auth failures** — 401 is cheap to spam. Low urgency given
LAN/Tailscale exposure, but can add a per-IP counter to the existing `RateLimiter` in
`marquee/core/rate_limit.py`.

### API versioning decision

Do **not** add a `/v1/` prefix to the UI-facing API. Frontend and backend ship in the same repo
and deploy together — they're always in lockstep. Instead:

- **Version the whole app** (`__version__` already exists) — expose it at
  `GET /api/system/status` so the UI can assert compatibility.
- **Freeze the webhook contract** — `/api/webhooks/radarr`, `/api/webhooks/sonarr`, and
  `/api/webhooks/subgen` are configured inside *arr. Treat these three paths as a stable,
  never-rename contract. Everything else is internal.
- **Export OpenAPI as a contract doc** — since docs are `DEBUG`-only, add a script/CI step
  that dumps `app.openapi()` to `design/api-schema.json` for a reviewable, diffable API spec
  without exposing it in prod.
- If the API is ever opened to third-party clients, introduce `/api/v1/` via a parent router
  prefix (a one-line change). Not now.

---

## Big Task: Directory cleanup & Docker

### Current Status

The cleanup is implemented in the codebase:

- Root `.dockerignore` excludes the repo-heavy build context junk.
- The Dockerfile no longer declares an orphan `/config` volume.
- Compose no longer mounts a stale taste-profile file directly.
- Live pipeline working output now writes to `data/runs/work/`.
- `data/` has tracked `.gitkeep` placeholders for the runtime directories.

Operational verification still matters:

- Build the CPU/NVIDIA/Intel images from `docker/docker-compose.yml`.
- Confirm `GET /api/system/status` reports `ocr.paddle_cuda_available: true` on NVIDIA.
- Confirm `GET /api/config/pipeline` still reports the expected execution provider.
- Keep historical `experiments/` output in place for now; it is no longer part of the live runtime path.

---

## Remaining items before starting the frontend

1. Global exception handler + body-size-cap middleware + uvicorn `--no-access-log` — closes
   API hardening gaps.
2. Build & GPU-test the nvidia image; verify `GET /api/system/status` shows
   `paddle_cuda_available: true`.
3. Run `pytest` + `pip-audit` to close the verification items.
4. Lock the login/CSRF design (httpOnly `SameSite=Strict` cookie, double-submit CSRF, 
   `POST /api/auth/login` + `POST /api/auth/logout`, extension point already in
   `marquee/api/auth.py`) — **then start the frontend.**

### TLS via tailscale serve — one known gotcha

When `tailscale serve https / http://localhost:3165` is enabled, uvicorn sees the proxy as the
client, not the real caller. The `AUTH_ALLOW_LOCAL` loopback check (`auth.py:58`) uses
`request.client.host` — behind Tailscale's proxy this will be `127.0.0.1` (the proxy), making
the loopback bypass fire for *all* Tailscale requests. Before enabling TLS proxy:
- Run uvicorn with `--proxy-headers --forwarded-allow-ips 127.0.0.1`
- Or disable `AUTH_ALLOW_LOCAL` when behind a proxy (`AUTH_ALLOW_LOCAL=false` in `.env`)
