class IngestError(Exception):
    """An intentionally safe error, suitable for job history."""

    def __init__(self, message, *, code="source_error", retryable=True, blocked=None):
        super().__init__(message)
        self.code = code
        self.retryable = retryable
        self.blocked = code in {"login_required", "invalid_cookie", "rate_limited", "access_denied"} if blocked is None else blocked


class PartialCaptureError(IngestError):
    pass
