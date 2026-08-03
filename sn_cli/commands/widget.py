"""`snow widget create | pull | push | render`.

A widget maps to a folder of Core-5 files plus .snow-meta.json (the source of
truth for identity — sys_id, not the folder name). push is guarded: it reconciles
the bound update set, diffs, requires confirmation, PATCHes, then verifies the
change actually captured into the expected set.
"""

import hashlib
import html
import json
import os
import re
import sys
import time

from .. import permissions
from ..errors import ApiError, ConfirmationRequired, SnowError
from . import updateset as us

META_FILE = ".snow-meta.json"

# Local file  ->  sp_widget field. These five are the widget's editable surface.
FILE_FIELDS = {
    "template.html": "template",
    "css.scss": "css",
    "client_script.js": "client_script",
    "server_script.js": "script",
    "option_schema.json": "option_schema",
}
CONTENT_FIELDS = list(FILE_FIELDS.values())
_SYSID_RE = re.compile(r"^[0-9a-f]{32}$")


# ── meta + files ──────────────────────────────────────────────────────────────
def load_meta(folder="."):
    path = os.path.join(folder, META_FILE)
    if not os.path.exists(path):
        raise SnowError(f"No {META_FILE} here — run this inside a pulled widget folder.")
    with open(path) as f:
        return json.load(f)


def save_meta(meta, folder="."):
    with open(os.path.join(folder, META_FILE), "w") as f:
        json.dump(meta, f, indent=2)


def _slug(name):
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-") or "widget"


def _hash(text):
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()


def _write_fields(folder, record):
    """Write the Core-5 files from a widget record; return field_hashes."""
    os.makedirs(folder, exist_ok=True)
    hashes = {}
    for fname, field in FILE_FIELDS.items():
        val = record.get(field) or ""
        with open(os.path.join(folder, fname), "w") as f:
            f.write(val)
        hashes[field] = _hash(val)
    return hashes


def _read_fields(folder):
    out = {}
    for fname, field in FILE_FIELDS.items():
        path = os.path.join(folder, fname)
        out[field] = open(path).read() if os.path.exists(path) else ""
    return out


def _confirm(args, prompt):
    if getattr(args, "yes", False) or getattr(args, "force", False):
        return True
    if sys.stdin.isatty():
        return input(f"{prompt} [y/N] ").strip().lower() in ("y", "yes")
    raise ConfirmationRequired("Refusing to write without confirmation; pass --yes.")


# ── resolve ─────────────────────────────────────────────────────────────────────
def _resolve(client, ref):
    """Resolve a widget by sys_id, exact internal id, or name search."""
    fields = ["sys_id", "id", "name"] + CONTENT_FIELDS
    if _SYSID_RE.match(ref):
        rec = client.table_get_record("sp_widget", ref, fields=fields)
        if rec:
            return rec
    exact = client.table_get("sp_widget", query=f"id={ref}", fields=fields, limit=2)
    if len(exact) == 1:
        return exact[0]
    if len(exact) > 1:
        raise SnowError(f"Multiple widgets with id={ref}; pass a sys_id.")
    matches = client.table_get(
        "sp_widget", query=f"nameLIKE{ref}^ORid LIKE{ref}",
        fields=["sys_id", "id", "name"], limit=10,
    )
    if not matches:
        raise SnowError(f"No widget matches '{ref}'.")
    if len(matches) > 1:
        listing = "\n".join(f"  {m['name']}  (id={m['id']}) [{m['sys_id']}]" for m in matches)
        raise SnowError(f"'{ref}' is ambiguous:\n{listing}\nRe-run with a sys_id or exact id.")
    return client.table_get_record("sp_widget", matches[0]["sys_id"], fields=fields)


