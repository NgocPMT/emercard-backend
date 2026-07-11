"""FastAPI application factory and ASGI entrypoint."""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any, cast

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from starlette.exceptions import HTTPException as StarletteHTTPException

from emercard.api.errors import (
    http_exception_handler,
    unhandled_exception_handler,
    validation_exception_handler,
)
from emercard.api.middleware import request_context_middleware
from emercard.api.routes import build_api_router, build_infrastructure_router
from emercard.core.config import Settings, get_settings
from emercard.db import Database, initialize_indexes


@asynccontextmanager
async def app_lifespan(app: FastAPI) -> AsyncIterator[None]:
    database: Database = app.state.database
    await database.start()
    if app.state.settings.mongodb_index_initialization_mode == "startup":
        await initialize_indexes(database.database, app.state.settings)
    try:
        yield
    finally:
        await database.close()


def create_app(
    settings: Settings | None = None,
    database: Database | None = None,
) -> FastAPI:
    app_settings = settings or get_settings()
    logging.getLogger("emercard.request").setLevel(app_settings.log_level)
    app = FastAPI(
        title=app_settings.app_name,
        version="0.1.0",
        debug=app_settings.debug,
        lifespan=app_lifespan,
    )
    app.state.settings = app_settings
    app.state.database = database or Database(app_settings)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=app_settings.cors_origins,
        allow_credentials=app_settings.cors_allow_credentials,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["Accept", "Content-Type", "X-Request-ID", "Authorization"],
        expose_headers=["X-Request-ID"],
    )
    app.middleware("http")(request_context_middleware)
    app.add_exception_handler(StarletteHTTPException, cast(Any, http_exception_handler))
    app.add_exception_handler(RequestValidationError, cast(Any, validation_exception_handler))
    app.add_exception_handler(Exception, cast(Any, unhandled_exception_handler))

    app.include_router(build_infrastructure_router())
    app.include_router(build_api_router(), prefix=app_settings.api_prefix)
    return app


app = create_app()


@app.get("/", include_in_schema=False)
async def root(request: Request) -> dict[str, Any]:
    return {"service": request.app.state.settings.app_name, "docs": "/docs"}
