"""Administrator-only card inventory and custody routes."""

from __future__ import annotations

import base64
import json
from datetime import datetime
from typing import Any

from bson.objectid import ObjectId
from fastapi import APIRouter, Depends, Header, Query, Request, Response

from emercard.api.auth_routes import require_admin
from emercard.modules.cards import (
    AdminCardOutput,
    AssignCardInput,
    CardListOutput,
    CardProvisioningOutput,
    CardRepository,
    CardService,
    ConfirmEncodingInput,
    ProvisioningOutput,
    ReassignCardInput,
    SafeUserOutput,
    to_admin_card,
)
from emercard.modules.cards.operations import CustodyEventRepository, IdempotencyRepository
from emercard.modules.cards.schemas import CardListQuery
from emercard.modules.users import CurrentUserOutput, UserRepository, canonicalize_email


def build_admin_card_router() -> APIRouter:
    router = APIRouter(prefix="/admin", tags=["admin cards"])

    @router.post("/cards", response_model=AdminCardOutput, status_code=201)
    async def create_blank_card(  # pyright: ignore[reportUnusedFunction]
        idempotency_key: str = Header(..., alias="Idempotency-Key"),
        _: CurrentUserOutput = Depends(require_admin),  # noqa: B008
        service: CardService = Depends(get_card_service),  # noqa: B008
    ) -> AdminCardOutput:
        card = await service.create_blank_card(operation_key=idempotency_key.strip())
        return to_admin_card(card)

    @router.post(
        "/cards/{card_id}/provision-link",
        response_model=CardProvisioningOutput,
        status_code=200,
    )
    async def provision_link(  # pyright: ignore[reportUnusedFunction]
        card_id: str,
        response: Response,
        _: CurrentUserOutput = Depends(require_admin),  # noqa: B008
        service: CardService = Depends(get_card_service),  # noqa: B008
    ) -> CardProvisioningOutput:
        result = await service.provision_link(card_id=card_id)
        response.headers["Cache-Control"] = "no-store"
        return CardProvisioningOutput(
            card=to_admin_card(result.card),
            provisioning=ProvisioningOutput(
                public_token=result.public_token,
                public_url=result.public_url,
            ),
        )

    @router.post(
        "/cards/{card_id}/reprovision-link",
        response_model=CardProvisioningOutput,
        status_code=200,
    )
    async def reprovision_link(  # pyright: ignore[reportUnusedFunction]
        card_id: str,
        response: Response,
        _: CurrentUserOutput = Depends(require_admin),  # noqa: B008
        service: CardService = Depends(get_card_service),  # noqa: B008
    ) -> CardProvisioningOutput:
        result = await service.reprovision_link(card_id=card_id)
        response.headers["Cache-Control"] = "no-store"
        return CardProvisioningOutput(
            card=to_admin_card(result.card),
            provisioning=ProvisioningOutput(
                public_token=result.public_token,
                public_url=result.public_url,
            ),
        )

    @router.post("/cards/{card_id}/confirm-encoding", response_model=AdminCardOutput)
    async def confirm_encoding(  # pyright: ignore[reportUnusedFunction]
        card_id: str,
        payload: ConfirmEncodingInput,
        current_admin: CurrentUserOutput = Depends(require_admin),  # noqa: B008
        service: CardService = Depends(get_card_service),  # noqa: B008
    ) -> AdminCardOutput:
        card = await service.confirm_encoding(
            card_id=card_id,
            public_url=payload.public_url,
            admin_id=current_admin.id,
        )
        return to_admin_card(card)

    @router.get("/users/lookup", response_model=SafeUserOutput)
    async def lookup_user(  # pyright: ignore[reportUnusedFunction]
        email: str = Query(..., min_length=3, max_length=254),
        _: CurrentUserOutput = Depends(require_admin),  # noqa: B008
        repository: UserRepository = Depends(get_card_user_repository),  # noqa: B008
    ) -> SafeUserOutput:
        user = await repository.find_by_email(canonicalize_email(email))
        if user is None:
            from emercard.modules.cards.errors import CardUserNotFoundError

            raise CardUserNotFoundError("card assignment target does not exist")
        return SafeUserOutput(
            id=str(user.id),
            email=user.email,
            role=user.role,
            created_at=user.created_at,
            updated_at=user.updated_at,
        )

    @router.post("/cards/{card_id}/assign", response_model=AdminCardOutput)
    async def assign_card(  # pyright: ignore[reportUnusedFunction]
        card_id: str,
        payload: AssignCardInput,
        current_admin: CurrentUserOutput = Depends(require_admin),  # noqa: B008
        service: CardService = Depends(get_card_service),  # noqa: B008
    ) -> AdminCardOutput:
        card = await service.assign_verified_to_user(
            card_id=card_id,
            user_id=payload.user_id,
            admin_id=current_admin.id,
        )
        return to_admin_card(card)

    @router.post("/cards/{card_id}/reassign", response_model=AdminCardOutput)
    async def reassign_card(  # pyright: ignore[reportUnusedFunction]
        card_id: str,
        payload: ReassignCardInput,
        current_admin: CurrentUserOutput = Depends(require_admin),  # noqa: B008
        service: CardService = Depends(get_card_service),  # noqa: B008
    ) -> AdminCardOutput:
        card = await service.reassign_before_issue(
            card_id=card_id,
            new_owner_id=payload.new_owner_id,
            admin_id=current_admin.id,
            reason=payload.reason,
        )
        return to_admin_card(card)

    @router.post("/cards/{card_id}/unassign", response_model=AdminCardOutput)
    async def unassign_card(  # pyright: ignore[reportUnusedFunction]
        card_id: str,
        current_admin: CurrentUserOutput = Depends(require_admin),  # noqa: B008
        service: CardService = Depends(get_card_service),  # noqa: B008
    ) -> AdminCardOutput:
        return to_admin_card(
            await service.unassign_before_issue(card_id=card_id, admin_id=current_admin.id)
        )

    @router.post("/cards/{card_id}/issue", response_model=AdminCardOutput)
    async def issue_card(  # pyright: ignore[reportUnusedFunction]
        card_id: str,
        current_admin: CurrentUserOutput = Depends(require_admin),  # noqa: B008
        service: CardService = Depends(get_card_service),  # noqa: B008
    ) -> AdminCardOutput:
        return to_admin_card(await service.issue(card_id=card_id, admin_id=current_admin.id))

    @router.post("/cards/{card_id}/void", response_model=AdminCardOutput)
    async def void_card(  # pyright: ignore[reportUnusedFunction]
        card_id: str,
        current_admin: CurrentUserOutput = Depends(require_admin),  # noqa: B008
        service: CardService = Depends(get_card_service),  # noqa: B008
    ) -> AdminCardOutput:
        return to_admin_card(await service.void(card_id=card_id, admin_id=current_admin.id))

    @router.get("/cards", response_model=CardListOutput)
    async def list_cards(  # pyright: ignore[reportUnusedFunction]
        filters: CardListQuery = Depends(),  # noqa: B008
        _: CurrentUserOutput = Depends(require_admin),  # noqa: B008
        repository: CardRepository = Depends(get_card_repository),  # noqa: B008
        user_repository: UserRepository = Depends(get_card_user_repository),  # noqa: B008
    ) -> CardListOutput:
        after = _decode_cursor(filters.cursor) if filters.cursor else None
        cards = await repository.list_admin(
            status=filters.status,
            owner_id=filters.owner_id,
            serial=filters.serial,
            is_current=filters.is_current,
            encoding_state=filters.encoding_state,
            issued=filters.issued,
            limit=filters.limit,
            after=after,
        )
        next_cursor = None
        if len(cards) > filters.limit:
            cards = cards[: filters.limit]
            last = cards[-1]
            next_cursor = _encode_cursor(last.created_at, last.id)
        owners = await _owner_outputs(cards, user_repository)
        return CardListOutput(
            items=[to_admin_card(card, owner=owners.get(str(card.owner_id))) for card in cards],
            next_cursor=next_cursor,
        )

    @router.get("/cards/{card_id}", response_model=AdminCardOutput)
    async def get_card(  # pyright: ignore[reportUnusedFunction]
        card_id: str,
        _: CurrentUserOutput = Depends(require_admin),  # noqa: B008
        service: CardService = Depends(get_card_service),  # noqa: B008
        repository: CardRepository = Depends(get_card_repository),  # noqa: B008
        user_repository: UserRepository = Depends(get_card_user_repository),  # noqa: B008
    ) -> AdminCardOutput:
        card = await repository.find_by_id(card_id)
        if card is None:
            from emercard.modules.cards.errors import CardNotFoundError

            raise CardNotFoundError("card does not exist")
        owners = await _owner_outputs([card], user_repository)
        return to_admin_card(card, owner=owners.get(str(card.owner_id)))

    return router


