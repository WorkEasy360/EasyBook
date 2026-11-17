"""The OpenID Connect protocol checks, now the workeasy360-sso connector's, proven through EasyBook's own API
(start, then callback) against the fake provider. Each ID-token test breaks one of the seven verification
steps of the integration guide and expects the sign-in to stop there, with the code the BFF has always been
given. (These replace the tests of the hand-written client, sso/oidc.py, that the connector replaced.)"""

import base64
import hashlib
import time

import jwt
from workeasy360_sso.models import PendingSignIn

from accounts.models import User
from sso.tests.fakes import (
    CLIENT_ID,
    CLIENT_PRIVATE_PEM,
    CLIENT_PUBLIC_PEM,
    ISSUER,
    JWKS_URI,
    KEY_ID,
    REDIRECT_URI,
    TOKEN_ENDPOINT,
    authorize_params,
)
from sso.tests.test_sign_in import W360_ORG, SignInTestCase


def _s256(verifier):
    return base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()


class ProtocolTestCase(SignInTestCase):
    def sign_in_with_token(self, make_token):
        """A sign-in whose ID token is ``make_token(nonce)``, exchanged for a code bound to this flow's PKCE
        challenge."""
        started = self.start(W360_ORG)
        params = authorize_params(started["authorization_url"])
        code = self.provider.issue_code(
            nonce=params["nonce"], code_challenge=params["code_challenge"], id_token=make_token(params["nonce"])
        )
        return self.complete(started, code=code)

    def assert_refused(self, response, code, status=403):
        self.assertEqual(response.status_code, status, response.content)
        self.assertEqual(response.data["error"]["code"], code)
        self.assertNotIn("access", response.data)
        self.assertFalse(User.objects.exists())

    def jwks_fetches(self):
        return sum(url == JWKS_URI for _, url in self.provider.calls)


class AuthorizationRequestTests(ProtocolTestCase):
    def test_request_carries_pkce_state_nonce_and_the_launch_organization(self):
        url = self.start(W360_ORG)["authorization_url"]
        params = authorize_params(url)
        self.assertTrue(url.startswith(f"{ISSUER}/oauth/authorize?"))
        self.assertEqual(params["response_type"], "code")
        self.assertEqual(params["client_id"], CLIENT_ID)
        self.assertEqual(params["redirect_uri"], REDIRECT_URI)
        self.assertEqual(params["scope"], "openid email profile organization")
        self.assertEqual(params["code_challenge_method"], "S256")
        self.assertEqual(params["organization"], W360_ORG)
        self.assertGreaterEqual(len(params["state"]), 43)
        self.assertGreaterEqual(len(params["nonce"]), 43)
        self.assertNotIn("prompt", params)
        pending = PendingSignIn.objects.get()
        self.assertNotIn(pending.code_verifier, url)  # the verifier never leaves the server
        self.assertGreaterEqual(len(pending.code_verifier), 43)
        self.assertEqual(params["code_challenge"], _s256(pending.code_verifier))  # S256 of it
        self.assertEqual(pending.nonce, params["nonce"])

    def test_each_start_is_a_fresh_state_nonce_and_verifier(self):
        first, second = (authorize_params(self.start()["authorization_url"]) for _ in range(2))
        for name in ("state", "nonce", "code_challenge"):
            self.assertNotEqual(first[name], second[name])

    def test_discovery_naming_another_issuer_is_refused(self):
        self.provider.discovery_issuer = "https://evil.example"
        response = self.client.post("/api/v1/auth/workeasy360/start/", {})
        self.assertEqual(response.status_code, 503)

    def test_the_client_assertion_is_rfc7523_and_never_reused(self):
        self.assertEqual(self.sign_in().status_code, 200)
        self.assertEqual(self.sign_in().status_code, 200)
        assertions = [form["client_assertion"] for form in self.provider.token_forms]
        claims = []
        for assertion in assertions:
            self.assertEqual(jwt.get_unverified_header(assertion)["kid"], KEY_ID)
            # Verified, not merely decoded: signed with EasyBook's key, for the token endpoint.
            claims.append(jwt.decode(assertion, CLIENT_PUBLIC_PEM, algorithms=["RS256"], audience=TOKEN_ENDPOINT))
        for claim in claims:
            self.assertEqual(claim["iss"], CLIENT_ID)
            self.assertEqual(claim["sub"], CLIENT_ID)
            self.assertLessEqual(claim["exp"] - claim["iat"], 60)
        self.assertNotEqual(claims[0]["jti"], claims[1]["jti"])
        form = self.provider.token_forms[0]
        self.assertEqual(form["grant_type"], "authorization_code")
        self.assertEqual(form["redirect_uri"], REDIRECT_URI)
        self.assertNotIn("client_secret", form)


