"""Infrastructure and versioned API routes."""

from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from emercard.api.errors import error_payload
from emercard.db import Database


def build_infrastructure_router() -> APIRouter:
    router = APIRouter()

    @router.get("/health", tags=["infrastructure"])
    async def health(request: Request) -> dict[str, str]:  # pyright: ignore[reportUnusedFunction]
        """Return process liveness without contacting MongoDB."""

        return {"status": "ok", "service": request.app.state.settings.app_name}

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
                message="The service is not ready",
            ),
            headers={"Retry-After": "5"},
        )

    return router


def build_api_router() -> APIRouter:
    router = APIRouter()

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
