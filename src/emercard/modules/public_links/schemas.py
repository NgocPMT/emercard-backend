"""HTTP-safe response contracts for anonymous public-profile lookup."""

from pydantic import BaseModel, ConfigDict

from emercard.modules.profiles.models import PublicProfileOutput


class PublicProfileResponse(BaseModel):
    """Envelope for the allowlisted public profile projection."""

    model_config = ConfigDict(extra="forbid")

    profile: PublicProfileOutput


class PublicProfilePreviewLinkResponse(BaseModel):
    """Envelope for an authenticated preview URL."""

    model_config = ConfigDict(extra="forbid")

    public_url: str


class PublicProfileLinkOperationResponse(BaseModel):
    """Safe envelope for standalone link lifecycle operations."""

    model_config = ConfigDict(extra="forbid")

    action: str
    status: str
    public_url: str | None = None
