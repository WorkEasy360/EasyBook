"""A fake Workeasy360 provider for tests: real keys, real signatures, no network. It behaves as the provider's
contract says (discovery, a JWK Set, a token endpoint that authenticates our client assertion and spends a
code once, an entitlement API answering ``self.entitlement`` - allow, by default - to a correctly
authenticated client, and back-channel logout tokens signed like its ID tokens), so the workeasy360-sso
connector under test does every check it does in production.

The connector talks to it through ``WORKEASY360_HTTP_TRANSPORT`` (``httpx_transport``); nothing is patched.
"""

import base64
import hashlib
import hmac
import json
import time
import uuid
from urllib.parse import parse_qs, urlsplit

import httpx
import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

ISSUER = "https://auth.workeasy360.test"
CLIENT_ID = "easybook-client-id"
KEY_ID = "easybook-credential"
REDIRECT_URI = "https://book.workeasy360.test/api/auth/callback/workeasy360"
TOKEN_ENDPOINT = f"{ISSUER}/oauth/token"
ENTITLEMENT_ENDPOINT = f"{ISSUER}/integrations/entitlements/verify"
JWKS_URI = f"{ISSUER}/jwks.json"
BACKCHANNEL_EVENT = "http://schemas.openid.net/event/backchannel-logout"


def _rsa_pem():
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private = key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
    ).decode()
    public = key.public_key().public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
    ).decode()
    return private, public


# Generated once per test process: RSA generation is the slow part.
CLIENT_PRIVATE_PEM, CLIENT_PUBLIC_PEM = _rsa_pem()
_PROVIDER_KEYS = {kid: _rsa_pem() for kid in ("provider-1", "provider-2")}
#: A key nobody published: what a forger signs with.
FORGER_PRIVATE_PEM, _ = _rsa_pem()


def provider_jwk(kid):
    jwk = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(
        serialization.load_pem_public_key(_PROVIDER_KEYS[kid][1].encode())
    ))
    return {**jwk, "kid": kid, "alg": "RS256", "use": "sig"}


