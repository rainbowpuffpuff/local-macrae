#!/usr/bin/env python3
"""Small helpers around deploy/.env, used by aws_deploy.sh and the Makefile (stdlib only).

    python3 deploy/envtool.py get FILE KEY [DEFAULT]      print a value (blank counts as unset)
    python3 deploy/envtool.py export FILE                 shell `export K='v'` lines for eval (process env wins)
    python3 deploy/envtool.py check FILE                   validate for a server deploy (exit 1 on errors)
    python3 deploy/envtool.py render FILE --domain D --data-dir DIR
                                                           print the env file to upload to the server
    python3 deploy/envtool.py worker-secrets FILE [--state STATE] [--wrangler-dir cloudflare] [--dry-run]
                                                           put the Worker secrets with `npx wrangler secret put`
                                                           (AWS backend: includes BACKEND_URL)
    python3 deploy/envtool.py cf-secrets FILE --existing JSON --out PATH [--wrangler-dir cloudflare]
                                                           Cloudflare: check that every secret the Worker and its
                                                           container need is on the Worker or in FILE (exit 1 with
                                                           the commands to run if not); write FILE's values to PATH
                                                           (JSON, for `wrangler deploy --secrets-file`)
"""

from __future__ import annotations

import argparse
import os
import re
import shlex
import subprocess
import sys
from pathlib import Path
from typing import Optional

try:
    import tomllib
except ModuleNotFoundError:  # Python 3.10
    tomllib = None  # type: ignore[assignment]

DEFAULT_MODAL_PROFILE = "acalincarol"
WORKER_SECRETS = ["BACKEND_URL", "MACRAE_TOOL_SECRET", "ELEVENLABS_API_KEY", "ELEVENLABS_AGENT_ID"]
# Keys the server copy gets from the deploy script, never from the local file.
SERVER_MANAGED = {"MACRAE_DOMAIN", "MACRAE_DATA_DIR", "MACRAE_PAPERS_DIR", "MACRAE_INDEX_DIR", "AGENT_RUNNER_HOME"}
_LINE = re.compile(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)$")


def parse_value(raw: str) -> str:
    raw = raw.strip()
    if len(raw) >= 2 and raw[0] == raw[-1] and raw[0] in "'\"":
        v = raw[1:-1]
        return v.replace('\\"', '"').replace("\\n", "\n") if raw[0] == '"' else v
    return re.split(r"\s+#", raw, maxsplit=1)[0].strip()  # unquoted: " # comment" ends the value


def parse(text: str) -> dict[str, str]:
    """dotenv → dict; later assignments win, comments and blank lines are ignored."""
    out: dict[str, str] = {}
    for line in text.splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        m = _LINE.match(line)
        if m:
            out[m.group(1)] = parse_value(m.group(2))
    return out


def load(path: str | Path) -> dict[str, str]:
    try:
        return parse(Path(path).read_text())
    except FileNotFoundError:
        return {}


def get(env: dict[str, str], key: str, default: str = "") -> str:
    """Process environment first (so `AWS_REGION=… make deploy-backend` works), then the file; blank = unset."""
    for v in (os.environ.get(key), env.get(key)):
        if v is not None and v.strip():
            return v.strip()
    return default


def export_lines(env: dict[str, str]) -> list[str]:
    """`export K='v'` for every non-blank value not already set (non-blank) in the process environment."""
    return [f"export {k}={shlex.quote(v)}" for k, v in env.items()
            if v.strip() and not os.environ.get(k, "").strip()]


def modal_tokens(profile: str, toml_path: Path) -> Optional[tuple[str, str]]:
    """(token_id, token_secret) of a profile in ~/.modal.toml, or None."""
    if tomllib is None:
        return None
    try:
        data = tomllib.loads(toml_path.read_text())
    except (OSError, ValueError):
        return None
    prof = data.get(profile) or {}
    tid, secret = prof.get("token_id"), prof.get("token_secret")
    return (tid, secret) if tid and secret else None


