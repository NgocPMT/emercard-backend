"""Create the initial trusted administrator from environment-provided credentials."""

import asyncio
import json
from dataclasses import dataclass

from emercard.core.config import Settings, get_settings
from emercard.db import Database
from emercard.db.repositories import RepositoryConflictError
from emercard.modules.auth.security import hash_password
from emercard.modules.users.models import UserRegistrationInput
from emercard.modules.users.repository import UserRepository


class AdminSeedError(RuntimeError):
    """The administrator seed operation cannot safely continue."""


@dataclass(frozen=True)
class AdminSeedResult:
    status: str
    email: str


async def seed_admin(
    repository: UserRepository,
    *,
    email: str,
    password: str,
) -> AdminSeedResult:
    """Create an admin without changing an existing account or its password."""

    credentials = UserRegistrationInput(email=email, password=password)
    existing = await repository.find_by_email(credentials.email)
    if existing is not None:
        if existing.role != "admin":
            raise AdminSeedError("an account with the admin email already exists as a user")
        return AdminSeedResult(status="already_exists", email=existing.email)

    try:
        created = await repository.create(
            email=credentials.email,
            password_hash=hash_password(credentials.password),
            role="admin",
        )
    except RepositoryConflictError as error:
        raise AdminSeedError(
            "the admin account was created concurrently; run the seed again"
        ) from error
    return AdminSeedResult(status="created", email=created.email)


def configured_credentials(settings: Settings) -> tuple[str, str]:
    """Read and validate seed credentials without exposing the password in errors."""

    if not settings.admin_email or settings.admin_password is None:
        raise AdminSeedError("EMERCARD_ADMIN_EMAIL and EMERCARD_ADMIN_PASSWORD must be set")
    return settings.admin_email, settings.admin_password.get_secret_value()


async def run() -> AdminSeedResult:
    settings = get_settings()
    email, password = configured_credentials(settings)
    database = Database(settings)
    await database.start()
    try:
        repository = UserRepository(database.database, settings)
        return await seed_admin(repository, email=email, password=password)
    finally:
        await database.close()


async def main() -> None:
    result = await run()
    print(json.dumps({"status": result.status, "email": result.email}, sort_keys=True))


if __name__ == "__main__":
    asyncio.run(main())