class IdTokenTests(ProtocolTestCase):
    def test_happy_path_signs_in(self):
        self.assertEqual(self.sign_in().status_code, 200)

    def test_a_code_is_spent_once(self):
        started = self.start(W360_ORG)
        params = authorize_params(started["authorization_url"])
        code = self.provider.issue_code(nonce=params["nonce"], code_challenge=params["code_challenge"])
        self.assertEqual(self.complete(started, code=code).status_code, 200)
        # The same code again, from a new flow: the provider has spent it.
        response = self.complete(self.start(W360_ORG), code=code)
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.data["error"]["code"], "sign_in_refused")

    def test_wrong_audience_is_refused(self):  # step 3
        self.assert_refused(self.sign_in(aud="another-product"), "invalid_token")

    def test_wrong_issuer_is_refused(self):  # step 4
        self.assert_refused(self.sign_in(iss="https://evil.example"), "invalid_token")

    def test_expired_token_is_refused(self):  # step 5
        now = int(time.time())
        self.assert_refused(self.sign_in(iat=now - 900, nbf=now - 900, exp=now - 600), "invalid_token")

    def test_a_token_not_yet_valid_is_refused(self):  # step 5
        now = int(time.time())
        self.assert_refused(self.sign_in(nbf=now + 600, exp=now + 900), "invalid_token")

    def test_access_token_is_not_an_identity(self):  # step 6
        self.assert_refused(self.sign_in(typ="access_token"), "invalid_token")

    def test_a_token_without_jti_is_refused(self):
        self.assert_refused(self.sign_in(jti=None), "invalid_token")

    def test_nonce_from_another_session_is_refused(self):  # step 7
        self.assert_refused(
            self.sign_in_with_token(lambda nonce: self.provider.sign({**self.provider.claims(), "nonce": "someone-elses"})),
            "invalid_token",
        )

    def test_algorithm_comes_from_the_key_set_not_the_header(self):  # step 2
        # A token whose header names HS256 is refused because the key set says RS256, whatever secret it was
        # signed with - the algorithm-confusion attack never reaches a signature check with an attacker's choice.
        self.assert_refused(
            self.sign_in_with_token(
                lambda nonce: self.provider.sign(
                    {**self.provider.claims(), "nonce": nonce}, algorithm="HS256",
                    key="a-shared-secret-of-sufficient-length-for-hs256",
                )
            ),
            "invalid_token",
        )

    def test_unknown_kid_refreshes_the_key_set_once_then_fails(self):  # step 1
        self.assertEqual(self.sign_in().status_code, 200)  # warm the cache
        fetches = self.jwks_fetches()
        response = self.sign_in_with_token(
            lambda nonce: self.provider.sign({**self.provider.claims(), "nonce": nonce}, kid="provider-2")
        )
        self.assertEqual(response.data["error"]["code"], "invalid_token")
        self.assertEqual(self.jwks_fetches(), fetches + 1)

    def test_a_rotated_signing_key_is_picked_up(self):
        self.assertEqual(self.sign_in().status_code, 200)
        self.provider.published = ["provider-1", "provider-2"]
        self.provider.signing_kid = "provider-2"
        self.assertEqual(self.sign_in().status_code, 200)

    def test_token_without_organization_is_refused(self):
        self.assert_refused(self.complete(self.start(), organization_id=None), "invalid_token")

    def test_a_token_response_without_an_id_token_is_refused(self):
        self.provider.token_status = (200, {"access_token": "opaque", "token_type": "Bearer"})
        self.assert_refused(self.sign_in(), "invalid_token")


