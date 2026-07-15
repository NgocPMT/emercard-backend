"""Idempotent Phase 1 collection and index definitions."""

from typing import Any

from pymongo import ASCENDING, IndexModel

from emercard.core.config import Settings

USERS_EMAIL_INDEX = "users_email_unique"
PROFILES_USER_INDEX = "medical_profiles_user_unique"
PROFILES_PUBLIC_TOKEN_INDEX = "medical_profiles_public_token_unique"
PUBLIC_ACCESS_LINKS_PROFILE_INDEX = "public_access_links_profile"
PUBLIC_ACCESS_LINKS_PROFILE_PURPOSE_INDEX = "public_access_links_profile_purpose"
PUBLIC_ACCESS_LINKS_TOKEN_HASH_INDEX = "public_access_links_token_hash_unique"
PUBLIC_ACCESS_LINKS_STATUS_INDEX = "public_access_links_status"
CARD_LINK_ASSIGNMENTS_CARD_INDEX = "card_link_assignments_card"
CARD_LINK_ASSIGNMENTS_LINK_INDEX = "card_link_assignments_link"
CARD_LINK_ASSIGNMENTS_STATUS_INDEX = "card_link_assignments_status"
CARD_LINK_ASSIGNMENTS_ACTIVE_CARD_INDEX = "card_link_assignments_active_card_unique"
CARD_LINK_ASSIGNMENTS_ACTIVE_LINK_INDEX = "card_link_assignments_active_link_unique"
CARDS_SERIAL_INDEX = "cards_serial_unique"
CARDS_TOKEN_HASH_INDEX = "cards_token_hash_unique"
CARDS_OWNER_INDEX = "cards_owner"
CARDS_STATUS_INDEX = "cards_status"
CARDS_OWNER_CURRENT_INDEX = "cards_owner_current"
CARDS_OWNER_STATUS_INDEX = "cards_owner_status"
CARDS_REPLACES_INDEX = "cards_replaces"
CARDS_REPLACEMENT_INDEX = "cards_replacement"
CARDS_ENCODING_INDEX = "cards_encoding_state"
CUSTODY_EVENT_CARD_INDEX = "card_custody_events_card_created"
CUSTODY_EVENT_OWNER_INDEX = "card_custody_events_owner_created"
IDEMPOTENCY_KEY_INDEX = "idempotency_keys_operation_unique"
LOCATION_ALERT_AUDIT_TTL_INDEX = "location_alert_audits_expires"
LOCATION_ALERT_AUDIT_LINK_INDEX = "location_alert_audits_link_created"


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
        settings.mongodb_public_access_links_collection: [
            IndexModel([("profile_id", ASCENDING)], name=PUBLIC_ACCESS_LINKS_PROFILE_INDEX),
            IndexModel(
                [("profile_id", ASCENDING), ("purpose", ASCENDING)],
                name=PUBLIC_ACCESS_LINKS_PROFILE_PURPOSE_INDEX,
            ),
            IndexModel(
                [("token_hash", ASCENDING)], name=PUBLIC_ACCESS_LINKS_TOKEN_HASH_INDEX, unique=True
            ),
            IndexModel([("status", ASCENDING)], name=PUBLIC_ACCESS_LINKS_STATUS_INDEX),
        ],
        settings.mongodb_card_link_assignments_collection: [
            IndexModel([("card_id", ASCENDING)], name=CARD_LINK_ASSIGNMENTS_CARD_INDEX),
            IndexModel(
                [("public_access_link_id", ASCENDING)],
                name=CARD_LINK_ASSIGNMENTS_LINK_INDEX,
            ),
            IndexModel([("status", ASCENDING)], name=CARD_LINK_ASSIGNMENTS_STATUS_INDEX),
            IndexModel(
                [("card_id", ASCENDING)],
                name=CARD_LINK_ASSIGNMENTS_ACTIVE_CARD_INDEX,
                unique=True,
                partialFilterExpression={"status": "active"},
            ),
            IndexModel(
                [("public_access_link_id", ASCENDING)],
                name=CARD_LINK_ASSIGNMENTS_ACTIVE_LINK_INDEX,
                unique=True,
                partialFilterExpression={"status": "active"},
            ),
        ],
        settings.mongodb_cards_collection: [
            IndexModel([("serial", ASCENDING)], name=CARDS_SERIAL_INDEX, unique=True),
            IndexModel(
                [("token_hash", ASCENDING)],
                name=CARDS_TOKEN_HASH_INDEX,
                unique=True,
                partialFilterExpression={"token_hash": {"$type": "string"}},
            ),
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
            IndexModel(
                [("encoding_verified_at", ASCENDING), ("provisioned_at", ASCENDING)],
                name=CARDS_ENCODING_INDEX,
            ),
        ],
        settings.mongodb_custody_events_collection: [
            IndexModel(
                [("card_id", ASCENDING), ("created_at", ASCENDING)],
                name=CUSTODY_EVENT_CARD_INDEX,
            ),
            IndexModel(
                [("previous_owner_id", ASCENDING), ("created_at", ASCENDING)],
                name=CUSTODY_EVENT_OWNER_INDEX,
            ),
        ],
        settings.mongodb_idempotency_collection: [
            IndexModel(
                [("operation_key", ASCENDING)],
                name=IDEMPOTENCY_KEY_INDEX,
                unique=True,
            ),
        ],
        settings.mongodb_location_alert_audits_collection: [
            IndexModel(
                [("expires_at", ASCENDING)],
                name=LOCATION_ALERT_AUDIT_TTL_INDEX,
                expireAfterSeconds=0,
            ),
            IndexModel(
                [("link_id", ASCENDING), ("created_at", ASCENDING)],
                name=LOCATION_ALERT_AUDIT_LINK_INDEX,
            ),
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
