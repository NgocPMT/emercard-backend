"""Application service for secure public scanner-location alerts."""

from __future__ import annotations

import hashlib
import time
from collections import deque
from collections.abc import Callable
from datetime import datetime
from typing import Protocol

from emercard.core.types import utc_now
from emercard.modules.location_alerts.models import (
    LocationAlertRequest,
    LocationAlertResult,
)
from emercard.modules.location_alerts.providers import (
    EmailDelivery,
    EmailProviderError,
    LocationProviderError,
    ReverseGeocoder,
)
from emercard.modules.public_links.errors import PublicProfileServiceUnavailableError
from emercard.modules.public_links.lookup import (
    PrivatePublicProfileLookupResult,
    PublicProfileLookupService,
)


class AuditRepository(Protocol):
    async def record(
        self,
        *,
        link_id: str,
        card_id: str | None,
        created_at: datetime,
        status: str,
        nearby_place: str | None,
        provider_id: str | None,
        location_bucket: str | None,
        client_key_hash: str,
    ) -> None: ...


class LocationAlertExternalError(PublicProfileServiceUnavailableError):
    """Google or Brevo failed without exposing provider details."""

    code = "location_alert.service_unavailable"
    message = "Không thể gửi cảnh báo vị trí lúc này. Vui lòng thử lại sau."


class LocationAlertLimiter:
    """Process-local abuse protection for public location-alert requests."""

    def __init__(
        self, *, token_cooldown_seconds: int, ip_window_seconds: int, ip_burst: int
    ) -> None:
        self._token_cooldown_seconds = token_cooldown_seconds
        self._ip_window_seconds = ip_window_seconds
        self._ip_burst = ip_burst
        self._token_last_sent: dict[str, float] = {}
        self._ip_requests: dict[str, deque[float]] = {}

    def allow(self, *, token: str, client_key: str, now: float | None = None) -> bool:
        current = time.monotonic() if now is None else now
        token_key = hashlib.sha256(token.encode("utf-8")).hexdigest()
        ip_key = hashlib.sha256(client_key.encode("utf-8")).hexdigest()
        self._prune(current)
        token_last = self._token_last_sent.get(token_key)
        if token_last is not None and current - token_last < self._token_cooldown_seconds:
            return False
        requests = self._ip_requests.setdefault(ip_key, deque())
        if len(requests) >= self._ip_burst:
            return False
        self._token_last_sent[token_key] = current
        requests.append(current)
        return True

    def _prune(self, current: float) -> None:
        cutoff = current - self._ip_window_seconds
        for key, requests in list(self._ip_requests.items()):
            while requests and requests[0] <= cutoff:
                requests.popleft()
            if not requests:
                del self._ip_requests[key]
        token_cutoff = current - self._token_cooldown_seconds
        self._token_last_sent = {
            key: timestamp
            for key, timestamp in self._token_last_sent.items()
            if timestamp > token_cutoff
        }


class LocationAlertService:
    """Resolve a public card, geocode the scanner location, and notify contacts."""

    def __init__(
        self,
        *,
        lookup: PublicProfileLookupService,
        geocoder: ReverseGeocoder,
        email_delivery: EmailDelivery,
        audit_repository: AuditRepository | None,
        limiter: LocationAlertLimiter,
        now: Callable[[], datetime] = utc_now,
    ) -> None:
        self._lookup = lookup
        self._geocoder = geocoder
        self._email_delivery = email_delivery
        self._audit_repository = audit_repository
        self._limiter = limiter
        self._now = now

    async def send(
        self,
        *,
        token: str,
        request: LocationAlertRequest,
        client_key: str,
    ) -> LocationAlertResult:
        if not self._limiter.allow(token=token, client_key=client_key):
            return LocationAlertResult(status="cooldown")

        resolved = await self._lookup.lookup_private(token)
        profile = resolved.profile
        client_hash = hashlib.sha256(client_key.encode("utf-8")).hexdigest()
        if not profile.location_alerts_enabled:
            await self._audit(
                resolved,
                status="unavailable",
                nearby_place=None,
                provider_id=None,
                location_bucket=None,
                client_key_hash=client_hash,
            )
            return LocationAlertResult(status="unavailable")

        recipients = [contact.email for contact in profile.emergency_contacts if contact.email]
        if not recipients:
            await self._audit(
                resolved,
                status="unavailable",
                nearby_place=None,
                provider_id=None,
                location_bucket=None,
                client_key_hash=client_hash,
            )
            return LocationAlertResult(status="unavailable")

        try:
            location = await self._geocoder.reverse(
                latitude=request.latitude, longitude=request.longitude
            )
        except LocationProviderError as error:
            await self._audit(
                resolved,
                status="provider_error",
                nearby_place=None,
                provider_id=None,
                location_bucket=_location_bucket(request.latitude, request.longitude),
                client_key_hash=client_hash,
            )
            raise LocationAlertExternalError from error

        provider_ids: list[str] = []
        try:
            for recipient in recipients:
                provider_id = await self._email_delivery.send_location_alert(
                    recipient=recipient,
                    display_name=profile.display_name,
                    nearby_place=location.nearby_place,
                    map_url=location.map_url,
                    occurred_at=request.occurred_at,
                    accuracy_meters=request.accuracy_meters,
                )
                if provider_id:
                    provider_ids.append(provider_id)
        except EmailProviderError as error:
            await self._audit(
                resolved,
                status="provider_error",
                nearby_place=location.nearby_place,
                provider_id=provider_ids[0] if provider_ids else None,
                location_bucket=_location_bucket(request.latitude, request.longitude),
                client_key_hash=client_hash,
            )
            raise LocationAlertExternalError from error

        await self._audit(
            resolved,
            status="sent",
            nearby_place=location.nearby_place,
            provider_id=provider_ids[0] if provider_ids else None,
            location_bucket=_location_bucket(request.latitude, request.longitude),
            client_key_hash=client_hash,
        )
        return LocationAlertResult(
            status="sent",
            provider_id=provider_ids[0] if provider_ids else None,
            recipient_count=len(recipients),
            nearby_place=location.nearby_place,
        )

    async def _audit(
        self,
        resolved: PrivatePublicProfileLookupResult,
        *,
        status: str,
        nearby_place: str | None,
        provider_id: str | None,
        location_bucket: str | None,
        client_key_hash: str,
    ) -> None:
        if self._audit_repository is None:
            return
        # The audit path must not turn a successful emergency notification into a 5xx.
        try:
            await self._audit_repository.record(
                link_id=resolved.link_id,
                card_id=resolved.card_id,
                created_at=self._now(),
                status=status,
                nearby_place=nearby_place,
                provider_id=provider_id,
                location_bucket=location_bucket,
                client_key_hash=client_key_hash,
            )
        except Exception:
            return


def _location_bucket(latitude: float, longitude: float) -> str:
    """Round coordinates to roughly kilometre-scale audit data."""

    return f"{round(latitude, 2):.2f},{round(longitude, 2):.2f}"
