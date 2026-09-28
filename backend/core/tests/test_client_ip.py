"""Which client a request is attributed to, for rate limiting.

Regressions (phase 12 P0 remediation):

- DRF's NUM_PROXIES was unset, and in that case DRF keys throttles on the
  ENTIRE X-Forwarded-For header, which the client writes. Behind the ALB
  (which appends the real peer address) an attacker changing one byte of a
  forged prefix per request got a fresh bucket each time: the login limit
  (auth scope, 20/min) never engaged — unlimited credential stuffing.
- The browser never calls Django: the Next.js BFF does, from its own address.
  Every browser user therefore shared ONE anonymous bucket — twenty failed
  sign-ins anywhere locked every user out of signing in, and token refreshes
  for all users shared the anon limit.

Client identity now comes from core.client_ip.get_client_ip: a client address
asserted by the BFF only when it proves itself with the shared proxy secret;
otherwise the address the trusted proxy (the ALB) recorded; otherwise the
socket peer.
"""

from django.conf import settings
from django.core.cache import cache
from django.test import RequestFactory, SimpleTestCase, override_settings
from rest_framework.test import APITestCase

from core.client_ip import BFF_CLIENT_IP_HEADER, BFF_PROXY_AUTH_HEADER, get_client_ip

ALB_PEER = "10.20.0.10"
BFF_EGRESS = "198.18.0.5"
PROXY_SECRET = "p" * 48
AUTH_LIMIT = int(settings.REST_FRAMEWORK["DEFAULT_THROTTLE_RATES"]["auth"].split("/")[0])


def _meta_header(name: str) -> str:
    return "HTTP_" + name.upper().replace("-", "_")


@override_settings(TRUSTED_PROXY_COUNT=1, BFF_PROXY_SECRET=PROXY_SECRET)
class GetClientIpTests(SimpleTestCase):
    def setUp(self):
        self.factory = RequestFactory()

    def _ip(self, **meta):
        return get_client_ip(self.factory.get("/", REMOTE_ADDR=ALB_PEER, **meta))

    def test_takes_the_address_the_trusted_proxy_appended_not_the_client_supplied_prefix(self):
        self.assertEqual(self._ip(HTTP_X_FORWARDED_FOR="1.2.3.4, 5.6.7.8, 203.0.113.7"), "203.0.113.7")

    def test_without_forwarded_for_uses_the_socket_peer(self):
        self.assertEqual(self._ip(), ALB_PEER)

    @override_settings(TRUSTED_PROXY_COUNT=0)
    def test_forwarded_for_is_ignored_when_no_proxy_is_trusted(self):
        self.assertEqual(self._ip(HTTP_X_FORWARDED_FOR="203.0.113.7"), ALB_PEER)

    def test_bff_asserted_address_is_used_only_with_the_proxy_secret(self):
        with_secret = {
            _meta_header(BFF_CLIENT_IP_HEADER): "192.0.2.10", _meta_header(BFF_PROXY_AUTH_HEADER): PROXY_SECRET,
        }
        self.assertEqual(self._ip(HTTP_X_FORWARDED_FOR=BFF_EGRESS, **with_secret), "192.0.2.10")

        forged = {_meta_header(BFF_CLIENT_IP_HEADER): "192.0.2.10", _meta_header(BFF_PROXY_AUTH_HEADER): "guess"}
        self.assertEqual(self._ip(HTTP_X_FORWARDED_FOR=BFF_EGRESS, **forged), BFF_EGRESS)
        self.assertEqual(self._ip(HTTP_X_FORWARDED_FOR=BFF_EGRESS, **{_meta_header(BFF_CLIENT_IP_HEADER): "192.0.2.10"}), BFF_EGRESS)

    @override_settings(BFF_PROXY_SECRET="")
    def test_no_bff_assertion_is_trusted_when_no_secret_is_configured(self):
        headers = {_meta_header(BFF_CLIENT_IP_HEADER): "192.0.2.10", _meta_header(BFF_PROXY_AUTH_HEADER): ""}
        self.assertEqual(self._ip(HTTP_X_FORWARDED_FOR=BFF_EGRESS, **headers), BFF_EGRESS)

    def test_values_that_are_not_ip_addresses_are_never_used(self):
        self.assertEqual(self._ip(HTTP_X_FORWARDED_FOR="not-an-ip"), ALB_PEER)
        headers = {_meta_header(BFF_CLIENT_IP_HEADER): "evil value", _meta_header(BFF_PROXY_AUTH_HEADER): PROXY_SECRET}
        self.assertEqual(self._ip(HTTP_X_FORWARDED_FOR=BFF_EGRESS, **headers), BFF_EGRESS)

    def test_ipv6_is_accepted(self):
        self.assertEqual(self._ip(HTTP_X_FORWARDED_FOR="2001:db8::1"), "2001:db8::1")


@override_settings(TRUSTED_PROXY_COUNT=1, BFF_PROXY_SECRET=PROXY_SECRET)
class LoginThrottleIdentityTests(APITestCase):
    def setUp(self):
        cache.clear()

    def _login(self, **meta):
        return self.client.post(
            "/api/v1/auth/login/", {"email": "nobody@example.com", "password": "wrong-password-1"},
            format="json", REMOTE_ADDR=ALB_PEER, **meta,
        ).status_code

    def test_rotating_a_forged_forwarded_for_prefix_does_not_evade_the_login_limit(self):
        statuses = [
            self._login(HTTP_X_FORWARDED_FOR=f"198.51.100.{attempt}, 203.0.113.7") for attempt in range(AUTH_LIMIT + 1)
        ]
        self.assertEqual(statuses[-1], 429)

    def test_distinct_clients_behind_the_alb_do_not_share_a_limit(self):
        for _ in range(AUTH_LIMIT + 1):
            self._login(HTTP_X_FORWARDED_FOR="203.0.113.7")
        self.assertEqual(self._login(HTTP_X_FORWARDED_FOR="203.0.113.7"), 429)
        self.assertNotEqual(self._login(HTTP_X_FORWARDED_FOR="203.0.113.8"), 429)

    def _via_bff(self, client_ip, secret=PROXY_SECRET):
        return self._login(
            HTTP_X_FORWARDED_FOR=BFF_EGRESS,
            **{_meta_header(BFF_CLIENT_IP_HEADER): client_ip, _meta_header(BFF_PROXY_AUTH_HEADER): secret},
        )

    def test_browser_users_signing_in_through_the_bff_do_not_share_a_limit(self):
        for _ in range(AUTH_LIMIT + 1):
            self._via_bff("192.0.2.10")
        self.assertEqual(self._via_bff("192.0.2.10"), 429)
        self.assertNotEqual(self._via_bff("192.0.2.11"), 429)

    def test_forging_the_bff_client_header_without_the_secret_does_not_evade_the_limit(self):
        statuses = [self._via_bff(f"192.0.2.{attempt}", secret="guess") for attempt in range(AUTH_LIMIT + 1)]
        self.assertEqual(statuses[-1], 429)
