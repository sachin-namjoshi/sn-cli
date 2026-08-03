"""Scrub secrets out of any string before it is printed or logged.

Two layers:
  1. Exact-match: strings explicitly registered as secret (client_secret at
     config load, live tokens at use) are replaced wherever they appear.
  2. Pattern-match: token-shaped substrings (Bearer headers, JSON token fields,
     X-UserToken) are masked even if never registered — belt and suspenders for
     error bodies echoed back from ServiceNow.
"""

import re

_MASK = "***REDACTED***"

_PATTERNS = [
    re.compile(r"(Bearer\s+)[A-Za-z0-9._\-]+", re.IGNORECASE),
    re.compile(
        r'("(?:access_token|refresh_token|client_secret|X-UserToken|g_ck)"\s*:\s*")[^"]*(")',
        re.IGNORECASE,
    ),
    re.compile(r"(X-UserToken['\"\s:]+)[0-9A-Za-z]+", re.IGNORECASE),
]

_secrets = set()


def register_secret(value):
    """Register a literal secret to be masked on sight. No-op for falsy input."""
    if value:
        _secrets.add(str(value))


def redact(text):
    """Return `text` with every known/known-shaped secret masked."""
    if text is None:
        return text
    if not isinstance(text, str):
        text = str(text)
    for s in _secrets:
        if s:
            text = text.replace(s, _MASK)
    for pat in _PATTERNS:
        text = pat.sub(lambda m: m.group(1) + _MASK + (m.group(2) if m.lastindex and m.lastindex >= 2 else ""), text)
    return text
