"""Authenticated owner-scoped card-control routes."""

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pymongo.errors import PyMongoError

from emercard.api.auth_routes import get_current_user
from emercard.db.repositories import InvalidIdentifierError, RepositoryError
from emercard.modules.card_link_assignments import CardLinkAssignmentRepository
from emercard.modules.cards import (
    CardNotFoundError,
    CardRepository,
    CardService,
    CardServiceUnavailableError,
    UserCardListOutput,
    UserCardOutput,
    to_user_card,
)
from emercard.modules.link_access_history import (
    LinkAccessHistoryOutput,
    LinkAccessHistoryRepository,
    to_link_access_event_output,
)
from emercard.modules.profiles.repository import ProfileRepository
from emercard.modules.public_links import PublicAccessLinkRepository, PublicLinkPurpose
from emercard.modules.users import CurrentUserOutput, UserRepository


def build_user_card_router() -> APIRouter:
    router = APIRouter(tags=["user cards"])

    @router.get("/me/cards", response_model=UserCardListOutput)
    async def list_my_cards(  # pyright: ignore[reportUnusedFunction]
        user: CurrentUserOutput = Depends(get_current_user),  # noqa: B008
        service: CardService = Depends(get_user_card_service),  # noqa: B008
    ) -> UserCardListOutput:
        cards = await service.list_user_cards(user_id=user.id)
        items: list[UserCardOutput] = []
        for card in cards:
            detail_card, link, _assignment = await service.describe_user_card(
                card_id=card.id,
                user_id=user.id,
            )
            items.append(to_user_card(detail_card, link=link))
        return UserCardListOutput(cards=items)

    @router.get("/me/cards/{card_id}", response_model=UserCardOutput)
    async def get_my_card(  # pyright: ignore[reportUnusedFunction]
        card_id: str,
        user: CurrentUserOutput = Depends(get_current_user),  # noqa: B008
        service: CardService = Depends(get_user_card_service),  # noqa: B008
    ) -> UserCardOutput:
        card, link, _assignment = await service.describe_user_card(
            card_id=card_id,
            user_id=user.id,
        )
        return to_user_card(card, link=link)

    @router.post("/me/cards/{card_id}/activate", response_model=UserCardOutput)
    async def activate_my_card(  # pyright: ignore[reportUnusedFunction]
        card_id: str,
        user: CurrentUserOutput = Depends(get_current_user),  # noqa: B008
        service: CardService = Depends(get_user_card_service),  # noqa: B008
    ) -> UserCardOutput:
        card = await service.activate_user_card(card_id=card_id, user_id=user.id)
        _card, link, _assignment = await service.describe_user_card(
            card_id=card_id,
            user_id=user.id,
        )
        return to_user_card(card, link=link)

    @router.post("/me/cards/{card_id}/disable", response_model=UserCardOutput)
    async def disable_my_card(  # pyright: ignore[reportUnusedFunction]
        card_id: str,
        user: CurrentUserOutput = Depends(get_current_user),  # noqa: B008
        service: CardService = Depends(get_user_card_service),  # noqa: B008
    ) -> UserCardOutput:
        card = await service.disable_user_card(card_id=card_id, user_id=user.id)
        _card, link, _assignment = await service.describe_user_card(
            card_id=card_id,
            user_id=user.id,
        )
        return to_user_card(card, link=link)

    @router.post("/me/cards/{card_id}/revoke", response_model=UserCardOutput)
    async def revoke_my_card_link(  # pyright: ignore[reportUnusedFunction]
        card_id: str,
        user: CurrentUserOutput = Depends(get_current_user),  # noqa: B008
        service: CardService = Depends(get_user_card_service),  # noqa: B008
    ) -> UserCardOutput:
        card = await service.revoke_user_card_link(card_id=card_id, user_id=user.id)
        _card, link, _assignment = await service.describe_user_card(
            card_id=card_id,
            user_id=user.id,
        )
        return to_user_card(card, link=link)

    @router.get(
        "/me/cards/{card_id}/access-history",
        response_model=LinkAccessHistoryOutput,
    )
    async def list_my_card_access_history(  # pyright: ignore[reportUnusedFunction]
        card_id: str,
        limit: int = Query(default=50, ge=1, le=100),  # noqa: B008
        cursor: str | None = Query(default=None, max_length=512),  # noqa: B008
        user: CurrentUserOutput = Depends(get_current_user),  # noqa: B008
        service: CardService = Depends(get_user_card_service),  # noqa: B008
        repository: Any = Depends(get_user_card_access_history_repository),  # noqa: B008
    ) -> LinkAccessHistoryOutput:
        _card, link, _assignment = await service.describe_user_card(
            card_id=card_id,
            user_id=user.id,
        )
        if link is None or link.purpose is not PublicLinkPurpose.CARD:
            raise CardNotFoundError("card does not exist")
        try:
            events, next_cursor = await repository.list_by_card_and_link(
                card_id=card_id,
                public_access_link_id=link.id,
                limit=limit,
                cursor=cursor,
            )
        except ValueError as error:
            raise HTTPException(status_code=422, detail="invalid access history cursor") from error
        except (InvalidIdentifierError, RepositoryError, PyMongoError) as error:
            raise CardServiceUnavailableError("card service is unavailable") from error
        return LinkAccessHistoryOutput(
            items=[to_link_access_event_output(event) for event in events],
            next_cursor=next_cursor,
        )

    @router.post("/me/cards/{card_id}/lost", response_model=UserCardOutput)
    async def report_my_card_lost(  # pyright: ignore[reportUnusedFunction]
        card_id: str,
        user: CurrentUserOutput = Depends(get_current_user),  # noqa: B008
        service: CardService = Depends(get_user_card_service),  # noqa: B008
    ) -> UserCardOutput:
        await service.get_user_card(card_id=card_id, user_id=user.id)
        card = await service.mark_lost(card_id=card_id)
        _card, link, _assignment = await service.describe_user_card(
            card_id=card_id,
            user_id=user.id,
        )
        return to_user_card(card, link=link)

    return router


