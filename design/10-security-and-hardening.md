# Marquee — Security & Hardening

**Status:** Drafted 2026-06-16. Security is the active focus now that the backend is
feature-complete and the v2 gauntlet has passed (500/507 expected outcomes). This document
is a **reference + roadmap** — a record of the threat review, the protection plan, the
self-hosted auth model we intend to copy, and the frontend/hygiene considerations that
follow. **Nothing here is implemented yet.**

Deployment context at time of writing: the app binds `0.0.0.0:3165` and is reachable over
the **home LAN + a Tailscale VPN**, **not** the public internet.

---

## 0. Purpose & scope

Marquee is a self-hosted FastAPI service over async SQLite that fetches movie posters,
runs an ML selection pipeline, and (importantly) **mutates the user's media files**
(letterbox crop-tags, subtitle remux/remove) and its own pipeline config. This doc answers:

1. How should we *think* about security for an app like this? (threat modeling)
2. What concrete threats does *this codebase* face, and what's already defended?
3. How do popular self-hosted apps (Radarr/Sonarr/etc.) handle auth + TLS, and what should we copy?
4. What's the prioritized protection roadmap, and the concrete Phase-1 plan?
5. What does the frontend choice have to do with security?
6. What other "now that the backend is done" work belongs at this stage?

---

## 1. How to think about this — the threat-modeling mental model

Security is a habit of asking four questions, not a checklist. These are the questions to
ask of every feature from now on:

1. **What am I protecting?** (*assets*) — the **media files** (the app can write to them),
   the **Radarr/Sonarr admin API keys** (full control of those apps), the **library DB**,
   and the **compute** (GPU).
2. **Who can reach it, and do I trust them?** (*trust boundary / exposure*) — the master
   variable. Draw a line around "things I control"; every arrow crossing it is attack surface.
3. **What can they do once across the line?** (*attack surface × privilege*) — today: anything,
   because there's no authentication.
4. **What's the blast radius if it goes wrong?** (*impact*) — media corruption, key theft →
   *arr takeover, or GPU pegged at 100%.

For each risk, choose among **prevent / limit / detect / recover**, guided by two principles:
**defense in depth** (don't rely on one wall) and **least privilege** (give every actor the
minimum it needs). When in doubt, **fail closed**.

---

## 2. Current exposure

| Fact | Value | Source |
|---|---|---|
| Bind address | `0.0.0.0` (all interfaces) | `marquee/config.py:28` |
| Port | `3165` | `marquee/config.py:29` |
| Transport | plain HTTP (no TLS in-app) | — |
| Reachable from | home LAN + Tailscale tailnet; **not** the internet | operator |
| Authentication | **none** on all 69 endpoints (one optional webhook token) | see §4 T1 |

Realistic adversary for this posture: a compromised or untrusted device on the **LAN or
tailnet** — not the open internet. That lowers urgency but does **not** remove the need for
app-level auth (defense in depth + protection against accidental future exposure).

---

## 3. What's already defended (do not re-solve these)

The *injection* classes are handled well. Recognize these patterns; keep using them:

- **No SQL injection** — every query uses parameterized SQLAlchemy `select().where(col == value)`.
  The only raw SQL is a static health probe, `text("SELECT 1")` at `marquee/main.py:295`.
- **No command injection** — every external tool call uses `create_subprocess_exec` /
  `subprocess.run` with a **constructed argument list, never `shell=True`**:
  `marquee/media/binaries.py:100`, `marquee/core/subtitles/mutation.py:230`,
  `marquee/core/media_jobs/handlers.py:64`, `marquee/media/letterbox_manager.py:211`.
