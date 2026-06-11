"""Marquee — AI-Powered Media Artwork Manager.

FastAPI application entry point. Sets up logging, manages client lifecycles,
registers middleware and routers, and exposes health/readiness endpoints.
"""

from __future__ import annotations

import asyncio
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text

from marquee import __version__
from marquee.config import settings
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

    # Rate limiter — shared across requests
    app.state.sync_rate_limiter = _sync_rate_limiter

    yield  # ── application runs here ──

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
)

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

from marquee.api.routes.library import router as library_router  # noqa: E402
from marquee.api.routes.pipeline import router as pipeline_router  # noqa: E402
from marquee.api.routes.sync import router as sync_router  # noqa: E402
from marquee.api.routes.test_pipeline import router as test_pipeline_router  # noqa: E402
from marquee.api.routes.webhooks import router as webhooks_router  # noqa: E402

app.include_router(sync_router)
app.include_router(library_router)
app.include_router(pipeline_router)
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
