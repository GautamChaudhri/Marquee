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

`data/marquee.db` is the only stateful artifact. No DB = no letterbox state, no subtitle
job history, no taste feedback, no pipeline run history.

Options (pick one):
- **Cron + sqlite3 `.backup`**: `sqlite3 data/marquee.db ".backup data/backups/marquee-$(date +%Y%m%d).db"` daily via cron or systemd timer
- **WAL checkpoint + rsync**: checkpoint first (`PRAGMA wal_checkpoint(TRUNCATE)`), then rsync to NAS
- **Litestream** (streaming replication to S3/local): zero-RPO, runs as a sidecar

The `data/` directory already has `.gitkeep` so it's tracked but contents are gitignored.
Backups should go somewhere outside the repo.

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

### allow_pickle → safetensors migration (risky, track separately)

`marquee/ml/` loads taste profile artifacts with `np.load(..., allow_pickle=True)`.
`allow_pickle=True` executes arbitrary Python on load — if an attacker can write to
`marquee/ml/models/`, they get RCE.

Current mitigation: filesystem permissions (model dir should be writable only by the
service user). The full fix is migrating to `safetensors` format, but this:
- Requires regenerating all existing taste artifacts
- May require changes to `taste_store.py`, `calibration.py`, `learned_head.py`
- Should be its own isolated PR with artifact regeneration instructions

Do NOT mix this with other changes.

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
