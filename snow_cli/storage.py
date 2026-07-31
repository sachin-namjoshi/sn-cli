"""Layer 1 — token persistence backend.

Credentials are stored ONLY in the OS keychain. There is deliberately no
cleartext-file storage anywhere in the CLI — no fallback, no override, no
tokens.json. If a machine has no OS keychain, `make_backend()` raises
ConfigError with remediation steps rather than writing tokens to disk. This
keeps the security posture simple and absolute for review: tokens are always
encrypted at rest by the OS.

KeychainBackend implements the seam credentials.py depends on
(`load()` -> dict|None, `save(dict)`): it stores the whole tokens dict as one
secret in the OS credential store, keyed by ("snow-cli", instance host). If a
store operation fails at runtime (e.g. the keychain is locked), the CLI fails
with a clear message — it never silently downgrades (Auth doc §5.1).

Platform routing (make_backend):
  macOS            -> `security` CLI (generic password item)
  Windows native   -> advapi32 CredWrite/Read/Delete via ctypes
  WSL              -> Windows Credential Manager through powershell.exe
  Linux (non-WSL)  -> `secret-tool` (libsecret / Secret Service)
  anything else    -> ConfigError (no supported keychain)

The secret is never placed on a command line: macOS `security` is the one
exception the platform forces (argv, single-user box); every other store
receives the payload over stdin or an environment variable.
"""

import base64
import json
import os
import shutil
import subprocess
import sys

from .errors import AuthError, ConfigError
from .redact import register_secret

# Keychain service/namespace. The account key is the SN instance host, so tokens
# for different instances never collide in one store.
SERVICE = "snow-cli"


def _register_tokens(tokens):
    if isinstance(tokens, dict):
        register_secret(tokens.get("access_token"))
        register_secret(tokens.get("refresh_token"))


# --------------------------------------------------------------------------
# KeychainBackend — the seam: whole-dict-as-one-secret in the OS store.
# --------------------------------------------------------------------------

class KeychainBackend:
    """Wraps a low-level keychain store behind the load/save seam.

    A "store" is any object with .get()->str|None, .set(str), .delete() and a
    human .name. If a store operation fails at runtime (keychain locked, helper
    missing, permission denied), we raise AuthError with a clear message — we
    NEVER silently fall back to cleartext. A broken keychain is a hard failure.
    """

    def __init__(self, store):
        self.store = store

    def _guard(self, fn, action):
        """Run a store op, converting any failure into a clean AuthError."""
        try:
            return fn()
        except Exception as e:
            raise AuthError(
                f"Could not {action} {self.store.name}: {e}. "
                "If your keychain is locked, unlock it and try again."
            )

    def load(self):
        raw = self._guard(self.store.get, "read from")
        if raw is None:
            return None
        try:
            tokens = json.loads(raw)
        except ValueError as e:
            raise AuthError(f"Corrupt token in {self.store.name}: {e}")
        _register_tokens(tokens)
        return tokens

    def save(self, tokens):
        _register_tokens(tokens)
        self._guard(lambda: self.store.set(json.dumps(tokens)), "write to")

    def delete(self):
        """Clear stored tokens from the keychain."""
        self._guard(self.store.delete, "delete from")


# --------------------------------------------------------------------------
# Low-level stores. Each is bound to one account (the instance host) and holds
# a single string secret. is_available() gates selection in make_backend().
# --------------------------------------------------------------------------

class _MacKeychain:
    """macOS login keychain via the `security` CLI (generic password item)."""

    name = "macOS Keychain"

    def __init__(self, account):
        self.account = account

    @staticmethod
    def is_available():
        return sys.platform == "darwin" and shutil.which("security") is not None

    def get(self):
        r = subprocess.run(
            ["security", "find-generic-password",
             "-s", SERVICE, "-a", self.account, "-w"],
            capture_output=True, text=True,
        )
        if r.returncode != 0:
            return None  # 44 = item not found; any nonzero -> treat as absent
        return r.stdout.rstrip("\n")

    def set(self, secret):
        # `security` only accepts the password via argv (-w); there is no stdin
        # form. Acceptable on a single-user macOS box and matches what the de
        # facto `keyring` library does.
        r = subprocess.run(
            ["security", "add-generic-password",
             "-s", SERVICE, "-a", self.account, "-U", "-w", secret],
            capture_output=True, text=True,
        )
        if r.returncode != 0:
            raise OSError(f"security add-generic-password failed: {r.stderr.strip()}")

    def delete(self):
        subprocess.run(
            ["security", "delete-generic-password",
             "-s", SERVICE, "-a", self.account],
            capture_output=True, text=True,
        )


