from datetime import UTC, datetime

import pytest
from bson import ObjectId
from pydantic import SecretStr

from emercard.core.config import Settings
from emercard.db.normalize_legacy_links import run
from emercard.modules.card_link_assignments import (
    CardLinkAssignmentDocument,
    CardLinkAssignmentStatus,
)
from emercard.modules.cards import CardDocument, CardStatus, generate_serial, hash_public_token
from emercard.modules.profiles import ProfileDocument
from emercard.modules.public_links import (
    PublicAccessLinkDocument,
    PublicAccessLinkStatus,
    PublicLinkPurpose,
)

NOW = datetime(2026, 1, 1, tzinfo=UTC)
OWNER_ID = ObjectId("507f1f77bcf86cd799439011")
PROFILE_ID = ObjectId("507f1f77bcf86cd799439012")
CARD_ID = ObjectId("507f1f77bcf86cd799439013")
TERMINAL_CARD_ID = ObjectId("507f1f77bcf86cd799439014")
ADMIN_ID = ObjectId("507f1f77bcf86cd799439015")


def settings() -> Settings:
    return Settings(
        environment="test",
        auth_secret=SecretStr("test-auth-secret-012345678901234567890"),
        public_card_base_url="https://app.example/e",
        public_profile_base_url="https://app.example/e",
    )


def card_document(*, card_id: ObjectId, token: str, status: CardStatus) -> CardDocument:
    return CardDocument.model_validate(
        {
            "_id": card_id,
            "serial": generate_serial(),
            "token_hash": hash_public_token(token),
            "status": status,
            "is_current": status not in {CardStatus.LOST, CardStatus.REPLACED, CardStatus.VOID},
            "owner_id": OWNER_ID,
            "provisioned_at": NOW,
            "encoding_verified_at": NOW if status is not CardStatus.UNASSIGNED else None,
            "encoded_by_admin_id": ADMIN_ID if status is not CardStatus.UNASSIGNED else None,
            "created_at": NOW,
            "updated_at": NOW,
            "assigned_at": NOW if status is not CardStatus.UNASSIGNED else None,
            "issued_at": NOW if status in {CardStatus.ACTIVE, CardStatus.DISABLED} else None,
        }
    )


def profile_document(*, enabled: bool, token: str) -> ProfileDocument:
    return ProfileDocument.model_validate(
        {
            "_id": PROFILE_ID,
            "user_id": OWNER_ID,
            "critical_allergies": [],
            "important_conditions": [],
            "critical_medications": [],
            "emergency_contacts": [],
            "public_access": {
                "token": token,
                "enabled": enabled,
                "published_at": NOW,
                "regenerated_at": NOW,
            },
            "created_at": NOW,
            "updated_at": NOW,
        }
    )


class FakeCursor:
    def __init__(self, documents: list[dict[str, object]]) -> None:
        self.documents = documents

    async def to_list(self, length: int | None = None) -> list[dict[str, object]]:
        del length
        return list(self.documents)


class FakeCollection:
    def __init__(self, documents: list[dict[str, object]]) -> None:
        self.documents = documents

    def find(self, query: dict[str, object]) -> FakeCursor:
        if query == {"token_hash": {"$type": "string"}}:
            return FakeCursor(
                [doc for doc in self.documents if isinstance(doc.get("token_hash"), str)]
            )
        if query == {"public_access.token": {"$type": "string"}}:
            return FakeCursor(
                [
                    doc
                    for doc in self.documents
                    if isinstance(doc.get("public_access", {}).get("token"), str)
                ]
            )
        raise AssertionError(f"unexpected query: {query!r}")


class FakeMongoDatabase:
    def __init__(self, cards: list[CardDocument], profiles: list[ProfileDocument]) -> None:
        self.collections = {
            "cards": FakeCollection(
                [card.model_dump(mode="python", by_alias=True) for card in cards]
            ),
            "medical_profiles": FakeCollection(
                [profile.model_dump(mode="python", by_alias=True) for profile in profiles]
            ),
        }

    def __getitem__(self, name: str) -> FakeCollection:
        return self.collections[name]


class FakeDatabase:
    def __init__(self, cards: list[CardDocument], profiles: list[ProfileDocument]) -> None:
        self.database = FakeMongoDatabase(cards, profiles)
        self.started = False
        self.closed = False

    async def start(self) -> None:
        self.started = True

    async def close(self) -> None:
        self.closed = True


class FakeProfileRepository:
    def __init__(self, profile: ProfileDocument) -> None:
        self.profile = profile

    async def find_by_user_id(self, user_id: ObjectId | str) -> ProfileDocument | None:
        return self.profile if str(self.profile.user_id) == str(user_id) else None


