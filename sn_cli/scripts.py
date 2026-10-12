"""Script-type CRUD helpers — library layer, no CLI or MCP coupling.

Every function accepts a SnowClient and returns plain dicts/lists. The MCP
server and any future CLI commands consume these without knowing each other.

Supported script types and their ServiceNow tables:

  business_rule    sys_script            Server-side; runs on CRUD ops
  script_include   sys_script_include    Server-side reusable library
  client_script    sys_script_client     Client-side; runs in the browser
  ui_action        sys_ui_action         Button/link with optional server script
  ui_page          sys_ui_page           Jelly UI page (html + client + processing)
  scheduled_script sysauto_script        Runs on a schedule
  fix_script       sys_script_fix        One-shot migration/repair script
"""

import re

from .errors import SnowError

_SYSID_RE = re.compile(r"^[0-9a-f]{32}$")

# ── type catalog ──────────────────────────────────────────────────────────────

SCRIPT_TYPES = {
    "business_rule": {
        "table": "sys_script",
        "label": "Business Rule",
        "side": "server",
        "fields": [
            "name", "collection", "when", "order", "active",
            "script", "condition", "filter_condition",
            "action_insert", "action_update", "action_delete", "action_query",
            "description", "sys_scope",
        ],
        "required": ["name", "collection", "when", "script"],
        "notes": {
            "collection": "Target table name (e.g. incident, sys_user).",
            "when": "before | after | async | display",
            "action_insert": "true/false — run on INSERT",
            "action_update": "true/false — run on UPDATE",
            "action_delete": "true/false — run on DELETE",
            "action_query":  "true/false — run on QUERY (display rules only)",
        },
    },
    "script_include": {
        "table": "sys_script_include",
        "label": "Script Include",
        "side": "server",
        "fields": [
            "name", "api_name", "script", "client_callable",
            "active", "description", "access", "sys_scope",
        ],
        "required": ["name", "script"],
        "notes": {
            "api_name": "Scoped API name, e.g. x_myapp.MyHelper. Auto-derived if omitted.",
            "client_callable": "true — expose via GlideAjax; false (default) — server-only.",
            "access": "public | package_private",
        },
    },
    "client_script": {
        "table": "sys_script_client",
        "label": "Client Script",
        "side": "client",
        "fields": [
            "name", "table", "type", "script", "active",
            "ui_type", "field_name", "applies_extended", "global",
            "description", "messages", "sys_scope",
        ],
        "required": ["name", "table", "type", "script"],
        "notes": {
            "type": "onLoad | onChange | onSubmit | onCellEdit",
            "ui_type": "desktop | mobile_or_service_portal | all (default: desktop)",
            "field_name": "Required when type is onChange or onCellEdit.",
            "applies_extended": "true — also runs on tables that extend this table.",
        },
    },
    "ui_action": {
        "table": "sys_ui_action",
        "label": "UI Action",
        "side": "client+server",
        "fields": [
            "name", "table", "action_name", "script", "condition",
            "active", "type", "order",
            "form_button", "list_banner_button", "form_context_menu",
            "form_link", "list_link",
            "client", "onclick",
            "comments", "sys_scope",
        ],
        "required": ["name", "table"],
        "notes": {
            "action_name": "Unique identifier used in conditions, e.g. sn_approve.",
            "type": "button | context_menu | link",
            "form_button": "true — show as a form button.",
            "client": "true — also has a client-side onclick script.",
            "onclick": "Client-side JavaScript (only used when client=true).",
        },
    },
    "ui_page": {
        "table": "sys_ui_page",
        "label": "UI Page",
        "side": "client+server",
        "fields": [
            "name", "description", "html", "client", "processing",
            "category", "direct", "sys_scope",
        ],
        "required": ["name", "html"],
        "notes": {
            "html": "Jelly/HTML body of the page.",
            "client": "Client-side JavaScript for the page.",
            "processing": "Server-side processing script (runs before page renders).",
            "category": "Grouping label, e.g. general.",
        },
    },
    "scheduled_script": {
        "table": "sysauto_script",
        "label": "Scheduled Script Execution",
        "side": "server",
        "fields": [
            "name", "script", "active",
            "run_type", "run_period", "run_start",
            "run_dayofweek", "run_time",
        ],
        "required": ["name", "script", "run_type"],
        "notes": {
            "run_type": "daily | weekly | monthly | periodically | once | on_demand",
            "run_period": "Interval for 'periodically', e.g. 00:05:00 (5 min).",
            "run_start": "datetime string for 'once'; start time for others.",
            "run_dayofweek": "0=Sunday … 6=Saturday; used with 'weekly'.",
        },
    },
    "fix_script": {
        "table": "sys_script_fix",
        "label": "Fix Script",
        "side": "server",
        "fields": ["name", "script", "active", "description", "sys_scope"],
        "required": ["name", "script"],
        "notes": {
            "active": "Set true only when ready to run; set false after execution.",
        },
    },
}


def _validate_type(script_type: str) -> dict:
    if script_type not in SCRIPT_TYPES:
        valid = ", ".join(sorted(SCRIPT_TYPES))
        raise SnowError(f"Unknown script type '{script_type}'. Valid: {valid}.")
    return SCRIPT_TYPES[script_type]


