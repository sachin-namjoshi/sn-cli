"""Layer 1 — the credential seam.

CredentialProvider is the ONLY thing above storage/oauth that commands and the
client touch. A command asks for an access token; it never touches the keychain
directly and never knows whether the token was cached, refreshed, or freshly
logged in.

Policy lives here: refresh proactively when the token is within SKEW seconds of
expiry, and let the client force a refresh on a 401 (allow_refresh path).
"""

import time

from . import oauth
from .errors import AuthError

# Refresh this many seconds BEFORE expiry so a request never races the clock.
SKEW_SECONDS = 120


class CredentialProvider:
    def __init__(self, config, backend):
        self.config = config
        self.backend = backend
        self._tokens = None

    def _load(self):
        if self._tokens is None:
            self._tokens = self.backend.load()
        return self._tokens

    def _store(self, tokens):
        # refresh_token grant may not re-issue a refresh token; keep the old one.
        prior = self._tokens or {}
        if not tokens.get("refresh_token"):
            tokens["refresh_token"] = prior.get("refresh_token")
        self._tokens = tokens
        self.backend.save(tokens)
        return tokens

    def login(self):
        """Full interactive auth-code flow. Overwrites stored tokens."""
        tokens = oauth.authorization_code_flow(self.config)
        return self._store(tokens)

    def refresh(self):
        """Force a refresh_token grant now."""
        tokens = self._load()
        if not tokens:
            raise AuthError("Not logged in — run `snow auth login`.")
        fresh = oauth.refresh(self.config, tokens.get("refresh_token"))
        return self._store(fresh)

    def get_access_token(self, allow_refresh=True):
        """Return a valid access token, refreshing proactively if near expiry."""
        tokens = self._load()
        if not tokens:
            raise AuthError("Not logged in — run `snow auth login`.")
        expires_at = int(tokens.get("expires_at", 0))
        if allow_refresh and expires_at - time.time() <= SKEW_SECONDS:
            tokens = self.refresh()
        return tokens["access_token"]

    def status(self):
        """Return {logged_in, expires_at, seconds_left, valid} without refreshing."""
        tokens = self._load()
        if not tokens:
            return {"logged_in": False}
        expires_at = int(tokens.get("expires_at", 0))
        left = int(expires_at - time.time())
        return {
            "logged_in": True,
            "expires_at": expires_at,
            "seconds_left": left,
            "valid": left > 0,
        }
