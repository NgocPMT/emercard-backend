"""HTTP-safe response contracts for anonymous public-profile lookup."""

from pydantic import BaseModel, ConfigDict

from emercard.modules.profiles.models import PublicProfileOutput


class PublicProfileResponse(BaseModel):
    """Envelope for the allowlisted public profile projection."""

    model_config = ConfigDict(extra="forbid")

    profile: PublicProfileOutput
