"""Instance-tier permission policy.

The security boundary between general-purpose querying and development
activities (widget, updateset, dict) is enforced here, in code, on purpose.

Two tiers:
  development  -> instances listed in config.json `dev_instances`
  restricted   -> everything else (unknown instances are restricted by default)

Operation buckets a command declares (via `set_defaults(bucket=...)`):
  LOCAL    - touches only local token storage (auth login / refresh). Allowed
             on every instance so a user can authenticate anywhere.
  GENERAL  - safe general-purpose use anywhere (auth status, query).
  DEV      - a development activity; allowed only on the development tier.

Fail-safe by construction:
  * an unknown instance resolves to `restricted`, and
  * a command with no declared bucket is treated as DEV (most restricted),
so a new command can never accidentally become allowed on prod without an
explicit LOCAL/GENERAL annotation.

Configure dev instances in ~/.snow/config.json:

    {
      "instance": "mycompany-dev.service-now.com",
      "client_id": "...",
      "redirect_uri": "http://localhost:49160/callback",
      "dev_instances": ["mycompany-dev", "mycompany-dev2"]
    }

If `dev_instances` is omitted, no instance is treated as a development
instance and DEV commands are blocked everywhere.
"""

from .errors import PermissionDeniedError

# ── operation buckets ─────────────────────────────────────────────────────────
LOCAL = "local"
GENERAL = "general"
DEV = "dev"

# ── tiers ─────────────────────────────────────────────────────────────────────
TIER_DEV = "development"
TIER_RESTRICTED = "restricted"


def _short(instance):
    """Short instance label: the host segment before the first dot, lowercased."""
    return (instance or "").split(".")[0].strip().lower()


def _dev_set(config):
    """Return the frozenset of short dev-instance labels from config."""
    raw = config.get("dev_instances") if config else None
    if not raw:
        return frozenset()
    return frozenset(s.split(".")[0].strip().lower() for s in raw if s)


def tier_for(instance, config=None):
    """Resolve an instance host to its tier. Unknown -> restricted (fail-safe)."""
    return TIER_DEV if _short(instance) in _dev_set(config) else TIER_RESTRICTED


def is_dev_instance(instance, config=None):
    return tier_for(instance, config) == TIER_DEV


def check(instance, bucket, config=None, label=None):
    """Enforce the tier policy for a command's bucket, before it runs.

    LOCAL and GENERAL are permitted on every instance. DEV — and any command
    that failed to declare a bucket — is permitted only on the development tier;
    otherwise raise PermissionDeniedError so nothing touches the instance.
    """
    if bucket in (LOCAL, GENERAL):
        return
    if is_dev_instance(instance, config):
        return
    dev_list = sorted(_dev_set(config))
    hint = (
        f"Configured dev instances: {', '.join(dev_list)}."
        if dev_list
        else "Add a \"dev_instances\" list to ~/.snow/config.json to enable development commands."
    )
    what = f"'{label}'" if label else "development commands"
    raise PermissionDeniedError(
        f"{what} can only run on a development instance. "
        f"'{_short(instance)}' is not a development instance — only "
        f"general-purpose commands (auth, query) are available there. "
        f"{hint}"
    )