async def get_card_repository(request: Request) -> CardRepository:
    repository = getattr(request.app.state, "card_repository", None)
    if repository is not None:
        return repository
    return CardRepository(request.app.state.database.database, request.app.state.settings)


async def get_card_user_repository(request: Request) -> UserRepository:
    repository = getattr(request.app.state, "card_user_repository", None)
    if repository is not None:
        return repository
    repository = getattr(request.app.state, "auth_repository", None)
    if repository is not None:
        return repository
    return UserRepository(request.app.state.database.database, request.app.state.settings)


async def get_card_service(request: Request) -> CardService:
    repository = await get_card_repository(request)
    user_repository = await get_card_user_repository(request)
    idempotency = getattr(request.app.state, "idempotency_repository", None)
    if idempotency is None:
        idempotency = IdempotencyRepository(
            request.app.state.database.database, request.app.state.settings
        )
    custody_events = getattr(request.app.state, "custody_event_repository", None)
    if custody_events is None:
        custody_events = CustodyEventRepository(
            request.app.state.database.database, request.app.state.settings
        )
    return CardService(
        repository,
        user_repository,
        public_card_base_url=request.app.state.settings.public_card_base_url,
        idempotency_repository=idempotency,
        custody_event_repository=custody_events,
    )


async def _owner_outputs(cards: list[Any], repository: UserRepository) -> dict[str, Any]:
    owners: dict[str, Any] = {}
    for card in cards:
        if card.owner_id is None or str(card.owner_id) in owners:
            continue
        user = await repository.find_by_id(card.owner_id)
        if user is not None:
            from emercard.modules.cards import CardOwnerOutput

            owners[str(card.owner_id)] = CardOwnerOutput(id=str(user.id), email=user.email)
    return owners


def _encode_cursor(created_at: datetime, card_id: ObjectId) -> str:
    payload = json.dumps({"created_at": created_at.isoformat(), "id": str(card_id)}).encode()
    return base64.urlsafe_b64encode(payload).decode().rstrip("=")


def _decode_cursor(value: str) -> tuple[datetime, ObjectId]:
    try:
        padded = value + "=" * (-len(value) % 4)
        payload = json.loads(base64.urlsafe_b64decode(padded).decode())
        return datetime.fromisoformat(payload["created_at"]), ObjectId(payload["id"])
    except (ValueError, KeyError, TypeError, json.JSONDecodeError) as error:
        from emercard.modules.cards.errors import CardEncodingMismatchError

        raise CardEncodingMismatchError("invalid inventory cursor") from error
