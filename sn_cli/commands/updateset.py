"""`snow updateset list | new | use` + the naming/preference helpers.

The "current update set" is a single per-user sys_user_preference
(name=sys_update_set). The Table API has no per-request capture target, so a
write lands in whatever this preference points at. widget.py calls reconcile()
immediately before every push so captures land in the folder's bound set.
"""

from .. import permissions
from ..errors import ApiError, ConfigError

PREF_NAME = "sys_update_set"
_PREF_QUERY = f"name={PREF_NAME}^userDYNAMICjavascript:gs.getUserID()"


# ── naming ────────────────────────────────────────────────────────────────────
def get_initials(ctx):
    """Config override, else first+last initial of the authenticated user."""
    override = ctx.config.initials
    if override:
        return override.strip()
    who = ctx.client.whoami()
    fi = (who.get("first_name") or "").strip()
    li = (who.get("last_name") or "").strip()
    initials = (fi[:1] + li[:1]).upper()
    if not initials:
        raise ConfigError(
            "Could not derive initials from your user record; set \"initials\" in config.json."
        )
    return initials


def build_name(initials, widget_name, feature):
    """Canonical update-set name: `{Initials}: {Widget Name} - {Feature}`."""
    return f"{initials}: {widget_name} - {feature}"


# ── preference (current update set) ─────────────────────────────────────────────
def get_current_pref(client):
    """Return the raw preference row (sys_id + value), or None."""
    rows = client.table_get(
        "sys_user_preference",
        query=_PREF_QUERY,
        fields=["sys_id", "value", "user", "user.user_name"],
        limit=1,
    )
    return rows[0] if rows else None


def get_current_update_set(client):
    """Return the current update set record, or None if unset."""
    pref = get_current_pref(client)
    if not pref or not pref.get("value"):
        return None
    return client.table_get_record(
        "sys_update_set", pref["value"], fields=["sys_id", "name", "state"]
    )


def set_current_update_set(client, us_sys_id):
    """Point the current-update-set preference at us_sys_id (create pref if needed)."""
    pref = get_current_pref(client)
    if pref:
        client.table_patch("sys_user_preference", pref["sys_id"], {"value": us_sys_id})
        return
    # No preference row yet — resolve the user sys_id and create one.
    who = client.whoami()
    client.table_post(
        "sys_user_preference",
        {"name": PREF_NAME, "value": us_sys_id, "user": who["sys_id"]},
    )


def ensure_update_set(client, name, description="Created by snow CLI"):
    """Return an in-progress update set with this name, creating it if absent."""
    existing = client.table_get(
        "sys_update_set",
        query=f"name={name}^state=in progress",
        fields=["sys_id", "name", "state"],
        limit=1,
    )
    if existing:
        return existing[0]
    created = client.table_post(
        "sys_update_set",
        {"name": name, "state": "in progress", "description": description},
    )
    return {"sys_id": created["sys_id"], "name": created["name"], "state": created["state"]}


def reconcile(client, us_sys_id):
    """Make us_sys_id the current update set; verify it stuck. Raise on mismatch."""
    set_current_update_set(client, us_sys_id)
    pref = get_current_pref(client)
    if not pref or pref.get("value") != us_sys_id:
        raise ApiError(0, "Failed to set current update set (reconcile mismatch).")


# ── commands ────────────────────────────────────────────────────────────────────
def register(sub):
    p = sub.add_parser("updateset", help="Manage the current update set.")
    s = p.add_subparsers(dest="action", metavar="<action>")
    s.required = True

    # Update-set management is a development activity -> dev instances only.
    s.add_parser("list", help="In-progress update sets for the current user.").set_defaults(
        func=cmd_list, bucket=permissions.DEV)

    pn = s.add_parser("new", help="Create + bind a feature update set (run in a widget folder).")
    pn.add_argument("feature", help="Feature label, e.g. \"Add logging\".")
    pn.set_defaults(func=cmd_new, bucket=permissions.DEV)

    pu = s.add_parser("use", help="Set the current update set by sys_id.")
    pu.add_argument("sys_id")
    pu.set_defaults(func=cmd_use, bucket=permissions.DEV)


def cmd_list(ctx, args):
    rows = ctx.client.table_get(
        "sys_update_set",
        query="state=in progress^ORDERBYDESCsys_updated_on",
        fields=["sys_id", "name", "state", "sys_updated_on"],
        limit=25,
    )
    current = get_current_update_set(ctx.client)
    current_id = current["sys_id"] if current else None
    if not rows:
        print("(no in-progress update sets)")
        return 0
    for r in rows:
        marker = "* " if r["sys_id"] == current_id else "  "
        print(f"{marker}{r['name']}  [{r['sys_id']}]")
    print("\n* = current update set")
    return 0


def cmd_new(ctx, args):
    from .widget import load_meta, save_meta  # local import avoids cycle

    meta = load_meta()  # raises if not in a widget folder
    initials = get_initials(ctx)
    name = build_name(initials, meta["widget"]["name"], args.feature)
    us = ensure_update_set(ctx.client, name)
    reconcile(ctx.client, us["sys_id"])
    meta["update_set"] = {"sys_id": us["sys_id"], "name": us["name"], "state": us["state"]}
    meta["feature"] = args.feature
    save_meta(meta)
    print(f"Bound feature update set:\n  {us['name']}\n  [{us['sys_id']}] (now current)")
    return 0


def cmd_use(ctx, args):
    reconcile(ctx.client, args.sys_id)
    us = ctx.client.table_get_record("sys_update_set", args.sys_id, fields=["name", "state"])
    print(f"Current update set -> {us['name'] if us else args.sys_id}")
    return 0
