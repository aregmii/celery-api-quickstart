class AppException(Exception):
    """Base exception for application errors."""
    pass


class InvalidApiKeyError(AppException):
    """Raised when API key is not found in database."""
    pass


class InsufficientCreditsError(AppException):
    """Raised when user has no credits remaining."""
    pass


class TaskNotFoundError(AppException):
    """Raised when task doesn't exist or user doesn't own it."""
    pass


class UserNotFoundError(AppException):
    """Raised when user doesn't exist."""
    pass