"""Authenticated medical-profile HTTP routes."""

from typing import Any

from fastapi import APIRouter, Depends, Request

from emercard.api.auth_routes import get_current_user
from emercard.modules.profiles import (
    ProfileRepository,
    ProfileService,
    ProfileUpsertInput,
    ProfileView,
    PublicProfileOutput,
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
