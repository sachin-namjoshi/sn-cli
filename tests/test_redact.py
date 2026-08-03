"""Redaction strips registered secrets and token-shaped substrings."""

import unittest

from sn_cli import redact


class RedactTest(unittest.TestCase):
    def test_registered_secret_is_masked(self):
        redact.register_secret("hunter2superSecret")
        out = redact.redact("value is hunter2superSecret here")
        self.assertNotIn("hunter2superSecret", out)
        self.assertIn("REDACTED", out)

    def test_bearer_header_is_masked(self):
        out = redact.redact("Authorization: Bearer abc123.def-456_XYZ")
        self.assertNotIn("abc123.def-456_XYZ", out)

    def test_json_token_field_is_masked(self):
        out = redact.redact('{"access_token":"zzzTOKENzzz","x":1}')
        self.assertNotIn("zzzTOKENzzz", out)
        self.assertIn('"x":1', out)

    def test_none_passthrough(self):
        self.assertIsNone(redact.redact(None))


if __name__ == "__main__":
    unittest.main()
