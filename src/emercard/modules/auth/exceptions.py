"""Expected authentication failures and their public API mappings."""


class AuthError(Exception):
    """Base class for failures safe to expose through the API error envelope."""

    status_code = 401
    code = "auth.invalid_session"
    message = "Phiên đăng nhập không hợp lệ hoặc đã hết hạn."


class DuplicateEmailError(AuthError):
    status_code = 409
    code = "auth.email_already_registered"
    message = "Email này đã được đăng ký."


class InvalidCredentialsError(AuthError):
    code = "auth.invalid_credentials"
    message = "Email hoặc mật khẩu không đúng."


class PrivateProfileAuthorizationError(AuthError):
    code = "auth.private_profile_authorization_invalid"
    message = "Không thể xác nhận quyền thay đổi thông tin riêng tư."


class AuthenticationRequiredError(AuthError):
    code = "auth.authentication_required"
    message = "Vui lòng đăng nhập để tiếp tục."


class InvalidSessionError(AuthError):
    code = "auth.invalid_session"
    message = "Phiên đăng nhập không hợp lệ hoặc đã hết hạn."


class RegistrationProvisioningError(AuthError):
    status_code = 503
    code = "auth.registration_provisioning_failed"
    message = "Không thể tạo tài khoản. Vui lòng thử lại sau."


class ForbiddenError(AuthError):
    status_code = 403
    code = "auth.forbidden"
    message = "Bạn không có quyền thực hiện thao tác này."
