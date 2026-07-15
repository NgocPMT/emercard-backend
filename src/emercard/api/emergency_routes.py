"""Anonymous emergency-profile lookup routes."""

from typing import Any

from fastapi import APIRouter, Depends, Request

from emercard.modules.card_link_assignments import CardLinkAssignmentRepository
from emercard.modules.emergency import EmergencyLookupService, EmergencyProfileResponse
from emercard.modules.profiles import ProfileRepository
from emercard.modules.public_links import PublicAccessLinkRepository


def build_emergency_router() -> APIRouter:
    router = APIRouter(tags=["emergency lookup"])

    @router.get("/emergency/{token}", response_model=EmergencyProfileResponse)
    async def lookup_emergency_profile(  # pyright: ignore[reportUnusedFunction]
        token: str,
        service: EmergencyLookupService = Depends(get_emergency_lookup_service),  # noqa: B008
    ) -> EmergencyProfileResponse:
        result = await service.lookup(token)
        return EmergencyProfileResponse(profile=result.profile)

    return router


async def get_emergency_lookup_service(request: Request) -> EmergencyLookupService:
    """Build the anonymous lookup service over managed or injected repositories."""

    link_repository: Any = getattr(request.app.state, "public_access_link_repository", None)
    if link_repository is None:
        link_repository = PublicAccessLinkRepository(
            request.app.state.database.database,
            request.app.state.settings,
        )

    profile_repository: Any = getattr(request.app.state, "profile_repository", None)
    if profile_repository is None:
        profile_repository = ProfileRepository(
            request.app.state.database.database,
            request.app.state.settings,
        )

    assignment_repository: Any = getattr(request.app.state, "card_link_assignment_repository", None)
    database = getattr(request.app.state, "database", None)
    database_value = getattr(database, "database", None)
    if assignment_repository is None and database_value is not None:
        assignment_repository = CardLinkAssignmentRepository(
            database_value,
            request.app.state.settings,
        )

    return EmergencyLookupService(
        link_repository,
        profile_repository,
        assignment_repository=assignment_repository,
        token_max_length=request.app.state.settings.emergency_token_max_length,
    )