class _SecretToolStore:
    """Linux Secret Service via libsecret's `secret-tool` (secret over stdin)."""

    name = "libsecret (Secret Service)"

    def __init__(self, account):
        self.account = account

    @staticmethod
    def is_available():
        return sys.platform.startswith("linux") and shutil.which("secret-tool") is not None

    def _attrs(self):
        return ["service", SERVICE, "account", self.account]

    def get(self):
        r = subprocess.run(
            ["secret-tool", "lookup", *self._attrs()],
            capture_output=True, text=True,
        )
        if r.returncode != 0:
            return None
        # `lookup` prints the secret with no trailing newline.
        return r.stdout

    def set(self, secret):
        r = subprocess.run(
            ["secret-tool", "store", "--label", f"{SERVICE} {self.account}",
             *self._attrs()],
            input=secret, capture_output=True, text=True,
        )
        if r.returncode != 0:
            raise OSError(f"secret-tool store failed: {r.stderr.strip()}")

    def delete(self):
        subprocess.run(
            ["secret-tool", "clear", *self._attrs()],
            capture_output=True, text=True,
        )


class _WinCredNative:
    """Windows Credential Manager via advapi32 (native Windows Python)."""

    name = "Windows Credential Manager"

    def __init__(self, account):
        self.target = f"{SERVICE}:{account}"

    @staticmethod
    def is_available():
        return sys.platform == "win32"

    # advapi32 plumbing is built lazily so this module still imports on non-Windows.
    @staticmethod
    def _lib():
        import ctypes
        from ctypes import wintypes

        class CREDENTIAL(ctypes.Structure):
            _fields_ = [
                ("Flags", wintypes.DWORD),
                ("Type", wintypes.DWORD),
                ("TargetName", wintypes.LPWSTR),
                ("Comment", wintypes.LPWSTR),
                ("LastWritten", wintypes.FILETIME),
                ("CredentialBlobSize", wintypes.DWORD),
                ("CredentialBlob", ctypes.POINTER(ctypes.c_char)),
                ("Persist", wintypes.DWORD),
                ("AttributeCount", wintypes.DWORD),
                ("Attributes", ctypes.c_void_p),
                ("TargetAlias", wintypes.LPWSTR),
                ("UserName", wintypes.LPWSTR),
            ]

        adv = ctypes.WinDLL("advapi32", use_last_error=True)
        adv.CredReadW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                                  ctypes.POINTER(ctypes.POINTER(CREDENTIAL))]
        adv.CredReadW.restype = wintypes.BOOL
        adv.CredWriteW.argtypes = [ctypes.POINTER(CREDENTIAL), wintypes.DWORD]
        adv.CredWriteW.restype = wintypes.BOOL
        adv.CredDeleteW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD]
        adv.CredDeleteW.restype = wintypes.BOOL
        adv.CredFree.argtypes = [ctypes.c_void_p]
        adv.CredFree.restype = None
        return ctypes, adv, CREDENTIAL

    _CRED_TYPE_GENERIC = 1
    _CRED_PERSIST_LOCAL_MACHINE = 2
    _ERROR_NOT_FOUND = 1168

    def get(self):
        ctypes, adv, CREDENTIAL = self._lib()
        ptr = ctypes.POINTER(CREDENTIAL)()
        if not adv.CredReadW(self.target, self._CRED_TYPE_GENERIC, 0, ctypes.byref(ptr)):
            err = ctypes.get_last_error()
            if err == self._ERROR_NOT_FOUND:
                return None
            raise OSError(f"CredReadW failed (error {err})")
        try:
            cred = ptr.contents
            blob = ctypes.string_at(cred.CredentialBlob, cred.CredentialBlobSize)
        finally:
            adv.CredFree(ptr)
        return blob.decode("utf-16-le")

    def set(self, secret):
        ctypes, adv, CREDENTIAL = self._lib()
        blob = secret.encode("utf-16-le")
        buf = ctypes.create_string_buffer(blob, len(blob))
        cred = CREDENTIAL()
        cred.Type = self._CRED_TYPE_GENERIC
        cred.TargetName = self.target
        cred.CredentialBlobSize = len(blob)
        cred.CredentialBlob = ctypes.cast(buf, ctypes.POINTER(ctypes.c_char))
        cred.Persist = self._CRED_PERSIST_LOCAL_MACHINE
        cred.UserName = SERVICE
        if not adv.CredWriteW(ctypes.byref(cred), 0):
            raise OSError(f"CredWriteW failed (error {ctypes.get_last_error()})")

    def delete(self):
        ctypes, adv, _ = self._lib()
        if not adv.CredDeleteW(self.target, self._CRED_TYPE_GENERIC, 0):
            err = ctypes.get_last_error()
            if err != self._ERROR_NOT_FOUND:
                raise OSError(f"CredDeleteW failed (error {err})")


