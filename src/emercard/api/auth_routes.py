"""Authentication HTTP routes and the reusable current-user dependency."""

from typing import Any

from fastapi import APIRouter, Depends, Request, Response

from emercard.modules.auth.service import AuthService
from emercard.modules.users import CurrentUserOutput, UserLoginInput, UserRegistrationInput
from emercard.modules.users.repository import UserRepository


def build_auth_router() -> APIRouter:
    router = APIRouter(prefix="/auth", tags=["authentication"])

    @router.post("/register", response_model=CurrentUserOutput, status_code=201)
    async def register(  # pyright: ignore[reportUnusedFunction]
        payload: UserRegistrationInput,
        service: AuthService = Depends(get_auth_service),  # noqa: B008
    ) -> CurrentUserOutput:  # pyright: ignore[reportUnusedFunction]
        return await service.register(payload)

    @router.post("/login", response_model=CurrentUserOutput)
    async def login(  # pyright: ignore[reportUnusedFunction]
        payload: UserLoginInput,
        request: Request,
        response: Response,
        service: AuthService = Depends(get_auth_service),  # noqa: B008
    ) -> CurrentUserOutput:  # pyright: ignore[reportUnusedFunction]
        result = await service.login(payload)
        settings = request.app.state.settings
        response.set_cookie(
            key=settings.auth_cookie_name,
            value=result.token,
            max_age=settings.auth_access_token_lifetime_seconds,
            httponly=settings.auth_cookie_http_only,
            secure=settings.auth_cookie_secure,
            samesite=settings.auth_cookie_same_site,
            path=settings.auth_cookie_path,
        )
        return result.user

    @router.post("/logout", status_code=204)
    async def logout(  # pyright: ignore[reportUnusedFunction]
        request: Request,
        response: Response,
    ) -> None:  # pyright: ignore[reportUnusedFunction]
        settings = request.app.state.settings
        response.delete_cookie(
            key=settings.auth_cookie_name,
            path=settings.auth_cookie_path,
            secure=settings.auth_cookie_secure,
            httponly=settings.auth_cookie_http_only,
            samesite=settings.auth_cookie_same_site,
        )

    return router


async def get_auth_service(request: Request) -> AuthService:
    """Build the service over the app's managed database and configured settings."""

    repository: Any = getattr(request.app.state, "auth_repository", None)
    if repository is None:
        repository = UserRepository(
            request.app.state.database.database,
            request.app.state.settings,
        )
    return AuthService(repository, request.app.state.settings)


async def get_current_user(
    request: Request,
    service: AuthService = Depends(get_auth_service),  # noqa: B008
) -> CurrentUserOutput:
    """Resolve the one trusted principal used by protected routes."""

    token = request.cookies.get(request.app.state.settings.auth_cookie_name)
    return await service.current_user(token)


def build_current_user_router() -> APIRouter:
    router = APIRouter(tags=["authentication"])

    @router.get("/me", response_model=CurrentUserOutput)
    async def current_user(  # pyright: ignore[reportUnusedFunction]
        user: CurrentUserOutput = Depends(get_current_user),  # noqa: B008
    ) -> CurrentUserOutput:  # pyright: ignore[reportUnusedFunction]
        return user

    return router
