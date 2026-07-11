"""Shared MongoDB and datetime types used by domain models."""

from datetime import UTC, datetime
from typing import Annotated

from bson import ObjectId
from pydantic import BeforeValidator, PlainSerializer


def validate_object_id(value: object) -> ObjectId:
    """Accept an ObjectId or its hexadecimal representation."""

    if isinstance(value, ObjectId):
        return value
    if isinstance(value, str) and ObjectId.is_valid(value):
        return ObjectId(value)
    raise ValueError("invalid MongoDB ObjectId")


def serialize_object_id(value: ObjectId) -> str:
    return str(value)


ObjectIdValue = Annotated[
    ObjectId,
    BeforeValidator(validate_object_id),
    PlainSerializer(serialize_object_id, return_type=str),
]


def validate_utc_datetime(value: object) -> datetime:
    """Require an aware datetime and normalize it to UTC."""

    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as error:
            raise ValueError("invalid datetime") from error
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("datetime must include timezone information")
    return value.astimezone(UTC)


UtcDateTime = Annotated[datetime, BeforeValidator(validate_utc_datetime)]


def utc_now() -> datetime:
    """Return a server-generated timezone-aware UTC timestamp."""

    return datetime.now(UTC)
