"""CredentialProvider refresh policy — never hits the network (oauth is mocked)."""

import time
import unittest

from sn_cli import oauth
from sn_cli.credentials import SKEW_SECONDS, CredentialProvider


class FakeBackend:
    def __init__(self, tokens):
        self.tokens = tokens
        self.saved = None

    def load(self):
        return self.tokens

    def save(self, tokens):
        self.saved = tokens


class FakeConfig:
    client_id = "cid"
    client_secret = "sec"

    @property
    def base_url(self):
        return "https://example.service-now.com"


class RefreshPolicyTest(unittest.TestCase):
    def setUp(self):
        self._orig = oauth.refresh
        oauth.refresh = self._fake_refresh
        self.refreshed = False

    def tearDown(self):
        oauth.refresh = self._orig

    def _fake_refresh(self, config, refresh_token):
        self.refreshed = True
        return {"access_token": "NEW", "refresh_token": "r2",
                "expires_at": int(time.time()) + 1800}

    def test_refreshes_when_near_expiry(self):
        near = int(time.time()) + (SKEW_SECONDS - 5)  # inside the skew window
        cp = CredentialProvider(FakeConfig(), FakeBackend(
            {"access_token": "OLD", "refresh_token": "r1", "expires_at": near}))
        token = cp.get_access_token()
        self.assertTrue(self.refreshed)
        self.assertEqual(token, "NEW")

    def test_no_refresh_when_fresh(self):
        far = int(time.time()) + 3600  # comfortably valid
        cp = CredentialProvider(FakeConfig(), FakeBackend(
            {"access_token": "OLD", "refresh_token": "r1", "expires_at": far}))
        token = cp.get_access_token()
        self.assertFalse(self.refreshed)
        self.assertEqual(token, "OLD")

    def test_keeps_prior_refresh_token_when_grant_omits_it(self):
        far = int(time.time()) + 3600
        cp = CredentialProvider(FakeConfig(), FakeBackend(
            {"access_token": "OLD", "refresh_token": "KEEPME", "expires_at": far}))
        oauth.refresh = lambda config, rt: {"access_token": "NEW", "expires_at": far}
        cp.refresh()
        self.assertEqual(cp._tokens["refresh_token"], "KEEPME")


if __name__ == "__main__":
    unittest.main()
