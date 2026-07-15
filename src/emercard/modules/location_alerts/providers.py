"""Outbound Google reverse-geocoding and Brevo email adapters."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Protocol, cast
from urllib.parse import urlencode

import httpx

from emercard.core.config import Settings
from emercard.modules.location_alerts.models import ReverseGeocodedLocation


class LocationProviderError(RuntimeError):
    """An external location provider could not answer safely."""


class EmailProviderError(RuntimeError):
    """An external email provider could not accept the message."""


class ReverseGeocoder(Protocol):
    async def reverse(self, *, latitude: float, longitude: float) -> ReverseGeocodedLocation: ...


class EmailDelivery(Protocol):
    async def send_location_alert(
        self,
        *,
        recipient: str,
        display_name: str | None,
        nearby_place: str,
        map_url: str,
        occurred_at: datetime,
        accuracy_meters: float,
    ) -> str | None: ...


class GoogleReverseGeocoder:
    """Call Google's server-side reverse geocoding REST API."""

    def __init__(self, settings: Settings, client: httpx.AsyncClient | None = None) -> None:
        self._settings = settings
        self._client = client

    async def reverse(self, *, latitude: float, longitude: float) -> ReverseGeocodedLocation:
        map_url = _map_url(latitude, longitude)
        params = {"languageCode": "vi"}
        headers = {
            "X-Goog-Api-Key": (
                self._settings.google_geocoding_api_key.get_secret_value()
                if self._settings.google_geocoding_api_key is not None
                else ""
            ),
            "X-Goog-FieldMask": "results.formattedAddress",
        }
        endpoint = (
            "https://geocode.googleapis.com/v4/geocode/location/"
            f"{latitude:.7f},{longitude:.7f}"
        )
        try:
            if self._client is None:
                async with httpx.AsyncClient(
                    timeout=self._settings.location_provider_timeout_seconds
                ) as client:
                    response = await client.get(endpoint, params=params, headers=headers)
            else:
                response = await self._client.get(endpoint, params=params, headers=headers)
            response.raise_for_status()
            payload: dict[str, Any] = response.json()
        except (httpx.HTTPError, ValueError) as error:
            raise LocationProviderError from error

        results = payload.get("results")
        nearby_place: str | None = None
        if isinstance(results, list) and results:
            first_result = cast(object, results[0])
            if isinstance(first_result, dict):
                candidate: object = cast(dict[str, object], first_result).get("formatted_address")
            else:
                candidate = None
            if isinstance(candidate, str):
                nearby_place = candidate
        if not nearby_place or not nearby_place.strip():
            nearby_place = "vị trí được chia sẻ"
        return ReverseGeocodedLocation(nearby_place=nearby_place.strip(), map_url=map_url)


class BrevoEmailDelivery:
    """Send transactional location alerts through Brevo's SMTP API."""

    def __init__(self, settings: Settings, client: httpx.AsyncClient | None = None) -> None:
        self._settings = settings
        self._client = client

    async def send_location_alert(
        self,
        *,
        recipient: str,
        display_name: str | None,
        nearby_place: str,
        map_url: str,
        occurred_at: datetime,
        accuracy_meters: float,
    ) -> str | None:
        holder = (
            display_name.strip()
            if display_name and display_name.strip()
            else "một người dùng EmerCard"
        )
        timestamp = occurred_at.astimezone().strftime("%d/%m/%Y %H:%M")
        subject = "Cảnh báo EmerCard: vị trí được chia sẻ"
        text = (
            f"Xin chào,\n\n"
            f"Có người vừa quét EmerCard của {holder} và chia sẻ vị trí gần {nearby_place}.\n\n"
            f"Thời gian: {timestamp}\n"
            f"Độ chính xác ước tính: khoảng {round(accuracy_meters)} mét.\n"
            f"Bản đồ: {map_url}\n\n"
            "Vị trí do trình duyệt cung cấp và có thể không chính xác tuyệt đối. "
            "Đây là email tự động từ EmerCard."
        )
        html = "<br>".join(_escape_html(line) for line in text.splitlines())
        payload: dict[str, Any] = {
            "sender": {
                "email": self._settings.brevo_sender_email,
                "name": self._settings.brevo_sender_name,
            },
            "to": [{"email": recipient}],
            "subject": subject,
            "textContent": text,
            "htmlContent": html,
        }
        if self._settings.brevo_reply_to_email:
            payload["replyTo"] = {"email": self._settings.brevo_reply_to_email}
        headers = {
            "accept": "application/json",
            "api-key": self._settings.brevo_api_key.get_secret_value()
            if self._settings.brevo_api_key is not None
            else "",
            "content-type": "application/json",
        }
        try:
            if self._client is None:
                async with httpx.AsyncClient(
                    timeout=self._settings.email_provider_timeout_seconds
                ) as client:
                    response = await client.post(
                        "https://api.brevo.com/v3/smtp/email", headers=headers, json=payload
                    )
            else:
                response = await self._client.post(
                    "https://api.brevo.com/v3/smtp/email", headers=headers, json=payload
                )
            response.raise_for_status()
            body = response.json()
        except (httpx.HTTPError, ValueError) as error:
            raise EmailProviderError from error
        message_id = body.get("messageId")
        return message_id if isinstance(message_id, str) else None


def _map_url(latitude: float, longitude: float) -> str:
    return "https://www.google.com/maps?" + urlencode(
        {"q": f"{latitude:.7f},{longitude:.7f}"}
    )


def _escape_html(value: str) -> str:
    return (
        value.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&#39;")
    )
