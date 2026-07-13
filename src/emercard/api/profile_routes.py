"""Authenticated medical-profile HTTP routes."""

from typing import Any

from fastapi import APIRouter, Depends, Request

from emercard.api.auth_routes import get_current_user
from emercard.modules.profiles import (
    ProfileProvisioningInconsistentError,
    ProfileRepository,
    ProfileService,
    ProfileUpsertInput,
    ProfileView,
    PublicProfileOutput,
)
from emercard.modules.public_links import (
    PublicAccessLinkRepository,
    PublicProfileLinkService,
    PublicProfilePreviewLinkResponse,
)
from emercard.modules.users import CurrentUserOutput


def build_profile_router() -> APIRouter:
    router = APIRouter(tags=["medical profile"])

    @router.get("/me/profile", response_model=ProfileView)
    async def get_profile(  # pyright: ignore[reportUnusedFunction]
        user: CurrentUserOutput = Depends(get_current_user),  # noqa: B008
        service: ProfileService = Depends(get_profile_service),  # noqa: B008
    ) -> ProfileView:  # pyright: ignore[reportUnusedFunction]
        return await service.get_profile(user_id=user.id)

    @router.put("/me/profile", response_model=ProfileView)
    async def replace_profile(  # pyright: ignore[reportUnusedFunction]
        payload: ProfileUpsertInput,
        user: CurrentUserOutput = Depends(get_current_user),  # noqa: B008
        service: ProfileService = Depends(get_profile_service),  # noqa: B008
    ) -> ProfileView:  # pyright: ignore[reportUnusedFunction]
        return await service.replace_profile(user_id=user.id, request=payload)

    @router.get("/me/profile/public-preview", response_model=PublicProfileOutput)
    async def public_preview(  # pyright: ignore[reportUnusedFunction]
        user: CurrentUserOutput = Depends(get_current_user),  # noqa: B008
        service: ProfileService = Depends(get_profile_service),  # noqa: B008
    ) -> PublicProfileOutput:  # pyright: ignore[reportUnusedFunction]
        return await service.get_public_preview(user_id=user.id)

    @router.post("/me/profile/public-preview-link", response_model=PublicProfilePreviewLinkResponse)
    async def public_preview_link(  # pyright: ignore[reportUnusedFunction]
        user: CurrentUserOutput = Depends(get_current_user),  # noqa: B008
        service: PublicProfileLinkService = Depends(get_public_profile_link_service),  # noqa: B008
        repository: ProfileRepository = Depends(get_profile_repository),  # noqa: B008
    ) -> PublicProfilePreviewLinkResponse:  # pyright: ignore[reportUnusedFunction]
        profile = await repository.find_by_user_id(user.id)
        if profile is None:
            raise ProfileProvisioningInconsistentError
        result = await service.create_preview_link(profile_id=profile.id)
        assert result.public_url is not None
        return PublicProfilePreviewLinkResponse(public_url=result.public_url)

    return router


async def get_profile_repository(request: Request) -> ProfileRepository:
    repository: Any = getattr(request.app.state, "profile_repository", None)
    if repository is not None:
        return repository
    return ProfileRepository(
        request.app.state.database.database,
        request.app.state.settings,
    )


async def get_profile_service(request: Request) -> ProfileService:
    """Build the profile service over the app's managed repository."""

    repository = await get_profile_repository(request)
    return ProfileService(repository)


async def get_public_profile_link_service(request: Request) -> PublicProfileLinkService:
    """Build the authenticated public-link service over managed repositories."""

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

    return PublicProfileLinkService(
        link_repository,
        profile_repository,
        public_profile_base_url=request.app.state.settings.public_profile_base_url,
    )
