"""Anonymous card-backed emergency lookup contracts and service."""

from emercard.modules.emergency.errors import (
    EmergencyLookupError,
    EmergencyProfileNotFoundError,
    EmergencyProfileServiceUnavailableError,
)
from emercard.modules.emergency.schemas import EmergencyProfileResponse
from emercard.modules.emergency.service import (
    EmergencyLookupService,
    PublicCardRepositoryProtocol,
    PublicProfileRepositoryProtocol,
)

__all__ = [
    "EmergencyLookupError",
    "EmergencyLookupService",
    "EmergencyProfileNotFoundError",
    "EmergencyProfileResponse",
    "EmergencyProfileServiceUnavailableError",
    "PublicCardRepositoryProtocol",
    "PublicProfileRepositoryProtocol",
]