class FakeWorkeasy360:
    def __init__(self):
        self.published = ["provider-1"]
        self.signing_kid = "provider-1"
        self.codes = {}
        #: (method, url) of every request the connector made.
        self.calls = []
        #: The form of every POST to the token endpoint, as sent.
        self.token_forms = []
        self.token_status = None  # force a token endpoint failure: (status, body)
        self.discovery_issuer = ISSUER
        self.advertise_end_session = True
        self.advertise_entitlement = True
        self.entitlement_status = 200
        self.entitlement = {"decision": "allow", "reason": "entitled", "write_allowed": True, "revalidate_after": 900}
        self.entitlement_forms = []
        #: Answers for a per-person question (the form carries ``subject``), by subject. A subject with no
        #: entry gets ``self.entitlement``, as the provider does for an unrestricted member.
        self.person_entitlements = {}

    # --- what the provider would mint -------------------------------------

    def claims(self, **overrides):
        now = int(time.time())
        base = {
            "iss": ISSUER, "sub": "w360-user-1", "aud": CLIENT_ID, "iat": now, "nbf": now, "exp": now + 300,
            "jti": uuid.uuid4().hex, "typ": "id_token", "organization_id": "7b0f4f7e-2a3c-4f4e-9a59-3f5c1b2d4e6a",
            "product_code": "easybook", "aal": 1, "email": "asha@example.com", "email_verified": True,
            "name": "Asha Rao", "organization_name": "Acme Traders",
        }
        base.update(overrides)
        return {key: value for key, value in base.items() if value is not None}

    def sign(self, claims, *, kid=None, algorithm="RS256", key=None, headers=None):
        kid = kid or self.signing_kid
        return jwt.encode(claims, key or _PROVIDER_KEYS[kid][0], algorithm=algorithm,
                          headers={"kid": kid, **(headers or {})})

    def issue_code(self, *, nonce, code_challenge, claims=None, id_token=None):
        """A code the token endpoint will exchange for this ID token - for whoever proves they hold the
        PKCE verifier of ``code_challenge`` (the S256 challenge the authorization request carried:
        ``authorize_params(url)["code_challenge"]``). A code is bound to its challenge, as it is at the real
        provider, so a test cannot forget PKCE by accident."""
        code = uuid.uuid4().hex
        token = id_token or self.sign({**(claims or self.claims()), "nonce": nonce})
        self.codes[code] = {"id_token": token, "challenge": code_challenge}
        return code

    def logout_token(self, *, sub=None, sid=None, kid=None, key=None, headers=None, **overrides):
        """A back-channel logout token (Back-Channel Logout 1.0 section 2.4), as Workeasy360 POSTs one:
        signed like an ID token, header ``typ: logout+jwt``, valid for 120 s."""
        now = int(time.time())
        claims = {
            "iss": ISSUER, "aud": CLIENT_ID, "iat": now, "exp": now + 120, "jti": uuid.uuid4().hex,
            "typ": "logout_token", "events": {BACKCHANNEL_EVENT: {}}, "sub": sub, "sid": sid,
        }
        claims.update(overrides)
        claims = {name: value for name, value in claims.items() if value is not None}
        kid = kid or self.signing_kid
        return jwt.encode(claims, key or _PROVIDER_KEYS[kid][0], algorithm="RS256",
                          headers={"kid": kid, "typ": "logout+jwt", **(headers or {})})

    # --- the transport the connector calls ---------------------------------

    @property
    def httpx_transport(self):
        """What ``WORKEASY360_HTTP_TRANSPORT`` takes: every request the connector makes lands in ``handle``."""
        return httpx.MockTransport(self.handle)

    def handle(self, request):
        form = None
        if request.method == "POST":
            form = {name: values[0] for name, values in parse_qs(request.content.decode()).items()}
        status, body = self.transport(request.method, str(request.url), form)
        return httpx.Response(status, json=body)

    def transport(self, method, url, form):
        self.calls.append((method, url))
        if url == f"{ISSUER}/.well-known/openid-configuration":
            document = {
                "issuer": self.discovery_issuer,
                "authorization_endpoint": f"{ISSUER}/oauth/authorize",
                "token_endpoint": TOKEN_ENDPOINT,
                "jwks_uri": JWKS_URI,
                "backchannel_logout_supported": True,
                "backchannel_logout_session_supported": True,
            }
            if self.advertise_end_session:
                document["end_session_endpoint"] = f"{ISSUER}/oauth/logout"
            if self.advertise_entitlement:
                document["workeasy360_entitlement_endpoint"] = ENTITLEMENT_ENDPOINT
            return 200, document
        if url == JWKS_URI:
            return 200, {"keys": [provider_jwk(kid) for kid in self.published]}
        if url == TOKEN_ENDPOINT and method == "POST":
            self.token_forms.append(dict(form or {}))
            return self._token(form or {})
        if url == ENTITLEMENT_ENDPOINT and method == "POST":
            return self._entitlement(form or {})
        return 404, {}

    def _client_authenticated(self, form):
        """RFC 7523, exactly as the provider checks it: the token endpoint is the audience for
        both endpoints."""
        try:
            header = jwt.get_unverified_header(form["client_assertion"])
            assertion = jwt.decode(
                form["client_assertion"], CLIENT_PUBLIC_PEM, algorithms=["RS256"],
                audience=TOKEN_ENDPOINT, issuer=CLIENT_ID,
                options={"require": ["iss", "sub", "aud", "exp", "iat", "jti"]},
            )
        except (KeyError, jwt.PyJWTError):
            return False
        return (
            header.get("kid") == KEY_ID
            and assertion["sub"] == CLIENT_ID
            and form.get("client_assertion_type") == "urn:ietf:params:oauth:client-assertion-type:jwt-bearer"
        )

    def _entitlement(self, form):
        if not self._client_authenticated(form):
            return 401, {"error": "invalid_client"}
        self.entitlement_forms.append(dict(form))
        if self.entitlement_status != 200:
            return self.entitlement_status, {"error": "temporarily_unavailable"}
        answer = self.entitlement
        if form.get("subject") in self.person_entitlements:
            answer = self.person_entitlements[form["subject"]]
        return 200, {**answer, "organization_id": form["organization_id"], "product_code": "easybook"}

    def _token(self, form):
        if self.token_status:
            return self.token_status
        if not self._client_authenticated(form):
            return 401, {"error": "invalid_client"}
        if form.get("redirect_uri") != REDIRECT_URI or form.get("grant_type") != "authorization_code":
            return 400, {"error": "invalid_grant"}
        issued = self.codes.pop(form.get("code"), None)  # single use, spent even by a failed exchange
        if issued is None:
            return 400, {"error": "invalid_grant"}
        # PKCE (RFC 7636 section 4.6): S256 of the verifier must equal the challenge the code was bound to.
        verifier = form.get("code_verifier") or ""
        derived = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
        if not verifier or not hmac.compare_digest(derived, issued["challenge"]):
            return 400, {"error": "invalid_grant"}
        return 200, {"id_token": issued["id_token"], "access_token": "opaque", "token_type": "Bearer"}


def authorize_params(url):
    """The query the connector sent the browser to Workeasy360 with."""
    return {key: values[0] for key, values in parse_qs(urlsplit(url).query).items()}


class Clock:
    """The connector's entitlement clock (``workeasy360_sso.entitlements._clock``), moved by hand:
    ``advance(901)`` makes every stored answer, back-off and refusal mark 901 seconds older."""

    def __init__(self):
        self.now = time.time()

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds
