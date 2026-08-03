"""Marquee — AI-Powered Media Artwork Manager.

FastAPI application entry point. Sets up logging, manages client lifecycles,
registers middleware and routers, and exposes health/readiness endpoints.
"""

from __future__ import annotations

import asyncio
import time
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from marquee import __version__
from marquee.api.auth import require_api_key
from marquee.api.request_limits import RequestBodyLimitMiddleware
from marquee.config import settings
from marquee.core.rate_limit import RateLimiter
from marquee.database import close_db, init_db
from marquee.logging import setup_logging

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
setup_logging(level=settings.LOG_LEVEL, fmt=settings.LOG_FORMAT)

import logging  # noqa: E402 — must be after setup_logging

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Rate Limiter (module-level — shared across requests)
# ---------------------------------------------------------------------------
# Shared limiter for expensive endpoints; callers pass an explicit per-op cooldown.
_op_rate_limiter = RateLimiter()


# ---------------------------------------------------------------------------
# Application Lifespan
# ---------------------------------------------------------------------------


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup and shutdown lifecycle.

    Startup:
        - Initialise external API clients (Radarr, Sonarr, TMDB).
        - Create database tables.
        - Store clients on ``app.state`` for route-level access.

    Shutdown:
        - Disconnect all clients with a configurable timeout.
        - Dispose of the database engine.
    """
    # ── STARTUP ──────────────────────────────────────────────────────
    logger.info("Starting %s v%s", settings.APP_NAME, __version__)
    logger.info("Debug: %s  |  Log level: %s", settings.DEBUG, settings.LOG_LEVEL)
    if settings.DEBUG:
        logger.warning(
            "DEBUG is on: API authentication and rate limits are DISABLED. "
            "Never run with DEBUG=true on a reachable host."
        )
    elif not settings.API_KEY:
        logger.warning(
            "No API_KEY set and DEBUG is off — protected endpoints will return 503. "
            "Set API_KEY in .env to enable the API."
        )
    else:
        logger.info("API-key authentication enabled.")

    # Bootstrap the database and configuration authorities before constructing
    # any client whose URL or credential may be managed from Settings.
    logger.info("Initialising database ...")
    await init_db()
    from marquee.core.configuration_cache import configuration_provider
    from marquee.core.integration_settings import read_integration_settings_snapshot
    from marquee.core.integration_urls import normalize_integration_url
    from marquee.core.jobs.event_stream import job_event_tailer
    from marquee.core.managed_secrets import managed_secret_provider
    from marquee.core.runtime_settings import effective_app_settings, initialize_effective_settings
    from marquee.database import session_factory

    await configuration_provider.start(role="api")
    await managed_secret_provider.start()
    initialize_effective_settings()
    app_settings = effective_app_settings()
    setup_logging(app_settings.LOG_LEVEL, app_settings.LOG_FORMAT)
    app.title = app_settings.APP_NAME
    async with session_factory()() as snapshot_session:
        integration_snapshot = await read_integration_settings_snapshot(snapshot_session)
    radarr_settings = integration_snapshot.for_provider("radarr")
    sonarr_settings = integration_snapshot.for_provider("sonarr")
    tmdb_settings = integration_snapshot.for_provider("tmdb")

    # Radarr
    if radarr_settings.url and radarr_settings.credential:
        from marquee.core.arr_clients.radarr_client import RadarrClient

        radarr_url = normalize_integration_url(radarr_settings.url)
        app.state.radarr_client = RadarrClient(
            radarr_url,
            radarr_settings.credential,
        )
        await app.state.radarr_client.connect()
        logger.info("Radarr connected at %s", radarr_url)
    else:
        app.state.radarr_client = None

    # Sonarr
    if sonarr_settings.url and sonarr_settings.credential:
        from marquee.core.arr_clients.sonarr_client import SonarrClient

        sonarr_url = normalize_integration_url(sonarr_settings.url)
        app.state.sonarr_client = SonarrClient(
            sonarr_url,
            sonarr_settings.credential,
        )
        await app.state.sonarr_client.connect()
        logger.info("Sonarr connected at %s", sonarr_url)
    else:
        app.state.sonarr_client = None

    # TMDB
    if tmdb_settings.credential:
        from marquee.core.poster_sources.tmdb import TMDBClient

        app.state.tmdb_client = TMDBClient(read_access_token=tmdb_settings.credential)
        await app.state.tmdb_client.connect()
        logger.info("TMDB connected")
    else:
        app.state.tmdb_client = None

    await job_event_tailer.start()
    app.state.job_event_tailer = job_event_tailer
    from marquee.core.jobs.readiness import require_startup_readiness

    try:
        await require_startup_readiness()
    except BaseException:
        await job_event_tailer.stop()
        for name in ("radarr_client", "sonarr_client", "tmdb_client"):
            client = getattr(app.state, name, None)
            if client is not None:
                try:
                    await client.disconnect()
                except Exception:
                    logger.warning(
                        "Error disconnecting %s after failed startup", name, exc_info=True
                    )
        try:
            await managed_secret_provider.stop()
        except Exception:
            logger.warning("Error clearing managed credentials after failed startup", exc_info=True)
        try:
            await configuration_provider.stop()
        except Exception:
            logger.warning(
                "Error stopping configuration provider after failed startup", exc_info=True
            )
        try:
            await close_db()
        except Exception:
            logger.warning("Error closing database after failed startup", exc_info=True)
        raise
    logger.info("Database ready.")

    # Rate limiters — shared across requests
    app.state.op_rate_limiter = _op_rate_limiter

    # Embedded job runtime — spawn the worker + scheduler as supervised child
    # processes so background work runs without a manual `python -m ...worker`.
    # Heavy work stays off the API event loop, and shutdown reaps the whole
    # process group (workers + their runner children) so nothing is orphaned.
    app.state.worker_supervisor = None
    if settings.JOB_EMBEDDED_WORKERS:
        from marquee.core.jobs.supervisor import WorkerSupervisor

        supervisor = WorkerSupervisor()
        await supervisor.start()
        app.state.worker_supervisor = supervisor
    else:
        logger.info("JOB_EMBEDDED_WORKERS=false — expecting external worker/scheduler.")

    # Host-telemetry sampler — independent of the job runtime above. Always
    # on; it's a single lightweight asyncio task, not a supervised process.
    from marquee.core.system_metrics_sampler import SystemMetricsSampler

    metrics_sampler = SystemMetricsSampler()
    await metrics_sampler.start()
    app.state.system_metrics_sampler = metrics_sampler

    yield  # ── application runs here ──

    # ── SHUTDOWN ─────────────────────────────────────────────────────
    logger.info("Shutting down (timeout=%ss) ...", settings.SHUTDOWN_TIMEOUT_SECONDS)

    async def _cleanup():
        """Run all cleanup tasks.  Each wrapped in try/except so one
        failure doesn't block the others."""
        supervisor = getattr(app.state, "worker_supervisor", None)
        if supervisor is not None:
            try:
                await supervisor.shutdown()
            except Exception:
                logger.warning("Error stopping embedded job runtime", exc_info=True)
        sampler = getattr(app.state, "system_metrics_sampler", None)
        if sampler is not None:
            try:
                await sampler.stop()
            except Exception:
                logger.warning("Error stopping system metrics sampler", exc_info=True)
        event_tailer = getattr(app.state, "job_event_tailer", None)
        if event_tailer is not None:
            try:
                await event_tailer.stop()
            except Exception:
                logger.warning("Error stopping job event tailer", exc_info=True)
        try:
            await configuration_provider.stop()
        except Exception:
            logger.warning("Error stopping configuration provider", exc_info=True)
        try:
            await managed_secret_provider.stop()
        except Exception:
            logger.warning("Error clearing managed credential provider", exc_info=True)
        for name in ("radarr_client", "sonarr_client", "tmdb_client"):
            client = getattr(app.state, name, None)
            if client is not None:
                try:
                    logger.debug("Disconnecting %s ...", name)
                    await client.disconnect()
                except Exception:
                    logger.warning("Error disconnecting %s", name, exc_info=True)
        try:
            await close_db()
        except Exception:
            logger.warning("Error closing database", exc_info=True)

    try:
        await asyncio.wait_for(_cleanup(), timeout=settings.SHUTDOWN_TIMEOUT_SECONDS)
    except TimeoutError:
        logger.error(
            "Shutdown timed out after %ss — forcing exit.",
            settings.SHUTDOWN_TIMEOUT_SECONDS,
        )
    logger.info("Shutdown complete.")