class ProofOfPossessionTests(ProtocolTestCase):
    """PKCE and the signature: the two things that stop a stolen code or a forged token."""

    def test_a_code_bound_to_another_pkce_challenge_is_refused_by_the_token_endpoint(self):
        started = self.start(W360_ORG)
        params = authorize_params(started["authorization_url"])
        # A code issued to someone else's authorization request: the verifier this flow holds does not match.
        code = self.provider.issue_code(nonce=params["nonce"], code_challenge=_s256("a-verifier-nobody-here-holds"))
        response = self.complete(started, code=code)
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.data["error"]["code"], "sign_in_refused")
        self.assertEqual(self.provider.token_forms[-1]["code_verifier"], PendingSignIn.objects.get().code_verifier)

    def test_the_right_verifier_is_accepted_and_the_fake_checks_it(self):
        # Guards the guard: the refusal above only means something if the fake lets the right one through.
        self.assertEqual(self.sign_in().status_code, 200)

    def test_a_known_kid_with_a_forged_rs256_signature_is_refused(self):
        # The kid and the algorithm are the provider's; the signature is an attacker's own RSA key.
        def forged(nonce):
            token = self.provider.sign(
                {**self.provider.claims(), "nonce": nonce}, kid="provider-1", algorithm="RS256", key=CLIENT_PRIVATE_PEM,
            )
            self.assertEqual(jwt.get_unverified_header(token)["kid"], "provider-1")
            return token

        self.assert_refused(self.sign_in_with_token(forged), "invalid_token")

    def test_a_genuine_token_with_a_swapped_payload_is_refused(self):
        def swapped(nonce):
            genuine = self.provider.sign({**self.provider.claims(), "nonce": nonce})
            header, _payload, signature = genuine.split(".")
            other = self.provider.sign({**self.provider.claims(sub="someone-else"), "nonce": nonce})
            return ".".join([header, other.split(".")[1], signature])

        self.assert_refused(self.sign_in_with_token(swapped), "invalid_token")


class AuthorizedPartyTests(ProtocolTestCase):
    """OIDC Core 3.1.3.7: ``azp`` and a list-valued ``aud``."""

    def test_an_azp_naming_another_client_is_refused(self):
        self.assert_refused(self.sign_in(azp="another-product"), "invalid_token")

    def test_an_azp_naming_this_client_is_accepted(self):
        self.assertEqual(self.sign_in(azp=CLIENT_ID).status_code, 200)

    def test_an_audience_list_of_only_this_client_is_accepted(self):
        self.assertEqual(self.sign_in(aud=[CLIENT_ID]).status_code, 200)

    def test_other_audiences_with_no_azp_are_refused(self):
        self.assert_refused(self.sign_in(aud=[CLIENT_ID, "another-product"]), "invalid_token")

    def test_other_audiences_with_an_azp_naming_this_client_are_accepted(self):
        self.assertEqual(self.sign_in(aud=[CLIENT_ID, "another-product"], azp=CLIENT_ID).status_code, 200)

    def test_other_audiences_with_an_azp_naming_somebody_else_are_refused(self):
        self.assert_refused(self.sign_in(aud=[CLIENT_ID, "another-product"], azp="another-product"), "invalid_token")

    def test_an_audience_list_without_this_client_is_still_refused(self):
        self.assert_refused(self.sign_in(aud=["another-product"], azp=CLIENT_ID), "invalid_token")
