"""Card serial and public-token identity primitives."""

from __future__ import annotations

import base64
import hashlib
import re
import secrets

from emercard.modules.cards.errors import CardInvariantError

CROCKFORD_ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
_SERIAL_PATTERN = re.compile(
    r"^EMC-[0-9A-HJKMNP-TV-Z]{4}-[0-9A-HJKMNP-TV-Z]{4}-"
    r"[0-9A-HJKMNP-TV-Z]{4}-[0-9A-HJKMNP-TV-Z]$"
)
_TOKEN_HASH_PATTERN = re.compile(r"^v1\$[0-9a-f]{64}$")


def _checksum(payload: str) -> str:
    """Return the deterministic checksum for a 12-character payload.

    The sum of Crockford Base32 values modulo 32 keeps the checksum within
    the approved Crockford alphabet while remaining easy to verify offline.
    """

    return CROCKFORD_ALPHABET[
        sum(CROCKFORD_ALPHABET.index(character) for character in payload) % len(CROCKFORD_ALPHABET)
    ]


def generate_serial() -> str:
    """Generate a canonical system-owned serial for a physical card."""

    payload = "".join(secrets.choice(CROCKFORD_ALPHABET) for _ in range(12))
    return f"EMC-{payload[:4]}-{payload[4:8]}-{payload[8:]}-{_checksum(payload)}"


def normalize_serial(value: str) -> str:
    """Validate and return an uppercase canonical serial."""

    normalized = value.strip().upper()
    if not _SERIAL_PATTERN.fullmatch(normalized):
        raise CardInvariantError("card serial has an invalid format")
    payload = normalized[4:8] + normalized[9:13] + normalized[14:18]
    if normalized[-1] != _checksum(payload):
        raise CardInvariantError("card serial checksum is invalid")
    return normalized


def generate_public_token() -> str:
    """Generate exactly 32 secure random bytes as unpadded Base64URL."""

    return base64.urlsafe_b64encode(secrets.token_bytes(32)).rstrip(b"=").decode("ascii")


def hash_public_token(token: str) -> str:
    """Hash the exact raw token bytes into the versioned lookup representation."""

    if not token:
        raise CardInvariantError("public token cannot be empty")
    digest = hashlib.sha256(token.encode("utf-8")).hexdigest()
    return f"v1${digest}"


def validate_token_hash(value: str) -> str:
    """Validate an internal versioned token hash without exposing its value."""

    if not _TOKEN_HASH_PATTERN.fullmatch(value):
        raise CardInvariantError("card token hash has an invalid format")
    return value
