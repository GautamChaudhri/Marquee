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
from sqlalchemy import text

from marquee import __version__
from marquee.api.auth import require_api_key
from marquee.config import settings
from marquee.core.backup import backup_service
from marquee.core.pipeline_config import migrate_legacy_runtime_state
from marquee.core.rate_limit import RateLimiter
from marquee.database import _get_engine, close_db, init_db
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
_sync_rate_limiter = RateLimiter(cooldown_seconds=settings.SYNC_COOLDOWN_SECONDS)
# Shared limiter for expensive endpoints; callers pass an explicit per-op cooldown.
_op_rate_limiter = RateLimiter()


async def _heal_loop() -> None:
    """Periodically run the self-heal poster existence scan."""
    from marquee.core.heal import heal_scan

    interval = max(60, settings.HEAL_INTERVAL_MINUTES * 60)
    while True:
        try:
            await asyncio.sleep(interval)
            await heal_scan()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.warning("Self-heal scan failed", exc_info=True)


async def _letterbox_heal_loop() -> None:
    """Periodically re-apply crop tags that drifted off tagged MKV files."""
    from marquee.core.letterbox_heal import letterbox_heal_scan

    interval = max(60, settings.LETTERBOX_HEAL_INTERVAL_MINUTES * 60)
    while True:
        try:
            await asyncio.sleep(interval)
            await letterbox_heal_scan()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.warning("Letterbox tag-drift scan failed", exc_info=True)


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

    # Radarr
    if settings.radarr_configured:
        from marquee.core.arr_clients.radarr_client import RadarrClient

        app.state.radarr_client = RadarrClient(
            settings.RADARR_URL, settings.RADARR_API_KEY
        )
        await app.state.radarr_client.connect()
        logger.info("Radarr connected at %s", settings.RADARR_URL)
    else:
        app.state.radarr_client = None

    # Sonarr
    if settings.sonarr_configured:
        from marquee.core.arr_clients.sonarr_client import SonarrClient

        app.state.sonarr_client = SonarrClient(
            settings.SONARR_URL, settings.SONARR_API_KEY
        )
        await app.state.sonarr_client.connect()
        logger.info("Sonarr connected at %s", settings.SONARR_URL)
    else:
        app.state.sonarr_client = None

    # TMDB
    if settings.tmdb_configured:
        from marquee.core.poster_sources.tmdb import TMDBClient

        app.state.tmdb_client = TMDBClient(
            read_access_token=settings.TMDB_READ_ACCESS_TOKEN
        )
        await app.state.tmdb_client.connect()
        logger.info("TMDB connected")
    else:
        app.state.tmdb_client = None

    # Database
    logger.info("Initialising database ...")
    await init_db()
    logger.info("Database ready.")

    # Rate limiters — shared across requests
    app.state.sync_rate_limiter = _sync_rate_limiter
    app.state.op_rate_limiter = _op_rate_limiter

    # Self-heal scan — periodically restore posters missing from disk.
    heal_task: asyncio.Task | None = None
    if settings.HEAL_ENABLED:
        heal_task = asyncio.create_task(_heal_loop())
        logger.info(
            "Self-heal scan enabled (every %d min)", settings.HEAL_INTERVAL_MINUTES
        )

    # Letterbox tag-drift scan — re-apply crop tags lost to foreign remuxes.
    letterbox_heal_task: asyncio.Task | None = None
    if settings.LETTERBOX_ENABLED and settings.LETTERBOX_HEAL_ENABLED:
        letterbox_heal_task = asyncio.create_task(_letterbox_heal_loop())
        logger.info(
            "Letterbox tag-drift scan enabled (every %d min)",
            settings.LETTERBOX_HEAL_INTERVAL_MINUTES,
        )

    # Durable media-job worker — restart-safe subtitle scan/mutation/generation.
    from marquee.core.media_jobs import media_job_manager  # noqa: PLC0415
    from marquee.core.subtitles.config import subtitle_settings  # noqa: PLC0415

    if subtitle_settings.SUBTITLE_ENABLED:
        await media_job_manager.start()

    backup_task: asyncio.Task | None = None
    if not settings.DEBUG and settings.BACKUP_INTERVAL_HOURS > 0:
        backup_task = asyncio.create_task(backup_service.scheduler_loop())
        logger.info(
            "Internal backup scheduler enabled (every %s hours, initial delay %ss)",
            settings.BACKUP_INTERVAL_HOURS,
            settings.BACKUP_INITIAL_DELAY_SECONDS,
        )

    yield  # ── application runs here ──

    if heal_task is not None:
        heal_task.cancel()
    if letterbox_heal_task is not None:
        letterbox_heal_task.cancel()
    if backup_task is not None:
        backup_task.cancel()
    if subtitle_settings.SUBTITLE_ENABLED:
        await media_job_manager.stop()

    # ── SHUTDOWN ─────────────────────────────────────────────────────
    logger.info(
        "Shutting down (timeout=%ss) ...", settings.SHUTDOWN_TIMEOUT_SECONDS
    )

    async def _cleanup():
        """Run all cleanup tasks.  Each wrapped in try/except so one
        failure doesn't block the others."""
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
        await asyncio.wait_for(
            _cleanup(), timeout=settings.SHUTDOWN_TIMEOUT_SECONDS
        )
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

# CORS — needed for web UI dev (Phase 6)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_methods=["*"],
    allow_headers=["*"],
)


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
from marquee.api.routes.letterbox import router as letterbox_router  # noqa: E402
from marquee.api.routes.library import router as library_router  # noqa: E402
from marquee.api.routes.media_jobs import router as media_jobs_router  # noqa: E402
from marquee.api.routes.pipeline import movies_router  # noqa: E402
from marquee.api.routes.pipeline import router as pipeline_router  # noqa: E402
from marquee.api.routes.subtitle_generators import (  # noqa: E402
    router as subtitle_generators_router,
)
from marquee.api.routes.subtitle_policies import router as subtitle_policies_router  # noqa: E402
from marquee.api.routes.subtitles import movies_router as subtitle_movies_router  # noqa: E402
from marquee.api.routes.subtitles import router as subtitles_router  # noqa: E402
from marquee.api.routes.sync import router as sync_router  # noqa: E402
from marquee.api.routes.system import router as system_router  # noqa: E402
from marquee.api.routes.taste import router as taste_router  # noqa: E402
from marquee.api.routes.test_pipeline import router as test_pipeline_router  # noqa: E402
from marquee.api.routes.webhooks import router as webhooks_router  # noqa: E402

app.include_router(sync_router)
app.include_router(library_router)
app.include_router(pipeline_router)
app.include_router(movies_router)
app.include_router(feedback_router)
app.include_router(taste_router)
app.include_router(config_router)
app.include_router(backup_router)
app.include_router(system_router)
app.include_router(letterbox_router)
app.include_router(subtitles_router)
app.include_router(subtitle_movies_router)
app.include_router(media_jobs_router)
app.include_router(subtitle_policies_router)
app.include_router(subtitle_generators_router)
app.include_router(test_pipeline_router)
app.include_router(webhooks_router)


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
