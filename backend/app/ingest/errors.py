class IngestError(Exception):
    """An intentionally safe error, suitable for job history."""

    def __init__(self, message, *, code="source_error", retryable=True, blocked=None, retry_after_seconds=None):
        super().__init__(message)
        self.code = code
        self.retryable = retryable
        self.retry_after_seconds = retry_after_seconds
        self.blocked = code in {"login_required", "invalid_cookie", "rate_limited", "access_denied"} if blocked is None else blocked


class PartialCaptureError(IngestError):
    pass


class IngestDeferred(IngestError):
    """Scheduling delay, not a failed source attempt; the queue must not charge it."""
    deferred = True

    def __init__(self, message, *, code="account_cooldown", retry_after_seconds=30):
        super().__init__(message, code=code, retryable=True, blocked=False,
                         retry_after_seconds=retry_after_seconds)
