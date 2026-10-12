"""Tests for sn_cli/scripts.py — the script-type CRUD helpers."""

import unittest
from unittest.mock import MagicMock, call

from sn_cli.errors import SnowError
from sn_cli.scripts import (
    SCRIPT_TYPES,
    create_business_rule,
    create_client_script,
    create_fix_script,
    create_scheduled_script,
    create_script_include,
    create_ui_action,
    get_script,
    list_script_type_catalog,
    query_scripts,
    update_script,
)


def _mock_client():
    c = MagicMock()
    c.table_get.return_value = []
    c.table_get_record.return_value = {"sys_id": "abc123", "name": "My Script"}
    c.table_post.return_value = {"sys_id": "new123", "name": "New Script"}
    c.table_patch.return_value = {"sys_id": "abc123", "name": "Updated"}
    return c


class TestCatalog(unittest.TestCase):
    def test_catalog_covers_all_types(self):
        cat = list_script_type_catalog()
        self.assertEqual(set(cat), set(SCRIPT_TYPES))

    def test_catalog_has_required_keys(self):
        for type_name, info in list_script_type_catalog().items():
            self.assertIn("table", info, type_name)
            self.assertIn("required_fields", info, type_name)
            self.assertIn("all_fields", info, type_name)

    def test_catalog_no_network(self):
        # list_script_type_catalog must not require a client
        cat = list_script_type_catalog()
        self.assertGreater(len(cat), 0)


class TestQueryScripts(unittest.TestCase):
    def test_calls_table_get_with_correct_table(self):
        c = _mock_client()
        query_scripts(c, "business_rule", query="active=true", limit=5)
        c.table_get.assert_called_once()
        args = c.table_get.call_args
        self.assertEqual(args[0][0], "sys_script")
        self.assertEqual(args[1].get("limit") or args[0][3], 5)

    def test_invalid_type_raises(self):
        c = _mock_client()
        with self.assertRaises(SnowError):
            query_scripts(c, "nonexistent_type")

    def test_all_types_hit_correct_tables(self):
        expected = {
            "business_rule": "sys_script",
            "script_include": "sys_script_include",
            "client_script": "sys_script_client",
            "ui_action": "sys_ui_action",
            "ui_page": "sys_ui_page",
            "scheduled_script": "sysauto_script",
            "fix_script": "sys_script_fix",
        }
        for stype, table in expected.items():
            c = _mock_client()
            query_scripts(c, stype)
            actual_table = c.table_get.call_args[0][0]
            self.assertEqual(actual_table, table, f"{stype} should query {table}")


class TestGetScript(unittest.TestCase):
    def test_sys_id_uses_get_record(self):
        c = _mock_client()
        sys_id = "a" * 32
        get_script(c, "script_include", sys_id)
        c.table_get_record.assert_called_once_with(
            "sys_script_include", sys_id,
            fields=SCRIPT_TYPES["script_include"]["fields"]
        )

    def test_name_falls_back_to_list_search(self):
        c = _mock_client()
        c.table_get.return_value = [{"sys_id": "abc123", "name": "My SI"}]
        get_script(c, "script_include", "My SI")
        # first call is exact name match
        self.assertIn("name=My SI", c.table_get.call_args_list[0][1].get("query", "") or
                      c.table_get.call_args_list[0][0][1])

    def test_ambiguous_name_raises(self):
        c = _mock_client()
        # exact match returns 2 — ambiguous
        c.table_get.return_value = [
            {"sys_id": "aaa", "name": "Dupe"},
            {"sys_id": "bbb", "name": "Dupe"},
        ]
        with self.assertRaises(SnowError):
            get_script(c, "script_include", "Dupe")

    def test_not_found_raises(self):
        c = _mock_client()
        c.table_get.return_value = []
        with self.assertRaises(SnowError):
            get_script(c, "script_include", "Ghost")


class TestUpdateScript(unittest.TestCase):
    def test_resolves_then_patches(self):
        c = _mock_client()
        c.table_get.return_value = [{"sys_id": "abc123", "name": "My BR"}]
        update_script(c, "business_rule", "My BR", {"active": False})
        c.table_patch.assert_called_once_with("sys_script", "abc123", {"active": False})