def claude_logins(env: dict[str, str]) -> list[str]:
    names = [k[len("AGENT_RUNNER_TOKEN_"):].lower() for k, v in env.items()
             if k.startswith("AGENT_RUNNER_TOKEN_") and v.strip()]
    if env.get("ANTHROPIC_API_KEY", "").strip():
        names.append("api-key")
    return sorted(names)


def check(env: dict[str, str], modal_toml: Path) -> tuple[list[str], list[str]]:
    """(errors, warnings) for deploying the backend with this env file."""
    errors, warnings = [], []
    secret = env.get("MACRAE_TOOL_SECRET", "").strip()
    if not secret:
        errors.append("MACRAE_TOOL_SECRET is empty (python3 -c \"import secrets; print(secrets.token_urlsafe(32))\")")
    elif len(secret) < 16:
        warnings.append("MACRAE_TOOL_SECRET is shorter than 16 characters; anyone who guesses it can start runs")
    has_pair = bool(env.get("MODAL_TOKEN_ID", "").strip() and env.get("MODAL_TOKEN_SECRET", "").strip())
    profile = env.get("MODAL_PROFILE", "").strip() or DEFAULT_MODAL_PROFILE
    if not has_pair and not modal_tokens(profile, modal_toml):
        warnings.append(f"no Modal login: set MODAL_TOKEN_ID/MODAL_TOKEN_SECRET (or have profile [{profile}] in "
                        f"{modal_toml}); tasks will fail, search still works")
    if not claude_logins(env):
        warnings.append("no Claude login (AGENT_RUNNER_TOKEN_<NAME> or ANTHROPIC_API_KEY): agent steps will fail")
    return errors, warnings


def render(text: str, *, domain: str, data_dir: str, modal_toml: Optional[Path] = None) -> str:
    """The env file for the server: the local lines minus server-managed keys, plus domain/data dir, plus the
    Modal token pair from ~/.modal.toml when the file has none."""
    env = parse(text)
    drop = set(SERVER_MANAGED)
    extra = [f"MACRAE_DOMAIN={domain}", f"MACRAE_DATA_DIR={data_dir}"]
    if modal_toml and not (env.get("MODAL_TOKEN_ID", "").strip() and env.get("MODAL_TOKEN_SECRET", "").strip()):
        profile = env.get("MODAL_PROFILE", "").strip() or DEFAULT_MODAL_PROFILE
        tok = modal_tokens(profile, modal_toml)
        if tok:
            drop |= {"MODAL_TOKEN_ID", "MODAL_TOKEN_SECRET"}
            extra += [f"MODAL_TOKEN_ID={tok[0]}", f"MODAL_TOKEN_SECRET={tok[1]}"]
            print(f"envtool: using Modal profile [{profile}] from {modal_toml}", file=sys.stderr)
    keep = []
    for line in text.splitlines():
        m = _LINE.match(line)
        if not (m and m.group(1) in drop):
            keep.append(line)
    body = "\n".join(keep).rstrip()
    return (body + "\n\n" if body else "") + "# set by deploy/aws_deploy.sh\n" + "\n".join(extra) + "\n"


def wrangler_vars(wrangler_dir: Path) -> set[str]:
    """Names defined under [vars] in wrangler.toml / wrangler.jsonc (a secret can't share a name with a var)."""
    toml = wrangler_dir / "wrangler.toml"
    if toml.exists() and tomllib is not None:
        try:
            return set((tomllib.loads(toml.read_text()).get("vars") or {}).keys())
        except ValueError:
            return set()
    for name in ("wrangler.jsonc", "wrangler.json"):
        p = wrangler_dir / name
        if p.exists():
            import json
            text = re.sub(r"(?m)^\s*//.*$", "", p.read_text())
            try:
                return set((json.loads(text).get("vars") or {}).keys())
            except ValueError:
                return set()
    return set()


