"""storage backend — KeychainBackend seam + factory. Never touches a real
keychain or the network; the OS store is replaced with an in-memory fake.

Posture under test: keychain-only. There is no cleartext file anywhere. A
missing keychain or a failing store op is a hard error, never a downgrade."""

import json
import unittest

from snow_cli import storage
from snow_cli.errors import AuthError, ConfigError
from snow_cli.storage import KeychainBackend, make_backend


class FakeStore:
    """In-memory stand-in for an OS keychain store (one string secret)."""

    name = "FakeStore"

    def __init__(self, value=None, fail_on=None):
        self.value = value
        self.fail_on = fail_on or set()  # ops that should raise
        self.deleted = False

    def _maybe_fail(self, op):
        if op in self.fail_on:
            raise OSError(f"boom on {op}")

    def get(self):
        self._maybe_fail("get")
        return self.value

    def set(self, secret):
        self._maybe_fail("set")
        self.value = secret

    def delete(self):
        self._maybe_fail("delete")
        self.value = None
        self.deleted = True


class FakeConfig:
    instance = "fake.service-now.com"


class KeychainBackendTest(unittest.TestCase):
    def test_save_then_load_roundtrip(self):
        store = FakeStore()
        be = KeychainBackend(store)
        tokens = {"access_token": "A", "refresh_token": "R", "expires_at": 1}
        be.save(tokens)
        # Whole dict is stored as one JSON secret in the keychain.
        self.assertEqual(json.loads(store.value), tokens)
        self.assertEqual(KeychainBackend(store).load(), tokens)

    def test_load_empty_returns_none(self):
        self.assertIsNone(KeychainBackend(FakeStore()).load())

    def test_delete_clears_store(self):
        store = FakeStore(value=json.dumps({"access_token": "A"}))
        KeychainBackend(store).delete()
        self.assertTrue(store.deleted)
        self.assertIsNone(store.value)

    def test_corrupt_keychain_value_raises(self):
        with self.assertRaises(AuthError):
            KeychainBackend(FakeStore(value="not json")).load()

    def test_store_get_failure_is_hard_error(self):
        with self.assertRaises(AuthError):
            KeychainBackend(FakeStore(fail_on={"get"})).load()

    def test_store_set_failure_is_hard_error(self):
        with self.assertRaises(AuthError):
            KeychainBackend(FakeStore(fail_on={"set"})).save(
                {"access_token": "A", "refresh_token": "R", "expires_at": 1})


class MakeBackendTest(unittest.TestCase):
    def test_no_keychain_is_hard_error(self):
        orig = storage._select_store
        storage._select_store = lambda account: None
        try:
            with self.assertRaises(ConfigError):
                make_backend(FakeConfig())
        finally:
            storage._select_store = orig

    def test_available_store_yields_keychain_backend(self):
        orig = storage._select_store
        storage._select_store = lambda account: FakeStore()
        try:
            be = make_backend(FakeConfig())
        finally:
            storage._select_store = orig
        self.assertIsInstance(be, KeychainBackend)


if __name__ == "__main__":
    unittest.main()
