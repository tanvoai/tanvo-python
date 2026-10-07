from __future__ import annotations

from typing import List, Optional

KEYS_URL = "https://tanvo.ai/settings/apikeys"


class TanvoError(Exception):
    """Base class. `code` is the API's stable error string, `status` the HTTP status (0 for network errors)."""

    def __init__(self, code: str, message: str, status: int = 0, issues: Optional[List[str]] = None, retry_after: Optional[float] = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status
        self.issues = issues or []
        self.retry_after = retry_after

    def __str__(self) -> str:
        extra = f" ({'; '.join(self.issues)})" if self.issues else ""
        return f"{self.code}: {self.message}{extra}"


class InvalidRequest(TanvoError):
    """Bad parameters, a prompt the moderator refused, a file too large, or a record that does not exist. Do not retry."""


class AuthError(TanvoError):
    """No identity, or an invalid / revoked API key."""


class QuotaError(TanvoError):
    """Out of credits, the free tier does not cover this, or a limit was hit. `busy` and `rate_limited` clear by waiting."""


class ServiceError(TanvoError):
    """The service or an upstream model failed. Safe to retry with the same request_key."""


class WebhookVerificationError(TanvoError):
    """A webhook's signature or timestamp did not check out."""


_KINDS = {
    InvalidRequest: {"invalid", "rejected", "too_large", "not_found"},
    AuthError: {"unauthorized", "invalid_api_key", "unknown_anon_id"},
    QuotaError: {"credits", "allowance", "needs_api_key", "anon_ip_daily", "trial_closed", "rate_limited", "limit", "busy"},
}
NEEDS_KEY = {"needs_api_key", "allowance", "anon_ip_daily", "trial_closed"}


def error_for(code: str, message: str, status: int, issues: Optional[List[str]] = None, retry_after: Optional[float] = None, has_key: bool = True) -> TanvoError:
    cls = next((c for c, codes in _KINDS.items() if code in codes), ServiceError)
    if code in NEEDS_KEY and not has_key:
        message = f"{message} Get a key at {KEYS_URL} (then pass api_key= or set TANVO_API_KEY)."
    return cls(code, message, status, issues, retry_after)
