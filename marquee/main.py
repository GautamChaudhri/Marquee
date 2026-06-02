"""Marquee — AI-Powered Media Artwork Manager.

FastAPI application entry point. Sets up logging, manages client lifecycles,
registers middleware, and exposes health/readiness endpoints.
"""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text

from marquee import __version__
from marquee.config import settings
from marquee.database import _get_engine, close_db, init_db

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
        - Initialise external API clients (stored on ``app.state``).
        - Create database tables if they don't exist.

    Shutdown:
        - Disconnect all API clients with a timeout.
        - Dispose of the database engine.
    """
    # ── STARTUP ──────────────────────────────────────────────────────
    logger.info("Starting %s v%s", settings.APP_NAME, __version__)
    logger.info("Debug: %s  |  Log level: %s", settings.DEBUG, settings.LOG_LEVEL)

    # External clients stored on app.state — routes access them via deps.
    app.state.radarr_client = None
    app.state.sonarr_client = None
    app.state.tmdb_client = None
    app.state.fanart_client = None
    app.state.tvdb_client = None

    # Initialise *arr clients if configured
    if settings.radarr_configured:
        logger.info("Radarr configured at: %s", settings.RADARR_URL)
    if settings.sonarr_configured:
        logger.info("Sonarr configured at: %s", settings.SONARR_URL)
    if settings.tmdb_configured:
        logger.info("TMDB configured")

    # Database
    logger.info("Initialising database ...")
    await init_db()
    logger.info("Database ready.")

    yield  # ── application runs here ──

    # ── SHUTDOWN ─────────────────────────────────────────────────────
    logger.info("Shutting down (timeout=%ss) ...", settings.SHUTDOWN_TIMEOUT_SECONDS)

    async def _cleanup():
        """Run all cleanup tasks. Each is wrapped in a try/except so one
        failure doesn't prevent the others from running."""
        # Disconnect clients (Phase 2)
        for name in ("radarr_client", "sonarr_client", "tmdb_client",
                     "fanart_client", "tvdb_client"):
            client = getattr(app.state, name, None)
            if client is not None:
                try:
                    logger.debug("Disconnecting %s ...", name)
                    await client.disconnect()
                except Exception:
                    logger.warning("Error disconnecting %s", name, exc_info=True)
        # Dispose database
        try:
            await close_db()
        except Exception:
            logger.warning("Error closing database", exc_info=True)

    try:
        await asyncio.wait_for(_cleanup(), timeout=settings.SHUTDOWN_TIMEOUT_SECONDS)
    except asyncio.TimeoutError:
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

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_methods=["*"],
    allow_headers=["*"],
)

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
