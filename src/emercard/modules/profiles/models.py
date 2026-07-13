"""Medical-profile persistence, input, and response boundaries."""

from __future__ import annotations

import re
from enum import StrEnum
from typing import Annotated, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from emercard.core.config import get_settings
from emercard.core.types import ObjectIdValue, UtcDateTime

ProfileState = Literal["incomplete", "ready_to_publish"]
ProfileReadinessStatus = Literal["not_started", "incomplete", "ready"]


class Gender(StrEnum):
    FEMALE = "female"
    MALE = "male"
    OTHER = "other"
    PREFER_NOT_TO_SAY = "prefer_not_to_say"

    @classmethod
    def _missing_(cls, value: object) -> Gender | None:
        if value == "non_binary":
            return cls.OTHER
        return None


class BloodType(StrEnum):
    A_POSITIVE = "A+"
    A_NEGATIVE = "A-"
    B_POSITIVE = "B+"
    B_NEGATIVE = "B-"
    AB_POSITIVE = "AB+"
    AB_NEGATIVE = "AB-"
    O_POSITIVE = "O+"
    O_NEGATIVE = "O-"
    UNKNOWN = "unknown"


class ProfileModel(BaseModel):
    model_config = ConfigDict(extra="forbid", arbitrary_types_allowed=True)


def _bounded_text(value: str, *, maximum: int, field_name: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise ValueError(f"{field_name} không được để trống")
    if len(normalized) > maximum:
        raise ValueError(f"{field_name} không được dài quá {maximum} ký tự")
    return normalized


def _normalize_medical_list(value: list[str]) -> list[str]:
    settings = get_settings()

    normalized: list[str] = []
    seen: set[str] = set()
    for item in value:
        cleaned = item.strip()
        if not cleaned:
            continue
        if len(cleaned) > settings.medical_item_max_length:
            raise ValueError(
                f"mục thông tin y tế không được dài quá {settings.medical_item_max_length} ký tự"
            )
        key = cleaned.casefold()
        if key in seen:
            continue
        if len(normalized) >= settings.medical_list_max_items:
            raise ValueError(
                f"medical list must contain at most {settings.medical_list_max_items} items"
            )
        seen.add(key)
        normalized.append(cleaned)
    return normalized


_PHONE_PATTERN = re.compile(r"^0\d{9}$")


class EmergencyContactInput(ProfileModel):
    """Client input; contact IDs are intentionally not client-controlled."""

    name: str
    relationship: str
    phone: str

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str) -> str:
        return _bounded_text(
            value,
            maximum=get_settings().contact_name_max_length,
            field_name="tên người liên hệ",
        )

    @field_validator("relationship")
    @classmethod
    def validate_relationship(cls, value: str) -> str:
        return _bounded_text(
            value,
            maximum=get_settings().contact_relationship_max_length,
            field_name="mối quan hệ",
        )

    @field_validator("phone")
    @classmethod
    def validate_phone(cls, value: str) -> str:
        normalized = _bounded_text(
            value,
            maximum=get_settings().contact_phone_max_length,
            field_name="số điện thoại người liên hệ",
        )
        if not _PHONE_PATTERN.fullmatch(normalized):
            raise ValueError("Số điện thoại phải có đúng 10 chữ số và bắt đầu bằng 0")
        return normalized


class EmergencyContactDocument(ProfileModel):
    """Embedded persistence contact, including legacy phone values."""

    id: str = Field(default_factory=lambda: str(uuid4()), min_length=1, max_length=36)
    name: str
    relationship: str
    phone: str

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str) -> str:
        return _bounded_text(
            value,
            maximum=get_settings().contact_name_max_length,
            field_name="tên người liên hệ",
        )

    @field_validator("relationship")
    @classmethod
    def validate_relationship(cls, value: str) -> str:
        return _bounded_text(
            value,
            maximum=get_settings().contact_relationship_max_length,
            field_name="mối quan hệ",
        )


def _empty_document_contacts() -> list[EmergencyContactDocument]:
    return []


def _empty_input_contacts() -> list[EmergencyContactInput]:
    return []


class EmergencyContactPublic(ProfileModel):
    """Explicit public allowlist; the internal contact ID is excluded."""

    name: str
    relationship: str
    phone: str


class PublicAccessDocument(ProfileModel):
    """Legacy public-link state retained until card-backed access is implemented."""

    token: str | None = Field(default=None, min_length=1, max_length=512)
    enabled: bool = False
    published_at: UtcDateTime | None = None
    regenerated_at: UtcDateTime | None = None

    @model_validator(mode="after")
    def validate_enabled_state(self) -> PublicAccessDocument:
        if self.enabled and self.token is None:
            raise ValueError("Quyền truy cập công khai đã bật phải có mã token")
        if self.enabled and self.published_at is None:
            raise ValueError("Quyền truy cập công khai đã bật phải có thời điểm công khai")
        return self


