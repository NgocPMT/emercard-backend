"""Idempotent Phase 1 collection and index definitions."""

from typing import Any

from pymongo import ASCENDING, IndexModel

from emercard.core.config import Settings

USERS_EMAIL_INDEX = "users_email_unique"
PROFILES_USER_INDEX = "medical_profiles_user_unique"
PROFILES_PUBLIC_TOKEN_INDEX = "medical_profiles_public_token_unique"
CARDS_SERIAL_INDEX = "cards_serial_unique"
CARDS_TOKEN_HASH_INDEX = "cards_token_hash_unique"
CARDS_OWNER_INDEX = "cards_owner"
CARDS_STATUS_INDEX = "cards_status"
CARDS_OWNER_CURRENT_INDEX = "cards_owner_current"
CARDS_OWNER_STATUS_INDEX = "cards_owner_status"
CARDS_REPLACES_INDEX = "cards_replaces"
CARDS_REPLACEMENT_INDEX = "cards_replacement"


def collection_indexes(settings: Settings) -> dict[str, list[IndexModel]]:
    """Return the complete required index set without touching database state."""

    return {
        settings.mongodb_users_collection: [
            IndexModel(
                [("email", ASCENDING)],
                name=USERS_EMAIL_INDEX,
                unique=True,
            )
        ],
        settings.mongodb_profiles_collection: [
            IndexModel(
                [("user_id", ASCENDING)],
                name=PROFILES_USER_INDEX,
                unique=True,
            ),
            IndexModel(
                [("public_access.token", ASCENDING)],
                name=PROFILES_PUBLIC_TOKEN_INDEX,
                unique=True,
                partialFilterExpression={"public_access.token": {"$type": "string"}},
            ),
        ],
        settings.mongodb_cards_collection: [
            IndexModel([("serial", ASCENDING)], name=CARDS_SERIAL_INDEX, unique=True),
            IndexModel([("token_hash", ASCENDING)], name=CARDS_TOKEN_HASH_INDEX, unique=True),
            IndexModel([("owner_id", ASCENDING)], name=CARDS_OWNER_INDEX),
            IndexModel([("status", ASCENDING)], name=CARDS_STATUS_INDEX),
            IndexModel(
                [("owner_id", ASCENDING), ("is_current", ASCENDING)],
                name=CARDS_OWNER_CURRENT_INDEX,
            ),
            IndexModel(
                [("owner_id", ASCENDING), ("status", ASCENDING)],
                name=CARDS_OWNER_STATUS_INDEX,
            ),
            IndexModel([("replaces_card_id", ASCENDING)], name=CARDS_REPLACES_INDEX),
            IndexModel([("replacement_card_id", ASCENDING)], name=CARDS_REPLACEMENT_INDEX),
        ],
    }


async def initialize_indexes(database: Any, settings: Settings) -> dict[str, list[str]]:
    """Create required indexes repeatedly without dropping or rebuilding data.

    MongoDB raises an index conflict if an existing named index has incompatible
    options. The exception intentionally propagates so deployment fails visibly.
    """

    created: dict[str, list[str]] = {}
    for collection_name, indexes in collection_indexes(settings).items():
        collection = database[collection_name]
        created[collection_name] = [str(name) for name in await collection.create_indexes(indexes)]
    return created