# ── commands ────────────────────────────────────────────────────────────────────
def register(sub):
    p = sub.add_parser("widget", help="Create, pull, push, and render widgets.")
    s = p.add_subparsers(dest="action", metavar="<action>")
    s.required = True

    # Every widget action is a development activity -> dev instances only.
    pc = s.add_parser("create", help="Scaffold a net-new widget in the global scope.")
    pc.add_argument("--id", required=True, dest="internal_id", help="Internal id (snake_case, unique).")
    pc.add_argument("--name", required=True, help="Friendly name.")
    pc.set_defaults(func=cmd_create, bucket=permissions.DEV)

    pp = s.add_parser("pull", help="Pull a widget's fields into a local folder.")
    pp.add_argument("ref", help="sys_id, exact id, or name.")
    pp.set_defaults(func=cmd_pull, bucket=permissions.DEV)

    ph = s.add_parser("push", help="Push local edits back (reconcile -> diff -> confirm -> verify).")
    ph.add_argument("--yes", action="store_true", help="Skip the confirm prompt (required when non-interactive).")
    ph.add_argument("--force", action="store_true", help="Push even if remote drifted since pull.")
    ph.set_defaults(func=cmd_push, bucket=permissions.DEV)

    pr = s.add_parser("render", help="Closed-loop: render server-side and show output + errors.")
    pr.add_argument("ref", nargs="?", help="sys_id or id (defaults to the current folder's widget).")
    pr.add_argument("--json", action="store_true", help="Emit the full render payload.")
    pr.set_defaults(func=cmd_render, bucket=permissions.DEV)


def cmd_create(ctx, args):
    dup = ctx.client.table_get("sp_widget", query=f"id={args.internal_id}", fields=["sys_id"], limit=1)
    if dup:
        raise SnowError(f"A widget with id '{args.internal_id}' already exists.")

    folder = _slug(args.name)
    if os.path.exists(os.path.join(folder, META_FILE)):
        raise SnowError(f"Folder '{folder}' already holds a widget.")

    # Capture the create into the current update set if one is set; warn if not.
    current = us.get_current_update_set(ctx.client)
    if not current:
        print("warning: no current update set — this create may land in Default and not capture.")

    scaffold = {
        "id": args.internal_id,
        "name": args.name,
        "template": f"<div>\n  <h2>{args.name}</h2>\n</div>\n",
        "css": "",
        "client_script": "api.controller = function() {\n  /* var c = this; */\n};\n",
        "script": "(function() {\n  /* data.x = 1; */\n})();\n",
        "option_schema": "[]",
    }
    created = ctx.client.table_post("sp_widget", scaffold)
    record = {**scaffold, "sys_id": created["sys_id"], "id": created["id"], "name": created["name"]}
    hashes = _write_fields(folder, record)
    save_meta({
        "instance": ctx.config.instance,
        "widget": {"sys_id": created["sys_id"], "id": created["id"], "name": created["name"]},
        "update_set": {"sys_id": current["sys_id"], "name": current["name"], "state": current["state"]} if current else None,
        "feature": None,
        "pulled_at": int(time.time()),
        "field_hashes": hashes,
    }, folder)
    print(f"Created widget '{created['name']}' (id={created['id']}) [{created['sys_id']}]\n  -> ./{folder}/")
    return 0


def cmd_pull(ctx, args):
    rec = _resolve(ctx.client, args.ref)
    folder = _slug(rec["name"])
    hashes = _write_fields(folder, rec)
    save_meta({
        "instance": ctx.config.instance,
        "widget": {"sys_id": rec["sys_id"], "id": rec["id"], "name": rec["name"]},
        "update_set": None,
        "feature": None,
        "pulled_at": int(time.time()),
        "field_hashes": hashes,
    }, folder)
    print(f"Pulled '{rec['name']}' (id={rec['id']}) [{rec['sys_id']}]\n  -> ./{folder}/ ({len(FILE_FIELDS)} files)")
    return 0


