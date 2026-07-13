"""Anonymous public-profile lookup routes."""

from typing import Any

from fastapi import APIRouter, Depends, Request

from emercard.modules.profiles import ProfileRepository
from emercard.modules.public_links import (
    PublicAccessLinkRepository,
    PublicProfileLookupService,
    PublicProfileResponse,
)


def build_public_profile_router() -> APIRouter:
    router = APIRouter(tags=["public profile lookup"])

    @router.get("/public/{token}", response_model=PublicProfileResponse)
    async def lookup_public_profile(  # pyright: ignore[reportUnusedFunction]
        token: str,
        service: PublicProfileLookupService = Depends(get_public_profile_lookup_service),  # noqa: B008
    ) -> PublicProfileResponse:
        profile = await service.lookup(token)
        return PublicProfileResponse(profile=profile)

    return router


async def get_public_profile_lookup_service(request: Request) -> PublicProfileLookupService:
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

    return PublicProfileLookupService(
        link_repository,
        profile_repository,
        token_max_length=request.app.state.settings.emergency_token_max_length,
    )