- **Path traversal defended on every file-serving route** (pattern: *canonicalize, then verify
  the prefix*):
  - `marquee/api/routes/pipeline.py:160-164` — resolve from the recorded record, 403 if outside the runs tree.
  - `marquee/api/routes/taste.py:237-240` — `_safe_exemplar_name` rejects `/`, `\`, `..`.
  - `marquee/api/routes/letterbox.py:595-597` — confine to the preview-cache tree.
  - `marquee/api/routes/subtitles.py:128-129` — confine to the media file's own directory.
- **Symlink-aware root validation** — `effective_media_roots` resolves real paths so symlink
  aliases can't defeat the check (`marquee/config.py:264`); `safe_translate_and_validate` for arr paths.
- **No stack-trace leakage** — `DEBUG=False` by default (`marquee/config.py:30`).
- **No secrets exposed via the API** — `GET /api/system/status` returns only operational
  stats (`marquee/api/routes/system.py:50`); `GET /api/config/pipeline` returns only pipeline
  knobs, not the `Settings` that hold API keys (`marquee/api/routes/config.py:63`).

**Conclusion:** the gap is the **perimeter** (authentication, abuse-resistance, secret
hygiene), not the plumbing.

---

## 4. Threats, ranked

### T1 — No authentication on any endpoint. **(Critical)**
All 69 routes are open. The app is constructed as `FastAPI(title=…, version=…, lifespan=…)`
with **no** `dependencies=[…]` (`marquee/main.py:205`), and routers are included without
auth dependencies (`marquee/main.py:261-276`). Every `Depends()` in the codebase is resource
injection (`get_db`, `get_tmdb`, `get_radarr`), not auth. The **only** credential check is the
*optional* `WEBHOOK_TOKEN` on the 3 webhook routes (`marquee/api/routes/webhooks.py:64`,
default off).

Why critical (not just untidy): several endpoints **mutate real files/config**:
- `POST /api/letterbox/.../apply` → writes crop tags into MKV files via mkvpropedit.
- `POST /api/media-files/{id}/subtitle-plans` → confirm → remuxes/removes subtitle tracks in media files (`marquee/api/routes/subtitles.py:147`).
- `PUT /api/config/pipeline` → rewrites + persists scoring weights/gates (`marquee/api/routes/config.py:77`).
- `POST /api/pipeline/movie/{id}/run`, `/api/taste/retrain`, `/api/letterbox/detect` → expensive GPU/CPU jobs.

**Blast radius:** anyone who can route to the port can read the whole library, peg the GPU,
rewrite pipeline config, and **corrupt media files**.
**Fix:** API-key auth gate (see §5–§7).

### T2 — Binds all interfaces, plain HTTP. **(High — exposure-dependent)**
`HOST="0.0.0.0"` (`marquee/config.py:28`); TLS belongs at a reverse proxy we don't have yet.
Severity scales with who can route to the box. **Fix:** app-level auth + TLS via
`tailscale serve` or a reverse proxy; optionally bind `127.0.0.1` when fronted.

### T3 — Permissive CORS, and CORS ≠ access control. **(Teaching point / Low)**
`allow_origins` is the dev list with `allow_methods=["*"]`, `allow_headers=["*"]`
(`marquee/main.py:216`, `marquee/config.py:84`). **CORS only controls whether one website's
browser JavaScript may read responses from another origin — it does nothing against curl,
scripts, or non-browser clients.** Never treat it as API auth. **Fix:** restrict to the real
UI origin; default same-origin; only enable `allow_credentials=True` deliberately alongside
cookie auth (never with `*`).

### T4 — No rate limiting on expensive endpoints. **(Medium — DoS)**
A `RateLimiter` exists (`marquee/core/rate_limit.py`) but is wired only to `POST /api/sync/all`
(`marquee/api/routes/sync.py:37`). Pipeline run, batch letterbox detect, retrain, and map
rebuild are unthrottled → easy resource exhaustion (even accidental). **Fix:** reuse the
limiter on those routes; return 429 with `remaining()`.

### T5 — Secret management at rest. **(Medium — host level)**
No endpoint leaks keys (good — see §3). The risk is on disk: TMDB/Radarr/Sonarr/Fanart/TVDB
keys live in `.env` (`marquee/config.py:100,112,124,140,158`), which is currently
**group-readable by `builders`**. Anyone in that group can read *arr admin keys → take over
Radarr/Sonarr. **Fix:** `.env` readable only by the service user (`chmod 600`, revisit the
group-readable change), confirm gitignored, never log secrets, provide a `.env.example`.

### T6 — Untrusted deserialization of model artifacts. **(Medium — supply chain / local trust)**
Many loaders use `np.load(..., allow_pickle=True)` — taste profile, learned head, taste map,
zeroshot axes: `marquee/ml/taste_store.py:107`, `learned_head.py:170`,
`taste_map.py:128/215/228/320`, `zeroshot.py:103`, `profile_updater.py:71`. `allow_pickle=True`
**executes whatever Python is pickled inside the file on load** → arbitrary code execution if
an attacker can replace the file, or if a taste profile is ever loaded from an untrusted source.
(The feature *cache* correctly uses `allow_pickle=False`: `marquee/pipeline/features.py:85,545`.)
**Fix:** treat `marquee/ml/models/` + taste artifacts as a trust boundary (only the service
user writes them); never load artifacts from untrusted sources; longer term, store as pure
arrays and flip to `allow_pickle=False`. *(Deferred — needs an artifact-format change.)*

### T7 — Interactive API docs are open. **(Low)**
FastAPI serves `/docs`, `/redoc`, `/openapi.json` unauthenticated by default — a full
browsable map of all endpoints. **Fix:** gate behind the API key or disable when `not DEBUG`.

### T8 — Argument injection into media tools. **(Low)**
Subprocess use is otherwise exemplary (T-§3). Residual nit: file paths are passed positionally
to ffmpeg/mkvpropedit; a path starting with `-` could be misread as a flag. Arr paths are
absolute (`/…`), so mostly theoretical. **Fix:** add a `--` end-of-options separator before
positional file args where the tool supports it.

### SSRF note. **(Low — contextual, future)**
The app fetches images from URLs (TMDB; `poster_source_url` fallback in restore). If a URL
field ever becomes attacker-influenced (e.g., a new poster source), validate it so the server
can't be coerced into requesting internal addresses. Today the URLs come from TMDB/the DB, so
risk is low.

---

## 5. How self-hosted apps handle auth & TLS (the model we copy)

Radarr/Sonarr (and the wider ecosystem) ship **two credentials, not one**:

1. **An API key** — a long random string auto-generated on first run, sent as an `X-Api-Key`
   header (or `?apikey=`). For *machines*: Prowlarr→Radarr, Overseerr→Radarr, scripts, **webhooks**.
2. **A login** for *humans* — Settings → Security → Authentication: *None / Basic / Forms*,
   plus **"Authentication Required: Disabled for Local Addresses"** (require login from outside,
   skip on LAN). That toggle maps directly onto our LAN+Tailscale situation.

They became **stricter over time**: because so many instances were exposed unauthenticated,
newer Radarr/Sonarr **refuse to start without auth configured**. Lesson: don't rely on
"nobody will find it."

**Why no HTTPS out of the box:** TLS needs a certificate bound to a hostname, and a *trusted*
cert needs a public domain + a CA (Let's Encrypt). At install time the app doesn't know your
hostname, can't get a public cert for an internal name, and self-signed certs throw browser
warnings + break API clients. So the convention is **"app speaks HTTP; TLS is somebody else's
job"** — a reverse proxy (Caddy = automatic Let's Encrypt in ~3 lines; nginx/Traefik), the
private network (Tailscale/WireGuard are already encrypted), or plain HTTP on a trusted LAN.

**Tailscale bonus (fits our setup):** `tailscale serve https / http://localhost:3165` issues a
valid Let's Encrypt cert for `machine.tailnet.ts.net` and terminates TLS — real HTTPS over the
VPN, no public exposure, no cert warnings.

