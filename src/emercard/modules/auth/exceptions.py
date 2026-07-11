"""Expected authentication failures and their public API mappings."""


class AuthError(Exception):
    """Base class for failures safe to expose through the API error envelope."""

    status_code = 401
    code = "auth.invalid_session"
    message = "The authentication session is invalid or has expired."


class DuplicateEmailError(AuthError):
    status_code = 409
    code = "auth.email_already_registered"
    message = "An account with this email already exists."


class InvalidCredentialsError(AuthError):
    code = "auth.invalid_credentials"
    message = "Invalid email or password."


class AuthenticationRequiredError(AuthError):
    code = "auth.authentication_required"
    message = "Authentication is required."


class InvalidSessionError(AuthError):
    code = "auth.invalid_session"
    message = "The authentication session is invalid or has expired."


class RegistrationProvisioningError(AuthError):
    status_code = 503
    code = "auth.registration_provisioning_failed"
    message = "The account could not be provisioned. Please try again."


class ForbiddenError(AuthError):
    status_code = 403
    code = "auth.forbidden"
    message = "You do not have permission to perform this action."
