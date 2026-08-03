"""`snow update <table> <sys_id> [--query <q>] --set field=value ...`

Updates one record in any ServiceNow table. The target record is identified
either by a direct sys_id argument or by a --query filter. If the query
matches more than one record the command aborts — use a sys_id or a tighter
query to avoid ambiguity.

    snow update incident abc123def456... --set state=6 close_code="Solution provided"
    snow update incident -q "number=INC0009009" --set assigned_to=jsmith priority=2
    snow update sys_user -q "user_name=jsmith" --set title="Senior Engineer" --json
"""

import json
import sys

from .. import permissions
from ..errors import SnowError


def register(sub):
    p = sub.add_parser("update", help="Update fields on a table record.")
    p.add_argument("table", help="Table name, e.g. incident, sys_user.")
    p.add_argument(
        "sys_id",
        nargs="?",
        default=None,
        help="sys_id of the record to update.",
    )
    p.add_argument(
        "--query", "-q",
        default=None,
        help="Encoded sysparm_query to locate the record (alternative to sys_id).",
    )
    p.add_argument(
        "--set", "-s",
        dest="fields",
        nargs="+",
        required=True,
        metavar="field=value",
        help="One or more field=value pairs to set (e.g. state=6 priority=2).",
    )
    p.add_argument("--json", action="store_true", help="Emit the updated record as JSON.")
    p.add_argument("--yes", action="store_true", help="Skip the confirmation prompt.")
    # General-purpose write — allowed on any instance.
    p.set_defaults(func=run, bucket=permissions.GENERAL)


def _parse_fields(raw_pairs):
    """Parse ['field=value', 'field2=value2'] into a dict.

    Values may contain '=' (e.g. encoded queries), so split on the first '='
    only.
    """
    data = {}
    for pair in raw_pairs:
        if "=" not in pair:
            raise SnowError(
                f"Invalid --set value '{pair}': expected field=value format."
            )
        field, value = pair.split("=", 1)
        field = field.strip()
        if not field:
            raise SnowError(f"Empty field name in --set value '{pair}'.")
        data[field] = value
    return data


def _confirm(args, prompt):
    if getattr(args, "yes", False):
        return True
    if sys.stdin.isatty():
        return input(f"{prompt} [y/N] ").strip().lower() in ("y", "yes")
    raise SnowError("Refusing to update without confirmation; pass --yes.")


def run(ctx, args):
    if not args.sys_id and not args.query:
        raise SnowError("Provide a sys_id argument or --query to identify the record.")
    if args.sys_id and args.query:
        raise SnowError("Provide either a sys_id or --query, not both.")

    # Resolve sys_id from query if needed.
    sys_id = args.sys_id
    if args.query:
        rows = ctx.client.table_get(
            args.table, query=args.query, fields=["sys_id", "number", "name"], limit=2
        )
        if not rows:
            raise SnowError(f"No records found in '{args.table}' for query: {args.query}")
        if len(rows) > 1:
            raise SnowError(
                f"Query matched {len(rows)}+ records — use a sys_id or a tighter query."
            )
        sys_id = rows[0]["sys_id"]
        label = rows[0].get("number") or rows[0].get("name") or sys_id
        print(f"Resolved to: {label} [{sys_id}]")

    data = _parse_fields(args.fields)

    print(f"Updating {args.table} [{sys_id}]:")
    for field, value in data.items():
        print(f"  {field} = {value!r}")

    if not _confirm(args, "Apply update?"):
        print("Cancelled.")
        return 0

    result = ctx.client.table_patch(args.table, sys_id, data)

    if args.json:
        print(json.dumps(result, indent=2))
        return 0

    print("Updated.")
    for field in data:
        updated_val = result.get(field, "")
        if isinstance(updated_val, dict):
            updated_val = updated_val.get("display_value") or updated_val.get("value", "")
        print(f"  {field} = {updated_val!r}")
    return 0