# ---------------------------------------------------------------------------
# FastAPI Application
# ---------------------------------------------------------------------------

app = FastAPI(
    title=settings.APP_NAME,
    version=__version__,
    lifespan=lifespan,
    # Every route requires the API key (see marquee.api.auth); /health is exempt
    # inside the dependency, and DEBUG bypasses it.
    dependencies=[Depends(require_api_key)],
    # Interactive docs exist only in debug — no unauthenticated API map in prod.
    docs_url="/docs" if settings.DEBUG else None,
    redoc_url="/redoc" if settings.DEBUG else None,
    openapi_url="/openapi.json" if settings.DEBUG else None,
)

# Shared op limiter on app.state — set here (not only in the lifespan) so it's
# available even when startup is skipped (e.g. ASGITransport in tests).
app.state.op_rate_limiter = _op_rate_limiter

# ---------------------------------------------------------------------------
# Middleware
# ---------------------------------------------------------------------------


class RuntimeCORSMiddleware:
    """Build development CORS policy from the process-pinned effective revision."""

    def __init__(self, app):
        self.app = app
        self._configured = None

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        from marquee.core.runtime_settings import effective_app_settings

        app_settings = effective_app_settings()
        if not app_settings.DEBUG:
            await self.app(scope, receive, send)
            return
        if self._configured is None:
            self._configured = CORSMiddleware(
                self.app,
                allow_origins=app_settings.CORS_ORIGINS,
                allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
                allow_headers=["Authorization", "X-Api-Key", "Content-Type"],
                allow_credentials=False,
            )
        await self._configured(scope, receive, send)


