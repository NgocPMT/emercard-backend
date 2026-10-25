"""Outbound LocationIQ reverse-geocoding and SpeedSMS adapters."""

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


class LocationIQReverseGeocoder:
    """Call LocationIQ's server-side reverse geocoding REST API."""

    def __init__(self, settings: Settings, client: httpx.AsyncClient | None = None) -> None:
        self._settings = settings
        self._client = client

    async def reverse(self, *, latitude: float, longitude: float) -> ReverseGeocodedLocation:
        map_url = build_map_url(latitude, longitude)
        api_key = (
            self._settings.locationiq_api_key.get_secret_value()
            if self._settings.locationiq_api_key is not None
            else ""
        )
        if not api_key:
            return ReverseGeocodedLocation(nearby_place="vị trí được chia sẻ", map_url=map_url)

        params = {
            "key": api_key,
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
            cutoff = max(0, remaining_for_place - 3)
            place = place[:cutoff] + "..." if remaining_for_place >= 3 else ""

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
            "sender": self._settings.speedsms_sender,
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


class AndroidSmsDelivery:
    """Send transactional location alerts through an Android SMS Gateway."""

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
        raw_url = self._settings.android_sms_gateway_url.rstrip("/")
        if raw_url.endswith("/message") or raw_url.endswith("/messages"):
            endpoint = raw_url
        else:
            endpoint = f"{raw_url}/message"
        username = self._settings.android_sms_gateway_username
        password = (
            self._settings.android_sms_gateway_password.get_secret_value()
            if self._settings.android_sms_gateway_password is not None
            else ""
        )
        recipient = recipient_phone.strip()
        if recipient.startswith("0") and len(recipient) == 10:
            formatted_phone = f"+84{recipient[1:]}"
        elif recipient.startswith("84") and len(recipient) == 11:
            formatted_phone = f"+{recipient}"
        else:
            formatted_phone = recipient

        auth = httpx.BasicAuth(username=username, password=password)
        payload: dict[str, Any] = {
            "phoneNumbers": [formatted_phone],
            "textMessage": {
                "text": content,
            },
            "withDeliveryReport": False,
        }

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

        message_id = body.get("id")
        return str(message_id) if message_id is not None else None


def build_map_url(latitude: float, longitude: float) -> str:
    return "https://www.google.com/maps?" + urlencode(
        {"q": f"{latitude:.7f},{longitude:.7f}"}
    )


_map_url = build_map_url
