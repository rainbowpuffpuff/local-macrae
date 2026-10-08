"""Which Claude accounts the runner can use, and their logins. Nothing here prints a token.

An account is a name with a login, from any of:
  1. keyring (secret-tool): service "agent-runner", attribute account=<name>
     (falls back to service "language-models", the desktop app's entries)
  2. environment: AGENT_RUNNER_TOKEN_<NAME>=sk-ant-oat01-…   (OAuth token from `claude setup-token`)
  3. environment: ANTHROPIC_API_KEY  → an account called "api-key" (pay-per-use API billing)
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import time
from typing import Optional

SERVICE = os.environ.get("AGENT_RUNNER_KEYRING", "agent-runner")
LEGACY_SERVICES = ["language-models"]
ENV_PREFIX = "AGENT_RUNNER_TOKEN_"
API_KEY_ACCOUNT = "api-key"
TOKEN_PREFIX = "sk-ant-oat"
_cache: dict[str, tuple[float, Optional[tuple[str, str]]]] = {}


def keyring_available() -> bool:
    return shutil.which("secret-tool") is not None


def looks_like_token(token: str) -> bool:
    """`claude setup-token` prints sk-ant-oat01-…; the browser's authorization code looks different."""
    return token.strip().startswith(TOKEN_PREFIX)


def _keyring_lookup(service: str, account: str) -> Optional[str]:
    if not keyring_available():
        return None
    r = subprocess.run(["secret-tool", "lookup", "service", service, "account", account],
                       capture_output=True, text=True, timeout=15)
    tok = (r.stdout or "").strip()
    return tok if r.returncode == 0 and tok else None


def _keyring_accounts(service: str) -> list[str]:
    if not keyring_available():
        return []
    # secret-tool prints attributes on stderr and the secrets on stdout: stdout goes straight to /dev/null
    r = subprocess.run(["secret-tool", "search", "--all", "service", service], stdout=subprocess.DEVNULL,
                       stderr=subprocess.PIPE, text=True, timeout=15)
    return sorted({m.group(1).strip() for m in re.finditer(r"^attribute\.account = (.+)$", r.stderr or "", re.M)})


def _env_name(account: str) -> str:
    return ENV_PREFIX + re.sub(r"[^A-Za-z0-9]", "_", account).upper()


def list_accounts() -> list[str]:
    names = set()
    for k in os.environ:
        if k.startswith(ENV_PREFIX) and os.environ[k].strip():
            names.add(k[len(ENV_PREFIX):].lower())
    for svc in [SERVICE] + LEGACY_SERVICES:
        names.update(_keyring_accounts(svc))
    if os.environ.get("ANTHROPIC_API_KEY"):
        names.add(API_KEY_ACCOUNT)
    return sorted(names)


def credential(account: str) -> Optional[tuple[str, str]]:
    """(kind, secret) where kind is 'oauth' or 'api_key'; None if the account has no login."""
    hit = _cache.get(account)
    if hit and time.time() - hit[0] < 30:
        return hit[1]
    cred: Optional[tuple[str, str]] = None
    if account == API_KEY_ACCOUNT and os.environ.get("ANTHROPIC_API_KEY"):
        cred = ("api_key", os.environ["ANTHROPIC_API_KEY"])
    elif os.environ.get(_env_name(account), "").strip():
        cred = ("oauth", "".join(os.environ[_env_name(account)].split()))
    else:
        for svc in [SERVICE] + LEGACY_SERVICES:
            tok = _keyring_lookup(svc, account)
            if tok:
                cred = ("oauth", tok)
                break
    _cache[account] = (time.time(), cred)
    return cred


def has_token(account: str) -> bool:
    return credential(account) is not None


def get_token(account: str) -> Optional[str]:
    c = credential(account)
    return c[1] if c else None


def store_token(account: str, token: str) -> None:
    token = "".join(token.split())  # a wrapped terminal copy can carry line breaks
    if not token:
        raise ValueError("empty token")
    if not looks_like_token(token):
        raise ValueError("that isn't the token: it should start with sk-ant-oat01- (the line printed at the "
                         "very end of `claude setup-token`, not the code from the browser)")
    if not keyring_available():
        raise RuntimeError(f"no keyring here; set {_env_name(account)} in the environment instead")
    r = subprocess.run(["secret-tool", "store", "--label", f"agent-runner: Claude token ({account})",
                        "service", SERVICE, "account", account], input=token, capture_output=True, text=True,
                       timeout=15)
    if r.returncode != 0:
        raise RuntimeError((r.stderr or "secret-tool store failed").strip())
    _cache.pop(account, None)


def clear_token(account: str) -> None:
    if keyring_available():
        subprocess.run(["secret-tool", "clear", "service", SERVICE, "account", account], capture_output=True,
                       timeout=15)
    _cache.pop(account, None)