# PowerShell bridge script (WSL -> Windows Credential Manager). One script,
# switched by $env:SNOW_OP. Target/payload arrive via env (declared in WSLENV so
# they cross the WSL/Win32 boundary) — the secret never touches the command line.
# get: prints base64(UTF-16LE blob) to stdout, exit 2 = not found, 3 = error.
_PS_BRIDGE = r'''
$ErrorActionPreference='Stop'
Add-Type -Namespace SnowCred -Name Native -MemberDefinition @'
[DllImport("advapi32.dll", CharSet=CharSet.Unicode, SetLastError=true)]
public static extern bool CredWriteW(ref CREDENTIAL cred, uint flags);
[DllImport("advapi32.dll", EntryPoint="CredReadW", CharSet=CharSet.Unicode, SetLastError=true)]
public static extern bool CredReadW(string target, uint type, uint flags, out IntPtr credential);
[DllImport("advapi32.dll", EntryPoint="CredDeleteW", CharSet=CharSet.Unicode, SetLastError=true)]
public static extern bool CredDeleteW(string target, uint type, uint flags);
[DllImport("advapi32.dll")]
public static extern void CredFree(IntPtr cred);
[StructLayout(LayoutKind.Sequential, CharSet=CharSet.Unicode)]
public struct CREDENTIAL {
  public uint Flags; public uint Type; public string TargetName; public string Comment;
  public System.Runtime.InteropServices.ComTypes.FILETIME LastWritten;
  public uint CredentialBlobSize; public IntPtr CredentialBlob; public uint Persist;
  public uint AttributeCount; public IntPtr Attributes; public string TargetAlias; public string UserName;
}
'@
$op=$env:SNOW_OP; $target=$env:SNOW_TARGET
if($op -eq 'get'){
  $p=[IntPtr]::Zero
  if(-not [SnowCred.Native]::CredReadW($target,1,0,[ref]$p)){
    $e=[Runtime.InteropServices.Marshal]::GetLastWin32Error()
    if($e -eq 1168){exit 2}; [Console]::Error.Write("read $e"); exit 3 }
  $c=[Runtime.InteropServices.Marshal]::PtrToStructure($p,[Type][SnowCred.Native+CREDENTIAL])
  $b=New-Object byte[] $c.CredentialBlobSize
  [Runtime.InteropServices.Marshal]::Copy($c.CredentialBlob,$b,0,$c.CredentialBlobSize)
  [SnowCred.Native]::CredFree($p)
  [Console]::Out.Write([Convert]::ToBase64String($b)); exit 0
}
elseif($op -eq 'set'){
  $bytes=[Text.Encoding]::Unicode.GetBytes($env:SNOW_PAYLOAD)
  $mem=[Runtime.InteropServices.Marshal]::AllocHGlobal($bytes.Length)
  [Runtime.InteropServices.Marshal]::Copy($bytes,0,$mem,$bytes.Length)
  $c=New-Object SnowCred.Native+CREDENTIAL
  $c.Type=1; $c.TargetName=$target; $c.CredentialBlobSize=$bytes.Length
  $c.CredentialBlob=$mem; $c.Persist=2; $c.UserName="snow-cli"
  $ok=[SnowCred.Native]::CredWriteW([ref]$c,0)
  $err=[Runtime.InteropServices.Marshal]::GetLastWin32Error()
  [Runtime.InteropServices.Marshal]::FreeHGlobal($mem)
  if(-not $ok){[Console]::Error.Write("write $err"); exit 3}; exit 0
}
elseif($op -eq 'delete'){
  if(-not [SnowCred.Native]::CredDeleteW($target,1,0)){
    $e=[Runtime.InteropServices.Marshal]::GetLastWin32Error()
    if($e -eq 1168){exit 2}; exit 3 }
  exit 0
}
'''


def _is_wsl():
    if os.environ.get("WSL_DISTRO_NAME"):
        return True
    try:
        with open("/proc/version") as f:
            return "microsoft" in f.read().lower()
    except OSError:
        return False