async def get_user_card_access_history_repository(request: Request) -> Any:
    repository: Any = getattr(request.app.state, "link_access_history_repository", None)
    if repository is not None:
        return repository
    return LinkAccessHistoryRepository(
        request.app.state.database.database,
        request.app.state.settings,
    )


async def get_user_card_service(request: Request) -> CardService:
    """Build the user-control service without admin mutation dependencies."""

    card_repository: Any = getattr(request.app.state, "card_repository", None)
    if card_repository is None:
        card_repository = CardRepository(
            request.app.state.database.database,
            request.app.state.settings,
        )

    user_repository: Any = getattr(request.app.state, "card_user_repository", None)
    if user_repository is None:
        user_repository = getattr(request.app.state, "auth_repository", None)
    if user_repository is None:
        user_repository = UserRepository(
            request.app.state.database.database,
            request.app.state.settings,
        )

    profile_repository: Any = getattr(request.app.state, "profile_repository", None)
    if profile_repository is None:
        profile_repository = ProfileRepository(
            request.app.state.database.database,
            request.app.state.settings,
        )

    public_access_link_repository: Any = getattr(
        request.app.state, "public_access_link_repository", None
    )
    if public_access_link_repository is None:
        public_access_link_repository = PublicAccessLinkRepository(
            request.app.state.database.database,
            request.app.state.settings,
        )

    card_link_assignment_repository: Any = getattr(
        request.app.state, "card_link_assignment_repository", None
    )
    if card_link_assignment_repository is None:
        card_link_assignment_repository = CardLinkAssignmentRepository(
            request.app.state.database.database,
            request.app.state.settings,
        )

    return CardService(
        card_repository,
        user_repository,
        profile_repository=profile_repository,
        public_access_link_repository=public_access_link_repository,
        card_link_assignment_repository=card_link_assignment_repository,
    )
