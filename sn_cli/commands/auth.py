"""`snow auth login | refresh | status`."""

import time

from .. import permissions


def register(sub):
    p = sub.add_parser("auth", help="Authenticate and inspect token state.")
    s = p.add_subparsers(dest="action", metavar="<action>")
    s.required = True

    # login/refresh touch only local token storage -> allowed on any instance.
    s.add_parser("login", help="Browser -> Okta/MFA -> loopback -> tokens.").set_defaults(
        func=login, bucket=permissions.LOCAL)
    s.add_parser("refresh", help="Silent refresh via refresh token.").set_defaults(
        func=refresh, bucket=permissions.LOCAL)
    s.add_parser("status", help="Show identity + token expiry.").set_defaults(
        func=status, bucket=permissions.GENERAL)


def login(ctx, args):
    ctx.credentials.login()
    who = ctx.client.whoami()
    print(f"Logged in as {who['user_name']} ({who.get('first_name','')} {who.get('last_name','')}).".strip())
    return 0


def refresh(ctx, args):
    ctx.credentials.refresh()
    st = ctx.credentials.status()
    print(f"Refreshed. Access token valid for ~{st['seconds_left'] // 60} min.")
    return 0


def status(ctx, args):
    st = ctx.credentials.status()
    if not st.get("logged_in"):
        print("Not logged in — run `snow auth login`.")
        return 4
    if not st["valid"]:
        print(f"Token EXPIRED {abs(st['seconds_left']) // 60} min ago — run `snow auth refresh`.")
    else:
        print(f"Token valid for ~{st['seconds_left'] // 60} min "
              f"(expires_at {st['expires_at']}, {time.strftime('%Y-%m-%d %H:%M', time.localtime(st['expires_at']))}).")
    # whoami confirms the token actually works against the instance.
    try:
        who = ctx.client.whoami()
        print(f"Identity: {who['user_name']} ({who.get('first_name','')} {who.get('last_name','')})".strip())
    except Exception as e:  # noqa: BLE001 - status should never hard-fail
        print(f"(could not confirm identity: {e})")
    return 0
