from datetime import UTC, datetime

import pytest
from bson import ObjectId

from emercard.modules.card_link_assignments import CardLinkAssignmentStatus
from emercard.modules.card_link_assignments.models import CardLinkAssignmentDocument
from emercard.modules.cards import CardLinkAlreadyProvisionedError, CardService, CardStatus
from emercard.modules.cards.identity import (
    generate_public_token,
    generate_serial,
    hash_public_token,
)
from emercard.modules.cards.models import CardDocument
from emercard.modules.profiles.models import ProfileDocument
from emercard.modules.public_links import (
    PublicAccessLinkDocument,
    PublicAccessLinkStatus,
    PublicLinkPurpose,
)

NOW = datetime(2026, 1, 1, tzinfo=UTC)
PROFILE_ID = ObjectId("507f1f77bcf86cd799439012")
ADMIN_ID = ObjectId("507f1f77bcf86cd799439013")


class MemoryProfileRepository:
    def __init__(self, profile: ProfileDocument) -> None:
        self.profile = profile

    async def find_by_id(self, profile_id: ObjectId | str) -> ProfileDocument | None:
        return self.profile if str(self.profile.id) == str(profile_id) else None

    async def find_by_user_id(self, user_id: ObjectId | str) -> ProfileDocument | None:
        return self.profile if str(self.profile.user_id) == str(user_id) else None


class MemoryUserRepository:
    async def find_by_id(self, user_id: ObjectId | str) -> object:
        return type("User", (), {"id": ObjectId(user_id), "role": "user"})()


class MemoryCardRepository:
    def __init__(self, card: CardDocument) -> None:
        self.card = card

    async def find_by_id(self, card_id: ObjectId | str) -> CardDocument | None:
        return self.card if str(self.card.id) == str(card_id) else None

    async def with_transaction(self, operation):
        return await operation(None)


class MemoryLinkRepository:
    def __init__(self, link: PublicAccessLinkDocument) -> None:
        self.links = {link.id: link}

    async def find_by_id(self, link_id: ObjectId | str, *, session=None):
        return self.links.get(ObjectId(link_id))

    async def revoke_link(self, *, link_id, now=None, session=None):
        link = self.links.get(ObjectId(link_id))
        if link is None:
            return None
        updated = link.model_copy(
            update={
                "status": PublicAccessLinkStatus.REVOKED,
                "revoked_at": now or NOW,
                "updated_at": now or NOW,
            }
        )
        self.links[updated.id] = updated
        return updated


class MemoryAssignmentRepository:
    def __init__(self) -> None:
        self.assignments = {}

    async def list_by_card_id(self, card_id, *, session=None):
        return [item for item in self.assignments.values() if item.card_id == ObjectId(card_id)]

    async def find_active_by_public_access_link_id(self, link_id, *, session=None):
        return next(
            (
                item
                for item in self.assignments.values()
                if item.public_access_link_id == ObjectId(link_id)
                and item.status is CardLinkAssignmentStatus.ACTIVE
            ),
            None,
        )

    async def attach_link(
        self, *, card_id, public_access_link_id, attached_by_admin_id=None, now=None, session=None
    ):
        assignment = CardLinkAssignmentDocument(
            _id=ObjectId(),
            card_id=ObjectId(card_id),
            public_access_link_id=ObjectId(public_access_link_id),
            status=CardLinkAssignmentStatus.ACTIVE,
            attached_at=now or NOW,
            updated_at=now or NOW,
            attached_by_admin_id=attached_by_admin_id,
        )
        self.assignments[assignment.id] = assignment
        return assignment

    async def detach_assignment(
        self,
        *,
        assignment_id,
        detached_by_admin_id=None,
        detach_reason=None,
        now=None,
        session=None,
    ):
        assignment = self.assignments.get(ObjectId(assignment_id))
        if assignment is None:
            return None
        updated = assignment.model_copy(
            update={
                "status": CardLinkAssignmentStatus.DETACHED,
                "detached_at": now or NOW,
                "detached_by_admin_id": detached_by_admin_id,
                "detach_reason": detach_reason,
                "updated_at": now or NOW,
            }
        )
        self.assignments[updated.id] = updated
        return updated


def profile() -> ProfileDocument:
    return ProfileDocument.model_validate(
        {
            "_id": PROFILE_ID,
            "user_id": ObjectId("507f1f77bcf86cd799439011"),
            "display_name": "Alex",
            "birth_year": 1995,
            "gender": "male",
            "blood_type": "O+",
            "critical_allergies": [],
            "important_conditions": [],
            "critical_medications": [],
            "emergency_note": None,
            "emergency_contacts": [
                {"name": "Sam", "relationship": "Family", "phone": "0900000000"}
            ],
            "created_at": NOW,
            "updated_at": NOW,
        }
    )


def pending_link() -> PublicAccessLinkDocument:
    return PublicAccessLinkDocument.model_validate(
        {
            "_id": ObjectId(),
            "profile_id": PROFILE_ID,
            "purpose": PublicLinkPurpose.STANDALONE,
            "token_hash": hash_public_token(generate_public_token()),
            "status": PublicAccessLinkStatus.PENDING,
            "created_at": NOW,
            "updated_at": NOW,
        }
    )


def blank_card() -> CardDocument:
    return CardDocument(
        _id=ObjectId(),
        serial=generate_serial(),
        owner_id=None,
        token_hash=None,
        status=CardStatus.UNASSIGNED,
        is_current=False,
        created_at=NOW,
        updated_at=NOW,
    )


@pytest.mark.asyncio
async def test_admin_binds_pending_profile_link_and_rebind_revokes_previous_link() -> None:
    first = pending_link()
    second = pending_link()
    links = MemoryLinkRepository(first)
    links.links[second.id] = second
    assignments = MemoryAssignmentRepository()
    cards = MemoryCardRepository(blank_card())
    service = CardService(
        cards,
        MemoryUserRepository(),
        profile_repository=MemoryProfileRepository(profile()),
        public_access_link_repository=links,
        card_link_assignment_repository=assignments,
    )

    await service.attach_card_link(
        card_id=cards.card.id,
        public_access_link_id=first.id,
        admin_id=ADMIN_ID,
        now=NOW,
    )
    await service.attach_card_link(
        card_id=cards.card.id,
        public_access_link_id=second.id,
        admin_id=ADMIN_ID,
        now=NOW,
    )

    assert links.links[first.id].status is PublicAccessLinkStatus.REVOKED
    current = [
        item
        for item in assignments.assignments.values()
        if item.status is CardLinkAssignmentStatus.ACTIVE
    ]
    assert len(current) == 1
    assert current[0].public_access_link_id == second.id


@pytest.mark.asyncio
async def test_card_local_provisioning_is_rejected() -> None:
    card = blank_card()
    service = CardService(MemoryCardRepository(card), MemoryUserRepository())

    with pytest.raises(CardLinkAlreadyProvisionedError):
        await service.provision_link(card_id=card.id, now=NOW)
