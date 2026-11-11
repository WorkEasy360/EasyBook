"""The Workeasy360 client's protocol checks. Each ID-token test breaks one of
the seven verification steps and expects the sign-in to stop there."""

import time

import jwt
from django.test import SimpleTestCase

from sso.oidc import SSOError, Workeasy360Client, pkce_pair
from sso.tests.fakes import (
    CLIENT_ID,
    CLIENT_PRIVATE_PEM,
    CLIENT_PUBLIC_PEM,
    ISSUER,
    KEY_ID,
    REDIRECT_URI,
    FakeWorkeasy360,
    authorize_params,
)


class ClientTestCase(SimpleTestCase):
    def setUp(self):
        self.provider = FakeWorkeasy360()
        self.client_ = Workeasy360Client(
            issuer=ISSUER, client_id=CLIENT_ID, redirect_uri=REDIRECT_URI,
            private_key_pem=CLIENT_PRIVATE_PEM, key_id=KEY_ID, transport=self.provider.transport,
        )

    def issue(self, request, **kwargs):
        """A code bound to this request's PKCE challenge, as the provider binds it."""
        return self.provider.issue_code(
            nonce=kwargs.pop("nonce", request.nonce), code_challenge=authorize_params(request.url)["code_challenge"],
            **kwargs,
        )

    def sign_in(self, **claim_overrides):
        request = self.client_.authorization_request()
        code = self.issue(request, claims=self.provider.claims(**claim_overrides))
        return self.client_.complete(code=code, returned_state=request.state, expected_state=request.state,
                                     nonce=request.nonce, code_verifier=request.code_verifier)

    def assert_refused(self, code, call):
        with self.assertRaises(SSOError) as caught:
            call()
        self.assertEqual(caught.exception.code, code, caught.exception.detail)


class AuthorizationRequestTests(ClientTestCase):
    def test_request_carries_pkce_state_nonce_and_the_launch_organization(self):
        request = self.client_.authorization_request(organization_id="7b0f4f7e-2a3c-4f4e-9a59-3f5c1b2d4e6a")
        params = authorize_params(request.url)
        self.assertTrue(request.url.startswith(f"{ISSUER}/oauth/authorize?"))
        self.assertEqual(params["response_type"], "code")
        self.assertEqual(params["client_id"], CLIENT_ID)
        self.assertEqual(params["redirect_uri"], REDIRECT_URI)
        self.assertEqual(params["scope"], "openid email profile organization")
        self.assertEqual(params["code_challenge_method"], "S256")
        self.assertEqual(params["state"], request.state)
        self.assertEqual(params["nonce"], request.nonce)
        self.assertEqual(params["organization"], "7b0f4f7e-2a3c-4f4e-9a59-3f5c1b2d4e6a")
        self.assertNotIn(request.code_verifier, request.url)

    def test_pkce_challenge_is_s256_of_the_verifier(self):
        import base64
        import hashlib

        verifier, challenge = pkce_pair()
        self.assertGreaterEqual(len(verifier), 43)
        expected = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
        self.assertEqual(challenge, expected)

    def test_discovery_naming_another_issuer_is_refused(self):
        self.provider.discovery_issuer = "https://evil.example"
        self.assert_refused("provider_misconfigured", self.client_.authorization_request)

    def test_client_assertion_is_rfc7523(self):
        assertion = self.client_.client_assertion()
        self.assertEqual(jwt.get_unverified_header(assertion)["kid"], KEY_ID)
        claims = jwt.decode(assertion, CLIENT_PUBLIC_PEM, algorithms=["RS256"], audience=f"{ISSUER}/oauth/token")
        self.assertEqual(claims["iss"], CLIENT_ID)
        self.assertEqual(claims["sub"], CLIENT_ID)
        self.assertLessEqual(claims["exp"] - claims["iat"], 60)
        self.assertNotEqual(claims["jti"], jwt.decode(
            self.client_.client_assertion(), options={"verify_signature": False})["jti"])


