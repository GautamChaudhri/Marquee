"""Marquee — AI-Powered Media Artwork Manager.

FastAPI application entry point. Sets up logging, manages client lifecycles,
and registers all API routers.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from marquee import __version__
from marquee.config import settings
from marquee.database import close_db, init_db

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=getattr(logging, settings.LOG_LEVEL),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
# Silence noisy third-party loggers
logging.getLogger("aiosqlite").setLevel(logging.ERROR)
logging.getLogger("sqlalchemy.engine").setLevel(logging.ERROR)
logging.getLogger("watchfiles").setLevel(logging.ERROR)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Application Lifespan
# ---------------------------------------------------------------------------


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup and shutdown lifecycle.

    Startup:
        - Initialise external API clients (Radarr, Sonarr, TMDB, etc.)
        - Create database tables if they don't exist.
        - Store clients on ``app.state`` for route-level access.

    Shutdown:
        - Disconnect all API clients.
        - Dispose of the database engine.
    """
    # ── STARTUP ──────────────────────────────────────────────────────
    logger.info("Starting %s v%s", settings.APP_NAME, __version__)
    logger.info("Debug: %s  |  Log level: %s", settings.DEBUG, settings.LOG_LEVEL)

    # External clients will be stored on app.state as they are initialised.
    # Routes access them through dependency helpers (see api/deps.py).
    app.state.radarr_client = None
    app.state.sonarr_client = None
    app.state.tmdb_client = None
    app.state.fanart_client = None
    app.state.tvdb_client = None

    # Initialise *arr clients if configured
    if settings.radarr_configured:
        # TODO: Phase 2 — import and init RadarrClient
        logger.info("Radarr configured at: %s", settings.RADARR_URL)

    if settings.sonarr_configured:
        # TODO: Phase 2 — import and init SonarrClient
        logger.info("Sonarr configured at: %s", settings.SONARR_URL)

    if settings.tmdb_configured:
        # TODO: Phase 2 — import and init TMDBClient
        logger.info("TMDB configured")

    # Database
    logger.info("Initialising database ...")
    await init_db()
    logger.info("Database ready.")

    yield  # ── application runs here ──

    # ── SHUTDOWN ─────────────────────────────────────────────────────
    logger.info("Shutting down ...")
    # TODO: Phase 2 — disconnect all clients
    await close_db()
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
# Health Check
# ---------------------------------------------------------------------------


@app.get("/health")
async def health_check():
    """Basic health check.

    Returns 200 with project metadata when the API is running.
    Used by Docker health checks and load balancers.
    """
    return {
        "status": "ok",
        "project": settings.APP_NAME,
        "version": __version__,
    }
