from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import patch

import pytest
from bson import ObjectId
from pydantic import SecretStr

from emercard.core.config import Settings
from emercard.db.retire_legacy_access_fields import LegacyAccessRetirementError, run

NOW = datetime(2026, 1, 1, tzinfo=UTC)
CARD_ID = ObjectId("507f1f77bcf86cd799439013")
PROFILE_ID = ObjectId("507f1f77bcf86cd799439014")


class FakeUpdateResult:
    def __init__(self, modified_count: int) -> None:
        self.modified_count = modified_count


class FakeCursor:
    def __init__(self, documents: list[dict[str, object]]) -> None:
        self._documents = documents

    async def to_list(self, length: int | None = None) -> list[dict[str, object]]:
        del length
        return list(self._documents)


class FakeCollection:
    def __init__(
        self, documents: list[dict[str, object]], indexes: dict[str, dict[str, object]]
    ) -> None:
        self.documents = documents
        self.indexes = indexes
        self.dropped_indexes: list[str] = []

    def _legacy_token_docs(self) -> list[dict[str, object]]:
        return [doc for doc in self.documents if isinstance(doc.get("token_hash"), str)]

    def _legacy_profile_docs(self) -> list[dict[str, object]]:
        matches: list[dict[str, object]] = []
        for doc in self.documents:
            public_access = doc.get("public_access")
            if isinstance(public_access, dict) and isinstance(public_access.get("token"), str):
                matches.append(doc)
        return matches

    async def count_documents(self, query: dict[str, object]) -> int:
        if query == {"token_hash": {"$type": "string"}}:
            return len(self._legacy_token_docs())
        if query == {"public_access.token": {"$type": "string"}}:
            return len(self._legacy_profile_docs())
        return 0

    async def update_many(
        self, query: dict[str, object], update: dict[str, dict[str, object]]
    ) -> FakeUpdateResult:
        if query == {"token_hash": {"$type": "string"}}:
            target_docs = self._legacy_token_docs()
        elif query == {"public_access.token": {"$type": "string"}}:
            target_docs = self._legacy_profile_docs()
        else:
            target_docs = []

        for doc in target_docs:
            if "$set" in update:
                for path, value in update["$set"].items():
                    _set_nested(doc, path, value)
            if "$unset" in update:
                for path in update["$unset"]:
                    _unset_nested(doc, path)
        return FakeUpdateResult(len(target_docs))

    async def drop_index(self, name: str) -> None:
        self.dropped_indexes.append(name)
        self.indexes.pop(name, None)


class FakeDatabase:
    instances: list[FakeDatabase] = []

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.started = False
        self.closed = False
        self.collections = {
            settings.mongodb_cards_collection: FakeCollection(
                [
                    {
                        "_id": CARD_ID,
                        "serial": "EMC-1ABC-2DEF-3GHJ-K",
                        "token_hash": "v1$" + "0" * 64,
                        "status": "assigned",
                        "is_current": True,
                        "provisioned_at": NOW,
                        "encoding_verified_at": NOW,
                        "encoded_by_admin_id": ObjectId("507f1f77bcf86cd799439011"),
                        "created_at": NOW,
                        "updated_at": NOW,
                    }
                ],
                {"cards_token_hash_unique": {}},
            ),
            settings.mongodb_profiles_collection: FakeCollection(
                [
                    {
                        "_id": PROFILE_ID,
                        "user_id": ObjectId("507f1f77bcf86cd799439012"),
                        "display_name": "Alex Example",
                        "blood_type": "O+",
                        "critical_allergies": [],
                        "important_conditions": [],
                        "critical_medications": [],
                        "public_access": {
                            "token": "legacy-secret",
                            "enabled": True,
                            "published_at": NOW,
                        },
                        "created_at": NOW,
                        "updated_at": NOW,
                    }
                ],
                {"medical_profiles_public_token_unique": {}},
            ),
        }

    async def start(self) -> None:
        self.started = True

    async def close(self) -> None:
        self.closed = True

    @property
    def database(self) -> FakeDatabase:
        return self

    def __getitem__(self, name: str) -> FakeCollection:
        return self.collections[name]