class CompleteTests(ClientTestCase):
    def test_happy_path_returns_verified_claims(self):
        claims = self.sign_in()
        self.assertEqual(claims["sub"], "w360-user-1")
        self.assertEqual(claims["organization_id"], "7b0f4f7e-2a3c-4f4e-9a59-3f5c1b2d4e6a")

    def test_state_mismatch_stops_before_the_token_endpoint(self):
        request = self.client_.authorization_request()
        self.assert_refused("state_mismatch", lambda: self.client_.complete(
            code="anything", returned_state="forged", expected_state=request.state, nonce=request.nonce,
            code_verifier=request.code_verifier))
        self.assertFalse(any(url.endswith("/oauth/token") for _, url in self.provider.calls))

    def test_a_code_is_spent_once(self):
        request = self.client_.authorization_request()
        code = self.issue(request)
        kwargs = {"code": code, "returned_state": request.state, "expected_state": request.state,
                  "nonce": request.nonce, "code_verifier": request.code_verifier}
        self.client_.complete(**kwargs)
        self.assert_refused("sign_in_refused", lambda: self.client_.complete(**kwargs))

    def test_provider_outage_is_unavailable_not_refused(self):
        self.provider.token_status = (503, {"error": "temporarily_unavailable"})
        self.assert_refused("provider_unavailable", self.sign_in)

    def test_wrong_audience_is_refused(self):  # step 3
        self.assert_refused("invalid_token", lambda: self.sign_in(aud="another-product"))

    def test_wrong_issuer_is_refused(self):  # step 4
        self.assert_refused("invalid_token", lambda: self.sign_in(iss="https://evil.example"))

    def test_expired_token_is_refused(self):  # step 5
        now = int(time.time())
        self.assert_refused("invalid_token", lambda: self.sign_in(iat=now - 900, nbf=now - 900, exp=now - 600))

    def test_access_token_is_not_an_identity(self):  # step 6
        self.assert_refused("invalid_token", lambda: self.sign_in(typ="access_token"))

    def test_nonce_from_another_session_is_refused(self):  # step 7
        request = self.client_.authorization_request()
        code = self.issue(request, nonce="someone-elses-nonce")
        self.assert_refused("invalid_token", lambda: self.client_.complete(
            code=code, returned_state=request.state, expected_state=request.state, nonce=request.nonce,
            code_verifier=request.code_verifier))

    def test_algorithm_comes_from_the_key_set_not_the_header(self):  # step 2
        # A token whose header names HS256 is refused because the key set says
        # RS256, whatever secret it was signed with - the algorithm-confusion
        # attack never reaches a signature check with an attacker's choice.
        request = self.client_.authorization_request()
        token = self.provider.sign({**self.provider.claims(), "nonce": request.nonce}, algorithm="HS256",
                                   key="a-shared-secret-of-sufficient-length-for-hs256")
        code = self.issue(request, id_token=token)
        self.assert_refused("invalid_token", lambda: self.client_.complete(
            code=code, returned_state=request.state, expected_state=request.state, nonce=request.nonce,
            code_verifier=request.code_verifier))

    def test_unknown_kid_refreshes_the_key_set_once_then_fails(self):  # step 1
        self.sign_in()  # warm the cache
        fetches = sum(url.endswith("/jwks.json") for _, url in self.provider.calls)
        request = self.client_.authorization_request()
        token = self.provider.sign({**self.provider.claims(), "nonce": request.nonce}, kid="provider-2")
        code = self.issue(request, id_token=token)
        self.assert_refused("invalid_token", lambda: self.client_.complete(
            code=code, returned_state=request.state, expected_state=request.state, nonce=request.nonce,
            code_verifier=request.code_verifier))
        self.assertEqual(sum(url.endswith("/jwks.json") for _, url in self.provider.calls), fetches + 1)

    def test_a_rotated_signing_key_is_picked_up(self):
        self.sign_in()
        self.provider.published = ["provider-1", "provider-2"]
        self.provider.signing_kid = "provider-2"
        self.assertEqual(self.sign_in()["sub"], "w360-user-1")

    def test_token_without_organization_is_refused(self):
        self.assert_refused("invalid_token", lambda: self.sign_in(organization_id=None))


