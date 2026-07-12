"""Anonymous emergency-profile lookup routes."""

from typing import Any

from fastapi import APIRouter, Depends, Request

from emercard.modules.cards import CardRepository
from emercard.modules.emergency import EmergencyLookupService, EmergencyProfileResponse
from emercard.modules.profiles import ProfileRepository


def build_emergency_router() -> APIRouter:
    router = APIRouter(tags=["emergency lookup"])

    @router.get("/emergency/{token}", response_model=EmergencyProfileResponse)
    async def lookup_emergency_profile(  # pyright: ignore[reportUnusedFunction]
        token: str,
        service: EmergencyLookupService = Depends(get_emergency_lookup_service),  # noqa: B008
    ) -> EmergencyProfileResponse:
        profile = await service.lookup(token)
        return EmergencyProfileResponse(profile=profile)

    return router


async def get_emergency_lookup_service(request: Request) -> EmergencyLookupService:
    """Build the anonymous lookup service over managed or injected repositories."""

    card_repository: Any = getattr(request.app.state, "card_repository", None)
    if card_repository is None:
        card_repository = CardRepository(
            request.app.state.database.database,
            request.app.state.settings,
        )

    profile_repository: Any = getattr(request.app.state, "profile_repository", None)
    if profile_repository is None:
        profile_repository = ProfileRepository(
            request.app.state.database.database,
            request.app.state.settings,
        )

    return EmergencyLookupService(
        card_repository,
        profile_repository,
        token_max_length=request.app.state.settings.emergency_token_max_length,
    )