**What this means for Marquee:** copy the *arr model — **API key for machine/webhook calls now**,
designed so a **session login** for the web UI slots in later; keep HTTP locally; optionally
`tailscale serve` for HTTPS over the VPN.

---

## 6. Protection roadmap (prioritized)

- **P0 — Authentication gate.** One auth dependency applied globally; `/health` exempt.
  Recommended: in-app **static API key** (the *arr model). Alternative: offload to a
  reverse-proxy/Authelia forward-auth.
- **P0/P1 — Shrink the perimeter.** TLS via `tailscale serve` (or a reverse proxy); optionally
  bind `127.0.0.1` when fronted. Keep the private network as a second layer.
- **P1 — Lock CORS** to the real UI origin (default same-origin); **rate-limit** expensive
  endpoints; **fix `.env` permissions**.
- **P2 — Gate `/docs`**, add `--` to media-tool args, plan the `allow_pickle` migration.

---

## 7. Phase-1 hardening plan (concrete)

> Mirrors the working plan from this session; the file targets below are the intended edits.

**1. API-key authentication layer (core)**
- New `marquee/api/auth.py` — `require_api_key(request)` dependency: accept key via
  `X-Api-Key`, `Authorization: Bearer <key>`, or `?apikey=`; constant-time compare
  (`secrets.compare_digest`); **fail closed** (401) when a key is configured and missing/wrong;
  exempt `/health` (and `/docs*` if gating vs. disabling); honor `AUTH_ALLOW_LOCAL`.
