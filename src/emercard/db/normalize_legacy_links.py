"""Operator command to normalize legacy card and profile tokens into public links."""

from __future__ import annotations

import argparse
import asyncio
import json
from collections import defaultdict
from dataclasses import dataclass
from typing import Any

from emercard.core.config import get_settings
from emercard.db import Database
from emercard.modules.card_link_assignments import CardLinkAssignmentRepository
from emercard.modules.cards import CardDocument, CardStatus, hash_public_token
from emercard.modules.profiles import ProfileDocument, ProfileRepository
from emercard.modules.public_links import (
    PublicAccessLinkRepository,
    PublicAccessLinkStatus,
    PublicLinkPurpose,
)


class LegacyLinkMigrationError(RuntimeError):
    pass


@dataclass(frozen=True)
class _LegacyCardSource:
    card: CardDocument
    profile: ProfileDocument | None


async def run(*, apply: bool = False) -> dict[str, Any]:
    settings = get_settings()
    database = Database(settings)
    await database.start()
    try:
        cards_collection = database.database[settings.mongodb_cards_collection]
        profiles_collection = database.database[settings.mongodb_profiles_collection]
        link_repository = PublicAccessLinkRepository(database.database, settings)
        assignment_repository = CardLinkAssignmentRepository(database.database, settings)
        profile_repository = ProfileRepository(database.database, settings)

        legacy_cards = [
            CardDocument.model_validate(document)
            for document in await cards_collection.find(
                {"token_hash": {"$type": "string"}}
            ).to_list(length=None)
        ]
        legacy_profiles = [
            ProfileDocument.model_validate(document)
            for document in await profiles_collection.find(
                {"public_access.token": {"$type": "string"}}
            ).to_list(length=None)
        ]
        report: dict[str, Any] = {
            "apply": apply,
            "dry_run": not apply,
            "cards_scanned": len(legacy_cards),
            "profiles_scanned": len(legacy_profiles),
            "legacy_card_sources": [],
            "legacy_profile_sources": [],
            "shared_hash_groups": [],
            "links_created": 0,
            "links_updated": 0,
            "assignments_created": 0,
            "assignments_updated": 0,
            "cards_skipped_without_profile": [],
            "cards_skipped_terminal": [],
            "ambiguous_records": [],
            "cardless_profile_links": [],
        }

        hash_groups: dict[str, dict[str, list[str]]] = defaultdict(
            lambda: {"card_ids": [], "profile_ids": []}
        )
        for card in legacy_cards:
            hash_groups[str(card.legacy_token_hash)]["card_ids"].append(str(card.id))
        for profile in legacy_profiles:
            token = profile.public_access.token
            if token is None:
                continue
            hash_groups[hash_public_token(token)]["profile_ids"].append(str(profile.id))

        shared_hash_groups = [
            {"card_ids": ids["card_ids"], "profile_ids": ids["profile_ids"]}
            for ids in hash_groups.values()
            if len(ids["card_ids"]) + len(ids["profile_ids"]) > 1
        ]
        report["shared_hash_groups"] = shared_hash_groups
        if shared_hash_groups and apply:
            raise LegacyLinkMigrationError("shared legacy token hashes require manual review")

        for card in legacy_cards:
            if card.owner_id is None:
                report["ambiguous_records"].append(
                    {"type": "card", "card_id": str(card.id), "reason": "missing_owner_profile"}
                )
        card_hashes = {
            str(card.legacy_token_hash) for card in legacy_cards if card.legacy_token_hash
        }
        for profile in legacy_profiles:
            token = profile.public_access.token
            if token is not None and hash_public_token(token) not in card_hashes:
                report["cardless_profile_links"].append(str(profile.id))

        if not apply:
            return report

        legacy_card_sources: list[_LegacyCardSource] = []
        for card in legacy_cards:
            if card.legacy_token_hash is None:
                continue
            profile = None
            if card.owner_id is not None:
                profile = await profile_repository.find_by_user_id(card.owner_id)
            legacy_card_sources.append(_LegacyCardSource(card=card, profile=profile))
            report["legacy_card_sources"].append(
                {
                    "card_id": str(card.id),
                    "owner_id": str(card.owner_id) if card.owner_id is not None else None,
                    "profile_id": str(profile.id) if profile is not None else None,
                }
            )
            if profile is None:
                report["cards_skipped_without_profile"].append(str(card.id))
                continue

            desired_link_status = _desired_link_status(card)
            link = await link_repository.find_by_token_hash(card.legacy_token_hash)
            if link is None:
                link = await link_repository.create_link(
                    profile_id=profile.id,
                    purpose=PublicLinkPurpose.CARD,
                    token_hash=card.legacy_token_hash,
                    label="Card access",
                    status=desired_link_status,
                )
                report["links_created"] += 1
            else:
                if link.profile_id != profile.id or link.purpose is not PublicLinkPurpose.CARD:
                    raise LegacyLinkMigrationError(
                        f"legacy card link {card.id} conflicts with an existing record"
                    )
                link = await _align_link_status(
                    link_repository,
                    link,
                    desired_link_status,
                )
                report["links_updated"] += int(link.status is not desired_link_status)

            if card.status in {CardStatus.LOST, CardStatus.REPLACED, CardStatus.VOID}:
                assignment = await assignment_repository.find_active_by_card_id(card.id)
                if assignment is not None:
                    detached = await assignment_repository.detach_assignment(
                        assignment_id=assignment.id,
                        detach_reason="legacy terminal card migration",
                    )
                    if detached is not None:
                        report["assignments_updated"] += 1
                continue

            assignment = await assignment_repository.find_active_by_card_id(card.id)
            if assignment is None:
                await assignment_repository.attach_link(
                    card_id=card.id,
                    public_access_link_id=link.id,
                    attached_by_admin_id=None,
                )
                report["assignments_created"] += 1
            elif assignment.public_access_link_id != link.id:
                raise LegacyLinkMigrationError(
                    f"card {card.id} is already assigned to a different public link"
                )

        for profile in legacy_profiles:
            token = profile.public_access.token
            if token is None:
                continue
            report["legacy_profile_sources"].append(
                {
                    "profile_id": str(profile.id),
                    "enabled": profile.public_access.enabled,
                }
            )
            # Profile links are pending until an administrator binds them to a
            # physical card and verifies its encoding. Legacy enabled state is
            # intentionally not carried into an unbound link.
            desired_link_status = PublicAccessLinkStatus.PENDING
            token_hash = hash_public_token(token)
            link = await link_repository.find_by_token_hash(token_hash)
            if link is None:
                await link_repository.create_link(
                    profile_id=profile.id,
                    purpose=PublicLinkPurpose.STANDALONE,
                    token_hash=token_hash,
                    label="Xem trước",
                    status=desired_link_status,
                )
                report["links_created"] += 1
                continue
            if link.profile_id != profile.id or link.purpose is not PublicLinkPurpose.STANDALONE:
                raise LegacyLinkMigrationError(
                    f"legacy profile token for {profile.id} conflicts with an existing record"
                )
            updated_link = await _align_link_status(
                link_repository,
                link,
                desired_link_status,
            )
            report["links_updated"] += int(updated_link.status is not desired_link_status)

        return report
    finally:
        await database.close()


