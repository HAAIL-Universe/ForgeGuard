"""Domain exception hierarchy for ForgeGuard.

Services raise these instead of bare ``ValueError`` so that the global
exception handler in ``main.py`` can map them to the correct HTTP status
code without fragile string matching.

During migration, existing ``ValueError`` raises are also handled
centrally (see ``_handle_value_error`` in ``main.py``).
"""


class ForgeError(Exception):
    """Base for all domain exceptions."""

    def __init__(self, message: str = "An unexpected error occurred", *, status_code: int = 500):
        super().__init__(message)
        self.status_code = status_code


class NotFoundError(ForgeError):
    """Resource not found (404)."""

    def __init__(self, message: str = "Not found"):
        super().__init__(message, status_code=404)


class BadRequestError(ForgeError):
    """Client sent an invalid request (400)."""

    def __init__(self, message: str = "Bad request"):
        super().__init__(message, status_code=400)


class BuildError(ForgeError):
    """Build-related error (400 by default)."""

    def __init__(self, message: str = "Build error", *, status_code: int = 400):
        super().__init__(message, status_code=status_code)


class BuildPausedError(BuildError):
    """Build is paused and awaiting user action (409)."""

    def __init__(self, message: str = "Build is paused"):
        super().__init__(message, status_code=409)


class CostCapExceededError(BuildError):
    """Build cost exceeds the user-defined spend cap (402)."""

    def __init__(self, message: str = "Cost cap exceeded"):
        super().__init__(message, status_code=402)


class ContractViolationError(ForgeError):
    """A project contract has been violated (422)."""

    def __init__(self, message: str = "Contract violation"):
        super().__init__(message, status_code=422)


class AuthError(ForgeError):
    """Authentication or authorization failure (401/403)."""

    def __init__(self, message: str = "Not authorized", *, status_code: int = 401):
        super().__init__(message, status_code=status_code)


def format_error_response(
    *,
    error: str,
    detail: object = None,
    request_id: str = "",
) -> dict:
    """Build a structured error response dict.

    Parameters
    ----------
    error : str
        Short error title (e.g. ``"Internal Server Error"``).
    detail : object
        Human-readable detail string or validation error list.
    request_id : str
        The request ID for tracing.

    Returns
    -------
    dict
        ``{"error": ..., "detail": ..., "request_id": ...}``
    """
    return {
        "error": error,
        "detail": detail if detail is not None else error,
        "request_id": request_id,
    }