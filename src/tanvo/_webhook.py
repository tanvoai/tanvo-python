from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from typing import Mapping, Optional, Union

from ._errors import WebhookVerificationError
from ._types import Generation


def _header(headers: Mapping[str, str], name: str) -> Optional[str]:
    for k, v in headers.items():
        if k.lower() == name:
            return v
    return None


def verify_webhook(secret: str, headers: Mapping[str, str], body: Union[bytes, str], tolerance: int = 300) -> Generation:
    """Checks a completion webhook (Standard Webhooks) and returns the Generation it carries.

    Pass the raw request body exactly as received, not re-serialised JSON. Raises WebhookVerificationError when the
    signature does not match or the timestamp is more than `tolerance` seconds away. The same `webhook-id` can arrive
    more than once (retries, manual resends): de-duplicate on it.
    """
    raw = body.encode() if isinstance(body, str) else body
    msg_id, ts, sig = _header(headers, "webhook-id"), _header(headers, "webhook-timestamp"), _header(headers, "webhook-signature")
    if not msg_id or not ts or not sig:
        raise WebhookVerificationError("missing_headers", "webhook-id, webhook-timestamp and webhook-signature are required.")
    try:
        sent = int(ts)
    except ValueError:
        raise WebhookVerificationError("bad_timestamp", "webhook-timestamp is not a number.") from None
    if abs(time.time() - sent) > tolerance:
        raise WebhookVerificationError("stale", f"The webhook timestamp is more than {tolerance} s from now.")
    key = base64.b64decode(secret[len("whsec_"):] if secret.startswith("whsec_") else secret)
    expected = base64.b64encode(hmac.new(key, f"{msg_id}.{ts}.".encode() + raw, hashlib.sha256).digest()).decode()
    candidates = [part.split(",", 1)[1] for part in sig.split() if part.startswith("v1,")]
    if not any(hmac.compare_digest(expected, c) for c in candidates):
        raise WebhookVerificationError("bad_signature", "The webhook signature does not match. Check the secret under Settings → Webhooks.")
    return Generation.from_dict(json.loads(raw))