async def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description="Normalize legacy card and profile tokens into public links"
    )
    parser.add_argument("--apply", action="store_true", help="Apply changes instead of dry-run")
    args = parser.parse_args(argv)

    try:
        result = await run(apply=args.apply)
    except LegacyLinkMigrationError as error:
        print(
            json.dumps(
                {"error": {"code": "migration.conflict", "message": str(error)}}, sort_keys=True
            )
        )
        raise SystemExit(1) from None

    print(json.dumps(result, sort_keys=True))


async def _align_link_status(
    repository: PublicAccessLinkRepository,
    link: Any,
    desired_status: PublicAccessLinkStatus,
) -> Any:
    if link.status is desired_status:
        return link
    if desired_status is PublicAccessLinkStatus.ACTIVE:
        updated = await repository.activate_link(link_id=link.id)
    elif desired_status is PublicAccessLinkStatus.DISABLED:
        updated = await repository.disable_link(link_id=link.id)
    elif desired_status is PublicAccessLinkStatus.REVOKED:
        updated = await repository.revoke_link(link_id=link.id)
    elif desired_status is PublicAccessLinkStatus.PENDING:
        updated = await repository.mark_pending(link_id=link.id)
    else:
        updated = await repository.disable_link(link_id=link.id)
    if updated is None:
        raise LegacyLinkMigrationError("legacy link status could not be updated")
    return updated


def _desired_link_status(card: CardDocument) -> PublicAccessLinkStatus:
    if card.status is CardStatus.ACTIVE:
        return PublicAccessLinkStatus.ACTIVE
    if card.status is CardStatus.DISABLED:
        return PublicAccessLinkStatus.DISABLED
    if card.status in {CardStatus.LOST, CardStatus.REPLACED, CardStatus.VOID}:
        return PublicAccessLinkStatus.REVOKED
    return PublicAccessLinkStatus.PENDING


if __name__ == "__main__":
    asyncio.run(main())
