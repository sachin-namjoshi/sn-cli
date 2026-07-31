"""Instance-tier permission policy. Pure, offline: no network, no store.

Posture under test: the dev-instance set comes from config; everything else is
restricted to general-purpose querying. Fail-safe: unknown instance ->
restricted, unannotated command -> DEV (blocked off dev).
"""

import unittest

from snow_cli import permissions as perm
from snow_cli.errors import PermissionDeniedError


def _cfg(*dev_instances):
    """Build a minimal fake config dict."""
    return {"dev_instances": list(dev_instances)}


class TierResolutionTest(unittest.TestCase):
    def test_configured_short_names_are_development(self):
        cfg = _cfg("mydev", "mydev2")
        self.assertEqual(perm.tier_for("mydev", cfg), perm.TIER_DEV)
        self.assertEqual(perm.tier_for("mydev2", cfg), perm.TIER_DEV)

    def test_configured_full_hosts_are_development(self):
        cfg = _cfg("mydev")
        self.assertEqual(
            perm.tier_for("mydev.service-now.com", cfg), perm.TIER_DEV)
        self.assertEqual(
            perm.tier_for("MYDEV.service-now.com", cfg), perm.TIER_DEV)

    def test_unconfigured_instance_is_restricted(self):
        cfg = _cfg("mydev")
        self.assertEqual(perm.tier_for("myprod", cfg), perm.TIER_RESTRICTED)
        self.assertEqual(perm.tier_for("mytest", cfg), perm.TIER_RESTRICTED)

    def test_unknown_instance_is_restricted(self):
        self.assertEqual(perm.tier_for("something-else"), perm.TIER_RESTRICTED)
        self.assertEqual(perm.tier_for(""), perm.TIER_RESTRICTED)
        self.assertEqual(perm.tier_for(None), perm.TIER_RESTRICTED)

    def test_no_dev_instances_configured_all_restricted(self):
        cfg = _cfg()
        self.assertFalse(perm.is_dev_instance("anything", cfg))

    def test_no_config_all_restricted(self):
        self.assertFalse(perm.is_dev_instance("anything", None))


class CheckAllowTest(unittest.TestCase):
    def setUp(self):
        self.cfg = _cfg("mydev", "mydev2")

    def test_local_allowed_everywhere(self):
        for inst in ("mydev", "myprod", "mystery"):
            perm.check(inst, perm.LOCAL, self.cfg, "auth login")  # no raise

    def test_general_allowed_everywhere(self):
        for inst in ("mydev", "myprod", "mystery"):
            perm.check(inst, perm.GENERAL, self.cfg, "query")  # no raise

    def test_dev_allowed_on_configured_dev_instances(self):
        for inst in ("mydev", "mydev2"):
            perm.check(inst, perm.DEV, self.cfg, "widget push")  # no raise


class CheckDenyTest(unittest.TestCase):
    def setUp(self):
        self.cfg = _cfg("mydev")

    def test_dev_denied_on_unconfigured_instance(self):
        with self.assertRaises(PermissionDeniedError):
            perm.check("myprod", perm.DEV, self.cfg, "widget push")

    def test_dev_denied_on_unknown(self):
        with self.assertRaises(PermissionDeniedError):
            perm.check("mystery", perm.DEV, self.cfg, "dict")

    def test_unannotated_bucket_is_treated_as_dev(self):
        with self.assertRaises(PermissionDeniedError):
            perm.check("myprod", "some-unknown-bucket", self.cfg, "future cmd")
        perm.check("mydev", "some-unknown-bucket", self.cfg, "future cmd")  # no raise

    def test_denial_message_names_command_and_instance(self):
        with self.assertRaises(PermissionDeniedError) as cm:
            perm.check("myprod", perm.DEV, self.cfg, "widget push")
        msg = str(cm.exception)
        self.assertIn("widget push", msg)
        self.assertIn("myprod", msg)

    def test_exit_code_is_seven(self):
        self.assertEqual(PermissionDeniedError.exit_code, 7)


if __name__ == "__main__":
    unittest.main()
