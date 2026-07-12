"""HTTP-safe response contracts for anonymous emergency lookup."""

from pydantic import BaseModel, ConfigDict

from emercard.modules.profiles.models import PublicProfileOutput


class EmergencyProfileResponse(BaseModel):
    """Envelope for the allowlisted emergency profile projection."""

    model_config = ConfigDict(extra="forbid")

    profile: PublicProfileOutput
