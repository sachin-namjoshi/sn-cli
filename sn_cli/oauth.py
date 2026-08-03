"""Layer 1 — OAuth protocol mechanics (no storage, no policy).

Ported from the validated ~/.snow/auth.py prototype. Two grants:
  - authorization_code_flow(): browser -> Okta/MFA -> loopback -> code exchange.
  - refresh(): silent refresh_token grant.

The auth-code exchange is protected by PKCE (S256, RFC 7636): a per-login random
`code_verifier` is generated, only its SHA-256 `code_challenge` goes out with the
auth request, and the verifier is revealed only at token exchange — so a stolen
auth code is useless without it (Auth doc §9). A per-login random `state` is also
sent and verified (constant-time) on the callback, so a forged or replayed
callback is rejected (CSRF protection).
"""

import base64
import hashlib
import json
import os
import secrets
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer

from .errors import AuthError
from .storage import _find_powershell, _is_wsl


def _open_browser(url):
    """Open the system browser; return True if a launcher was actually invoked.

    Python's `webbrowser` can't reach a browser under WSL — there's no Linux GUI
    browser, so `webbrowser.open()` latches onto something like sensible-browser,
    reports success, and nothing pops. On WSL we instead hand the URL to the
    Windows default browser via powershell.exe. The URL is passed through the
    environment (declared in WSLENV) rather than the command line, so a URL full
    of `&` and `=` is never re-parsed by Windows-side quoting and there is no
    command injection surface. The loopback callback still works because WSL2
    forwards the Windows browser's 127.0.0.1:PORT hit to our WSL listener.
    """
    if _is_wsl():
        ps = _find_powershell()
        if ps is None:
            return False
        env = dict(os.environ)
        env["SNOW_URL"] = url
        prior = os.environ.get("WSLENV", "")
        env["WSLENV"] = ":".join(["SNOW_URL"] + ([prior] if prior else []))
        try:
            subprocess.run(
                [ps, "-NoProfile", "-NonInteractive", "-Command",
                 "Start-Process $env:SNOW_URL"],
                env=env, timeout=15,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
            return True
        except (OSError, subprocess.SubprocessError):
            return False
    return webbrowser.open(url)


def _token_request(config, form):
    """POST the token endpoint and normalize the response to our token dict."""
    data = urllib.parse.urlencode(form).encode()
    req = urllib.request.Request(
        f"{config.base_url}/oauth_token.do",
        data=data,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            payload = json.loads(resp.read())
    except urllib.error.HTTPError as e:
        # Do NOT echo the raw body — it can contain the submitted secret/code.
        raise AuthError(f"Token endpoint returned {e.code}")
    except urllib.error.URLError as e:
        raise AuthError(f"Cannot reach token endpoint: {e.reason}")

    if "access_token" not in payload:
        raise AuthError("Token endpoint response had no access_token")

    return {
        "access_token": payload["access_token"],
        "refresh_token": payload.get("refresh_token"),
        "expires_at": int(time.time()) + int(payload.get("expires_in", 1800)),
    }


def _generate_pkce():
    """Return (code_verifier, code_challenge) for a PKCE S256 exchange.

    The verifier is a high-entropy random string held only in memory for this
    one login; the challenge is its SHA-256, base64url-encoded without padding
    (per RFC 7636). Only the challenge travels with the auth request — the
    verifier is disclosed just once, at token exchange.
    """
    verifier = secrets.token_urlsafe(64)  # ~86 chars, within RFC 7636's 43–128
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")
    return verifier, challenge


def build_auth_url(config, code_challenge=None, state=None):
    url = (
        f"{config.base_url}/oauth_auth.do"
        f"?response_type=code"
        f"&client_id={config.client_id}"
        f"&redirect_uri={urllib.parse.quote(config.redirect_uri)}"
    )
    if code_challenge:
        url += f"&code_challenge={code_challenge}&code_challenge_method=S256"
    if state:
        url += f"&state={urllib.parse.quote(state)}"
    return url


def _callback_port(redirect_uri):
    parsed = urllib.parse.urlparse(redirect_uri)
    return parsed.port or 49160


def authorization_code_flow(config, open_browser=True):
    """Run the loopback auth-code flow; return a token dict."""
    captured = {"code": None, "state": None}

    class CallbackHandler(BaseHTTPRequestHandler):
        def do_GET(self):
            qs = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            captured["code"] = qs.get("code", [None])[0]
            captured["state"] = qs.get("state", [None])[0]
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"Auth complete - you can close this tab.")

        def log_message(self, *args):
            pass

    verifier, challenge = _generate_pkce()
    state = secrets.token_urlsafe(32)  # CSRF guard: echoed back on the callback
    url = build_auth_url(config, code_challenge=challenge, state=state)
    print(f"\nIf your browser does not open, visit:\n\n  {url}\n")
    if open_browser and not _open_browser(url):
        print("(Could not launch a browser automatically — open the URL above.)")

    port = _callback_port(config.redirect_uri)
    # Bind loopback only. The callback is a redirect from this user's own browser
    # to localhost; accepting connections from other interfaces would let any host
    # on the network reach (or pre-empt) the one-shot callback listener.
    server = HTTPServer(("127.0.0.1", port), CallbackHandler)
    print(f"Waiting for Okta authentication on port {port} ...")
    server.handle_request()  # blocks until the one callback GET
    server.server_close()

    # CSRF check: the callback must echo back the exact state we generated.
    # A forged/replayed callback won't know it, so a mismatch aborts the login.
    if not captured["state"] or not secrets.compare_digest(captured["state"], state):
        raise AuthError("OAuth callback state did not match — possible CSRF; aborting.")

    if not captured["code"]:
        raise AuthError("No authorization code received from the callback")

    form = {
        "grant_type": "authorization_code",
        "code": captured["code"],
        "redirect_uri": config.redirect_uri,
        "client_id": config.client_id,
        "code_verifier": verifier,
    }
    # Public client (PKCE) sends no secret; confidential still does. PKCE is what
    # authenticates the exchange either way, so the secret is optional here.
    if config.client_secret:
        form["client_secret"] = config.client_secret
    return _token_request(config, form)


def refresh(config, refresh_token):
    """Silent refresh_token grant; return a fresh token dict."""
    if not refresh_token:
        raise AuthError("No refresh token available — run `snow auth login`.")
    form = {
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
        "client_id": config.client_id,
    }
    if config.client_secret:
        form["client_secret"] = config.client_secret
    return _token_request(config, form)
