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
    auth_exception_handler,
    card_exception_handler,
    emergency_exception_handler,
    http_exception_handler,
    profile_exception_handler,
    public_profile_exception_handler,
    unhandled_exception_handler,
    validation_exception_handler,
)
from emercard.api.middleware import (
    EmergencyRateLimiter,
    install_uvicorn_access_log_redaction,
    request_context_middleware,
)
from emercard.api.routes import build_api_router, build_infrastructure_router
from emercard.core.config import Settings, get_settings
from emercard.db import Database, initialize_indexes
from emercard.modules.auth.exceptions import AuthError
from emercard.modules.cards.errors import CardError
from emercard.modules.emergency.errors import EmergencyLookupError
from emercard.modules.profiles.exceptions import ProfileError
from emercard.modules.public_links.errors import PublicProfileError


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
    auth_repository: Any | None = None,
    profile_repository: Any | None = None,
    card_repository: Any | None = None,
    card_user_repository: Any | None = None,
    idempotency_repository: Any | None = None,
    custody_event_repository: Any | None = None,
    emergency_rate_limiter: Any | None = None,
    public_access_link_repository: Any | None = None,
    card_link_assignment_repository: Any | None = None,
) -> FastAPI:
    app_settings = settings or get_settings()
    logging.getLogger("emercard.request").setLevel(app_settings.log_level)
    install_uvicorn_access_log_redaction(app_settings.api_prefix)
    app = FastAPI(
        title=app_settings.app_name,
        version="0.1.0",
        debug=app_settings.debug,
        lifespan=app_lifespan,
    )
    app.state.settings = app_settings
    app.state.database = database or Database(app_settings)
    app.state.emergency_rate_limiter = emergency_rate_limiter or EmergencyRateLimiter(
        window_seconds=app_settings.emergency_rate_limit_window_seconds,
        burst=app_settings.emergency_rate_limit_burst,
    )
    if auth_repository is not None:
        app.state.auth_repository = auth_repository
    if profile_repository is not None:
        app.state.profile_repository = profile_repository
    if card_repository is not None:
        app.state.card_repository = card_repository
    if card_user_repository is not None:
        app.state.card_user_repository = card_user_repository
    if idempotency_repository is not None:
        app.state.idempotency_repository = idempotency_repository
    if custody_event_repository is not None:
        app.state.custody_event_repository = custody_event_repository
    if emergency_rate_limiter is not None:
        app.state.emergency_rate_limiter = emergency_rate_limiter
    if public_access_link_repository is not None:
        app.state.public_access_link_repository = public_access_link_repository
    if card_link_assignment_repository is not None:
        app.state.card_link_assignment_repository = card_link_assignment_repository

    app.add_middleware(
        CORSMiddleware,
        allow_origins=app_settings.cors_origins,
        allow_credentials=app_settings.cors_allow_credentials,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["Accept", "Content-Type", "X-Request-ID", "Authorization"],
        expose_headers=["X-Request-ID"],
    )
    app.middleware("http")(request_context_middleware)
    app.add_exception_handler(AuthError, cast(Any, auth_exception_handler))
    app.add_exception_handler(CardError, cast(Any, card_exception_handler))
    app.add_exception_handler(ProfileError, cast(Any, profile_exception_handler))
    app.add_exception_handler(PublicProfileError, cast(Any, public_profile_exception_handler))
    app.add_exception_handler(EmergencyLookupError, cast(Any, emergency_exception_handler))
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
