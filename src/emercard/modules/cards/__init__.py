"""Physical-card domain models, identity helpers, persistence, and service."""

from emercard.modules.cards.errors import (
    CardAlreadyAssignedError,
    CardError,
    CardIdentityConflictError,
    CardInvalidTransitionError,
    CardInvariantError,
    CardNotFoundError,
    CardOwnershipMismatchError,
    CardProvisioningError,
    CardReplacementError,
    CardSerialConflictError,
    CardTerminalStateError,
    CardTokenHashConflictError,
    CardUserNotFoundError,
)
from emercard.modules.cards.identity import (
    CROCKFORD_ALPHABET,
    generate_public_token,
    generate_serial,
    hash_public_token,
    normalize_serial,
    validate_token_hash,
)
from emercard.modules.cards.models import CardDocument, CardProvisioningResult, CardStatus
from emercard.modules.cards.repository import CardRepository
from emercard.modules.cards.service import (
    CardRepositoryProtocol,
    CardService,
    UserRepositoryProtocol,
)

__all__ = [
    "CROCKFORD_ALPHABET",
    "CardAlreadyAssignedError",
    "CardDocument",
    "CardError",
    "CardIdentityConflictError",
    "CardInvalidTransitionError",
    "CardInvariantError",
    "CardNotFoundError",
    "CardOwnershipMismatchError",
    "CardProvisioningError",
    "CardProvisioningResult",
    "CardRepository",
    "CardRepositoryProtocol",
    "CardReplacementError",
    "CardSerialConflictError",
    "CardService",
    "CardStatus",
    "CardTerminalStateError",
    "CardTokenHashConflictError",
    "CardUserNotFoundError",
    "UserRepositoryProtocol",
    "generate_public_token",
    "generate_serial",
    "hash_public_token",
    "normalize_serial",
    "validate_token_hash",
]