class FakeLinkRepository:
    def __init__(self) -> None:
        self.links: list[PublicAccessLinkDocument] = []

    async def find_by_token_hash(
        self, token_hash: str, *, session: object | None = None
    ) -> PublicAccessLinkDocument | None:
        del session
        for link in self.links:
            if link.token_hash == token_hash:
                return link
        return None

    async def create_link(
        self,
        *,
        profile_id: ObjectId | str,
        purpose: PublicLinkPurpose,
        token_hash: str,
        label: str | None = None,
        status: PublicAccessLinkStatus = PublicAccessLinkStatus.ACTIVE,
        created_by: ObjectId | str | None = None,
        now: datetime | None = None,
        session: object | None = None,
    ) -> PublicAccessLinkDocument:
        del created_by, session
        link = PublicAccessLinkDocument.model_validate(
            {
                "_id": ObjectId(),
                "profile_id": profile_id,
                "purpose": purpose,
                "label": label,
                "token_hash": token_hash,
                "status": status,
                "created_at": now or NOW,
                "updated_at": now or NOW,
                "activated_at": now or NOW if status is PublicAccessLinkStatus.ACTIVE else None,
                "disabled_at": now or NOW if status is PublicAccessLinkStatus.DISABLED else None,
                "revoked_at": now or NOW if status is PublicAccessLinkStatus.REVOKED else None,
                "expires_at": None,
                "expired_at": None,
            }
        )
        self.links.append(link)
        return link

    async def activate_link(
        self,
        *,
        link_id: ObjectId | str,
        now: datetime | None = None,
        session: object | None = None,
    ) -> PublicAccessLinkDocument | None:
        del session
        for index, link in enumerate(self.links):
            if str(link.id) == str(link_id):
                updated = link.model_copy(
                    update={
                        "status": PublicAccessLinkStatus.ACTIVE,
                        "updated_at": now or NOW,
                        "activated_at": now or NOW,
                        "disabled_at": None,
                        "revoked_at": None,
                    }
                )
                self.links[index] = updated
                return updated
        return None

    async def disable_link(
        self,
        *,
        link_id: ObjectId | str,
        now: datetime | None = None,
        session: object | None = None,
    ) -> PublicAccessLinkDocument | None:
        del session
        for index, link in enumerate(self.links):
            if str(link.id) == str(link_id):
                updated = link.model_copy(
                    update={
                        "status": PublicAccessLinkStatus.DISABLED,
                        "updated_at": now or NOW,
                        "disabled_at": now or NOW,
                    }
                )
                self.links[index] = updated
                return updated
        return None

    async def revoke_link(
        self,
        *,
        link_id: ObjectId | str,
        now: datetime | None = None,
        session: object | None = None,
    ) -> PublicAccessLinkDocument | None:
        del session
        for index, link in enumerate(self.links):
            if str(link.id) == str(link_id):
                updated = link.model_copy(
                    update={
                        "status": PublicAccessLinkStatus.REVOKED,
                        "updated_at": now or NOW,
                        "revoked_at": now or NOW,
                    }
                )
                self.links[index] = updated
                return updated
        return None


class FakeAssignmentRepository:
    def __init__(self) -> None:
        self.assignments: list[CardLinkAssignmentDocument] = []

    async def find_active_by_card_id(
        self, card_id: ObjectId | str, *, session: object | None = None
    ) -> CardLinkAssignmentDocument | None:
        del session
        for assignment in self.assignments:
            if (
                str(assignment.card_id) == str(card_id)
                and assignment.status is CardLinkAssignmentStatus.ACTIVE
            ):
                return assignment
        return None

    async def attach_link(
        self,
        *,
        card_id: ObjectId | str,
        public_access_link_id: ObjectId | str,
        attached_by_admin_id: ObjectId | str | None = None,
        now: datetime | None = None,
        session: object | None = None,
    ) -> CardLinkAssignmentDocument:
        del session
        assignment = CardLinkAssignmentDocument.model_validate(
            {
                "_id": ObjectId(),
                "card_id": card_id,
                "public_access_link_id": public_access_link_id,
                "status": CardLinkAssignmentStatus.ACTIVE,
                "attached_at": now or NOW,
                "updated_at": now or NOW,
                "attached_by_admin_id": attached_by_admin_id,
                "disabled_at": None,
                "disabled_by_admin_id": None,
                "detached_at": None,
                "detached_by_admin_id": None,
                "detach_reason": None,
            }
        )
        self.assignments.append(assignment)
        return assignment

    async def detach_assignment(
        self,
        *,
        assignment_id: ObjectId | str,
        detached_by_admin_id: ObjectId | str | None = None,
        detach_reason: str | None = None,
        now: datetime | None = None,
        session: object | None = None,
    ) -> CardLinkAssignmentDocument | None:
        del detached_by_admin_id, session
        for index, assignment in enumerate(self.assignments):
            if str(assignment.id) == str(assignment_id):
                updated = assignment.model_copy(
                    update={
                        "status": CardLinkAssignmentStatus.DETACHED,
                        "updated_at": now or NOW,
                        "detached_at": now or NOW,
                        "detach_reason": detach_reason,
                    }
                )
                self.assignments[index] = updated
                return updated
        return None


