"""SSRF protections for the call_webhook action (phase sections 38-41).

Validates both the URL scheme and every IP address its hostname resolves
to, blocking loopback, private (RFC1918), link-local (including the cloud
metadata address, 169.254.169.254), and other reserved ranges. Checked at
BOTH configuration time (automation/actions/registry.py::validate_action_config)
and execution time (automation/actions/handlers.py) — phase section 41.

DNS rebinding between validation and the actual request is a residual risk
inherent to any pre-request DNS check; blocking the metadata/private ranges
at both checkpoints is the primary, documented defense here, matching what
a real egress proxy would enforce and consistent with this codebase not
inventing infrastructure beyond what's justified (root CLAUDE.md).

A validated URL redirecting the actual request to an unvalidated one (e.g.
to 169.254.169.254) is NOT a residual risk here: `webhook_sender.py`'s
`_OPENER` refuses every redirect rather than following it, so a check done
once here at the original URL cannot be bypassed by a 3xx response later —
verified against a real HTTP server returning a redirect to the metadata
address; the default urllib opener does follow it, the hardened one does not.
"""

import ipaddress
import socket
from urllib.parse import urlparse

from core.exceptions import ApplicationError

_BLOCKED_HOSTNAMES = frozenset({"localhost", "metadata.google.internal"})


def _is_blocked_ip(ip_str: str) -> bool:
    try:
        ip = ipaddress.ip_address(ip_str)
    except ValueError:
        return True  # unparseable address -> fail closed
    return ip.is_loopback or ip.is_private or ip.is_link_local or ip.is_reserved or ip.is_multicast or ip.is_unspecified


def validate_webhook_url(url: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme != "https":
        raise ApplicationError("Webhook URLs must use HTTPS.", code="automation_webhook_url_insecure")
    hostname = parsed.hostname
    if not hostname:
        raise ApplicationError("Webhook URL has no hostname.", code="automation_webhook_url_invalid")
    if hostname.lower() in _BLOCKED_HOSTNAMES:
        raise ApplicationError("Webhook URL targets a blocked host.", code="automation_webhook_url_blocked")

    try:
        addr_infos = socket.getaddrinfo(hostname, None)
    except socket.gaierror as exc:
        raise ApplicationError(
            "Webhook hostname could not be resolved.", code="automation_webhook_url_unresolvable"
        ) from exc
    for _family, _type, _proto, _canonname, sockaddr in addr_infos:
        if _is_blocked_ip(sockaddr[0]):
            raise ApplicationError(
                "Webhook URL resolves to a blocked network range.", code="automation_webhook_url_blocked"
            )