- `marquee/config.py` — add `API_KEY: str | None`, `AUTH_ALLOW_LOCAL: bool = True`
  (skip auth for loopback so local tooling + the gauntlet work; **recommend loopback only —
  require the key over the tailnet**, since Tailscale `100.64.0.0/10` IPs are not loopback);
  default `CORS_ORIGINS=[]` (same-origin).
- `marquee/main.py` — wire globally via `FastAPI(..., dependencies=[Depends(require_api_key)])`.
- `marquee/api/routes/webhooks.py` — replace bespoke `WEBHOOK_TOKEN`/`_check_token` with the
  unified key (keep `WEBHOOK_TOKEN` as an accepted alias); keep the `Test` event reachable.

**2. Lock down interactive docs** — keep `/docs`,`/redoc`,`/openapi.json` behind the key
(default) or disable when `not DEBUG`.

**3. Rate-limit expensive endpoints** — reuse `RateLimiter` (shared instance on `app.state`),
guard `POST /api/pipeline/movie/{id}/run`, `POST /api/test/pipeline/...`,
`POST /api/letterbox/detect`, `POST /api/taste/retrain`, `POST /api/taste/map/rebuild`;
return 429 with `remaining()`.

**4. Secret hygiene** — add `.env.example` (keys, no values); confirm `.env` gitignored;
`chmod 600` owned by the service user; **revert the `builders` group-readable change**; keep
the request-logging middleware logging only `request.url.path` (never the query string, so
`?apikey=` is never logged — currently correct at `marquee/main.py:225-237`).

**5. TLS over Tailscale (ops/docs)** — document `tailscale serve`; `HOST` stays configurable.

**6. Low-severity (same PR or fast follow)** — `--` end-of-options before file args in
ffmpeg/mkvpropedit/ffprobe calls; track the `allow_pickle` migration as its own work item.

**Files:** new `marquee/api/auth.py`, `.env.example`; edit `marquee/main.py`,
`marquee/config.py`, `marquee/api/routes/webhooks.py`, and add rate-limit wiring to
`pipeline.py`, `test_pipeline.py`, `letterbox.py`, `taste.py`; tests in new `tests/test_auth.py`
+ update `tests/conftest.py` and the gauntlet harness to send the key.

**Verification:** `ruff check marquee tests` (the lint gate) + `pytest` green; `curl :3165/health`
→ 200 no key; `/api/library/movies` → 401, then 200 with `X-Api-Key`; webhook `?apikey=` accepted;
second rapid pipeline run → 429; logs never show the key.

**Decisions to confirm before building:**
1. In-app static API key now (session login deferred to frontend) vs. reverse-proxy forward-auth.
2. `AUTH_ALLOW_LOCAL` scope: loopback only (recommended) vs. also treat Tailscale `100.64/10` as local.
3. Revert the `.env` `builders` group-readable change (recommended yes).

---

## 8. Frontend stack & its security

The backend is a clean JSON API, so all options are open. Three architectures:
**(A) SPA** (React/Svelte/Vue via Vite) — browser bundle calls `/api`.
**(B) Meta-framework w/ SSR** (SvelteKit/Next/Nuxt) — server can render + hold sessions.
**(C) Server-rendered HTML** (FastAPI + Jinja2, optionally **HTMX**) — stay in Python.