@pytest.mark.asyncio
async def test_retirement_dry_run_reports_only_safe_counts() -> None:
    settings = Settings(environment="test", auth_secret=SecretStr("x" * 32))
    fake_database = FakeDatabase(settings)

    with (
        patch("emercard.db.retire_legacy_access_fields.get_settings", return_value=settings),
        patch("emercard.db.retire_legacy_access_fields.Database", return_value=fake_database),
    ):
        report = await run(apply=False)

    assert fake_database.started is True
    assert fake_database.closed is True
    assert report["dry_run"] is True
    assert report["cards_with_legacy_token"] == 1
    assert report["profiles_with_legacy_token"] == 1
    assert report["indexes_to_drop"] == [
        "cards_token_hash_unique",
        "medical_profiles_public_token_unique",
    ]
    assert report["indexes_dropped"] == []
    assert report["cards_retired"] == 0
    assert report["profiles_retired"] == 0


@pytest.mark.asyncio
async def test_retirement_apply_requires_safety_flags() -> None:
    settings = Settings(environment="test", auth_secret=SecretStr("x" * 32))
    fake_database = FakeDatabase(settings)

    with (
        patch("emercard.db.retire_legacy_access_fields.get_settings", return_value=settings),
        patch("emercard.db.retire_legacy_access_fields.Database", return_value=fake_database),
        pytest.raises(
            LegacyAccessRetirementError,
            match="validation, backup, and rollback mapping",
        ),
    ):
        await run(apply=True)


@pytest.mark.asyncio
async def test_retirement_apply_drops_indexes_and_clears_legacy_fields() -> None:
    settings = Settings(environment="test", auth_secret=SecretStr("x" * 32))
    fake_database = FakeDatabase(settings)

    with (
        patch("emercard.db.retire_legacy_access_fields.get_settings", return_value=settings),
        patch("emercard.db.retire_legacy_access_fields.Database", return_value=fake_database),
    ):
        report = await run(
            apply=True,
            validated=True,
            backup_confirmed=True,
            rollback_mapped=True,
        )

    cards_collection = fake_database.collections[settings.mongodb_cards_collection]
    profiles_collection = fake_database.collections[settings.mongodb_profiles_collection]
    assert report["indexes_dropped"] == [
        "cards_token_hash_unique",
        "medical_profiles_public_token_unique",
    ]
    assert cards_collection.dropped_indexes == ["cards_token_hash_unique"]
    assert profiles_collection.dropped_indexes == ["medical_profiles_public_token_unique"]
    assert report["cards_retired"] == 1
    assert report["profiles_retired"] == 1
    assert cards_collection.documents[0].get("token_hash") is None
    # Link-backed physical verification metadata survives legacy token retirement.
    assert cards_collection.documents[0].get("provisioned_at") is not None
    assert cards_collection.documents[0].get("encoding_verified_at") is not None
    assert cards_collection.documents[0].get("encoded_by_admin_id") is not None
    assert profiles_collection.documents[0]["public_access"]["enabled"] is False
    assert profiles_collection.documents[0]["public_access"].get("token") is None


def _set_nested(document: dict[str, object], path: str, value: object) -> None:
    parts = path.split(".")
    current: dict[str, object] = document
    for part in parts[:-1]:
        next_value = current.setdefault(part, {})
        assert isinstance(next_value, dict)
        current = next_value
    current[parts[-1]] = value


def _unset_nested(document: dict[str, object], path: str) -> None:
    parts = path.split(".")
    current: dict[str, object] = document
    for part in parts[:-1]:
        next_value = current.get(part)
        if not isinstance(next_value, dict):
            return
        current = next_value
    current.pop(parts[-1], None)