def cmd_push(ctx, args):
    meta = load_meta()
    sys_id = meta["widget"]["sys_id"]
    local = _read_fields(".")

    remote = ctx.client.table_get_record("sp_widget", sys_id, fields=["sys_id", "name"] + CONTENT_FIELDS)
    if not remote:
        raise SnowError(f"Widget {sys_id} no longer exists on {meta['instance']}.")

    stored = meta.get("field_hashes", {})
    changed, drifted = [], []
    for field in CONTENT_FIELDS:
        if _hash(local[field]) != _hash(remote.get(field) or ""):
            changed.append(field)
        # Remote drift = remote differs from what we pulled (someone else edited).
        if stored.get(field) and _hash(remote.get(field) or "") != stored[field]:
            drifted.append(field)

    if not changed:
        print("No local changes — nothing to push.")
        return 0

    if drifted and not args.force:
        raise SnowError(
            "Remote changed since pull for: " + ", ".join(drifted)
            + ".\nRe-pull or push with --force to overwrite."
        )

    print("Fields to push: " + ", ".join(changed))
    if not _confirm(args, f"Push {len(changed)} field(s) to '{remote['name']}'?"):
        raise ConfirmationRequired("Push cancelled.")

    # Reconcile the bound update set immediately before writing so it captures.
    bound = meta.get("update_set")
    if bound and bound.get("sys_id"):
        us.reconcile(ctx.client, bound["sys_id"])
    else:
        print("warning: no bound update set (run `snow updateset new`); capture not guaranteed.")

    ctx.client.table_patch("sp_widget", sys_id, {f: local[f] for f in changed})

    # Verify the change captured into the expected update set.
    captured = _verify_capture(ctx.client, sys_id, bound)
    for field in changed:
        meta.setdefault("field_hashes", {})[field] = _hash(local[field])
    meta["pulled_at"] = int(time.time())
    save_meta(meta)

    print(f"Pushed {len(changed)} field(s).")
    if bound:
        print(f"Capture verified in: {captured}" if captured else
              "warning: could not confirm capture in the bound update set.")
    return 0


def _verify_capture(client, widget_sys_id, bound):
    rows = client.table_get(
        "sys_update_xml",
        query=f"name=sp_widget_{widget_sys_id}^ORDERBYDESCsys_created_on",
        fields=["update_set", "update_set.name"],
        limit=1,
    )
    if not rows:
        return None
    landed = rows[0].get("update_set")
    landed_id = landed.get("value") if isinstance(landed, dict) else landed
    name = rows[0].get("update_set.name")
    if bound and bound.get("sys_id") and landed_id != bound["sys_id"]:
        return f"{name} (WARNING: not the bound set!)"
    return name


def cmd_render(ctx, args):
    ref = args.ref
    if not ref:
        ref = load_meta()["widget"]["id"]  # default to this folder's widget

    payload = _render_with_retry(ctx.client, ref)
    result = (payload or {}).get("result", {}) or {}

    server_error = _extract_error(result)
    data = result.get("data")
    exec_ms = result.get("_script_execution_time")

    if args.json:
        print(json.dumps({
            "ok": server_error is None,
            "server_error": server_error,
            "data": data,
            "exec_ms": exec_ms,
            "template": result.get("template"),
            "css": result.get("css"),
            "client_script": result.get("client_script"),
        }, indent=2))
        return 0 if server_error is None else 5

    if server_error:
        print("RENDER FAILED (server-side):")
        print(f"  message: {server_error.get('message')}")
        if server_error.get("line"):
            print(f"  line:    {server_error['line']}")
        if server_error.get("field"):
            print(f"  field:   {server_error['field']}")
        if data:
            print(f"  partial data: {json.dumps(data)[:300]}")
        return 5

    print("RENDER OK" + (f" ({exec_ms} ms)" if exec_ms is not None else ""))
    print(f"  data: {json.dumps(data)[:600] if data is not None else '(none)'}")
    return 0


def _render_with_retry(client, ref):
    """First render call can hang (~2 min) then succeed instantly on retry."""
    try:
        return client.render_widget(ref, timeout=45)
    except (ApiError, OSError) as first:
        try:
            return client.render_widget(ref, timeout=45)
        except (ApiError, OSError):
            raise first


def _extract_error(result):
    """Pull a thrown server error out of the render payload, if any."""
    notes = result.get("$$uiNotification") or result.get("$uiNotification") or []
    if not notes:
        return None
    texts = []
    for n in notes:
        texts.append(n.get("message", "") if isinstance(n, dict) else str(n))
    # ServiceNow HTML-escapes notification text (&#34; &#39;); unescape for humans.
    joined = html.unescape(" ".join(texts))
    line = None
    m = re.search(r"[Ll]ine number\s+(\d+)", joined)
    if m:
        line = int(m.group(1))
    field = None
    m = re.search(r"sp_widget\.[0-9a-f]+\.(\w+)", joined)
    if m:
        field = m.group(1)
    return {"message": joined.strip() or "server script error", "line": line, "field": field}
