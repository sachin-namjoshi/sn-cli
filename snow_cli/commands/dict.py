"""`snow dict <table>` — inspect sys_dictionary field metadata for a table.

ServiceNow stores each field's sys_dictionary record only on the table where it
is *defined*; a child table inherits its parents' fields at runtime without any
duplicate dictionary rows. So querying `name=<table>` alone shows only the
fields declared directly on the leaf and silently omits everything inherited
(e.g. `alm_hardware` misses `serial_number`, which lives on its parent
`alm_asset`). By default we therefore walk the `super_class` chain and union the
dictionary across the whole hierarchy, tagging each field with its source
table. `--own` restores the direct-only view.
"""

import json

from .. import permissions


def register(sub):
    p = sub.add_parser("dict", help="Show a table's fields (sys_dictionary).")
    p.add_argument("table", help="Table name to inspect.")
    p.add_argument("--own", action="store_true",
                   help="Only fields defined directly on this table (skip inherited).")
    p.add_argument("--json", action="store_true", help="Emit raw JSON.")
    # Schema inspection is a development activity -> dev instances only.
    p.set_defaults(func=run, bucket=permissions.DEV)


def run(ctx, args):
    chain = [args.table] if args.own else _table_chain(ctx.client, args.table)
    name_in = ",".join(chain)

    rows = ctx.client.table_get(
        "sys_dictionary",
        query=f"nameIN{name_in}^elementISNOTEMPTY^ORDERBYelement",
        fields=["name", "element", "column_label", "internal_type",
                "reference", "mandatory"],
        limit=2000,
    )

    # A child may override an inherited field with its own dictionary record;
    # keep the most-derived one (earliest in the chain).
    rank = {name: i for i, name in enumerate(chain)}
    by_element = {}
    for r in rows:
        el = r.get("element")
        src = _flat(r.get("name"))
        if el not in by_element or rank.get(src, 999) < rank.get(_flat(by_element[el].get("name")), 999):
            by_element[el] = r
    rows = sorted(by_element.values(), key=lambda r: r.get("element") or "")

    if args.json:
        print(json.dumps(rows, indent=2))
        return 0

    if not rows:
        print(f"(no fields found for '{args.table}' — check the table name)")
        return 0

    inherited = len(chain) > 1
    header = f"{args.table}: {len(rows)} field(s)"
    if inherited:
        header += f"  [hierarchy: {' <- '.join(chain)}]"
    print(header)
    for r in rows:
        el = r.get("element")
        typ = _flat(r.get("internal_type"))
        ref = _flat(r.get("reference"))
        suffix = f" -> {ref}" if ref else ""
        mand = " *" if r.get("mandatory") in ("true", True) else ""
        src = _flat(r.get("name"))
        origin = f"  ({src})" if inherited and src != args.table else ""
        print(f"  {el:<32} {typ}{suffix}{mand}{origin}")
    return 0


def _table_chain(client, table, max_depth=15):
    """Return [table, parent, grandparent, ...] by walking super_class.

    super_class is a reference to another sys_db_object (by sys_id); resolve
    each hop back to a table name. Guarded against cycles and runaway depth.
    """
    chain = []
    seen = set()
    current = table
    for _ in range(max_depth):
        if not current or current in seen:
            break
        seen.add(current)
        chain.append(current)
        rows = client.table_get(
            "sys_db_object", query=f"name={current}",
            fields=["name", "super_class"], limit=1,
        )
        if not rows:
            break
        parent_sysid = _flat(rows[0].get("super_class"))
        if not parent_sysid:
            break
        prow = client.table_get(
            "sys_db_object", query=f"sys_id={parent_sysid}",
            fields=["name"], limit=1,
        )
        if not prow:
            break
        current = _flat(prow[0].get("name"))
    return chain


def _flat(v):
    if isinstance(v, dict):
        return v.get("value", "")
    return "" if v is None else str(v)
