"""MCP server entry point — exposes ServiceNow as an AI tool surface.

Requires the optional [mcp] extra:

    pip install sn-cli[mcp]

Auth reuses the token stored by `snow auth login`; no new credential flow is
needed. If the token is expired it is silently refreshed via the stored
refresh_token (same logic as the CLI).

Start the server:

    snow-mcp

Claude Desktop config (~/.claude/claude_desktop_config.json):

    {
      "mcpServers": {
        "servicenow": {
          "command": "snow-mcp"
        }
      }
    }

Tools (16 total):
  Generic records  : query_records, get_record, create_record, update_record, whoami
  Incidents        : create_incident, resolve_incident
  Scripts (catalog): list_script_types
  Scripts (generic): query_scripts, get_script, update_script
  Scripts (creates): create_business_rule, create_script_include,
                     create_client_script, create_ui_action,
                     create_scheduled_script, create_fix_script
"""

import asyncio
import json
import sys

from .config import load_config
from .credentials import CredentialProvider
from .errors import SnowError
from .storage import make_backend
from . import scripts as sc

try:
    from mcp.server import Server
    from mcp.server.stdio import stdio_server
    from mcp.types import TextContent, Tool
except ImportError:
    print(
        "error: the 'mcp' package is required.\n"
        "Install it with:  pip install sn-cli[mcp]",
        file=sys.stderr,
    )
    sys.exit(1)


def _client():
    """Build a fresh SnowClient from stored credentials."""
    from .client import SnowClient

    config = load_config()
    creds = CredentialProvider(config, make_backend(config))
    return SnowClient(config, creds)


def _ok(data) -> list[TextContent]:
    return [TextContent(type="text", text=json.dumps(data, indent=2))]


def _err(msg: str) -> list[TextContent]:
    return [TextContent(type="text", text=f"error: {msg}")]


# ── server ────────────────────────────────────────────────────────────────────

app = Server("servicenow")


