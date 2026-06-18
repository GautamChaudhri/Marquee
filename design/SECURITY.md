# Security

Marquee is a self-hosted service meant to run on a **trusted network** (a home LAN
or a private VPN such as Tailscale), not exposed directly to the public internet.

## Authentication

Every API endpoint requires a static API key **except** `GET /health`. Provide the
key on each request in any one of three ways:

- `Authorization: Bearer <key>`
- `X-Api-Key: <key>`
- `?apikey=<key>` (query string — used by webhook URLs)

Set it in `.env`:

```
API_KEY=<a long random string>
```

Generate one with:

```
python -c "import secrets; print(secrets.token_urlsafe(32))"
```

### Behaviour

| Condition | Result |
|---|---|
| `DEBUG=true` | Auth **and** rate limits disabled, `/docs` served — **local dev only** |
| `DEBUG=false`, `API_KEY` set | Key required (loopback may be exempt) |
| `DEBUG=false`, `API_KEY` unset | Protected endpoints return `503` (fail closed) |

`AUTH_ALLOW_LOCAL=true` (default) lets requests from `127.0.0.1`/`::1` skip the key
so same-host tooling works. Tailscale (`100.64.0.0/10`) and LAN addresses always
need the key. Set `AUTH_ALLOW_LOCAL=false` to require it even from localhost.

> **Never run with `DEBUG=true` on a reachable host** — it disables authentication
> entirely. A warning is logged at startup when DEBUG is on.

## Webhooks

Radarr/Sonarr must carry the key in the webhook URL:

```
http://<host>:3165/api/webhooks/radarr?apikey=<API_KEY>
```

The Subgen callback (`/api/webhooks/subgen`) is authenticated by its own
`SUBGEN_CALLBACK_TOKEN` (set it when enabling Subgen) and is exempt from the
global key.

## Transport (HTTPS)

The app speaks plain HTTP; terminate TLS in front of it. On a Tailscale tailnet:

```
tailscale serve https / http://localhost:3165
```

gives a valid certificate for `<host>.<tailnet>.ts.net` with no public exposure.
Otherwise put a reverse proxy (Caddy/nginx/Traefik) in front.

## Stable API contracts

The following webhook paths are **frozen** — they are configured directly inside Radarr and
Sonarr and cannot be renamed without requiring all operators to update their *arr settings:

- `POST /api/webhooks/radarr`
- `POST /api/webhooks/sonarr`
- `POST /api/webhooks/subgen` (authenticated by `SUBGEN_CALLBACK_TOKEN`, exempt from global key)

All other `/api/...` routes may change between versions. The committed
`design/api-schema.json` (regenerate with `python scripts/export_openapi.py`) is the
reviewable, diffable contract for the full API surface.

## Running the server

**Always pass `--no-access-log`** when Marquee is started with a real `API_KEY` set. The
custom request logger (`log_requests` middleware) records method + path only, but uvicorn's
own access log includes the full URL — which leaks `?apikey=<key>` to disk.

```bash
# Production / Docker (--no-access-log is already in the Dockerfile CMD)
uvicorn marquee.main:app --host 0.0.0.0 --port 3165 --no-access-log

# Dev (DEBUG=true → key not in use, but good habit)
uvicorn marquee.main:app --reload --no-access-log
```

## Secret hygiene

- `.env` holds your TMDB/Radarr/Sonarr keys. Keep it readable only by the service
  user (`chmod 600 .env`). It is gitignored — never commit it.
- API keys are never written to logs (the request logger records only the path).
- After 10 failed authentication attempts from the same IP within 60 seconds, that
  IP is locked out for 5 minutes (`AUTH_BRUTE_LOCKOUT_ATTEMPTS` / `AUTH_BRUTE_WINDOW_SECONDS`
  / `AUTH_BRUTE_LOCKOUT_SECONDS` are all configurable).

## Model artifacts

The taste profile and learned-head files are loaded with NumPy's pickle support,
which executes code on load. Only the service user should be able to write
`marquee/ml/models/` and the taste artifacts, and you should never load a profile
from an untrusted source. (A migration to a non-executable format is tracked
separately.)

## Frontend (planned)

The web UI will be served **same-origin** and authenticate humans with an
`HttpOnly`, `SameSite` session cookie plus CSRF protection; the API key remains for
machine/webhook callers.
