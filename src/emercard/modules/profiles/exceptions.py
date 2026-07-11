"""Expected failures from authenticated medical-profile operations."""


class ProfileError(Exception):
    """Base class for profile failures safe to expose through the API."""

    status_code = 500
    code = "profile.service_error"
    message = "Không thể tải hồ sơ y tế."


class ProfileProvisioningInconsistentError(ProfileError):
    """The authenticated user unexpectedly has no provisioned profile."""

    code = "profile.provisioning_inconsistent"
    message = "Không thể tải hồ sơ y tế."


class ProfileServiceUnavailableError(ProfileError):
    """The profile persistence service could not complete an operation."""

    status_code = 503
    code = "profile.service_unavailable"
    message = "Dịch vụ hồ sơ y tế tạm thời không khả dụng. Vui lòng thử lại sau."
