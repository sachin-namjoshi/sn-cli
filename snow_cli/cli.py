"""Argument parsing + dispatch. The only place that talks to argv/stdout/exit.

Command handlers receive (ctx, args) and either return an int exit code or raise
a SnowError. Everything below the CLI is import-only and side-effect-free, which
is what lets the tests drive the layers without a process boundary.
"""

import argparse
import sys

from . import __version__
from . import permissions
from .client import SnowClient
from .config import load_config
from .credentials import CredentialProvider
from .errors import SnowError
from .redact import redact
from .storage import make_backend
from .commands import auth as auth_cmd
from .commands import dict as dict_cmd
from .commands import query as query_cmd
from .commands import updateset as updateset_cmd
from .commands import widget as widget_cmd


class Context:
    """Lazily builds the config -> credentials -> client stack on first use."""

    def __init__(self, config_path=None):
        self._config_path = config_path
        self._config = None
        self._credentials = None
        self._client = None

    @property
    def config(self):
        if self._config is None:
            self._config = load_config(self._config_path)
        return self._config

    @property
    def credentials(self):
        if self._credentials is None:
            self._credentials = CredentialProvider(
                self.config, make_backend(self.config)
            )
        return self._credentials

    @property
    def client(self):
        if self._client is None:
            self._client = SnowClient(self.config, self.credentials)
        return self._client


def build_parser():
    parser = argparse.ArgumentParser(
        prog="snow",
        description="ServiceNow CLI — query tables, manage widgets, and more (stdlib-only).",
    )
    parser.add_argument("--version", action="version", version=f"snow {__version__}")
    parser.add_argument("--config", help="Path to config.json (default ~/.snow/config.json)")

    sub = parser.add_subparsers(dest="group", metavar="<group>")
    sub.required = True

    auth_cmd.register(sub)
    widget_cmd.register(sub)
    updateset_cmd.register(sub)
    query_cmd.register(sub)
    dict_cmd.register(sub)

    return parser


def _command_label(args):
    """Human label like 'widget push' for policy error messages."""
    group = getattr(args, "group", None)
    action = getattr(args, "action", None)
    return f"{group} {action}" if group and action else group


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)

    if not getattr(args, "func", None):
        parser.print_help()
        return 2

    ctx = Context(config_path=args.config)
    try:
        bucket = getattr(args, "bucket", permissions.DEV)
        permissions.check(ctx.config.instance, bucket, ctx.config, _command_label(args))
        return args.func(ctx, args) or 0
    except SnowError as e:
        print(f"error: {redact(str(e))}", file=sys.stderr)
        return e.exit_code
    except KeyboardInterrupt:
        print("\naborted", file=sys.stderr)
        return 130
