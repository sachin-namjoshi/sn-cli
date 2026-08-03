"""Layer 2 — the single transport choke point.

Every ServiceNow call goes through SnowClient.request(). It owns:
  - auth: pulls the Bearer from the CredentialProvider (never the token file);
  - TLS: https only — a non-https base URL is refused;
  - recovery: one 401 -> force refresh -> retry;
  - redaction: error messages are scrubbed before they become exceptions.

Table-API convenience wrappers and the closed-loop render live here too, because
render needs its own cookie-jar opener (a second transport concern), and keeping
it beside request() keeps all network I/O in one file.
"""

import http.cookiejar
import json
import re
import urllib.error
import urllib.parse
import urllib.request

from .errors import ApiError, AuthError
from .redact import redact, register_secret

_G_CK_RE = re.compile(r"g_ck\s*=\s*['\"]([0-9a-zA-Z]+)['\"]")


class SnowClient:
    # ServiceNow Table API version. v2 is used for broadest OAuth Bearer
    # compatibility across instance configurations; v1 and v2 are
    # request/response-compatible for table operations.
    TABLE_API = "/api/now/v2/table"

    def __init__(self, config, credentials):
        self.config = config
        self.credentials = credentials
        if not config.base_url.startswith("https://"):
            raise AuthError("Refusing to send credentials over non-HTTPS.")

    # ── core ────────────────────────────────────────────────────────────────
    def request(self, method, path, params=None, data=None, headers=None,
                timeout=30, _retried=False):
        url = self.config.base_url + path
        if params:
            url += "?" + urllib.parse.urlencode(params)

        body = json.dumps(data).encode() if data is not None else None
        req_headers = {
            "Authorization": f"Bearer {self.credentials.get_access_token()}",
            "Accept": "application/json",
        }
        if body is not None:
            req_headers["Content-Type"] = "application/json"
        if headers:
            req_headers.update(headers)

        req = urllib.request.Request(url, data=body, headers=req_headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                raw = resp.read()
                if resp.status == 204 or not raw:
                    return None
                return json.loads(raw)
        except urllib.error.HTTPError as e:
            if e.code == 401 and not _retried:
                self.credentials.refresh()
                return self.request(method, path, params, data, headers,
                                    timeout, _retried=True)
            detail = redact(e.read().decode("utf-8", "replace"))[:500]
            raise ApiError(e.code, f"{method} {path} -> {e.code}: {detail}", body=detail)
        except urllib.error.URLError as e:
            raise ApiError(0, redact(f"{method} {path} failed: {e.reason}"))

    # ── Table API convenience ─────────────────────────────────────────────────
    def table_get(self, table, query=None, fields=None, limit=None, params=None):
        p = dict(params or {})
        if query:
            p["sysparm_query"] = query
        if fields:
            p["sysparm_fields"] = ",".join(fields) if not isinstance(fields, str) else fields
        if limit is not None:
            p["sysparm_limit"] = str(limit)
        result = self.request("GET", f"{self.TABLE_API}/{table}", params=p)
        return (result or {}).get("result", [])

    def table_get_record(self, table, sys_id, fields=None):
        p = {}
        if fields:
            p["sysparm_fields"] = ",".join(fields) if not isinstance(fields, str) else fields
        result = self.request("GET", f"{self.TABLE_API}/{table}/{sys_id}", params=p)
        return (result or {}).get("result")

    def table_post(self, table, data):
        result = self.request("POST", f"{self.TABLE_API}/{table}", data=data)
        return (result or {}).get("result")

    def table_patch(self, table, sys_id, data):
        result = self.request("PATCH", f"{self.TABLE_API}/{table}/{sys_id}", data=data)
        return (result or {}).get("result")

    def table_delete(self, table, sys_id):
        self.request("DELETE", f"{self.TABLE_API}/{table}/{sys_id}")
        return True

    def whoami(self):
        """Current authenticated user's sys_user record (dynamic self-lookup)."""
        rows = self.table_get(
            "sys_user",
            query="sys_idDYNAMICjavascript:gs.getUserID()",
            fields=["sys_id", "user_name", "first_name", "last_name"],
            limit=1,
        )
        if not rows:
            raise ApiError(0, "Could not resolve current user (whoami).")
        return rows[0]

    # ── closed-loop render ────────────────────────────────────────────────────
    def render_widget(self, id_or_sysid, timeout=30):
        """Render a widget server-side; return the parsed sp/widget payload.

        Needs a shared HTTP session (cookie jar) so the scraped g_ck matches the
        JSESSIONID the render POST rides on. Bearer alone yields invalid_token.
        Caller (widget.py) does timeout+retry and error extraction.
        """
        cj = http.cookiejar.CookieJar()
        opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))
        bearer = f"Bearer {self.credentials.get_access_token()}"

        # 1) Authenticated portal page on this session -> establishes JSESSIONID.
        page_req = urllib.request.Request(
            f"{self.config.base_url}/sp?id=index",
            headers={"Authorization": bearer},
        )
        with opener.open(page_req, timeout=timeout) as r:
            html = r.read().decode("utf-8", "replace")
        m = _G_CK_RE.search(html)
        if not m:
            raise ApiError(0, "Could not scrape g_ck from portal page.")
        g_ck = m.group(1)
        register_secret(g_ck)

        # 2) Render POST on the SAME session, carrying g_ck as X-UserToken.
        render_req = urllib.request.Request(
            f"{self.config.base_url}/api/now/sp/widget/{urllib.parse.quote(str(id_or_sysid))}",
            data=json.dumps({}).encode(),
            headers={
                "Authorization": bearer,
                "Content-Type": "application/json",
                "Accept": "application/json",
                "X-UserToken": g_ck,
            },
            method="POST",
        )
        try:
            with opener.open(render_req, timeout=timeout) as r:
                return json.loads(r.read())
        except urllib.error.HTTPError as e:
            detail = redact(e.read().decode("utf-8", "replace"))[:500]
            raise ApiError(e.code, f"render -> {e.code}: {detail}", body=detail)
