class NotFoundError(ForgeError):
    """Resource not found (404)."""

    status_code = 404


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