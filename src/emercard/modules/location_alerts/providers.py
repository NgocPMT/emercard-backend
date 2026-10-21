"""Outbound LocationIQ reverse-geocoding, SpeedSMS, and Brevo adapters."""

from __future__ import annotations

import unicodedata
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


class SmsProviderError(RuntimeError):
    """An external SMS provider could not accept the message."""


class ReverseGeocoder(Protocol):
    async def reverse(self, *, latitude: float, longitude: float) -> ReverseGeocodedLocation: ...


class SmsDelivery(Protocol):
    async def send_location_alert(
        self,
        *,
        recipient_phone: str,
        display_name: str | None,
        nearby_place: str,
        map_url: str,
        occurred_at: datetime,
        accuracy_meters: float,
    ) -> str | None: ...


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


class LocationIQReverseGeocoder:
    """Call LocationIQ's server-side reverse geocoding REST API."""

    def __init__(self, settings: Settings, client: httpx.AsyncClient | None = None) -> None:
        self._settings = settings
        self._client = client

    async def reverse(self, *, latitude: float, longitude: float) -> ReverseGeocodedLocation:
        map_url = _map_url(latitude, longitude)
        params = {
            "key": (
                self._settings.locationiq_api_key.get_secret_value()
                if self._settings.locationiq_api_key is not None
                else ""
            ),
            "lat": f"{latitude:.7f}",
            "lon": f"{longitude:.7f}",
            "format": "json",
            "accept-language": "vi",
        }
        endpoint = "https://us1.locationiq.com/v1/reverse"
        try:
            if self._client is None:
                async with httpx.AsyncClient(
                    timeout=self._settings.location_provider_timeout_seconds
                ) as client:
                    response = await client.get(endpoint, params=params)
            else:
                response = await self._client.get(endpoint, params=params)
            if response.status_code == 404:
                return ReverseGeocodedLocation(nearby_place="vị trí được chia sẻ", map_url=map_url)
            response.raise_for_status()
            payload: dict[str, Any] = response.json()
        except (httpx.HTTPError, ValueError) as error:
            raise LocationProviderError from error

        display_name = payload.get("display_name")
        nearby_place: str | None = display_name if isinstance(display_name, str) else None
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


def strip_vietnamese_accents(text: str) -> str:
    """Strip Vietnamese combining diacritics and convert đ/Đ to d/D for GSM-7 encoding."""
    text = text.replace("đ", "d").replace("Đ", "D")
    normalized = unicodedata.normalize("NFD", text)
    return "".join(c for c in normalized if unicodedata.category(c) != "Mn")


def format_location_alert_sms(
    *,
    display_name: str | None,
    nearby_place: str,
    map_url: str,
    max_length: int = 160,
) -> str:
    """Build a concise, unaccented SMS alert fitting within GSM-7 single-segment limit."""
    holder = strip_vietnamese_accents(
        display_name.strip() if display_name and display_name.strip() else "nguoi dung EmerCard"
    )
    place = strip_vietnamese_accents(nearby_place.strip() or "vi tri duoc chia se")

    prefix = "[EmerCard] Canh bao: Co nguoi quet the cua "
    middle = " tai "
    suffix = f". Vi tri: {map_url}"

    overhead = len(prefix) + len(middle) + len(suffix)
    available = max_length - overhead

    if available <= 0:
        return f"[EmerCard] Canh bao quet the: {map_url}"[:max_length]

    if len(holder) + len(place) > available:
        max_holder = min(len(holder), 20)
        holder = holder[:max_holder]
        remaining_for_place = available - len(holder)
        if len(place) > remaining_for_place:
            cutoff = max(0, remaining_for_place - 1)
            place = place[:cutoff] + "…" if remaining_for_place > 1 else ""

    return f"{prefix}{holder}{middle}{place}{suffix}"


class SpeedSmsDelivery:
    """Send transactional location alerts through SpeedSMS HTTP API."""

    def __init__(self, settings: Settings, client: httpx.AsyncClient | None = None) -> None:
        self._settings = settings
        self._client = client

    async def send_location_alert(
        self,
        *,
        recipient_phone: str,
        display_name: str | None,
        nearby_place: str,
        map_url: str,
        occurred_at: datetime,
        accuracy_meters: float,
    ) -> str | None:
        content = format_location_alert_sms(
            display_name=display_name,
            nearby_place=nearby_place,
            map_url=map_url,
            max_length=160,
        )
        token = (
            self._settings.speedsms_access_token.get_secret_value()
            if self._settings.speedsms_access_token is not None
            else ""
        )
        payload: dict[str, Any] = {
            "to": [recipient_phone],
            "content": content,
            "sms_type": self._settings.speedsms_sms_type,
            "sender": "",
        }
        endpoint = f"{self._settings.speedsms_api_base_url.rstrip('/')}/sms/send"
        auth = httpx.BasicAuth(username=token, password="x")

        try:
            if self._client is None:
                async with httpx.AsyncClient(
                    timeout=self._settings.sms_provider_timeout_seconds
                ) as client:
                    response = await client.post(endpoint, auth=auth, json=payload)
            else:
                response = await self._client.post(endpoint, auth=auth, json=payload)
            response.raise_for_status()
            body: dict[str, Any] = response.json()
        except (httpx.HTTPError, ValueError) as error:
            raise SmsProviderError from error

        status = body.get("status")
        code = str(body.get("code", ""))
        if status != "success" or code != "00":
            message = body.get("message", "unknown error")
            raise SmsProviderError(f"SpeedSMS returned error code {code}: {message}")

        data_obj: object = body.get("data")
        if isinstance(data_obj, dict):
            typed_data = cast(dict[str, Any], data_obj)
            invalid_phones: object = typed_data.get("invalidPhone")
            if isinstance(invalid_phones, list) and recipient_phone in invalid_phones:
                raise SmsProviderError(
                    f"SpeedSMS reported recipient {recipient_phone} as invalid"
                )
            tran_id = typed_data.get("tranId")
            if tran_id is not None:
                return str(tran_id)
        return None


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
