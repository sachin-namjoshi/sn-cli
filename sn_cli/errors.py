"""Typed errors + their process exit codes.

cli.py catches SnowError and maps `.exit_code` to the process return value, so a
command handler signals failure by raising, never by calling sys.exit itself.
"""


class SnowError(Exception):
    """Base for every expected (non-bug) failure. Carries an exit code."""

    exit_code = 1


class ConfigError(SnowError):
    """~/.snow/config.json missing, unreadable, or missing a required key."""

    exit_code = 3


class AuthError(SnowError):
    """No usable token and it cannot be obtained/refreshed silently."""

    exit_code = 4


class ApiError(SnowError):
    """ServiceNow returned a non-2xx response."""

    exit_code = 5

    def __init__(self, status, message, body=None):
        self.status = status
        self.body = body
        super().__init__(message)


class ConfirmationRequired(SnowError):
    """A write was refused because the user did not confirm it."""

    exit_code = 6


class PermissionDeniedError(SnowError):
    """A development command was invoked against a non-development instance.

    The instance-tier policy (permissions.py) confines development activities
    (dict, widget, updateset) to the development tier; general-purpose querying
    is allowed everywhere. Raised before the command runs, so nothing touches
    the instance.
    """

    exit_code = 7