| Option | Learning curve | Pros | Cons | Security posture | Self-hosted users |
|---|---|---|---|---|---|
| **SvelteKit** (recommended) | Gentlest JS | Tiny bundles (good for image grids), least boilerplate, built-in SSR + cookie/session | Smaller ecosystem, fewer ready components | Auto-escapes; SSR makes httpOnly-cookie + CSRF natural; `{@html}` is the only footgun | **Immich** (image-heavy, like Marquee) |
| **React + Vite / Next.js** | Steeper | Biggest ecosystem (shadcn/ui, MUI…), endless tutorials, matches the *arr stack | More boilerplate, larger bundles, decision fatigue | Auto-escapes; `dangerouslySetInnerHTML` footgun; SPA nudges toward token auth unless same-origin+cookies | **Radarr, Sonarr, Prowlarr, Jellyfin, Overseerr**; **Bazarr** (React on a *Python* backend — closest analog) |
| **HTMX + FastAPI/Jinja2** | Lowest (if you know Python/HTML) | One language, no build step, smallest attack surface, auth = same FastAPI dependency | Rich/dynamic UIs (taste-map drag, live grids) get clunky; round-trip per interaction | **Best of the three** — minimal JS → tiny XSS surface; server holds state + session | **Tautulli** (Python, server-rendered), SABnzbd; HTMX is the modern revival |
| **Vue/Nuxt** | Middle | Friendly docs, decent ecosystem | Less momentum for new projects | Same as other SPAs | **Nextcloud** (Vue); Paperless-ngx (Angular) |

**Why the frontend is a security decision:** the choice that matters isn't the framework, it's
**(1) serve the UI same-origin with the API** (eliminates CORS, makes cookies trivial) and
**(2) put the human credential in an httpOnly session cookie** (unreadable by JS → survives XSS),
with a **CSRF** token on state-changing requests. With that, React/Svelte/HTMX are roughly
equally safe; XSS hygiene (no `dangerouslySetInnerHTML` / `{@html}`) + a CSP header at the proxy
covers the rest.

**Recommendation for this project** (no FE experience, security-first, image-heavy, Python backend):
- **SvelteKit** for a rich UI — Immich proves it for image-heavy self-hosted; least boilerplate.
- **HTMX + Jinja** to stay in Python and keep the attack surface minimal — auth becomes the same
  dependency we're building; tradeoff is a less "appy" feel on the dynamic screens.
- **React** only to match the *arr/Bazarr ecosystem or use a big component library.

Auth is designed (§7) to support whichever is chosen: API key for machines now; add a cookie
session for the human UI in the frontend phase.

---

## 9. Beyond security — engineering hygiene at this stage

Now that the backend is solid, the "make it durable" work:

- **Tests + CI** — wire `ruff check` + `pytest` to run on every change (GitHub Actions or
  pre-commit). Codify a subset of the gauntlet as an integration suite so regressions are caught.
- **Dependency vulnerability scanning** — `pip-audit` and/or Dependabot over the heavy ML stack;
  keep versions pinned (cu12 torch is already pinned).
- **Backups + migrations** — Alembic is baselined; establish a **backup cadence for
  `data/marquee.db` and a tested restore**. Confirm SQLite WAL mode + the concurrency story
  under the job workers.
- **Observability** — flip `LOG_FORMAT=json` for structured logs; add light metrics for the job
  queue + pipeline runs; decide on error tracking.
- **API contract** — resolve the known-missing routes (poster-restore endpoint, library filters)
  as documented 404s or implement them; consider versioning (`/api/v1`) **before** a UI hardcodes
  paths; adopt a consistent error envelope.
- **Deploy ergonomics** — `.env.example`, documented required env, Docker packaging (per the
  deployment docs).

---

## 10. Open decisions (carry forward)

1. **Auth mechanism:** in-app static API key (recommended) vs. reverse-proxy/Authelia forward-auth.
2. **`AUTH_ALLOW_LOCAL` scope:** loopback only (recommended) vs. also trust Tailscale `100.64/10`.
3. **`.env` permissions:** revert the `builders` group-readable change (recommended yes).
4. **Frontend stack:** SvelteKit vs. HTMX vs. React (leaning SvelteKit / HTMX).
5. **Sequencing:** security hardening first (chosen), then hygiene, then frontend.