def worker_secrets(env: dict[str, str], state: dict[str, str], plain_vars: set[str]) -> tuple[dict[str, str], list[str]]:
    """(secrets to put, messages). BACKEND_URL falls back to the URL aws_deploy.sh saved."""
    values = {k: get(env, k) for k in WORKER_SECRETS}
    if not values["BACKEND_URL"] and state.get("URL"):
        values["BACKEND_URL"] = state["URL"]
    out, msgs = {}, []
    for k, v in values.items():
        if k in plain_vars:
            msgs.append(f"skip {k}: defined under [vars] in wrangler config (edit it there)")
        elif not v:
            msgs.append(f"skip {k}: empty")
        else:
            out[k] = v
    return out, msgs


# Cloudflare: the Worker's secrets, which it also passes to the backend container (cloudflare/worker.js containerEnv).
CF_REQUIRED = ["MACRAE_TOOL_SECRET", "MODAL_TOKEN_ID", "MODAL_TOKEN_SECRET"]
CF_OPTIONAL = ["ANTHROPIC_API_KEY", "ELEVENLABS_API_KEY", "ELEVENLABS_AGENT_ID"]
TOKEN_PREFIX = "AGENT_RUNNER_TOKEN_"
_TOKEN_NAME = re.compile(r"^AGENT_RUNNER_TOKEN_[A-Z0-9_]+$")


