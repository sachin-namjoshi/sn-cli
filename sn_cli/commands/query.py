"""`snow query <table>` — read-only escape hatch over the Table API."""

import json

from .. import permissions


def register(sub):
    p = sub.add_parser("query", help="Read-only table query.")
    p.add_argument("table", help="Table name, e.g. sys_user, sp_widget.")
    p.add_argument("--query", "-q", default=None, help="Encoded sysparm_query.")
    p.add_argument("--fields", "-f", default=None, help="Comma-separated field list.")
    p.add_argument("--limit", "-l", type=int, default=10, help="Max rows (default 10).")
    p.add_argument("--json", action="store_true", help="Emit raw JSON.")
    # General-purpose read -> allowed on every instance, including prod.
    p.set_defaults(func=run, bucket=permissions.GENERAL)


def run(ctx, args):
    rows = ctx.client.table_get(
        args.table, query=args.query, fields=args.fields, limit=args.limit
    )
    if args.json:
        print(json.dumps(rows, indent=2))
        return 0

    if not rows:
        print("(no rows)")
        return 0

    # Column set = the fields asked for, else the union of keys in the first row.
    cols = [c.strip() for c in args.fields.split(",")] if args.fields else list(rows[0].keys())
    print(f"{len(rows)} row(s):")
    for r in rows:
        line = "  " + "  ".join(f"{c}={_flat(r.get(c))}" for c in cols)
        print(line)
    return 0


def _flat(v):
    # Reference fields come back as {"value":..,"link":..}; show the value.
    if isinstance(v, dict):
        return v.get("value", "")
    return "" if v is None else str(v)