@app.list_tools()
async def list_tools() -> list[Tool]:
    return [
        # ── generic records ──────────────────────────────────────────────────
        Tool(
            name="query_records",
            description="Query any ServiceNow table via the Table API.",
            inputSchema={
                "type": "object",
                "properties": {
                    "table":  {"type": "string", "description": "Table name, e.g. incident, sys_user."},
                    "query":  {"type": "string", "description": "Encoded sysparm_query."},
                    "fields": {"type": "string", "description": "Comma-separated field list."},
                    "limit":  {"type": "integer", "default": 10, "description": "Max rows (default 10)."},
                },
                "required": ["table"],
            },
        ),
        Tool(
            name="get_record",
            description="Fetch a single ServiceNow record by sys_id.",
            inputSchema={
                "type": "object",
                "properties": {
                    "table":  {"type": "string"},
                    "sys_id": {"type": "string"},
                    "fields": {"type": "string", "description": "Comma-separated field list."},
                },
                "required": ["table", "sys_id"],
            },
        ),
        Tool(
            name="create_record",
            description="Create a new record in any ServiceNow table.",
            inputSchema={
                "type": "object",
                "properties": {
                    "table": {"type": "string"},
                    "data":  {"type": "object", "description": "Field/value pairs for the new record."},
                },
                "required": ["table", "data"],
            },
        ),
        Tool(
            name="update_record",
            description="Update fields on an existing ServiceNow record.",
            inputSchema={
                "type": "object",
                "properties": {
                    "table":  {"type": "string"},
                    "sys_id": {"type": "string"},
                    "data":   {"type": "object", "description": "Fields to update."},
                },
                "required": ["table", "sys_id", "data"],
            },
        ),
        Tool(
            name="whoami",
            description="Return the currently authenticated ServiceNow user.",
            inputSchema={"type": "object", "properties": {}},
        ),
        # ── incidents ────────────────────────────────────────────────────────
        Tool(
            name="create_incident",
            description="Create a new incident record.",
            inputSchema={
                "type": "object",
                "properties": {
                    "short_description": {"type": "string"},
                    "description":       {"type": "string"},
                    "urgency":           {"type": "integer", "enum": [1, 2, 3], "description": "1=High 2=Medium 3=Low"},
                    "impact":            {"type": "integer", "enum": [1, 2, 3]},
                    "assignment_group":  {"type": "string", "description": "Group name or sys_id."},
                    "category":          {"type": "string"},
                    "subcategory":       {"type": "string"},
                    "caller_id":         {"type": "string", "description": "User sys_id or user_name."},
                },
                "required": ["short_description"],
            },
        ),
        Tool(
            name="resolve_incident",
            description="Resolve an incident (state=6) by number or sys_id.",
            inputSchema={
                "type": "object",
                "properties": {
                    "identifier":        {"type": "string", "description": "Incident number (INC0001234) or sys_id."},
                    "resolution_code":   {"type": "string", "description": "close_code value, e.g. 'Solved (Permanently)'."},
                    "resolution_notes":  {"type": "string"},
                },
                "required": ["identifier", "resolution_notes"],
            },
        ),
        # ── script catalog ───────────────────────────────────────────────────
        Tool(
            name="list_script_types",
            description=(
                "Return the catalog of supported script types, their ServiceNow tables, "
                "required/optional fields, and field notes. Use this before creating scripts "
                "to know which fields are available."
            ),
            inputSchema={"type": "object", "properties": {}},
        ),
        # ── scripts generic ──────────────────────────────────────────────────
        Tool(
            name="query_scripts",
            description="List scripts of a given type (business_rule, script_include, client_script, ui_action, ui_page, scheduled_script, fix_script).",
            inputSchema={
                "type": "object",
                "properties": {
                    "script_type": {"type": "string"},
                    "query":  {"type": "string", "description": "Encoded sysparm_query."},
                    "fields": {"type": "string", "description": "Comma-separated fields (default: sys_id, name, active)."},
                    "limit":  {"type": "integer", "default": 10},
                },
                "required": ["script_type"],
            },
        ),
        Tool(
            name="get_script",
            description="Fetch a single script record by sys_id or name.",
            inputSchema={
                "type": "object",
                "properties": {
                    "script_type": {"type": "string"},
                    "identifier":  {"type": "string", "description": "sys_id (32 hex chars) or script name."},
                },
                "required": ["script_type", "identifier"],
            },
        ),
        Tool(
            name="update_script",
            description="Update fields on a script record (any type) by sys_id or name.",
            inputSchema={
                "type": "object",
                "properties": {
                    "script_type": {"type": "string"},
                    "identifier":  {"type": "string", "description": "sys_id or name."},
                    "data":        {"type": "object", "description": "Fields to update, e.g. {\"script\": \"...\", \"active\": true}."},
                },
                "required": ["script_type", "identifier", "data"],
            },
        ),
        # ── script type-specific creates ─────────────────────────────────────
        Tool(
            name="create_business_rule",
            description="Create a Business Rule (sys_script) — server-side, triggers on table CRUD.",
            inputSchema={
                "type": "object",
                "properties": {
                    "name":            {"type": "string"},
                    "collection":      {"type": "string", "description": "Target table, e.g. incident."},
                    "when":            {"type": "string", "enum": ["before", "after", "async", "display"]},
                    "script":          {"type": "string"},
                    "condition":       {"type": "string", "description": "JavaScript condition expression."},
                    "action_insert":   {"type": "boolean", "default": False},
                    "action_update":   {"type": "boolean", "default": False},
                    "action_delete":   {"type": "boolean", "default": False},
                    "action_query":    {"type": "boolean", "default": False},
                    "order":           {"type": "integer", "default": 100},
                    "active":          {"type": "boolean", "default": True},
                },
                "required": ["name", "collection", "when", "script"],
            },
        ),
        Tool(
            name="create_script_include",
            description="Create a Script Include (sys_script_include) — reusable server-side JS library.",
            inputSchema={
                "type": "object",
                "properties": {
                    "name":            {"type": "string"},
                    "script":          {"type": "string"},
                    "api_name":        {"type": "string", "description": "Scoped API name, e.g. x_myapp.MyHelper. Auto-derived if omitted."},
                    "client_callable": {"type": "boolean", "default": False, "description": "Expose via GlideAjax."},
                    "description":     {"type": "string"},
                    "active":          {"type": "boolean", "default": True},
                    "access":          {"type": "string", "enum": ["public", "package_private"], "default": "public"},
                },
                "required": ["name", "script"],
            },
        ),
        Tool(
            name="create_client_script",
            description="Create a Client Script (sys_script_client) — runs in the browser on form events.",
            inputSchema={
                "type": "object",
                "properties": {
                    "name":             {"type": "string"},
                    "table":            {"type": "string", "description": "Table this script applies to."},
                    "script_type":      {"type": "string", "enum": ["onLoad", "onChange", "onSubmit", "onCellEdit"], "description": "When the script fires."},
                    "script":           {"type": "string"},
                    "field_name":       {"type": "string", "description": "Required for onChange/onCellEdit."},
                    "ui_type":          {"type": "string", "enum": ["desktop", "mobile_or_service_portal", "all"], "default": "desktop"},
                    "applies_extended": {"type": "boolean", "default": False},
                    "description":      {"type": "string"},
                    "active":           {"type": "boolean", "default": True},
                },
                "required": ["name", "table", "script_type", "script"],
            },
        ),
        Tool(
            name="create_ui_action",
            description="Create a UI Action (sys_ui_action) — button, context-menu item, or link on a form/list.",
            inputSchema={
                "type": "object",
                "properties": {
                    "name":                {"type": "string"},
                    "table":               {"type": "string"},
                    "script":              {"type": "string", "description": "Server-side script executed on action."},
                    "action_name":         {"type": "string", "description": "Unique identifier, e.g. sn_approve."},
                    "condition":           {"type": "string"},
                    "type":                {"type": "string", "enum": ["button", "context_menu", "link"]},
                    "form_button":         {"type": "boolean", "default": True},
                    "list_banner_button":  {"type": "boolean", "default": False},
                    "form_context_menu":   {"type": "boolean", "default": False},
                    "client":              {"type": "boolean", "default": False, "description": "Also has a client-side onclick script."},
                    "onclick":             {"type": "string", "description": "Client-side JS (only used when client=true)."},
                    "order":               {"type": "integer"},
                    "active":              {"type": "boolean", "default": True},
                },
                "required": ["name", "table"],
            },
        ),
        Tool(
            name="create_scheduled_script",
            description="Create a Scheduled Script Execution (sysauto_script) — runs server-side JS on a schedule.",
            inputSchema={
                "type": "object",
                "properties": {
                    "name":          {"type": "string"},
                    "script":        {"type": "string"},
                    "run_type":      {"type": "string", "enum": ["daily", "weekly", "monthly", "periodically", "once", "on_demand"]},
                    "run_period":    {"type": "string", "description": "Interval for 'periodically', e.g. 00:05:00."},
                    "run_start":     {"type": "string", "description": "datetime string for 'once'; start time for others."},
                    "run_dayofweek": {"type": "string", "description": "0=Sunday … 6=Saturday (for weekly)."},
                    "active":        {"type": "boolean", "default": True},
                },
                "required": ["name", "script", "run_type"],
            },
        ),
        Tool(
            name="create_fix_script",
            description="Create a Fix Script (sys_script_fix) — one-shot server-side script for data migration or repair. Created inactive by default.",
            inputSchema={
                "type": "object",
                "properties": {
                    "name":        {"type": "string"},
                    "script":      {"type": "string"},
                    "description": {"type": "string"},
                    "active":      {"type": "boolean", "default": False, "description": "Set true only when ready to run."},
                },
                "required": ["name", "script"],
            },
        ),
    ]


