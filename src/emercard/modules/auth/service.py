"""Application service for registration, login, and current-user resolution."""

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from emercard.core.config import Settings
from emercard.db.repositories import InvalidIdentifierError, RepositoryConflictError
from emercard.modules.auth.exceptions import (
    AuthenticationRequiredError,
    DuplicateEmailError,
    InvalidCredentialsError,
    InvalidSessionError,
)
from emercard.modules.auth.security import (
    DUMMY_PASSWORD_HASH,
    hash_password,
    issue_session_token,
    validate_session_token,
    verify_password,
)
from emercard.modules.users.models import (
    CurrentUserOutput,
    UserDocument,
    UserLoginInput,
    UserRegistrationInput,
)


class UserRepositoryProtocol(Protocol):
    async def find_by_email(self, email: str) -> UserDocument | None: ...

    async def find_by_id(self, user_id: str) -> UserDocument | None: ...

    async def create(
        self,
        *,
        email: str,
        password_hash: str,
        now: datetime | None = None,
    ) -> UserDocument: ...


@dataclass(frozen=True)
class LoginResult:
    user: CurrentUserOutput
    token: str


class AuthService:
    """Coordinate authentication without exposing persistence models to routes."""

    def __init__(self, repository: UserRepositoryProtocol, settings: Settings) -> None:
        self._repository = repository
        self._settings = settings

    async def register(self, request: UserRegistrationInput) -> CurrentUserOutput:
        try:
            user = await self._repository.create(
                email=request.email,
                password_hash=hash_password(request.password),
            )
        except RepositoryConflictError as error:
            raise DuplicateEmailError from error
        return _current_user_output(user)

    async def login(self, request: UserLoginInput) -> LoginResult:
        user = await self._repository.find_by_email(request.email)
        password_hash = user.password_hash if user is not None else DUMMY_PASSWORD_HASH
        password_valid = verify_password(request.password, password_hash)
        if user is None or not password_valid:
            raise InvalidCredentialsError

        current_user = _current_user_output(user)
        return LoginResult(
            user=current_user,
            token=issue_session_token(str(user.id), self._settings),
        )

    async def current_user(self, token: str | None) -> CurrentUserOutput:
        if not token:
            raise AuthenticationRequiredError
        try:
            subject = validate_session_token(token, self._settings)
            user = await self._repository.find_by_id(subject)
        except (ValueError, InvalidIdentifierError) as error:
            raise InvalidSessionError from error
        if user is None:
            raise InvalidSessionError
        return _current_user_output(user)


def _current_user_output(user: UserDocument) -> CurrentUserOutput:
    return CurrentUserOutput(
        id=str(user.id),
        email=user.email,
        created_at=user.created_at,
        updated_at=user.updated_at,
    )
