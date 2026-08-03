"""Tests for `snow update` — field parsing, validation, offline only."""

import unittest

from sn_cli.commands.update import _parse_fields
from sn_cli.errors import SnowError


class ParseFieldsTest(unittest.TestCase):
    def test_single_pair(self):
        self.assertEqual(_parse_fields(["state=6"]), {"state": "6"})

    def test_multiple_pairs(self):
        result = _parse_fields(["state=6", "priority=2", "close_code=Solution provided"])
        self.assertEqual(result, {
            "state": "6",
            "priority": "2",
            "close_code": "Solution provided",
        })

    def test_value_containing_equals(self):
        # Values like encoded queries may contain '='
        result = _parse_fields(["sysparm_query=active=true^state=1"])
        self.assertEqual(result, {"sysparm_query": "active=true^state=1"})

    def test_empty_value_allowed(self):
        self.assertEqual(_parse_fields(["description="]), {"description": ""})

    def test_missing_equals_raises(self):
        with self.assertRaises(SnowError):
            _parse_fields(["no_equals_here"])

    def test_empty_field_name_raises(self):
        with self.assertRaises(SnowError):
            _parse_fields(["=value"])

    def test_whitespace_stripped_from_field(self):
        result = _parse_fields([" state =6"])
        self.assertIn("state", result)


class CommandRegistrationTest(unittest.TestCase):
    def test_update_registered_in_parser(self):
        from sn_cli.cli import build_parser
        parser = build_parser()
        # Should parse without error
        args = parser.parse_args([
            "update", "incident", "abc123",
            "--set", "state=6", "priority=2",
            "--yes",
        ])
        self.assertEqual(args.table, "incident")
        self.assertEqual(args.sys_id, "abc123")
        self.assertEqual(args.fields, ["state=6", "priority=2"])
        self.assertTrue(args.yes)

    def test_query_flag_parsed(self):
        from sn_cli.cli import build_parser
        parser = build_parser()
        args = parser.parse_args([
            "update", "incident",
            "-q", "number=INC0009009",
            "--set", "state=6",
            "--yes",
        ])
        self.assertIsNone(args.sys_id)
        self.assertEqual(args.query, "number=INC0009009")

    def test_set_required(self):
        from sn_cli.cli import build_parser
        parser = build_parser()
        with self.assertRaises(SystemExit):
            parser.parse_args(["update", "incident", "abc123"])


if __name__ == "__main__":
    unittest.main()
