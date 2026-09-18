"""The client a request is attributed to — the one place that decides it.

Used for rate-limit identity (core/throttling.py). Never used for
authorization: an address is not a credential.

Order of trust:

1. The Next.js BFF (frontend/src/app/api/bff, the auth routes) calls Django
   server-to-server, so every browser request arrives from the BFF's own
   address. It forwards the browser's address in BFF_CLIENT_IP_HEADER and
   proves it is the BFF with BFF_PROXY_AUTH_HEADER. The asserted address is
   used only when that secret matches (constant-time) — anyone else can set
   the header, and it is then ignored.
2. Otherwise X-Forwarded-For, counting TRUSTED_PROXY_COUNT entries from the
   RIGHT: each trusted proxy (the ALB) appends the address that connected to
   it, so those right-hand entries were written by infrastructure, and
   everything to their left was written by the client. DRF's own default
   (NUM_PROXIES unset) keyed on the whole header instead, which a client
   rotates freely.
3. Otherwise the socket peer (REMOTE_ADDR).

Anything that does not parse as an IP address is ignored at every step.
"""

import hmac
import ipaddress

from django.conf import settings

BFF_CLIENT_IP_HEADER = "X-EasyBook-Client-IP"
BFF_PROXY_AUTH_HEADER = "X-EasyBook-Proxy-Auth"


def _valid_ip(value) -> str | None:
    if not value:
        return None
    candidate = str(value).strip()
    try:
        return str(ipaddress.ip_address(candidate))
    except ValueError:
        return None


def _meta_key(header: str) -> str:
    return "HTTP_" + header.upper().replace("-", "_")


def _bff_asserted_ip(request) -> str | None:
    secret = getattr(settings, "BFF_PROXY_SECRET", "")
    if not secret:
        return None
    presented = request.META.get(_meta_key(BFF_PROXY_AUTH_HEADER), "")
    if not presented or not hmac.compare_digest(presented.encode(), secret.encode()):
        return None
    return _valid_ip(request.META.get(_meta_key(BFF_CLIENT_IP_HEADER)))


def _proxy_recorded_ip(request) -> str | None:
    trusted = getattr(settings, "TRUSTED_PROXY_COUNT", 0)
    forwarded_for = request.META.get("HTTP_X_FORWARDED_FOR")
    if trusted <= 0 or not forwarded_for:
        return None
    entries = [entry.strip() for entry in forwarded_for.split(",") if entry.strip()]
    if not entries:
        return None
    return _valid_ip(entries[-min(trusted, len(entries))])


def get_client_ip(request) -> str | None:
    return _bff_asserted_ip(request) or _proxy_recorded_ip(request) or _valid_ip(request.META.get("REMOTE_ADDR"))
