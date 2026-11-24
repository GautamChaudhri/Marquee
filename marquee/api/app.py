"""The HTTP API: the app factory and the server ``marquee api`` runs.

The lifespan builds the database engine and disposes of it; nothing connects at import time.
``/health/live`` and ``/health/ready`` need no key. Everything under ``/api`` needs the API
key (:mod:`marquee.api.auth`). The interactive documentation and ``/openapi.json`` exist only
when ``MARQUEE_ENVIRONMENT`` is ``development``.

Requests are logged as method, path, status, and time taken: never a header, and never a
query string. Uvicorn's own access log, which prints the query string, is off.
"""

from __future__ import annotations

import logging
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager

from fastapi import APIRouter, Depends, FastAPI, Request
from fastapi.responses import Response

from marquee import __version__
from marquee.api.auth import require_api_key
from marquee.api.routes import deployments, health, jobs, library, runs
from marquee.config import settings
from marquee.db import migrate
from marquee.db.engine import create_engine, create_session_factory

request_log = logging.getLogger("marquee.api.requests")


def create_app(*, database_url: str | None = None) -> FastAPI:
    """The API, on ``database_url`` or the configured database."""

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        engine = create_engine(database_url)
        app.state.engine = engine
        app.state.sessions = create_session_factory(engine)
        app.state.head_revision = migrate.head_revision()
        try:
            yield
        finally:
            await engine.dispose()

    development = settings.MARQUEE_ENVIRONMENT == "development"
    app = FastAPI(
        title="Marquee",
        version=__version__,
        lifespan=lifespan,
        docs_url="/docs" if development else None,
        redoc_url=None,
        openapi_url="/openapi.json" if development else None,
    )

    @app.middleware("http")
    async def log_request(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        started = time.perf_counter()
        status_code = 500
        try:
            response = await call_next(request)
            status_code = response.status_code
            return response
        finally:
            request_log.info(
                "%s %s %d %.1f ms",
                request.method,
                request.url.path,
                status_code,
                (time.perf_counter() - started) * 1000,
            )

    app.include_router(health.router)
    api = APIRouter(prefix="/api", dependencies=[Depends(require_api_key)])
    api.include_router(library.router)
    api.include_router(jobs.router)
    api.include_router(runs.router)
    api.include_router(deployments.router)
    app.include_router(api)
    return app


def serve(host: str, port: int) -> None:
    """Run the API under uvicorn until interrupted."""
    import uvicorn

    uvicorn.run(
        create_app(),
        host=host,
        port=port,
        # Marquee's own logging is already set up; uvicorn's access log would print query
        # strings, so the request middleware above logs instead.
        log_config=None,
        access_log=False,
        server_header=False,
    )