class TestCreateBusinessRule(unittest.TestCase):
    def test_posts_to_sys_script(self):
        c = _mock_client()
        create_business_rule(c, "My BR", "incident", "before", "// script")
        c.table_post.assert_called_once()
        table, data = c.table_post.call_args[0]
        self.assertEqual(table, "sys_script")
        self.assertEqual(data["collection"], "incident")
        self.assertEqual(data["when"], "before")

    def test_invalid_when_raises(self):
        c = _mock_client()
        with self.assertRaises(SnowError):
            create_business_rule(c, "BR", "incident", "sideways", "// script")

    def test_kwargs_forwarded(self):
        c = _mock_client()
        create_business_rule(c, "BR", "incident", "after", "// s",
                              action_update=True, order=200)
        _, data = c.table_post.call_args[0]
        self.assertTrue(data["action_update"])
        self.assertEqual(data["order"], 200)


class TestCreateScriptInclude(unittest.TestCase):
    def test_posts_to_correct_table(self):
        c = _mock_client()
        create_script_include(c, "MyHelper", "var MyHelper = Class.create();")
        table, data = c.table_post.call_args[0]
        self.assertEqual(table, "sys_script_include")
        self.assertEqual(data["name"], "MyHelper")

    def test_defaults(self):
        c = _mock_client()
        create_script_include(c, "MyHelper", "// script")
        _, data = c.table_post.call_args[0]
        self.assertTrue(data["active"])
        self.assertFalse(data["client_callable"])


class TestCreateClientScript(unittest.TestCase):
    def test_valid_type_accepted(self):
        c = _mock_client()
        create_client_script(c, "My CS", "incident", "onLoad", "function onLoad(){}")
        table, data = c.table_post.call_args[0]
        self.assertEqual(table, "sys_script_client")
        self.assertEqual(data["type"], "onLoad")

    def test_invalid_type_raises(self):
        c = _mock_client()
        with self.assertRaises(SnowError):
            create_client_script(c, "CS", "incident", "onHover", "function(){}")

    def test_defaults(self):
        c = _mock_client()
        create_client_script(c, "CS", "incident", "onSubmit", "// s")
        _, data = c.table_post.call_args[0]
        self.assertEqual(data["ui_type"], "desktop")
        self.assertTrue(data["active"])


class TestCreateUiAction(unittest.TestCase):
    def test_posts_to_sys_ui_action(self):
        c = _mock_client()
        create_ui_action(c, "Approve", "change_request", script="current.state=3;")
        table, data = c.table_post.call_args[0]
        self.assertEqual(table, "sys_ui_action")
        self.assertEqual(data["name"], "Approve")
        self.assertEqual(data["script"], "current.state=3;")


class TestCreateScheduledScript(unittest.TestCase):
    def test_valid_run_type(self):
        c = _mock_client()
        create_scheduled_script(c, "Nightly Cleanup", "// cleanup", "daily")
        table, data = c.table_post.call_args[0]
        self.assertEqual(table, "sysauto_script")
        self.assertEqual(data["run_type"], "daily")

    def test_invalid_run_type_raises(self):
        c = _mock_client()
        with self.assertRaises(SnowError):
            create_scheduled_script(c, "S", "// s", "every_second")


class TestCreateFixScript(unittest.TestCase):
    def test_created_inactive_by_default(self):
        c = _mock_client()
        create_fix_script(c, "Backfill SLA", "// fix")
        _, data = c.table_post.call_args[0]
        self.assertFalse(data["active"])

    def test_active_can_be_overridden(self):
        c = _mock_client()
        create_fix_script(c, "Backfill SLA", "// fix", active=True)
        _, data = c.table_post.call_args[0]
        self.assertTrue(data["active"])

    def test_posts_to_sys_script_fix(self):
        c = _mock_client()
        create_fix_script(c, "Backfill SLA", "// fix")
        table, _ = c.table_post.call_args[0]
        self.assertEqual(table, "sys_script_fix")


if __name__ == "__main__":
    unittest.main()