MedicalList = Annotated[list[str], Field(default_factory=list)]


class ProfileDocument(ProfileModel):
    """MongoDB medical profile document."""

    id: ObjectIdValue = Field(alias="_id")
    user_id: ObjectIdValue
    display_name: str | None = None
    birth_year: int | None = None
    gender: Gender | None = None
    blood_type: BloodType | None = None
    critical_allergies: MedicalList
    important_conditions: MedicalList
    critical_medications: MedicalList
    emergency_note: str | None = None
    emergency_contacts: list[EmergencyContactDocument] = Field(
        default_factory=_empty_document_contacts
    )
    public_access: PublicAccessDocument = Field(default_factory=PublicAccessDocument)
    created_at: UtcDateTime
    updated_at: UtcDateTime

    @field_validator("display_name")
    @classmethod
    def validate_display_name(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _bounded_text(
            value,
            maximum=get_settings().display_name_max_length,
            field_name="display name",
        )

    @field_validator("birth_year")
    @classmethod
    def validate_birth_year(cls, value: int | None) -> int | None:
        if value is None:
            return None
        settings = get_settings()
        if not settings.birth_year_min <= value <= settings.birth_year_max:
            raise ValueError(
                f"birth year must be between {settings.birth_year_min} "
                f"and {settings.birth_year_max}"
            )
        return value

    @field_validator("critical_allergies", "important_conditions", "critical_medications")
    @classmethod
    def validate_medical_list(cls, value: list[str]) -> list[str]:
        return _normalize_medical_list(value)

    @field_validator("emergency_note")
    @classmethod
    def validate_emergency_note(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _bounded_text(
            value,
            maximum=get_settings().emergency_note_max_length,
            field_name="emergency note",
        )

    @field_validator("emergency_contacts")
    @classmethod
    def validate_contacts(
        cls, value: list[EmergencyContactDocument]
    ) -> list[EmergencyContactDocument]:
        maximum = get_settings().emergency_contacts_max_count
        if len(value) > maximum:
            raise ValueError(f"emergency contacts must contain at most {maximum} items")
        ids = [contact.id for contact in value]
        if len(ids) != len(set(ids)):
            raise ValueError("emergency contact IDs must be unique")
        return value


class ProfileUpsertInput(ProfileModel):
    """Profile save input; ownership, timestamps, IDs, and public state are server-controlled."""

    display_name: str | None = None
    birth_year: int | None = None
    gender: Gender | None = None
    blood_type: BloodType | None = None
    critical_allergies: MedicalList
    important_conditions: MedicalList
    critical_medications: MedicalList
    emergency_note: str | None = None
    emergency_contacts: list[EmergencyContactInput] = Field(default_factory=_empty_input_contacts)

    @field_validator("display_name")
    @classmethod
    def validate_display_name(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _bounded_text(
            value,
            maximum=get_settings().display_name_max_length,
            field_name="display name",
        )

    @field_validator("birth_year")
    @classmethod
    def validate_birth_year(cls, value: int | None) -> int | None:
        if value is None:
            return None
        settings = get_settings()
        if not settings.birth_year_min <= value <= settings.birth_year_max:
            raise ValueError(
                f"birth year must be between {settings.birth_year_min} "
                f"and {settings.birth_year_max}"
            )
        return value

    @field_validator("critical_allergies", "important_conditions", "critical_medications")
    @classmethod
    def validate_medical_list(cls, value: list[str]) -> list[str]:
        return _normalize_medical_list(value)

    @field_validator("emergency_note")
    @classmethod
    def validate_emergency_note(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _bounded_text(
            value,
            maximum=get_settings().emergency_note_max_length,
            field_name="emergency note",
        )

    @field_validator("emergency_contacts")
    @classmethod
    def validate_contacts(cls, value: list[EmergencyContactInput]) -> list[EmergencyContactInput]:
        maximum = get_settings().emergency_contacts_max_count
        if len(value) > maximum:
            raise ValueError(f"emergency contacts must contain at most {maximum} items")
        return value


class PublicLinkActionInput(ProfileModel):
    """Empty action body reserved for authenticated publish/link operations."""

    model_config = ConfigDict(extra="forbid")


class ProfileDashboardOutput(ProfileModel):
    """Authenticated dashboard response, including link state but not account data."""

    id: str
    display_name: str | None
    birth_year: int | None
    gender: Gender | None
    blood_type: BloodType | None
    critical_allergies: list[str]
    important_conditions: list[str]
    critical_medications: list[str]
    emergency_note: str | None
    emergency_contacts: list[EmergencyContactDocument]
    public_access: PublicAccessDocument
    state: ProfileState
    created_at: UtcDateTime
    updated_at: UtcDateTime


class AuthenticatedProfileOutput(ProfileModel):
    """Sanitized current-user profile response without ownership or readiness metadata."""

    display_name: str | None
    birth_year: int | None
    gender: Gender | None
    blood_type: BloodType | None
    critical_allergies: list[str]
    important_conditions: list[str]
    critical_medications: list[str]
    emergency_note: str | None
    emergency_contacts: list[EmergencyContactPublic]
    created_at: UtcDateTime
    updated_at: UtcDateTime


class ProfileReadiness(ProfileModel):
    """Derived profile completion state for authenticated UI flows."""

    status: ProfileReadinessStatus
    missing_fields: list[str]
    required_contact_count: int
    completed_required_field_count: int
    total_required_field_count: int


class ProfileView(ProfileModel):
    """Current-user profile envelope with explicit readiness metadata."""

    profile: AuthenticatedProfileOutput | None
    readiness: ProfileReadiness


class PublicProfileOutput(ProfileModel):
    """Explicit emergency-page allowlist with no persistence or ownership metadata."""

    display_name: str | None
    birth_year: int | None
    gender: Gender | None
    blood_type: BloodType | None
    critical_allergies: list[str]
    important_conditions: list[str]
    critical_medications: list[str]
    emergency_note: str | None
    emergency_contacts: list[EmergencyContactPublic]
    profile_updated_at: UtcDateTime


def _public_contacts(profile: ProfileDocument) -> list[EmergencyContactPublic]:
    return [
        EmergencyContactPublic(
            name=contact.name,
            relationship=contact.relationship,
            phone=contact.phone,
        )
        for contact in profile.emergency_contacts
    ]


def _required_profile_fields(profile: ProfileDocument | None) -> tuple[list[str], int]:
    required_fields = ["display_name", "birth_year", "gender", "blood_type", "emergency_contacts"]
    if profile is None:
        return required_fields, 0
    completed = sum(
        (
            bool(profile.display_name),
            profile.birth_year is not None,
            profile.gender is not None,
            profile.blood_type is not None,
            len(profile.emergency_contacts) > 0,
        )
    )
    missing = [
        field
        for field, satisfied in zip(
            required_fields,
            (
                bool(profile.display_name),
                profile.birth_year is not None,
                profile.gender is not None,
                profile.blood_type is not None,
                len(profile.emergency_contacts) > 0,
            ),
            strict=True,
        )
        if not satisfied
    ]
    return missing, completed


def profile_readiness(profile: ProfileDocument | None) -> ProfileReadiness:
    """Derive the authenticated profile completion state."""

    missing_fields, completed = _required_profile_fields(profile)
    if profile is None:
        return ProfileReadiness(
            status="not_started",
            missing_fields=missing_fields,
            required_contact_count=1,
            completed_required_field_count=0,
            total_required_field_count=5,
        )
    if profile_state(profile) == "ready_to_publish":
        return ProfileReadiness(
            status="ready",
            missing_fields=[],
            required_contact_count=1,
            completed_required_field_count=5,
            total_required_field_count=5,
        )
    return ProfileReadiness(
        status="incomplete",
        missing_fields=missing_fields,
        required_contact_count=1,
        completed_required_field_count=completed,
        total_required_field_count=5,
    )


def to_authenticated_profile(profile: ProfileDocument) -> AuthenticatedProfileOutput:
    """Map a persisted profile to the allowlisted authenticated response."""

    return AuthenticatedProfileOutput(
        display_name=profile.display_name,
        birth_year=profile.birth_year,
        gender=profile.gender,
        blood_type=profile.blood_type,
        critical_allergies=list(profile.critical_allergies),
        important_conditions=list(profile.important_conditions),
        critical_medications=list(profile.critical_medications),
        emergency_note=profile.emergency_note,
        emergency_contacts=_public_contacts(profile),
        created_at=profile.created_at,
        updated_at=profile.updated_at,
    )


def to_profile_view(profile: ProfileDocument | None) -> ProfileView:
    """Map the current-user profile envelope for the authenticated API."""

    return ProfileView(
        profile=to_authenticated_profile(profile) if profile is not None else None,
        readiness=profile_readiness(profile),
    )


def to_public_profile(profile: ProfileDocument) -> PublicProfileOutput:
    """Map a persisted profile to the stable public emergency projection."""

    return PublicProfileOutput(
        display_name=profile.display_name,
        birth_year=profile.birth_year,
        gender=profile.gender,
        blood_type=profile.blood_type,
        critical_allergies=list(profile.critical_allergies),
        important_conditions=list(profile.important_conditions),
        critical_medications=list(profile.critical_medications),
        emergency_note=profile.emergency_note,
        emergency_contacts=_public_contacts(profile),
        profile_updated_at=profile.updated_at,
    )


def profile_state(profile: ProfileDocument) -> ProfileState:
    """Derive profile completeness independently of legacy public-link state."""

    required_values_present = all(
        (
            profile.display_name,
            profile.birth_year is not None,
            profile.gender is not None,
            profile.blood_type is not None,
            len(profile.emergency_contacts) > 0,
        )
    )
    return "ready_to_publish" if required_values_present else "incomplete"