@pytest.mark.asyncio
async def test_migration_dry_run_reports_only_safe_inventory(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cards = [
        card_document(card_id=CARD_ID, token="card-token", status=CardStatus.ASSIGNED),
        card_document(card_id=TERMINAL_CARD_ID, token="terminal-token", status=CardStatus.LOST),
    ]
    profiles = [profile_document(enabled=True, token="profile-token")]
    fake_database = FakeDatabase(cards, profiles)

    monkeypatch.setattr("emercard.db.normalize_legacy_links.get_settings", lambda: settings())
    monkeypatch.setattr(
        "emercard.db.normalize_legacy_links.Database", lambda _settings: fake_database
    )
    monkeypatch.setattr(
        "emercard.db.normalize_legacy_links.ProfileRepository",
        lambda *args, **kwargs: FakeProfileRepository(profiles[0]),
    )
    monkeypatch.setattr(
        "emercard.db.normalize_legacy_links.PublicAccessLinkRepository",
        lambda *args, **kwargs: FakeLinkRepository(),
    )
    monkeypatch.setattr(
        "emercard.db.normalize_legacy_links.CardLinkAssignmentRepository",
        lambda *args, **kwargs: FakeAssignmentRepository(),
    )

    report = await run(apply=False)

    assert fake_database.started is True
    assert fake_database.closed is True
    assert report["dry_run"] is True
    assert report["cards_scanned"] == 2
    assert report["profiles_scanned"] == 1
    assert report["links_created"] == 0
    assert report["assignments_created"] == 0
    assert report["shared_hash_groups"] == []


@pytest.mark.asyncio
async def test_migration_apply_creates_safe_link_and_assignment_records(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cards = [
        card_document(card_id=CARD_ID, token="card-token", status=CardStatus.ASSIGNED),
        card_document(card_id=TERMINAL_CARD_ID, token="terminal-token", status=CardStatus.LOST),
    ]
    profile = profile_document(enabled=True, token="profile-token")
    fake_database = FakeDatabase(cards, [profile])
    link_repository = FakeLinkRepository()
    assignment_repository = FakeAssignmentRepository()

    monkeypatch.setattr("emercard.db.normalize_legacy_links.get_settings", lambda: settings())
    monkeypatch.setattr(
        "emercard.db.normalize_legacy_links.Database", lambda _settings: fake_database
    )
    monkeypatch.setattr(
        "emercard.db.normalize_legacy_links.ProfileRepository",
        lambda *args, **kwargs: FakeProfileRepository(profile),
    )
    monkeypatch.setattr(
        "emercard.db.normalize_legacy_links.PublicAccessLinkRepository",
        lambda *args, **kwargs: link_repository,
    )
    monkeypatch.setattr(
        "emercard.db.normalize_legacy_links.CardLinkAssignmentRepository",
        lambda *args, **kwargs: assignment_repository,
    )

    report = await run(apply=True)

    assert report["dry_run"] is False
    assert report["links_created"] == 3
    assert report["assignments_created"] == 1
    assert len(link_repository.links) == 3
    assert len(assignment_repository.assignments) == 1
    assert [link.status for link in link_repository.links] == [
        PublicAccessLinkStatus.PENDING,
        PublicAccessLinkStatus.REVOKED,
        PublicAccessLinkStatus.PENDING,
    ]


@pytest.mark.asyncio
async def test_migration_apply_refuses_shared_legacy_hashes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    shared_token = "shared-token"
    cards = [
        card_document(card_id=CARD_ID, token=shared_token, status=CardStatus.ASSIGNED),
        card_document(card_id=TERMINAL_CARD_ID, token=shared_token, status=CardStatus.ASSIGNED),
    ]
    profile = profile_document(enabled=True, token="profile-token")
    fake_database = FakeDatabase(cards, [profile])

    monkeypatch.setattr("emercard.db.normalize_legacy_links.get_settings", lambda: settings())
    monkeypatch.setattr(
        "emercard.db.normalize_legacy_links.Database", lambda _settings: fake_database
    )
    monkeypatch.setattr(
        "emercard.db.normalize_legacy_links.ProfileRepository",
        lambda *args, **kwargs: FakeProfileRepository(profile),
    )
    monkeypatch.setattr(
        "emercard.db.normalize_legacy_links.PublicAccessLinkRepository",
        lambda *args, **kwargs: FakeLinkRepository(),
    )
    monkeypatch.setattr(
        "emercard.db.normalize_legacy_links.CardLinkAssignmentRepository",
        lambda *args, **kwargs: FakeAssignmentRepository(),
    )

    with pytest.raises(RuntimeError, match="shared legacy token hashes"):
        await run(apply=True)