def _find_powershell():
    exe = shutil.which("powershell.exe")
    if exe:
        return exe
    fallback = "/mnt/c/Windows/System32/WindowsPowerShell/v1.0/powershell.exe"
    return fallback if os.path.exists(fallback) else None


class _WinCredPowershell:
    """Windows Credential Manager reached from WSL through powershell.exe."""

    name = "Windows Credential Manager (WSL bridge)"

    def __init__(self, account):
        self.target = f"{SERVICE}:{account}"
        self._enc = base64.b64encode(_PS_BRIDGE.encode("utf-16-le")).decode("ascii")

    @staticmethod
    def is_available():
        return _is_wsl() and _find_powershell() is not None

    def _run(self, op, payload=None, timeout=20):
        env = dict(os.environ)
        env["SNOW_OP"] = op
        env["SNOW_TARGET"] = self.target
        shared = ["SNOW_OP", "SNOW_TARGET"]
        if payload is not None:
            env["SNOW_PAYLOAD"] = payload
            shared.append("SNOW_PAYLOAD")
        prior = os.environ.get("WSLENV", "")
        # WSLENV must name each var or it won't cross into the Windows process.
        env["WSLENV"] = ":".join(shared + ([prior] if prior else []))
        proc = subprocess.run(
            [_find_powershell(), "-NoProfile", "-NonInteractive",
             "-EncodedCommand", self._enc],
            capture_output=True, env=env, timeout=timeout,
        )
        return proc.returncode, proc.stdout

    def get(self):
        rc, out = self._run("get")
        if rc == 2:
            return None
        if rc != 0:
            raise OSError(f"WinCM bridge read failed (rc {rc})")
        # stdout is clean base64; PowerShell progress noise goes to stderr.
        b64 = out.decode("ascii", "ignore").strip()
        if not b64:
            return None
        return base64.b64decode(b64).decode("utf-16-le")

    def set(self, secret):
        rc, _ = self._run("set", payload=secret)
        if rc != 0:
            raise OSError(f"WinCM bridge write failed (rc {rc})")

    def delete(self):
        rc, _ = self._run("delete")
        if rc not in (0, 2):
            raise OSError(f"WinCM bridge delete failed (rc {rc})")


def _select_store(account):
    """Return the best keychain store for this platform, or None."""
    # Native platform stores first.
    if _MacKeychain.is_available():
        return _MacKeychain(account)
    if _WinCredNative.is_available():
        return _WinCredNative(account)
    # WSL: the real store is Windows Credential Manager via powershell; a Linux
    # Secret Service is almost never running under WSL. Prefer the bridge, then
    # fall through to secret-tool if somehow present.
    if _is_wsl() and _WinCredPowershell.is_available():
        return _WinCredPowershell(account)
    if _SecretToolStore.is_available():
        return _SecretToolStore(account)
    return None


def _keychain_guidance():
    """Platform-specific remediation for a missing keychain."""
    if sys.platform == "darwin":
        return ("macOS token storage uses the built-in `security` command, which "
                "was not found on your PATH. This is unusual on macOS — check your "
                "PATH and that /usr/bin is on it.")
    if sys.platform == "win32":
        return ("Windows Credential Manager could not be reached via advapi32.")
    if _is_wsl():
        return ("Under WSL, snow stores tokens in Windows Credential Manager via "
                "powershell.exe, which was not found. Ensure Windows PowerShell is "
                "reachable (typically /mnt/c/Windows/System32/WindowsPowerShell/"
                "v1.0/powershell.exe) and that PATH includes the Windows system "
                "directories.")
    if sys.platform.startswith("linux"):
        return ("Linux token storage uses the Secret Service via `secret-tool`, "
                "which was not found. Install libsecret and a keyring daemon:\n"
                "  Debian/Ubuntu:      apt install libsecret-tools gnome-keyring\n"
                "  Fedora/RHEL/Alma:   dnf install libsecret gnome-keyring\n"
                "and make sure a Secret Service provider (gnome-keyring or KWallet) "
                "is running in your session.")
    return f"No supported OS keychain for platform '{sys.platform}'."


def make_backend(config):
    """Build the token backend for the current platform.

    Credentials are stored only in the OS keychain. If no keychain is available,
    raise ConfigError with remediation — never write tokens in cleartext.
    """
    store = _select_store(config.instance)
    if store is None:
        raise ConfigError(
            "snow stores credentials in your OS keychain, but none is available "
            "on this machine.\n\n" + _keychain_guidance()
        )
    return KeychainBackend(store)
