"""Update-set naming is the canonical `{Initials}: {Widget} - {Feature}`."""

import unittest

from snow_cli.commands.updateset import build_name


class NamingTest(unittest.TestCase):
    def test_canonical_format(self):
        self.assertEqual(
            build_name("JB", "Inventory Reservation Manager", "Add logging"),
            "JB: Inventory Reservation Manager - Add logging",
        )


if __name__ == "__main__":
    unittest.main()
