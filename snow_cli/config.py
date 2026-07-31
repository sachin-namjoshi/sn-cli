"""Layer 1 — static configuration from ~/.snow/config.json.

config.json holds the OAuth-app identifiers (instance, client_id, redirect_uri)
and must be chmod 600. The app is a public client using PKCE, so there is NO
client_secret to distribute; if a legacy config still carries one it is honored
but no longer required. Tokens are stored in the OS keychain (see storage.py),
never on disk.

Optional fields:
  initials       - initials used in update-set names (e.g. "JB"); otherwise
                   derived from your ServiceNow user record.
  dev_instances  - list of instance short-names that allow DEV commands
                   (widget, updateset, dict). Omit to restrict everywhere.
                   Example: ["mycompany-dev", "mycompany-dev2"]
"""

import json
import os

from .errors import ConfigError
from .redact import register_secret

DEFAULT_CONFIG_PATH = os.path.expanduser("~/.snow/config.json")
_REQUIRED = ("instance", "client_id", "redirect_uri")


class Config:
    def __init__(self, data, path):
        self._data = data
        self.path = path

    @property
    def instance(self):
        return self._data["instance"]

    @property
    def base_url(self):
        return f"https://{self._data['instance']}"

    @property
    def client_id(self):
        return self._data["client_id"]

    @property
    def client_secret(self):
        # Optional: a public (PKCE) client has none, so this is not in _REQUIRED
        # and returns None when absent. A legacy confidential config that still
        # carries one is honored (oauth.py sends it only if present).
        return self._data.get("client_secret")

    @property
    def redirect_uri(self):
        return self._data["redirect_uri"]

    @property
    def initials(self):
        """Override for update-set naming; None means derive from `whoami`."""
        return self._data.get("initials")

    def get(self, key, default=None):
        return self._data.get(key, default)


def load_config(path=None):
    path = path or DEFAULT_CONFIG_PATH
    if not os.path.exists(path):
        raise ConfigError(
            f"No config at {path}. Create it with instance, client_id, "
            f"redirect_uri (chmod 600)."
        )
    try:
        with open(path) as f:
            data = json.load(f)
    except (OSError, ValueError) as e:
        raise ConfigError(f"Cannot read {path}: {e}")

    missing = [k for k in _REQUIRED if not data.get(k)]
    if missing:
        raise ConfigError(f"{path} is missing required key(s): {', '.join(missing)}")

    if data.get("client_secret"):
        register_secret(data["client_secret"])
    return Config(data, path)
