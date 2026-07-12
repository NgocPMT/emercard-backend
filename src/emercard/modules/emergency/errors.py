"""Safe failures for anonymous emergency-profile lookup."""


class EmergencyLookupError(Exception):
    """Base class for public lookup failures without sensitive detail."""

    status_code = 404
    code = "emergency_profile.not_found"
    message = "Thông tin cấp cứu không khả dụng cho thẻ này."


class EmergencyProfileNotFoundError(EmergencyLookupError):
    """The token or its associated card/profile is not publicly resolvable."""


class EmergencyProfileServiceUnavailableError(EmergencyLookupError):
    """A required lookup dependency failed without exposing its details."""

    status_code = 503
    code = "emergency_profile.service_unavailable"
    message = "Dịch vụ tra cứu cấp cứu tạm thời không khả dụng. Vui lòng thử lại sau."
