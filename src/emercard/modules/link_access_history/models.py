"""MongoDB documents and owner-safe output models for public-link access history."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, model_validator

from emercard.core.types import ObjectIdValue, UtcDateTime


def parse_device_label(user_agent: str | None) -> str:
    """Parse a User-Agent header into a clean, human-readable device label."""
    if not user_agent or not user_agent.strip():
        return "Thiết bị di động"

    ua = user_agent.strip()
    ua_lower = ua.lower()

    browser = ""
    if "edgios" in ua_lower or "edg" in ua_lower:
        browser = "Edge"
    elif "crios" in ua_lower or "chrome" in ua_lower:
        browser = "Chrome"
    elif "fxios" in ua_lower or "firefox" in ua_lower:
        browser = "Firefox"
    elif "safari" in ua_lower and "chrome" not in ua_lower:
        browser = "Safari"

    browser_suffix = f" ({browser})" if browser else ""

    if "iphone" in ua_lower:
        return f"Apple iPhone{browser_suffix or ' (Safari)'}"
    if "ipad" in ua_lower:
        return f"Apple iPad{browser_suffix or ' (Safari)'}"

    if "android" in ua_lower:
        if "pixel" in ua_lower:
            return f"Google Pixel{browser_suffix or ' (Chrome)'}"
        if "samsung" in ua_lower or "sm-" in ua_lower:
            return f"Samsung Galaxy{browser_suffix or ' (Chrome)'}"
        if "xiaomi" in ua_lower or "redmi" in ua_lower:
            return f"Xiaomi{browser_suffix or ' (Chrome)'}"
        return f"Android{browser_suffix or ' (Chrome)'}"

    if "macintosh" in ua_lower or "mac os x" in ua_lower:
        return f"Mac{browser_suffix or ' (Safari)'}"
    if "windows" in ua_lower:
        return f"Windows PC{browser_suffix or ' (Chrome)'}"
    if "linux" in ua_lower:
        return f"Linux PC{browser_suffix or ' (Trình duyệt)'}"

    return "Thiết bị di động"


class LinkAccessHistoryModel(BaseModel):
    model_config = ConfigDict(extra="forbid", arbitrary_types_allowed=True)


class LinkAccessEventDocument(LinkAccessHistoryModel):
    """Append-only event for a successful active card-link lookup."""

    id: ObjectIdValue = Field(alias="_id")
    card_id: ObjectIdValue
    public_access_link_id: ObjectIdValue
    accessed_at: UtcDateTime
    expires_at: UtcDateTime
    device: str = "Thiết bị di động"
    latency_ms: int | None = None

    @model_validator(mode="after")
    def validate_expiry(self) -> LinkAccessEventDocument:
        if self.expires_at < self.accessed_at:
            raise ValueError("expires_at cannot be earlier than accessed_at")
        return self


class LinkAccessEventOutput(LinkAccessHistoryModel):
    """Event projection exposed to the authenticated profile owner."""

    id: str
    accessed_at: UtcDateTime
    device: str = "Thiết bị di động"
    latency_ms: int | None = None


class LinkAccessHistoryOutput(LinkAccessHistoryModel):
    items: list[LinkAccessEventOutput]
    next_cursor: str | None = None
    scans_this_month: int = 0
    average_latency_ms: int | None = None


def to_link_access_event_output(event: LinkAccessEventDocument) -> LinkAccessEventOutput:
    return LinkAccessEventOutput(
        id=str(event.id),
        accessed_at=event.accessed_at,
        device=event.device,
        latency_ms=event.latency_ms,
    )
