"""Safe failures for profile-backed public link operations."""


class PublicProfileError(Exception):
    """Base class for public-profile failures without sensitive detail."""

    status_code = 404
    code = "public_profile.not_found"
    message = "Hồ sơ công khai không khả dụng."


class PublicProfileNotFoundError(PublicProfileError):
    """The token did not resolve to any public profile link."""


class PublicProfileDisabledError(PublicProfileError):
    """The token resolved to a disabled public profile link."""

    status_code = 410
    code = "public_profile.disabled"
    message = "Liên kết hồ sơ công khai này đã bị vô hiệu hóa."


class PublicProfileRevokedError(PublicProfileError):
    """The token resolved to a revoked public profile link."""

    status_code = 410
    code = "public_profile.revoked"
    message = "Liên kết hồ sơ công khai này đã bị thu hồi."


class PublicProfileExpiredError(PublicProfileError):
    """The token resolved to an expired public profile link."""

    status_code = 410
    code = "public_profile.expired"
    message = "Liên kết hồ sơ công khai này đã hết hạn."


class PublicProfileNotReadyError(PublicProfileError):
    """The linked profile is missing or incomplete for public display."""

    status_code = 409
    code = "public_profile.not_ready"
    message = "Hồ sơ công khai này hiện chưa sẵn sàng."


class PublicProfileServiceUnavailableError(PublicProfileError):
    """A required lookup dependency failed without exposing its details."""

    status_code = 503
    code = "public_profile.service_unavailable"
    message = "Dịch vụ hồ sơ công khai tạm thời không khả dụng. Vui lòng thử lại sau."