app.add_middleware(RuntimeCORSMiddleware)

app.add_middleware(RequestBodyLimitMiddleware)


# Baseline security headers (full CSP arrives with the web UI).
@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "no-referrer")
    return response


# Request logging — method, path, status, duration
@app.middleware("http")
async def log_requests(request: Request, call_next):
    start = time.monotonic()
    response = await call_next(request)
    duration = time.monotonic() - start
    logger.info(
        "%s %s → %d (%.3fs)",
        request.method,
        request.url.path,
        response.status_code,
        duration,
    )
    return response


# ---------------------------------------------------------------------------
# Routers
# ---------------------------------------------------------------------------

from marquee.api.routes.backup import router as backup_router  # noqa: E402
from marquee.api.routes.config import router as config_router  # noqa: E402
from marquee.api.routes.feedback import router as feedback_router  # noqa: E402
from marquee.api.routes.jobs import router as jobs_router  # noqa: E402
from marquee.api.routes.library import router as library_router  # noqa: E402
from marquee.api.routes.onboarding import router as onboarding_router  # noqa: E402
from marquee.api.routes.pipeline import movies_router  # noqa: E402
from marquee.api.routes.pipeline import router as pipeline_router  # noqa: E402
from marquee.api.routes.pipeline_tv import router as pipeline_tv_router  # noqa: E402
from marquee.api.routes.pipeline_tv import series_router as tv_series_router  # noqa: E402
from marquee.api.routes.settings import router as settings_router  # noqa: E402
from marquee.api.routes.sync import router as sync_router  # noqa: E402
from marquee.api.routes.system import router as system_router  # noqa: E402
from marquee.api.routes.taste import router as taste_router  # noqa: E402
from marquee.api.routes.text_profiles import router as text_profiles_router  # noqa: E402

app.include_router(sync_router)
app.include_router(library_router)
app.include_router(pipeline_tv_router)
app.include_router(tv_series_router)
app.include_router(pipeline_router)
app.include_router(movies_router)
app.include_router(feedback_router)
app.include_router(taste_router)
app.include_router(config_router)
app.include_router(text_profiles_router)
app.include_router(backup_router)
app.include_router(settings_router)
app.include_router(system_router)
app.include_router(jobs_router)
app.include_router(onboarding_router)
if settings.DEBUG:
    from marquee.api.routes.dev_ocr_labels import router as dev_ocr_labels_router  # noqa: E402

    app.include_router(dev_ocr_labels_router)


# ---------------------------------------------------------------------------
# Exception Handler
# ---------------------------------------------------------------------------


@app.exception_handler(RequestValidationError)
async def _request_validation_handler(
    _request: Request, exc: RequestValidationError
) -> JSONResponse:
    """Return useful field errors without reflecting candidate secret input."""

    details = [
        {
            "type": str(error.get("type", "validation_error"))[:100],
            "loc": [str(part)[:100] for part in error.get("loc", ())[:10]],
            "msg": str(error.get("msg", "Invalid value"))[:300],
        }
        for error in exc.errors()[:50]
    ]
    return JSONResponse(status_code=422, content={"detail": details})


# FastAPI's own HTTPException handler takes priority — this only fires for
# genuinely unhandled exceptions so internal details never reach the client.
@app.exception_handler(Exception)
async def _unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    logger.exception("Unhandled error on %s %s", request.method, request.url.path)
    return JSONResponse(status_code=500, content={"detail": "Internal error"})


# ---------------------------------------------------------------------------
# Health Check
# ---------------------------------------------------------------------------


@app.get("/health/live")
async def liveness_check():
    """Process-only liveness; deliberately performs no dependency access."""
    from marquee.core.runtime_settings import effective_settings

    return {
        "status": "live",
        "project": effective_settings.APP_NAME,
        "version": __version__,
    }


async def _readiness_response():
    from marquee.core.jobs.readiness import check_readiness

    report = await check_readiness()
    return JSONResponse(
        status_code=200 if report["status"] == "ready" else 503,
        content=report,
    )


@app.get("/health/ready")
async def readiness_check():
    """Bounded dependency readiness with sanitized component states."""
    return await _readiness_response()


@app.get("/health")
async def health_check():
    """Temporary compatibility alias for dependency readiness."""
    return await _readiness_response()
