"""Outbound webhook HTTP delivery. Uses the stdlib (`urllib`) rather than
adding a new HTTP client dependency (root CLAUDE.md: dependencies need
justification) — this is a single POST with a short timeout, not a client
library's worth of functionality.

Tests select the deterministic fake via AUTOMATION_WEBHOOK_SENDER_BACKEND
(phase section 90) — automated tests must never call a real external
endpoint. `get_sender()` is resolved fresh on every call (never cached at
import time) so tests can override the setting or monkeypatch this
function directly.
"""

import time
import urllib.error
import urllib.request

from django.conf import settings

from automation.actions.errors import AutomationTransientError

DEFAULT_TIMEOUT_SECONDS = 10


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Refuse every redirect rather than follow it.

    `webhook_security.validate_webhook_url` checks the URL once, before this
    module ever sees it — urllib's DEFAULT opener follows a 3xx response
    automatically, which would let a webhook endpoint that passed validation
    redirect the actual request to an unvalidated target (e.g. the cloud
    metadata address, 169.254.169.254) entirely unchecked. A legitimate
    webhook receiver has no reason to redirect a POST anyway, so the 3xx
    response is returned to the caller as-is (recorded as that status code)
    instead of raising or retrying.
    """

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


_OPENER = urllib.request.build_opener(_NoRedirectHandler)


class WebhookResponse:
    def __init__(self, *, http_status: int, latency_ms: int):
        self.http_status = http_status
        self.latency_ms = latency_ms


def _http_send(*, url: str, payload: bytes, headers: dict, timeout: float) -> WebhookResponse:
    request = urllib.request.Request(url, data=payload, headers=headers, method="POST")  # noqa: S310 -- url is validated by webhook_security.validate_webhook_url before this is ever called
    started = time.monotonic()
    try:
        with _OPENER.open(request, timeout=timeout) as response:  # nosec B310 -- url is validated by webhook_security.validate_webhook_url (https+SSRF-checked) before this is ever called; _OPENER never follows redirects
            status = response.status
    except urllib.error.HTTPError as exc:
        status = exc.code
    except (urllib.error.URLError, TimeoutError) as exc:
        raise AutomationTransientError(f"Webhook delivery failed: {exc}") from exc

    latency_ms = int((time.monotonic() - started) * 1000)
    if status >= 500:
        raise AutomationTransientError(f"Webhook endpoint returned HTTP {status}.")
    return WebhookResponse(http_status=status, latency_ms=latency_ms)


def _fake_send(*, url: str, payload: bytes, headers: dict, timeout: float) -> WebhookResponse:
    return WebhookResponse(http_status=200, latency_ms=1)


def get_sender():
    backend = getattr(settings, "AUTOMATION_WEBHOOK_SENDER_BACKEND", "http")
    if backend == "fake":
        return _fake_send
    if backend == "http":
        return _http_send
    raise ValueError(f"Unknown AUTOMATION_WEBHOOK_SENDER_BACKEND: {backend!r}")