def _resolve(client, table: str, identifier: str, fields: list[str]) -> dict:
    """Return one record by sys_id (exact) or name (exact then fuzzy).

    Mirrors the widget command's _resolve strategy so the UX is consistent.
    """
    if _SYSID_RE.match(identifier):
        rec = client.table_get_record(table, identifier, fields=fields)
        if rec:
            return rec
        raise SnowError(f"No record found in '{table}' for sys_id {identifier}.")

    exact = client.table_get(table, query=f"name={identifier}", fields=fields, limit=2)
    if len(exact) == 1:
        return exact[0]
    if len(exact) > 1:
        ids = "  ".join(r["sys_id"] for r in exact)
        raise SnowError(f"Multiple records named '{identifier}' in {table}; use a sys_id: {ids}")

    fuzzy = client.table_get(
        table, query=f"nameLIKE{identifier}", fields=["sys_id", "name"], limit=10
    )
    if not fuzzy:
        raise SnowError(f"No record in '{table}' matches '{identifier}'.")
    if len(fuzzy) > 1:
        listing = "\n".join(f"  {r['name']}  [{r['sys_id']}]" for r in fuzzy)
        raise SnowError(
            f"'{identifier}' is ambiguous in {table}:\n{listing}\n"
            "Re-run with a sys_id or the exact name."
        )
    return client.table_get_record(table, fuzzy[0]["sys_id"], fields=fields)


# ── generic CRUD ───────────────────────────────────────────────────────────────

def list_script_type_catalog() -> dict:
    """Return the full type catalog (no network call)."""
    return {
        k: {
            "label": v["label"],
            "table": v["table"],
            "side": v["side"],
            "required_fields": v["required"],
            "all_fields": v["fields"],
            "field_notes": v.get("notes", {}),
        }
        for k, v in SCRIPT_TYPES.items()
    }


def query_scripts(client, script_type: str, query: str | None = None,
                  fields: str | None = None, limit: int = 10) -> list:
    """List scripts of a given type. Returns the raw Table API result rows."""
    meta = _validate_type(script_type)
    flds = fields or ",".join(["sys_id", "name", "active"] + (
        ["collection"] if script_type == "business_rule" else
        ["table"] if script_type in ("client_script", "ui_action") else []
    ))
    return client.table_get(meta["table"], query=query, fields=flds, limit=limit)


def get_script(client, script_type: str, identifier: str) -> dict:
    """Fetch a single script record by sys_id or name."""
    meta = _validate_type(script_type)
    return _resolve(client, meta["table"], identifier, meta["fields"])


def update_script(client, script_type: str, identifier: str, data: dict) -> dict:
    """PATCH fields on a script record; returns the updated record."""
    meta = _validate_type(script_type)
    rec = _resolve(client, meta["table"], identifier, ["sys_id", "name"])
    return client.table_patch(meta["table"], rec["sys_id"], data)


# ── type-specific creates ─────────────────────────────────────────────────────

def create_business_rule(client, name: str, collection: str, when: str,
                          script: str, **kwargs) -> dict:
    """Create a Business Rule (sys_script)."""
    _check_enum("when", when, ("before", "after", "async", "display"))
    data = {
        "name": name,
        "collection": collection,
        "when": when,
        "script": script,
        "active": True,
        "action_insert": False,
        "action_update": False,
        "action_delete": False,
        "action_query": False,
    }
    data.update(kwargs)
    return client.table_post("sys_script", data)


def create_script_include(client, name: str, script: str, **kwargs) -> dict:
    """Create a Script Include (sys_script_include)."""
    data = {"name": name, "script": script, "active": True, "client_callable": False}
    data.update(kwargs)
    return client.table_post("sys_script_include", data)


def create_client_script(client, name: str, table: str, script_type: str,
                          script: str, **kwargs) -> dict:
    """Create a Client Script (sys_script_client). `script_type` = onLoad/onChange/onSubmit/onCellEdit."""
    _check_enum("type", script_type, ("onLoad", "onChange", "onSubmit", "onCellEdit"))
    data = {
        "name": name,
        "table": table,
        "type": script_type,
        "script": script,
        "active": True,
        "ui_type": "desktop",
        "global": True,
        "applies_extended": False,
    }
    data.update(kwargs)
    return client.table_post("sys_script_client", data)


def create_ui_action(client, name: str, table: str, **kwargs) -> dict:
    """Create a UI Action (sys_ui_action). script and condition are optional."""
    data = {
        "name": name,
        "table": table,
        "active": True,
        "form_button": True,
    }
    data.update(kwargs)
    return client.table_post("sys_ui_action", data)


def create_scheduled_script(client, name: str, script: str, run_type: str,
                              **kwargs) -> dict:
    """Create a Scheduled Script Execution (sysauto_script)."""
    _check_enum("run_type", run_type,
                ("daily", "weekly", "monthly", "periodically", "once", "on_demand"))
    data = {"name": name, "script": script, "run_type": run_type, "active": True}
    data.update(kwargs)
    return client.table_post("sysauto_script", data)


def create_fix_script(client, name: str, script: str, **kwargs) -> dict:
    """Create a Fix Script (sys_script_fix). Set active=true only when ready to run."""
    data = {"name": name, "script": script, "active": False}
    data.update(kwargs)
    return client.table_post("sys_script_fix", data)


# ── helpers ────────────────────────────────────────────────────────────────────

def _check_enum(field: str, value: str, allowed: tuple) -> None:
    if value not in allowed:
        raise SnowError(
            f"Invalid {field} '{value}'. Allowed: {' | '.join(allowed)}."
        )