def cf_secrets(env: dict[str, str], existing: set[str], plain_vars: set[str],
               modal_toml: Optional[Path] = None) -> tuple[dict[str, str], list[str], list[str]]:
    """(values to upload, errors, notes) for a Cloudflare deploy.

    `existing`: secret names already on the Worker (`wrangler secret list`); `env`: deploy/.env (the process
    environment wins, as everywhere in this file). Values found here are uploaded with the deploy; names that are
    neither here nor on the Worker are errors, each with the command that fixes it."""
    names = CF_REQUIRED + CF_OPTIONAL + sorted({k for k in [*env, *os.environ, *existing] if _TOKEN_NAME.match(k)})
    values = {k: get(env, k) for k in names}
    values = {k: v for k, v in values.items() if v}
    errors, notes = [], []
    if not ("MODAL_TOKEN_ID" in values or "MODAL_TOKEN_ID" in existing) and modal_toml:
        profile = get(env, "MODAL_PROFILE") or DEFAULT_MODAL_PROFILE
        tok = modal_tokens(profile, modal_toml)
        if tok and "MODAL_TOKEN_SECRET" not in values:
            values["MODAL_TOKEN_ID"], values["MODAL_TOKEN_SECRET"] = tok
            notes.append(f"Modal login: profile [{profile}] from {modal_toml}")
    for k in sorted(values):
        if k in plain_vars:
            errors.append(f"{k} is under [vars] in cloudflare/wrangler.toml; a secret can't share its name: remove it there")
    have = existing | set(values)
    fix = "npx wrangler secret put {0}   (run in cloudflare/), or set {0}= in deploy/.env"
    for k in CF_REQUIRED:
        if k not in have:
            errors.append(f"{k} is missing: {fix.format(k)}")
    if not any(k.startswith(TOKEN_PREFIX) for k in have) and "ANTHROPIC_API_KEY" not in have:
        errors.append("no Claude login for agent steps: set at least one AGENT_RUNNER_TOKEN_<NAME> (`claude setup-token`) "
                      "or ANTHROPIC_API_KEY: " + fix.format("AGENT_RUNNER_TOKEN_MAIN"))
    if "BACKEND_URL" in existing or "BACKEND_URL" in plain_vars:
        errors.append("BACKEND_URL is set on the Worker, so /api/* would go there instead of the backend container "
                      "(it is only a local-dev override). Remove it: npx wrangler secret delete BACKEND_URL (in cloudflare/)")
    for k in ("ELEVENLABS_API_KEY", "ELEVENLABS_AGENT_ID"):
        if k not in have:
            notes.append(f"{k} not set: voice stays off until it is (see deploy/cloudflare.md, voice)")
    if "MACRAE_TOOL_SECRET" in values and len(values["MACRAE_TOOL_SECRET"]) < 16:
        notes.append("MACRAE_TOOL_SECRET is shorter than 16 characters; anyone who guesses it can start runs")
    for k in sorted(have & set(names)):
        notes.append(f"secret {k}: " + ("from deploy/.env, uploaded with this deploy" if k in values else "on the Worker"))
    return values, errors, notes


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="deploy/.env helpers")
    sub = ap.add_subparsers(dest="cmd", required=True)
    g = sub.add_parser("get"); g.add_argument("file"); g.add_argument("key"); g.add_argument("default", nargs="?", default="")
    e = sub.add_parser("export"); e.add_argument("file")
    c = sub.add_parser("check"); c.add_argument("file")
    r = sub.add_parser("render"); r.add_argument("file"); r.add_argument("--domain", required=True)
    r.add_argument("--data-dir", required=True)
    w = sub.add_parser("worker-secrets"); w.add_argument("file"); w.add_argument("--state", default="")
    w.add_argument("--wrangler-dir", default="cloudflare"); w.add_argument("--dry-run", action="store_true")
    cf = sub.add_parser("cf-secrets"); cf.add_argument("file"); cf.add_argument("--existing", default="[]")
    cf.add_argument("--out", required=True); cf.add_argument("--wrangler-dir", default="cloudflare")
    for p in (c, r, cf):
        p.add_argument("--modal-toml", default=str(Path.home() / ".modal.toml"))
    a = ap.parse_args(argv)

    if a.cmd == "get":
        print(get(load(a.file), a.key, a.default))
        return 0
    if a.cmd == "export":
        print("\n".join(export_lines(load(a.file))))
        return 0
    if a.cmd == "check":
        if not Path(a.file).exists():
            print(f"error: {a.file} not found (cp deploy/env.example {a.file} and fill it in)", file=sys.stderr)
            return 1
        errors, warnings = check(load(a.file), Path(a.modal_toml))
        for w_ in warnings:
            print(f"warning: {w_}", file=sys.stderr)
        for e in errors:
            print(f"error: {e}", file=sys.stderr)
        return 1 if errors else 0
    if a.cmd == "render":
        sys.stdout.write(render(Path(a.file).read_text(), domain=a.domain, data_dir=a.data_dir,
                                modal_toml=Path(a.modal_toml)))
        return 0
    if a.cmd == "cf-secrets":
        import json
        try:
            listed = json.loads(a.existing or "[]")
        except ValueError:
            listed = []
        existing = {x.get("name") if isinstance(x, dict) else str(x) for x in listed if x} if isinstance(listed, list) else set()
        values, errors, notes = cf_secrets(load(a.file), existing, wrangler_vars(Path(a.wrangler_dir)),
                                           Path(a.modal_toml))
        for n in notes:
            print(f"  {n}", file=sys.stderr)
        if errors:
            print("Missing or conflicting secrets, nothing was deployed:", file=sys.stderr)
            for e in errors:
                print(f"  - {e}", file=sys.stderr)
            return 1
        fd = os.open(a.out, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            json.dump(values, f)
        return 0
    if a.cmd == "worker-secrets":
        wdir = Path(a.wrangler_dir)
        secrets, msgs = worker_secrets(load(a.file), load(a.state) if a.state else {}, wrangler_vars(wdir))
        for m in msgs:
            print(m, file=sys.stderr)
        for k, v in secrets.items():
            print(f"wrangler secret put {k}", file=sys.stderr)
            if a.dry_run:
                continue
            r_ = subprocess.run(["npx", "--yes", "wrangler@4", "secret", "put", k], input=v, text=True, cwd=wdir)
            if r_.returncode:
                print(f"error: wrangler secret put {k} failed (deploy the Worker first: make deploy-web)",
                      file=sys.stderr)
                return r_.returncode
        return 0
    return 2


if __name__ == "__main__":
    sys.exit(main())
