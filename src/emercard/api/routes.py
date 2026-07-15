"""Infrastructure and versioned API routes."""

from typing import Any
from urllib.parse import quote

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, RedirectResponse

from emercard.api.admin_card_routes import build_admin_card_router
from emercard.api.auth_routes import build_auth_router, build_current_user_router
from emercard.api.emergency_routes import build_emergency_router
from emercard.api.errors import error_payload
from emercard.api.profile_routes import build_profile_router
from emercard.api.public_profile_routes import build_public_profile_router
from emercard.api.user_card_routes import build_user_card_router
from emercard.db import Database


def build_infrastructure_router() -> APIRouter:
    router = APIRouter()

    @router.get("/health", tags=["infrastructure"])
    async def health(request: Request) -> dict[str, str]:  # pyright: ignore[reportUnusedFunction]
        """Return process liveness without contacting MongoDB."""

        return {"status": "ok", "service": request.app.state.settings.app_name}

    @router.get("/e/{token}", include_in_schema=False)
    async def redirect_legacy_card_link(token: str, request: Request) -> RedirectResponse:  # pyright: ignore[reportUnusedFunction]
        """Keep older API-hosted local card URLs pointed at the frontend page."""

        settings = request.app.state.settings
        if settings.frontend_base_url:
            base_url = f"{settings.frontend_base_url.rstrip('/')}/e"
        else:
            base_url = settings.public_profile_base_url.rstrip("/")
        target = f"{base_url}/{quote(token, safe='')}"
        return RedirectResponse(
            target,
            status_code=307,
            headers={"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"},
        )

    @router.get("/ready", tags=["infrastructure"], response_model=None)
    async def ready(request: Request) -> dict[str, str] | JSONResponse:  # pyright: ignore[reportUnusedFunction]
        """Return readiness only when the managed database answers a ping."""

        database: Database = request.app.state.database
        if await database.ping():
            return {"status": "ready", "database": "ok"}
        return JSONResponse(
            status_code=503,
            content=error_payload(
                request,
                code="database_unavailable",
                message="Dịch vụ chưa sẵn sàng.",
            ),
            headers={"Retry-After": "5"},
        )

    return router


def build_api_router() -> APIRouter:
    router = APIRouter()

    router.include_router(build_auth_router())
    router.include_router(build_current_user_router())
    router.include_router(build_profile_router())
    router.include_router(build_user_card_router())
    router.include_router(build_admin_card_router())
    router.include_router(build_public_profile_router())
    router.include_router(build_emergency_router())

    @router.get("/meta", tags=["infrastructure"])
    async def meta(request: Request) -> dict[str, Any]:  # pyright: ignore[reportUnusedFunction]
        settings = request.app.state.settings
        return {
            "name": settings.app_name,
            "environment": settings.environment,
            "api_prefix": settings.api_prefix,
            "build_revision": settings.build_revision,
        }

    return router
