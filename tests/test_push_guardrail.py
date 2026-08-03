"""push refuses to write without confirmation when non-interactive."""

import types
import unittest
from unittest import mock

from sn_cli.commands import widget
from sn_cli.errors import ConfirmationRequired


class ConfirmGuardrailTest(unittest.TestCase):
    def _args(self, yes=False, force=False):
        return types.SimpleNamespace(yes=yes, force=force)

    def test_refuses_without_yes_when_no_tty(self):
        with mock.patch("sys.stdin.isatty", return_value=False):
            with self.assertRaises(ConfirmationRequired):
                widget._confirm(self._args(), "Push?")

    def test_yes_flag_bypasses_prompt(self):
        with mock.patch("sys.stdin.isatty", return_value=False):
            self.assertTrue(widget._confirm(self._args(yes=True), "Push?"))

    def test_force_flag_bypasses_prompt(self):
        with mock.patch("sys.stdin.isatty", return_value=False):
            self.assertTrue(widget._confirm(self._args(force=True), "Push?"))


if __name__ == "__main__":
    unittest.main()