class ProofOfPossessionTests(ClientTestCase):
    """PKCE and the signature: the two things that stop a stolen code or a forged token."""

    def test_a_wrong_pkce_verifier_is_refused_by_the_token_endpoint(self):
        request = self.client_.authorization_request()
        code = self.issue(request)
        other_verifier, _ = pkce_pair()
        self.assert_refused("sign_in_refused", lambda: self.client_.complete(
            code=code, returned_state=request.state, expected_state=request.state, nonce=request.nonce,
            code_verifier=other_verifier))

    def test_a_missing_pkce_verifier_is_refused_by_the_token_endpoint(self):
        request = self.client_.authorization_request()
        code = self.issue(request)
        self.assert_refused("sign_in_refused", lambda: self.client_.complete(
            code=code, returned_state=request.state, expected_state=request.state, nonce=request.nonce,
            code_verifier=""))

    def test_the_right_verifier_is_accepted_and_the_fake_checks_it(self):
        # Guards the guard: the two refusals above only mean something if the fake lets the right one through.
        self.assertEqual(self.sign_in()["sub"], "w360-user-1")

    def test_a_known_kid_with_a_forged_rs256_signature_is_refused(self):
        # The kid and the algorithm are the provider's; the signature is an attacker's own RSA key.
        request = self.client_.authorization_request()
        forged = self.provider.sign(
            {**self.provider.claims(), "nonce": request.nonce}, kid="provider-1", algorithm="RS256",
            key=CLIENT_PRIVATE_PEM,
        )
        self.assertEqual(jwt.get_unverified_header(forged)["kid"], "provider-1")
        code = self.issue(request, id_token=forged)
        self.assert_refused("invalid_token", lambda: self.client_.complete(
            code=code, returned_state=request.state, expected_state=request.state, nonce=request.nonce,
            code_verifier=request.code_verifier))

    def test_a_genuine_token_with_a_swapped_payload_is_refused(self):
        request = self.client_.authorization_request()
        genuine = self.provider.sign({**self.provider.claims(), "nonce": request.nonce})
        header, _payload, signature = genuine.split(".")
        other = self.provider.sign({**self.provider.claims(sub="someone-else"), "nonce": request.nonce})
        swapped = ".".join([header, other.split(".")[1], signature])
        code = self.issue(request, id_token=swapped)
        self.assert_refused("invalid_token", lambda: self.client_.complete(
            code=code, returned_state=request.state, expected_state=request.state, nonce=request.nonce,
            code_verifier=request.code_verifier))


class AuthorizedPartyTests(ClientTestCase):
    """OIDC Core 3.1.3.7: ``azp`` and a list-valued ``aud``."""

    def test_an_azp_naming_another_client_is_refused(self):
        self.assert_refused("invalid_token", lambda: self.sign_in(azp="another-product"))

    def test_an_azp_naming_this_client_is_accepted(self):
        self.assertEqual(self.sign_in(azp=CLIENT_ID)["sub"], "w360-user-1")

    def test_an_audience_list_of_only_this_client_is_accepted(self):
        self.assertEqual(self.sign_in(aud=[CLIENT_ID])["sub"], "w360-user-1")

    def test_other_audiences_with_no_azp_are_refused(self):
        self.assert_refused("invalid_token", lambda: self.sign_in(aud=[CLIENT_ID, "another-product"]))

    def test_other_audiences_with_an_azp_naming_this_client_are_accepted(self):
        self.assertEqual(self.sign_in(aud=[CLIENT_ID, "another-product"], azp=CLIENT_ID)["sub"], "w360-user-1")

    def test_other_audiences_with_an_azp_naming_somebody_else_are_refused(self):
        self.assert_refused(
            "invalid_token", lambda: self.sign_in(aud=[CLIENT_ID, "another-product"], azp="another-product")
        )

    def test_an_audience_list_without_this_client_is_still_refused(self):
        self.assert_refused("invalid_token", lambda: self.sign_in(aud=["another-product"], azp=CLIENT_ID))