# ── tool dispatch ─────────────────────────────────────────────────────────────

@app.call_tool()
async def call_tool(name: str, arguments: dict) -> list[TextContent]:
    try:
        return _dispatch(name, arguments)
    except SnowError as e:
        return _err(str(e))
    except Exception as e:
        return _err(f"unexpected error: {e}")


def _dispatch(name: str, a: dict) -> list[TextContent]:
    c = _client()

    # ── generic records ───────────────────────────────────────────────────────
    if name == "query_records":
        rows = c.table_get(a["table"], a.get("query"), a.get("fields"), a.get("limit", 10))
        return _ok(rows)

    if name == "get_record":
        fields = [f.strip() for f in a["fields"].split(",")] if a.get("fields") else None
        rec = c.table_get_record(a["table"], a["sys_id"], fields=fields)
        return _ok(rec)

    if name == "create_record":
        rec = c.table_post(a["table"], a["data"])
        return _ok(rec)

    if name == "update_record":
        rec = c.table_patch(a["table"], a["sys_id"], a["data"])
        return _ok(rec)

    if name == "whoami":
        return _ok(c.whoami())

    # ── incidents ─────────────────────────────────────────────────────────────
    if name == "create_incident":
        rec = c.table_post("incident", a)
        return _ok({"sys_id": rec["sys_id"], "number": rec.get("number"), "state": rec.get("state")})

    if name == "resolve_incident":
        identifier = a["identifier"]
        # Resolve number → sys_id if needed
        if not __import__("re").match(r"^[0-9a-f]{32}$", identifier):
            rows = c.table_get("incident", query=f"number={identifier}",
                               fields=["sys_id", "number"], limit=1)
            if not rows:
                return _err(f"Incident not found: {identifier}")
            sys_id = rows[0]["sys_id"]
        else:
            sys_id = identifier
        patch = {
            "state": "6",
            "close_notes": a["resolution_notes"],
        }
        if a.get("resolution_code"):
            patch["close_code"] = a["resolution_code"]
        rec = c.table_patch("incident", sys_id, patch)
        return _ok({"sys_id": sys_id, "state": rec.get("state"), "close_notes": rec.get("close_notes")})

    # ── scripts catalog ───────────────────────────────────────────────────────
    if name == "list_script_types":
        return _ok(sc.list_script_type_catalog())

    # ── scripts generic ───────────────────────────────────────────────────────
    if name == "query_scripts":
        rows = sc.query_scripts(c, a["script_type"], a.get("query"), a.get("fields"), a.get("limit", 10))
        return _ok(rows)

    if name == "get_script":
        rec = sc.get_script(c, a["script_type"], a["identifier"])
        return _ok(rec)

    if name == "update_script":
        rec = sc.update_script(c, a["script_type"], a["identifier"], a["data"])
        return _ok(rec)

    # ── script type-specific creates ──────────────────────────────────────────
    if name == "create_business_rule":
        required = ("name", "collection", "when", "script")
        kwargs = {k: v for k, v in a.items() if k not in required}
        rec = sc.create_business_rule(c, a["name"], a["collection"], a["when"], a["script"], **kwargs)
        return _ok({"sys_id": rec["sys_id"], "name": rec.get("name")})

    if name == "create_script_include":
        kwargs = {k: v for k, v in a.items() if k not in ("name", "script")}
        rec = sc.create_script_include(c, a["name"], a["script"], **kwargs)
        return _ok({"sys_id": rec["sys_id"], "name": rec.get("name"), "api_name": rec.get("api_name")})

    if name == "create_client_script":
        kwargs = {k: v for k, v in a.items() if k not in ("name", "table", "script_type", "script")}
        rec = sc.create_client_script(c, a["name"], a["table"], a["script_type"], a["script"], **kwargs)
        return _ok({"sys_id": rec["sys_id"], "name": rec.get("name")})

    if name == "create_ui_action":
        kwargs = {k: v for k, v in a.items() if k not in ("name", "table")}
        rec = sc.create_ui_action(c, a["name"], a["table"], **kwargs)
        return _ok({"sys_id": rec["sys_id"], "name": rec.get("name")})

    if name == "create_scheduled_script":
        kwargs = {k: v for k, v in a.items() if k not in ("name", "script", "run_type")}
        rec = sc.create_scheduled_script(c, a["name"], a["script"], a["run_type"], **kwargs)
        return _ok({"sys_id": rec["sys_id"], "name": rec.get("name")})

    if name == "create_fix_script":
        kwargs = {k: v for k, v in a.items() if k not in ("name", "script")}
        rec = sc.create_fix_script(c, a["name"], a["script"], **kwargs)
        return _ok({"sys_id": rec["sys_id"], "name": rec.get("name")})

    return _err(f"Unknown tool: {name}")


# ── entry point ───────────────────────────────────────────────────────────────

def main():
    async def _run():
        async with stdio_server() as (read_stream, write_stream):
            await app.run(
                read_stream,
                write_stream,
                app.create_initialization_options(),
            )

    asyncio.run(_run())
