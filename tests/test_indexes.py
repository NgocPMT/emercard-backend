from unittest.mock import AsyncMock

import pytest

from emercard.core.config import Settings
from emercard.db.indexes import (
    CARDS_ENCODING_INDEX,
    CARDS_OWNER_CURRENT_INDEX,
    CARDS_OWNER_INDEX,
    CARDS_OWNER_STATUS_INDEX,
    CARDS_REPLACEMENT_INDEX,
    CARDS_REPLACES_INDEX,
    CARDS_SERIAL_INDEX,
    CARDS_STATUS_INDEX,
    CARDS_TOKEN_HASH_INDEX,
    CARDS_TOKEN_REVISION_INDEX,
    CUSTODY_EVENT_CARD_INDEX,
    CUSTODY_EVENT_OWNER_INDEX,
    IDEMPOTENCY_KEY_INDEX,
    PROFILES_PUBLIC_TOKEN_INDEX,
    PROFILES_USER_INDEX,
    USERS_EMAIL_INDEX,
    initialize_indexes,
)


class FakeCollection:
    def __init__(self, names: list[str]) -> None:
        self.create_indexes = AsyncMock(return_value=names)


class FakeDatabase:
    def __init__(self) -> None:
        self.collections = {
            "users": FakeCollection([USERS_EMAIL_INDEX]),
            "medical_profiles": FakeCollection([PROFILES_USER_INDEX, PROFILES_PUBLIC_TOKEN_INDEX]),
            "cards": FakeCollection(
                [
                    CARDS_SERIAL_INDEX,
                    CARDS_TOKEN_HASH_INDEX,
                    CARDS_TOKEN_REVISION_INDEX,
                    CARDS_OWNER_INDEX,
                    CARDS_STATUS_INDEX,
                    CARDS_OWNER_CURRENT_INDEX,
                    CARDS_OWNER_STATUS_INDEX,
                    CARDS_REPLACES_INDEX,
                    CARDS_REPLACEMENT_INDEX,
                    CARDS_ENCODING_INDEX,
                ]
            ),
            "card_custody_events": FakeCollection(
                [CUSTODY_EVENT_CARD_INDEX, CUSTODY_EVENT_OWNER_INDEX]
            ),
            "idempotency_keys": FakeCollection([IDEMPOTENCY_KEY_INDEX]),
        }

    def __getitem__(self, name: str) -> FakeCollection:
        return self.collections[name]


@pytest.mark.asyncio
async def test_initialize_indexes_is_explicit_and_uses_required_collections() -> None:
    settings = Settings(environment="test")
    database = FakeDatabase()

    result = await initialize_indexes(database, settings)

    assert result == {
        "users": [USERS_EMAIL_INDEX],
        "medical_profiles": [PROFILES_USER_INDEX, PROFILES_PUBLIC_TOKEN_INDEX],
        "cards": [
            CARDS_SERIAL_INDEX,
            CARDS_TOKEN_HASH_INDEX,
            CARDS_TOKEN_REVISION_INDEX,
            CARDS_OWNER_INDEX,
            CARDS_STATUS_INDEX,
            CARDS_OWNER_CURRENT_INDEX,
            CARDS_OWNER_STATUS_INDEX,
            CARDS_REPLACES_INDEX,
            CARDS_REPLACEMENT_INDEX,
            CARDS_ENCODING_INDEX,
        ],
        "card_custody_events": [CUSTODY_EVENT_CARD_INDEX, CUSTODY_EVENT_OWNER_INDEX],
        "idempotency_keys": [IDEMPOTENCY_KEY_INDEX],
    }
    database.collections["users"].create_indexes.assert_awaited_once()
    database.collections["medical_profiles"].create_indexes.assert_awaited_once()

    profile_indexes = database.collections["medical_profiles"].create_indexes.await_args.args[0]
    assert profile_indexes[1].document["partialFilterExpression"] == {
        "public_access.token": {"$type": "string"}
    }
    card_indexes = database.collections["cards"].create_indexes.await_args.args[0]
    token_index = next(
        index for index in card_indexes if index.document["name"] == CARDS_TOKEN_HASH_INDEX
    )
    assert token_index.document["partialFilterExpression"] == {"token_hash": {"$type": "string"}}
    assert not any(
        index.document.get("unique") and "owner_id" in str(index.document["key"])
        for index in card_indexes
    )
