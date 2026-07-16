"""Authenticated medical-profile HTTP routes."""

from typing import Any

from fastapi import APIRouter, Depends, Header, Request, Response

from emercard.api.auth_routes import get_auth_service, get_current_user
from emercard.modules.auth.service import AuthService
from emercard.modules.profiles import (
    ProfileRepository,
    ProfileService,
    ProfileUpsertInput,
    ProfileView,
    PublicProfileOutput,
)
from emercard.modules.users import (
    CurrentUserOutput,
    PrivateProfileAuthorizationInput,
    PrivateProfileAuthorizationOutput,
)


def build_profile_router() -> APIRouter:
    router = APIRouter(tags=["medical profile"])

    @router.post(
        "/me/profile/private/authorize",
        response_model=PrivateProfileAuthorizationOutput,
        tags=["authentication"],
    )
    async def authorize_private_profile_write(  # pyright: ignore[reportUnusedFunction]
        payload: PrivateProfileAuthorizationInput,
        response: Response,
        user: CurrentUserOutput = Depends(get_current_user),  # noqa: B008
        service: AuthService = Depends(get_auth_service),  # noqa: B008
    ) -> PrivateProfileAuthorizationOutput:
        response.headers["Cache-Control"] = "no-store"
        response.headers["Referrer-Policy"] = "no-referrer"
        return await service.authorize_private_profile_write(
            user_id=user.id,
            password=payload.password,
        )

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
        auth_service: AuthService = Depends(get_auth_service),  # noqa: B008
        private_authorization: str | None = Header(
            default=None,
            alias="X-Private-Profile-Authorization",
        ),
    ) -> ProfileView:  # pyright: ignore[reportUnusedFunction]
        if "private_profile_envelope" in payload.model_fields_set:
            await auth_service.validate_private_profile_write_authorization(
                user_id=user.id,
                token=private_authorization,
            )
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
