from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from emercard.modules.profiles import (
    BloodType,
    EmergencyContactDocument,
    EmergencyContactInput,
    Gender,
    ProfileDocument,
    ProfileUpsertInput,
    PublicAccessDocument,
    PublicProfileOutput,
    profile_state,
)
from emercard.modules.users import (
    UserDocument,
    UserLoginInput,
    UserRegistrationInput,
    canonicalize_email,
)


def test_email_is_canonicalized_without_provider_specific_transformations() -> None:
    assert canonicalize_email("  Person+demo@Example.COM ") == "person+demo@example.com"
    assert (
        UserLoginInput(email=" Person@Example.COM ", password="password").email
        == "person@example.com"
    )


def test_user_document_defaults_legacy_role_and_serializes_object_id() -> None:
    document = UserDocument.model_validate(
        {
            "_id": "507f1f77bcf86cd799439011",
            "email": "person@example.com",
            "password_hash": "argon2-hash",
            "created_at": "2026-01-01T00:00:00Z",
            "updated_at": datetime(2026, 1, 1, tzinfo=UTC),
        }
    )

    serialized = document.model_dump(mode="json", by_alias=True)
    assert serialized["_id"] == "507f1f77bcf86cd799439011"
    assert serialized["role"] == "user"
    assert serialized["created_at"].endswith("Z")


def test_registration_input_does_not_accept_a_client_selected_role() -> None:
    with pytest.raises(ValidationError):
        UserRegistrationInput.model_validate(
            {"email": "person@example.com", "password": "password-123", "role": "admin"}
        )


def test_profile_input_does_not_accept_client_controlled_ids_or_public_state() -> None:
    with pytest.raises(ValidationError):
        ProfileUpsertInput.model_validate(
            {
                "user_id": "507f1f77bcf86cd799439011",
                "emergency_contacts": [],
            }
        )


@pytest.mark.parametrize(
    "phone",
    ["1234567890", "090123456", "09012345678", "090 1234567", "+84901234567"],
)
def test_contact_phone_must_be_a_ten_digit_vietnamese_number(phone: str) -> None:
    with pytest.raises(ValidationError):
        EmergencyContactInput(name="Alex Example", relationship="Friend", phone=phone)


def test_legacy_profile_phone_can_be_read_without_relaxing_new_input_validation() -> None:
    legacy_contact = EmergencyContactDocument(
        name="Alex Example",
        relationship="Friend",
        phone="036493303822",
    )

    assert legacy_contact.phone == "036493303822"
    with pytest.raises(ValidationError):
        EmergencyContactInput(
            name="Alex Example",
            relationship="Friend",
            phone="036493303822",
        )


def test_profile_limits_and_contact_ids_are_enforced() -> None:
    contact = EmergencyContactDocument(
        name="Alex Example",
        relationship="Friend",
        phone="0901234567",
    )
    assert contact.id

    with pytest.raises(ValidationError):
        EmergencyContactInput(name="", relationship="Friend", phone="0901234567")
    with pytest.raises(ValidationError):
        ProfileUpsertInput(birth_year=1800, emergency_contacts=[])


def _profile(**overrides: object) -> ProfileDocument:
    values: dict[str, object] = {
        "_id": "507f1f77bcf86cd799439011",
        "user_id": "507f1f77bcf86cd799439012",
        "display_name": "Alex Example",
        "birth_year": 1995,
        "gender": Gender.PREFER_NOT_TO_SAY,
        "blood_type": BloodType.O_POSITIVE,
        "critical_allergies": [],
        "important_conditions": [],
        "critical_medications": [],
        "emergency_contacts": [
            {
                "name": "Sam Example",
                "relationship": "Friend",
                "phone": "0901234567",
            }
        ],
        "created_at": "2026-01-01T00:00:00Z",
        "updated_at": "2026-01-01T00:00:00Z",
    }
    values.update(overrides)
    return ProfileDocument.model_validate(values)


def test_profile_state_ignores_legacy_public_link_state() -> None:
    assert profile_state(_profile(display_name=None)) == "incomplete"
    ready = _profile()
    assert profile_state(ready) == "ready_to_publish"
    published = _profile(
        public_access={
            "token": "secret-token",
            "enabled": True,
            "published_at": "2026-01-01T00:00:00Z",
        }
    )
    assert profile_state(published) == "ready_to_publish"
    disabled = _profile(public_access={"token": "old-token", "enabled": False})
    assert profile_state(disabled) == "ready_to_publish"


def test_public_output_is_an_explicit_allowlist() -> None:
    output = PublicProfileOutput.model_validate(
        {
            "display_name": "Alex Example",
            "birth_year": 1995,
            "gender": "prefer_not_to_say",
            "blood_type": "O+",
            "critical_allergies": [],
            "important_conditions": [],
            "critical_medications": [],
            "emergency_note": None,
            "emergency_contacts": [
                {"name": "Sam Example", "relationship": "Friend", "phone": "0901234567"}
            ],
        }
    )
    serialized = output.model_dump()
    assert "user_id" not in serialized
    assert "token" not in serialized
    assert "id" not in serialized["emergency_contacts"][0]


def test_enabled_public_access_requires_token_and_publication_timestamp() -> None:
    with pytest.raises(ValidationError):
        PublicAccessDocument(enabled=True)
    with pytest.raises(ValidationError):
        PublicAccessDocument(token="token", enabled=True)
