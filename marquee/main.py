"""Marquee — AI-Powered Media Artwork Manager.

FastAPI application entry point. Sets up logging, manages client lifecycles,
registers middleware and routers, and exposes health/readiness endpoints.
"""

from __future__ import annotations

import asyncio
import time
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response
from sqlalchemy import text

from marquee import __version__
from marquee.api.auth import require_api_key
from marquee.config import settings
from marquee.core.jobs import (
    builtin_handlers,  # noqa: F401 - registers handlers for create_and_run
    dovi_handlers,  # noqa: F401 - registers dovi analysis handler
    job_manager,
    legacy_media,  # noqa: F401 - registers bridge handlers
)
from marquee.core.pipeline_config import migrate_legacy_runtime_state
from marquee.core.rate_limit import RateLimiter
from marquee.database import _get_engine, _get_session_factory, close_db, init_db
from marquee.logging import setup_logging
from marquee.ml.migrate_artifacts import migrate_live_artifacts

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

    migrated_paths = migrate_legacy_runtime_state()
    for moved in migrated_paths:
        logger.info("Runtime state migrated to data/: %s", moved)
    migrated_artifacts = migrate_live_artifacts()
    for moved in migrated_artifacts:
        logger.info("Legacy .npz artifact migrated: %s", moved)

    # Radarr
    if settings.radarr_configured:
        from marquee.core.arr_clients.radarr_client import RadarrClient

        app.state.radarr_client = RadarrClient(settings.RADARR_URL, settings.RADARR_API_KEY)
        await app.state.radarr_client.connect()
        logger.info("Radarr connected at %s", settings.RADARR_URL)
    else:
        app.state.radarr_client = None

    # Sonarr
    if settings.sonarr_configured:
        from marquee.core.arr_clients.sonarr_client import SonarrClient

        app.state.sonarr_client = SonarrClient(settings.SONARR_URL, settings.SONARR_API_KEY)
        await app.state.sonarr_client.connect()
        logger.info("Sonarr connected at %s", settings.SONARR_URL)
    else:
        app.state.sonarr_client = None

    # TMDB
    if settings.tmdb_configured:
        from marquee.core.poster_sources.tmdb import TMDBClient

        app.state.tmdb_client = TMDBClient(read_access_token=settings.TMDB_READ_ACCESS_TOKEN)
        await app.state.tmdb_client.connect()
        logger.info("TMDB connected")
    else:
        app.state.tmdb_client = None

    # Database
    logger.info("Initialising database ...")
    await init_db()
    logger.info("Database ready.")
    async with _get_session_factory()() as db:
        await job_manager.bootstrap_resources(db)

    # Rate limiters — shared across requests
    app.state.op_rate_limiter = _op_rate_limiter

    # Embedded job runtime — spawn the worker + scheduler as supervised child
    # processes so background work runs without a manual `python -m ...worker`.
    # Heavy work stays off the API event loop, and shutdown reaps the whole
    # process group (workers + their ffmpeg children) so nothing is orphaned.
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

# CORS — dev only (DEBUG=True). In production the frontend is served same-origin
# so no CORS headers are needed. Wildcards are narrowed here so adding
# allow_credentials=True for cookie auth later won't break browsers.
if settings.DEBUG:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.CORS_ORIGINS,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["Authorization", "X-Api-Key", "Content-Type"],
        allow_credentials=False,
    )


# Reject oversized bodies before they reach any handler.
@app.middleware("http")
async def limit_body_size(request: Request, call_next):
    cl = request.headers.get("content-length")
    if cl and int(cl) > settings.MAX_REQUEST_BODY_BYTES:
        return Response(
            status_code=413,
            content='{"detail":"Request body too large"}',
            media_type="application/json",
        )
    return await call_next(request)


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

