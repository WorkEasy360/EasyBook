"""The Workeasy360 OpenID Connect client: authorization code + PKCE, with
``private_key_jwt`` client authentication.

Everything about the provider is learnt from its discovery document at
runtime; only the issuer is configured. The ID token is verified offline
against the provider's published key set, in the seven steps the Workeasy360
product integration guide requires (docs/workeasy360-sso.md). Each step closes
a named attack, so none of them is optional. The same client asks the
entitlement API whether an organization may still use EasyBook
(``verify_entitlement``, used by ``entitlement.py``).

No Django import: the module is pure protocol, which keeps it testable with a
fake transport and reusable from a management command.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import threading
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import Request, urlopen

import jwt

#: How long discovery metadata and the key set are reused before a refetch.
#: An unknown ``kid`` forces one early refresh regardless (key rotation).
METADATA_TTL_SECONDS = 3600
#: Clock skew tolerated on ``exp``/``nbf``/``iat`` (the guide's +-30 s).
LEEWAY_SECONDS = 30
#: A client assertion is single-use and short-lived; the provider caps it.
ASSERTION_LIFETIME_SECONDS = 60
HTTP_TIMEOUT_SECONDS = 10
_ASSERTION_TYPE = "urn:ietf:params:oauth:client-assertion-type:jwt-bearer"
_REQUIRED_CLAIMS = ["iss", "sub", "aud", "exp", "iat", "jti", "typ"]


class SSOError(Exception):
    """A sign-in that must not complete. ``code`` is safe to show a user's
    browser (a fixed vocabulary); ``detail`` is for logs only."""

    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code
        self.detail = detail
        self.status: int | None = None  # the provider's HTTP status, when the error came from one


#: (method, url, form-or-None) -> (status, parsed JSON body or {}).
Transport = Callable[[str, str, "dict[str, str] | None"], "tuple[int, dict[str, Any]]"]


def urllib_transport(method: str, url: str, form: dict[str, str] | None) -> tuple[int, dict[str, Any]]:
    """The production transport: stdlib, TLS-verified, bounded by a timeout.

    Only ever called with URLs derived from the configured issuer (discovery)
    or named by its discovery document, never with anything a browser sent.
    """
    if urlsplit(url).scheme not in ("https", "http"):
        raise SSOError("provider_unavailable", f"refusing non-HTTP URL {url!r}")
    data = urlencode(form).encode() if form is not None else None
    request = Request(url, data=data, method=method)  # noqa: S310 - scheme checked above
    request.add_header("Accept", "application/json")
    if data is not None:
        request.add_header("Content-Type", "application/x-www-form-urlencoded")
    try:
        with urlopen(request, timeout=HTTP_TIMEOUT_SECONDS) as response:  # noqa: S310  # nosec B310
            status, raw = response.status, response.read()
    except HTTPError as exc:
        status, raw = exc.code, exc.read()
    except (URLError, TimeoutError, OSError) as exc:
        raise SSOError("provider_unavailable", str(exc)) from None
    try:
        body = json.loads(raw or b"{}")
    except ValueError:
        body = {}
    return status, body if isinstance(body, dict) else {}


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def pkce_pair() -> tuple[str, str]:
    """(code_verifier, S256 code_challenge). 43 characters of verifier."""
    verifier = _b64url(secrets.token_bytes(32))
    challenge = _b64url(hashlib.sha256(verifier.encode("ascii")).digest())
    return verifier, challenge


@dataclass(frozen=True)
class AuthorizationRequest:
    """What the browser is sent to, and what must be kept to finish."""

    url: str
    state: str
    nonce: str
    code_verifier: str


@dataclass
class Workeasy360Client:
    issuer: str
    client_id: str
    redirect_uri: str
    private_key_pem: str
    key_id: str
    scopes: tuple[str, ...] = ("openid", "email", "profile", "organization")
    transport: Transport = urllib_transport
    clock: Callable[[], float] = time.time

    _metadata: dict[str, Any] = field(default_factory=dict, repr=False)
    _metadata_at: float = field(default=0.0, repr=False)
    _jwks: dict[str, Any] = field(default_factory=dict, repr=False)
    _jwks_at: float = field(default=0.0, repr=False)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    # --- discovery ---------------------------------------------------------

    def metadata(self, *, refresh: bool = False) -> dict[str, Any]:
        with self._lock:
            fresh = self.clock() - self._metadata_at < METADATA_TTL_SECONDS
            if self._metadata and fresh and not refresh:
                return self._metadata
        url = f"{self.issuer.rstrip('/')}/.well-known/openid-configuration"
        status, body = self.transport("GET", url, None)
        if status != 200 or not body:
            raise SSOError("provider_unavailable", f"discovery returned {status}")
        # A document naming another issuer describes somebody else's
        # authorization server. Exact string equality, as OIDC Core requires.
        if body.get("issuer") != self.issuer:
            raise SSOError("provider_misconfigured", f"discovery names issuer {body.get('issuer')!r}")
        for key in ("authorization_endpoint", "token_endpoint", "jwks_uri"):
            if not isinstance(body.get(key), str) or not body[key]:
                raise SSOError("provider_misconfigured", f"discovery has no {key}")
        with self._lock:
            self._metadata, self._metadata_at = body, self.clock()
        return body

    def _key_set(self, *, refresh: bool = False) -> dict[str, Any]:
        with self._lock:
            fresh = self.clock() - self._jwks_at < METADATA_TTL_SECONDS
            if self._jwks and fresh and not refresh:
                return self._jwks
        status, body = self.transport("GET", self.metadata()["jwks_uri"], None)
        if status != 200 or not isinstance(body.get("keys"), list):
            raise SSOError("provider_unavailable", f"key set returned {status}")
        with self._lock:
            self._jwks, self._jwks_at = body, self.clock()
        return body

    def _find_key(self, kid: str) -> dict[str, Any]:
        for refresh in (False, True):
            # Unknown kid: refresh ONCE (rotation), then fail. Refreshing
            # unconditionally would let any garbage token make us hammer the
            # provider.
            for key in self._key_set(refresh=refresh).get("keys", []):
                if isinstance(key, dict) and key.get("kid") == kid:
                    return key
        raise SSOError("invalid_token", f"no key {kid!r} in the provider's key set")

    # --- the flow ----------------------------------------------------------

    def authorization_request(self, *, organization_id: str = "") -> AuthorizationRequest:
        state = secrets.token_urlsafe(32)
        nonce = secrets.token_urlsafe(32)
        verifier, challenge = pkce_pair()
        params = {
            "response_type": "code",
            "client_id": self.client_id,
            "redirect_uri": self.redirect_uri,
            "scope": " ".join(self.scopes),
            "state": state,
            "nonce": nonce,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
        }
        if organization_id:
            # The Workeasy360 launcher names the organization the user chose;
            # without it the provider shows its own organization chooser.
            params["organization"] = organization_id
        url = f"{self.metadata()['authorization_endpoint']}?{urlencode(params)}"
        return AuthorizationRequest(url=url, state=state, nonce=nonce, code_verifier=verifier)

    def end_session_url(self, *, post_logout_redirect_uri: str) -> str:
        """Where "sign out of Workeasy360 too" sends the browser (OpenID Connect RP-Initiated
        Logout 1.0), or "" when the provider advertises no ``end_session_endpoint``. Workeasy360
        asks the person to confirm, then returns them to ``post_logout_redirect_uri``, which must be
        registered for this client exactly."""
        endpoint = self.metadata().get("end_session_endpoint")
        if not isinstance(endpoint, str) or not endpoint:
            return ""
        query = urlencode({"client_id": self.client_id, "post_logout_redirect_uri": post_logout_redirect_uri})
        return f"{endpoint}?{query}"

    def client_assertion(self) -> str:
        """RFC 7523 section 3: iss = sub = client id, aud = token endpoint."""
        now = int(self.clock())
        return jwt.encode(
            {
                "iss": self.client_id,
                "sub": self.client_id,
                "aud": self.metadata()["token_endpoint"],
                "iat": now,
                "exp": now + ASSERTION_LIFETIME_SECONDS,
                "jti": uuid.uuid4().hex,
            },
            self.private_key_pem,
            algorithm=_algorithm_for(self.private_key_pem),
            headers={"kid": self.key_id},
        )

    def complete(self, *, code: str, returned_state: str, expected_state: str, nonce: str,
                 code_verifier: str) -> dict[str, Any]:
        """Exchange the code and return the verified ID token's claims."""
        # Constant-time: a callback whose state is not ours is not ours.
        if not expected_state or not hmac.compare_digest(returned_state.encode(), expected_state.encode()):
            raise SSOError("state_mismatch")
        if not code:
            raise SSOError("invalid_request", "no code")
        status, body = self.transport(
            "POST",
            self.metadata()["token_endpoint"],
            {
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": self.redirect_uri,
                "code_verifier": code_verifier,
                "client_assertion_type": _ASSERTION_TYPE,
                "client_assertion": self.client_assertion(),
            },
        )
        if status != 200:
            error = str(body.get("error") or "")
            if status >= 500 or error == "temporarily_unavailable":
                raise SSOError("provider_unavailable", f"token endpoint {status} {error}")
            # invalid_grant is routine (expired code, membership changed since
            # the redirect): the user simply starts again.
            raise SSOError("sign_in_refused", f"token endpoint {status} {error}")
        id_token = body.get("id_token")
        if not isinstance(id_token, str) or not id_token:
            raise SSOError("invalid_token", "token response has no id_token")
        return self.verify_id_token(id_token, nonce=nonce)

    # --- the entitlement API -------------------------------------------------

    def verify_entitlement(self, organization_id: str, subject: str | None = None) -> dict[str, Any]:
        """Ask Workeasy360 whether ``organization_id`` may still use this product (the entitlement API of
        the product integration guide). There is no product parameter: the product is whoever the client
        assertion proves this is. Returns the decision document (``decision`` is ``allow`` or ``deny``).

        With ``subject`` (the ID token ``sub`` the person signed in with) the question is about that person
        too, and the answer may also be ``deny`` with ``membership_inactive`` or ``no_seat``. Without it the
        question is the organization's alone (background work).

        Anything that is not an answer raises ``SSOError``: ``provider_unavailable`` for an outage (5xx,
        429, network) - never a denial, the caller keeps its last known answer, as the guide requires -
        and ``provider_refused`` when the platform answered and refused this client (400/401/403: a
        revoked or wrong credential, a suspended integration). The caller must not keep serving on that."""
        endpoint = self.metadata().get("workeasy360_entitlement_endpoint")
        if not isinstance(endpoint, str) or not endpoint:
            raise SSOError("provider_misconfigured", "discovery has no workeasy360_entitlement_endpoint")
        form = {
            "organization_id": organization_id,
            "client_assertion_type": _ASSERTION_TYPE,
            "client_assertion": self.client_assertion(),
        }
        if subject:
            form["subject"] = subject
        status, body = self.transport("POST", endpoint, form)
        if status == 200 and body.get("decision") in ("allow", "deny"):
            return body
        detail = f"entitlement endpoint {status} {body.get('error', '')}".strip()
        if status in (400, 401, 403):
            raise SSOError("provider_refused", detail)
        error = SSOError("provider_unavailable", detail)
        error.status = status  # a 429 may be the failed-client-authentication limiter (entitlement.py)
        raise error

    def verify_id_token(self, id_token: str, *, nonce: str) -> dict[str, Any]:
        try:
            header = jwt.get_unverified_header(id_token)  # untrusted: kid only
        except jwt.PyJWTError as exc:
            raise SSOError("invalid_token", str(exc)) from None
        kid = header.get("kid")
        if not isinstance(kid, str) or not kid:
            raise SSOError("invalid_token", "no kid")
        key = self._find_key(kid)  # 1. key by kid, from OUR cached key set
        algorithm = key.get("alg")
        if algorithm not in ("RS256", "ES256"):
            raise SSOError("invalid_token", f"key {kid!r} has unsupported alg {algorithm!r}")
        try:
            claims = jwt.decode(
                id_token,
                jwt.PyJWK(key).key,
                algorithms=[algorithm],  # 2. from the key set, never the header
                audience=self.client_id,  # 3. audience confusion
                issuer=self.issuer,  # 4. token substitution
                leeway=LEEWAY_SECONDS,  # 5. exp / nbf
                options={"require": _REQUIRED_CLAIMS},
            )
        except jwt.PyJWTError as exc:
            raise SSOError("invalid_token", str(exc)) from None
        if claims.get("typ") != "id_token":  # 6. not an access token
            raise SSOError("invalid_token", "typ is not id_token")
        returned_nonce = str(claims.get("nonce") or "")
        if not nonce or not hmac.compare_digest(returned_nonce.encode(), nonce.encode()):  # 7. our request
            raise SSOError("invalid_token", "nonce mismatch")
        if not claims.get("organization_id"):
            raise SSOError("invalid_token", "no organization_id")
        return claims


def _algorithm_for(private_key_pem: str) -> str:
    """RS256 for an RSA key, ES256 for a P-256 key: the two the provider
    accepts. Derived from the key so the two settings cannot disagree."""
    from cryptography.hazmat.primitives.asymmetric import ec, rsa
    from cryptography.hazmat.primitives.serialization import load_pem_private_key

    key = load_pem_private_key(private_key_pem.encode(), password=None)
    if isinstance(key, rsa.RSAPrivateKey):
        return "RS256"
    if isinstance(key, ec.EllipticCurvePrivateKey) and key.curve.name == "secp256r1":
        return "ES256"
    raise SSOError("provider_misconfigured", "the client key must be RSA or EC P-256")