from marquee.api.routes.activity import router as activity_router  # noqa: E402
from marquee.api.routes.audio_subs import router as audio_subs_router  # noqa: E402
from marquee.api.routes.backup import router as backup_router  # noqa: E402
from marquee.api.routes.config import router as config_router  # noqa: E402
from marquee.api.routes.feedback import router as feedback_router  # noqa: E402
from marquee.api.routes.hdr import router as hdr_router  # noqa: E402
from marquee.api.routes.jobs import router as jobs_router  # noqa: E402
from marquee.api.routes.letterbox import router as letterbox_router  # noqa: E402
from marquee.api.routes.library import router as library_router  # noqa: E402
from marquee.api.routes.media_jobs import router as media_jobs_router  # noqa: E402
from marquee.api.routes.onboarding import router as onboarding_router  # noqa: E402
from marquee.api.routes.pipeline import movies_router  # noqa: E402
from marquee.api.routes.pipeline import router as pipeline_router  # noqa: E402
from marquee.api.routes.pipeline_tv import router as pipeline_tv_router  # noqa: E402
from marquee.api.routes.pipeline_tv import series_router as tv_series_router  # noqa: E402
from marquee.api.routes.settings import router as settings_router  # noqa: E402
from marquee.api.routes.subtitle_generators import (  # noqa: E402
    router as subtitle_generators_router,
)
from marquee.api.routes.subtitle_policies import router as subtitle_policies_router  # noqa: E402
from marquee.api.routes.subtitles import movies_router as subtitle_movies_router  # noqa: E402
from marquee.api.routes.subtitles import router as subtitles_router  # noqa: E402
from marquee.api.routes.sync import router as sync_router  # noqa: E402
from marquee.api.routes.system import router as system_router  # noqa: E402
from marquee.api.routes.taste import router as taste_router  # noqa: E402
from marquee.api.routes.text_profiles import router as text_profiles_router  # noqa: E402
from marquee.api.routes.webhooks import router as webhooks_router  # noqa: E402

app.include_router(sync_router)
app.include_router(library_router)
app.include_router(pipeline_tv_router)
app.include_router(tv_series_router)
app.include_router(pipeline_router)
app.include_router(movies_router)
app.include_router(feedback_router)
app.include_router(taste_router)
app.include_router(onboarding_router)
app.include_router(config_router)
app.include_router(text_profiles_router)
app.include_router(backup_router)
app.include_router(activity_router)
app.include_router(hdr_router)
app.include_router(settings_router)
app.include_router(system_router)
app.include_router(letterbox_router)
app.include_router(jobs_router)
app.include_router(subtitles_router)
app.include_router(subtitle_movies_router)
app.include_router(audio_subs_router)
app.include_router(media_jobs_router)
app.include_router(subtitle_policies_router)
app.include_router(subtitle_generators_router)
app.include_router(webhooks_router)
if settings.DEBUG:
    from marquee.api.routes.dev_ocr_labels import router as dev_ocr_labels_router  # noqa: E402

    app.include_router(dev_ocr_labels_router)


# ---------------------------------------------------------------------------
# Exception Handler
# ---------------------------------------------------------------------------


# FastAPI's own HTTPException handler takes priority — this only fires for
# genuinely unhandled exceptions so internal details never reach the client.
@app.exception_handler(Exception)
async def _unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    logger.exception("Unhandled error on %s %s", request.method, request.url.path)
    return JSONResponse(status_code=500, content={"detail": "Internal error"})


# ---------------------------------------------------------------------------
# Health Check
# ---------------------------------------------------------------------------


@app.get("/health")
async def health_check():
    """Health check — probes the database connection.

    Returns 200 when healthy, 503 when the database is unreachable.
    Docker health checks and load balancers depend on this endpoint.
    """
    db_ok = False
    try:
        engine = _get_engine()
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
            db_ok = True
    except Exception:
        logger.warning("Health check: database probe failed", exc_info=True)

    status = "ok" if db_ok else "degraded"

    return {
        "status": status,
        "project": settings.APP_NAME,
        "version": __version__,
        "database": "connected" if db_ok else "unreachable",
    }
