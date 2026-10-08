"""AUTHORITY: what any capability may do. Constants in code, never in the registry; no capability can change them.

Capabilities (tools, environment images, writing templates) are created by the agent and evolve run over run.
Their authority does not: every limit below is a frozen constant in this file, which is part of the backend's code
and is not under $AGENT_RUNNER_HOME. Nothing the agent writes is read back as policy. The installer
(server/installer.py) checks each manifest against these limits and records `rejected` with the reason when a
manifest asks for more; the export to a run (evolve.capabilities.export) and agent_runner only ever mount what the
installer accepted, inside the Harbor sandbox.

    check_manifest(manifest) -> list[str]     reasons to reject (empty = within the policy)
    scan_text(name, text) -> list[str]        static red flags in a capability file (secret names, the registry)
    describe() -> dict                        the policy as shown on the page's "Authority" card
    POLICY_SHA256                             hash of the policy itself, recorded with every install
"""

from __future__ import annotations

import hashlib
import json
import re
from types import MappingProxyType
from typing import Any, Mapping

# ── the limits ──────────────────────────────────────────────────────────────

# Where a capability may run. Only inside the Harbor sandbox (Modal or local Docker), never on the backend host:
# the host only hashes, reads and copies capability files; it never executes them.
RUNS_IN = "harbor-sandbox"
ALLOWED_RUNTIMES = frozenset({"harbor-sandbox"})

# What a capability may be.
KINDS = frozenset({"tool", "image", "writing"})

# Environment variables a capability may read. Nothing else is passed to tools, and none of these carry secrets.
ENV_ALLOWLIST = frozenset({
    "PATH", "HOME", "USER", "LANG", "LC_ALL", "TZ", "TMPDIR", "PWD", "TERM",
    "PYTHONPATH", "PYTHONUNBUFFERED", "PYTHONHASHSEED", "MPLBACKEND", "MPLCONFIGDIR",
    "OMP_NUM_THREADS", "MKL_NUM_THREADS", "OPENBLAS_NUM_THREADS", "NUMEXPR_MAX_THREADS",
    "CUDA_VISIBLE_DEVICES", "GMX_MAXBACKUP",
})
# Names that are secrets whatever a manifest says (checked case-insensitively, as whole words or parts).
SECRET_NAME_RE = re.compile(
    r"(SECRET|TOKEN|PASSWORD|PASSWD|API[_-]?KEY|ACCESS[_-]?KEY|PRIVATE[_-]?KEY|CREDENTIAL|AUTH|COOKIE|SESSION)"
    r"|^(ANTHROPIC|CLAUDE|MODAL|AWS|GCP|GOOGLE|AZURE|OPENAI|ELEVENLABS|CLOUDFLARE|CF|R2|GITHUB|GH|HF|MACRAE|"
    r"AGENT_RUNNER)_", re.I)

# Network hosts a capability may reach. Tools at run time: none (they compute, they don't fetch). Image
# capabilities may install pinned packages while their image is built, from these package indexes only.
TOOL_NETWORK = frozenset()
BUILD_NETWORK = frozenset({
    "pypi.org", "files.pythonhosted.org",
    "conda.anaconda.org", "repo.anaconda.com", "conda-forge.org",
    "archive.ubuntu.com", "security.ubuntu.com", "deb.debian.org",
})
NETWORK_BY_KIND: Mapping[str, frozenset] = MappingProxyType({
    "tool": TOOL_NETWORK, "writing": TOOL_NETWORK, "image": BUILD_NETWORK,
})

# A capability never writes the registry. In the sandbox it gets a copy (Harbor uploads it); on the host the
# registry files are read-only and only the installer adds to it.
REGISTRY_WRITE = False

# Time limits (seconds).
MAX_RUNTIME_S = 900          # one tool invocation
MAX_TEST_RUNTIME_S = 600     # the capability's whole test suite
MAX_BUILD_S = 1200           # building an image capability on Modal

# Size limits.
MAX_FILES = 24
MAX_FILE_BYTES = 256 * 1024
MAX_TOTAL_BYTES = 1024 * 1024
MAX_TESTS = 40
MIN_TESTS = 1

# Shape.
NAME_RE = re.compile(r"^[a-z][a-z0-9-]{1,47}$")
FILE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,80}(/[A-Za-z0-9][A-Za-z0-9._-]{0,80}){0,2}$")
FILE_EXT = frozenset({".py", ".sh", ".md", ".txt", ".json", ".yaml", ".yml", ".toml", ".csv", ".tsv", ".dat",
                      ".mdp", ".top", ".itp", ".gro", ".pdb", ".xyz", ".tex", ".bib", ".cfg", ".ini", ""})
IMAGE_FILES = frozenset({"Dockerfile.fragment", "requirements.txt"})
# Dockerfile instructions an image fragment may use. No FROM (the base is ours), no COPY/ADD (we copy the
# capability's own files to /opt/macrae-cap/<name>/ before the fragment), no USER/ENTRYPOINT/CMD/SHELL that would
# change how the agent runs, no ARG (build args could carry secrets).
FRAGMENT_INSTRUCTIONS = frozenset({"RUN", "ENV", "WORKDIR", "LABEL"})
IMAGE_CAP_DIR = "/opt/macrae-cap"
PIN_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._\-\[\],]*\s*==\s*[A-Za-z0-9.+!_\-]+(\s*;.*)?$")

# Static red flags in capability files: a tool that reads the registry, the backend's state, or a secret by name.
# (Defence in depth: the sandbox gets no secrets and no registry anyway.)
FORBIDDEN_TEXT = (
    (re.compile(r"evolve/capabilities|AGENT_RUNNER_HOME|MACRAE_SERVER_DATA|/\.local/share/agent-runner"),
     "touches the capability registry or the backend's state"),
    (re.compile(r"\b(ANTHROPIC_API_KEY|CLAUDE_CODE_OAUTH_TOKEN|MACRAE_TOOL_SECRET|MACRAE_LIVE_TOKEN|MODAL_TOKEN_ID|"
                r"MODAL_TOKEN_SECRET|AGENT_RUNNER_TOKEN_\w*|AWS_SECRET_ACCESS_KEY|ELEVENLABS_API_KEY)\b"),
     "reads a secret"),
    (re.compile(r"/proc/\d*/?environ|/proc/self/environ"), "reads another process's environment"),
)


# ── checks ──────────────────────────────────────────────────────────────────


def is_secret_name(name: str) -> bool:
    return bool(SECRET_NAME_RE.search(str(name or "")))


def _list(v: Any) -> list:
    if v is None:
        return []
    return list(v) if isinstance(v, (list, tuple)) else [v]


def check_manifest(m: Any) -> list[str]:
    """Reasons the manifest is outside the policy (empty = OK). Pure: reads nothing but the manifest."""
    if not isinstance(m, dict):
        return ["the manifest is not a JSON object"]
    out: list[str] = []
    name = str(m.get("name") or "")
    if not NAME_RE.match(name):
        out.append(f"name {name!r} must be 2–48 characters: lowercase letters, digits and '-', starting with a letter")
    kind = str(m.get("kind") or "tool")
    if kind not in KINDS:
        out.append(f"kind {kind!r} is not one of {sorted(KINDS)}")
    if not str(m.get("purpose") or "").strip():
        out.append("purpose is empty")
    runtime = str(m.get("runs_in") or RUNS_IN)
    if runtime not in ALLOWED_RUNTIMES:
        out.append(f"runs_in {runtime!r}: capabilities run only inside the Harbor sandbox")
    perms = m.get("permissions") if isinstance(m.get("permissions"), dict) else {}
    if m.get("permissions") is not None and not isinstance(m.get("permissions"), dict):
        out.append("permissions must be an object")

    # secrets / environment
    env = _list(perms.get("env")) + _list(m.get("env"))
    for e in env:
        e = str(e)
        if is_secret_name(e):
            out.append(f"asks for the secret {e}: capabilities get no secrets")
        elif e not in ENV_ALLOWLIST:
            out.append(f"asks for the environment variable {e}, which is not on the allowlist")
    if perms.get("secrets") or m.get("secrets"):
        out.append("asks for secrets: capabilities get no secrets")

    # network
    allowed = NETWORK_BY_KIND.get(kind, TOOL_NETWORK)
    hosts = _list(perms.get("network")) + _list(m.get("network"))
    for h in hosts:
        h = str(h).strip().lower()
        if h in ("*", "any", "all", "internet", "public"):
            out.append(f"asks for unrestricted network ({h})")
        elif h not in allowed:
            out.append(f"asks for network host {h}, which is not on the policy list"
                       + (" (tools get no network)" if not allowed else ""))

    # registry / self-modification / host
    if perms.get("registry_write") or perms.get("write_registry") or m.get("registry_write"):
        out.append("asks to write the capability registry")
    if perms.get("host") or perms.get("run_on_host") or perms.get("docker") or perms.get("privileged"):
        out.append("asks to run outside the sandbox (host, docker or privileged)")
    if any(k in perms for k in ("policy", "authority")) or any(k in m for k in ("policy", "authority")):
        out.append("asks to change the policy: authority is fixed")

    # runtime
    for key, cap in (("max_runtime_s", MAX_RUNTIME_S), ("test_runtime_s", MAX_TEST_RUNTIME_S),
                     ("build_s", MAX_BUILD_S)):
        v = perms.get(key, m.get(key))
        if v is None:
            continue
        try:
            fv = float(v)
        except (TypeError, ValueError):
            out.append(f"{key} must be a number")
            continue
        if fv <= 0 or fv > cap:
            out.append(f"{key} = {v} is over the limit of {cap} s")

    # files
    files = m.get("files")
    if not isinstance(files, list) or not files:
        out.append("files is empty")
        files = []
    if len(files) > MAX_FILES:
        out.append(f"{len(files)} files, more than {MAX_FILES}")
    seen = set()
    for f in files:
        p = str(f.get("path") if isinstance(f, dict) else f)
        if not FILE_RE.match(p) or ".." in p.split("/"):
            out.append(f"file path {p!r} is not a plain relative path")
            continue
        ext = ("." + p.rsplit(".", 1)[1].lower()) if "." in p.rsplit("/", 1)[-1] else ""
        base = p.rsplit("/", 1)[-1]
        if ext not in FILE_EXT and base not in IMAGE_FILES:
            out.append(f"file {p} has a type capabilities can't ship ({ext or 'none'})")
        if p in seen:
            out.append(f"file {p} is listed twice")
        seen.add(p)
        if isinstance(f, dict) and f.get("sha256") is not None and not re.match(r"^[0-9a-f]{64}$", str(f["sha256"])):
            out.append(f"file {p} has a malformed sha256")

    # tests
    tests = m.get("tests")
    if not isinstance(tests, list) or len(tests) < MIN_TESTS:
        out.append("no tests: every capability ships tests that must pass in the sandbox")
        tests = []
    if len(tests) > MAX_TESTS:
        out.append(f"{len(tests)} tests, more than {MAX_TESTS}")
    for t in tests:
        if not isinstance(t, dict) or not str(t.get("cmd") or "").strip():
            out.append("each test needs a cmd")
            break

    if kind == "image":
        img = m.get("image") if isinstance(m.get("image"), dict) else {}
        if not img.get("fragment"):
            out.append("an image capability needs image.fragment (a Dockerfile fragment file)")
        if not str(img.get("smoke") or "").strip():
            out.append("an image capability needs image.smoke (the command that proves the image works)")
        if not img.get("requirements"):
            out.append("an image capability needs image.requirements (a pinned requirements file)")
    else:
        if kind == "tool" and not str(m.get("entrypoint") or "").strip():
            out.append("a tool needs an entrypoint (the file to run)")
    return sorted(set(out), key=out.index)


def check_fragment(text: str) -> list[str]:
    """A Dockerfile fragment for an image capability: only the instructions we allow, no remote ADD, no secrets."""
    out = []
    joined = re.sub(r"\\\s*\n", " ", text or "")
    for ln in joined.splitlines():
        s = ln.strip()
        if not s or s.startswith("#"):
            continue
        ins = s.split(None, 1)[0].upper()
        if ins not in FRAGMENT_INSTRUCTIONS:
            out.append(f"Dockerfile instruction {ins} is not allowed in a fragment (allowed: "
                       f"{', '.join(sorted(FRAGMENT_INSTRUCTIONS))})")
        if ins == "ENV":
            for name in re.findall(r"([A-Za-z_][A-Za-z0-9_]*)\s*=", s[3:]) or s[3:].split()[:1]:
                if is_secret_name(name):
                    out.append(f"ENV {name} looks like a secret")
        for host in re.findall(r"https?://([A-Za-z0-9.-]+)", s):
            if host.lower() not in BUILD_NETWORK:
                out.append(f"fetches from {host}, which is not on the policy list")
    return sorted(set(out), key=out.index)


def check_requirements(text: str) -> list[str]:
    """Every requirement pinned with == (version breakage is what image capabilities exist to stop)."""
    out = []
    for ln in (text or "").splitlines():
        s = ln.split("#", 1)[0].strip()
        if not s:
            continue
        if s.startswith("-"):
            if s.startswith(("--index-url", "--extra-index-url", "-i ", "-f ", "--find-links")):
                hosts = re.findall(r"https?://([A-Za-z0-9.-]+)", s)
                bad = [h for h in hosts if h.lower() not in BUILD_NETWORK]
                if bad or not hosts:
                    out.append(f"package index {s} is not on the policy list")
            else:
                out.append(f"requirements option {s.split()[0]} is not allowed")
            continue
        if not PIN_RE.match(s):
            out.append(f"requirement {s!r} is not pinned (use name==version)")
    return out


def scan_text(path: str, text: str) -> list[str]:
    """Static red flags in one capability file."""
    out = []
    for rx, why in FORBIDDEN_TEXT:
        m = rx.search(text or "")
        if m:
            out.append(f"{path} {why} ({m.group(0)})")
    return out


def sandbox_env(env: Mapping[str, str]) -> dict[str, str]:
    """The environment a capability may see: the allowlist, minus anything that looks like a secret."""
    return {k: v for k, v in env.items() if k in ENV_ALLOWLIST and not is_secret_name(k)}


def network_for(kind: str) -> list[str]:
    return sorted(NETWORK_BY_KIND.get(kind, TOOL_NETWORK))


def describe() -> dict:
    """The fixed limits, for GET /api/capabilities ("authority") and the page's Authority card."""
    return {
        "runs_in": RUNS_IN,
        "rules": [
            "Capabilities run only inside the Harbor sandbox (Modal), never on the backend.",
            "They get no secrets: only an allowlist of plain environment variables.",
            "Tools get no network; image builds may reach only the package indexes on the policy list.",
            "No capability can write the registry; only the installer adds to it, after its tests pass.",
            f"Each tool call is limited to {MAX_RUNTIME_S // 60} min, a test suite to {MAX_TEST_RUNTIME_S // 60} min, "
            f"an image build to {MAX_BUILD_S // 60} min.",
            "These limits are constants in the backend's code; no capability can change them.",
        ],
        "env_allowlist": sorted(ENV_ALLOWLIST),
        "network": {k: sorted(v) for k, v in NETWORK_BY_KIND.items()},
        "registry_write": REGISTRY_WRITE,
        "max_runtime_s": MAX_RUNTIME_S, "max_test_runtime_s": MAX_TEST_RUNTIME_S, "max_build_s": MAX_BUILD_S,
        "max_files": MAX_FILES, "max_file_bytes": MAX_FILE_BYTES, "max_total_bytes": MAX_TOTAL_BYTES,
        "kinds": sorted(KINDS),
        "policy_sha256": POLICY_SHA256,
        "source": "server/policy.py",
    }


def _policy_fingerprint() -> str:
    data = {"runs_in": sorted(ALLOWED_RUNTIMES), "kinds": sorted(KINDS), "env": sorted(ENV_ALLOWLIST),
            "secret_re": SECRET_NAME_RE.pattern, "network": {k: sorted(v) for k, v in NETWORK_BY_KIND.items()},
            "registry_write": REGISTRY_WRITE, "limits": [MAX_RUNTIME_S, MAX_TEST_RUNTIME_S, MAX_BUILD_S, MAX_FILES,
                                                         MAX_FILE_BYTES, MAX_TOTAL_BYTES, MAX_TESTS, MIN_TESTS],
            "fragment": sorted(FRAGMENT_INSTRUCTIONS), "forbidden": [rx.pattern for rx, _ in FORBIDDEN_TEXT]}
    return hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()


POLICY_SHA256 = _policy_fingerprint()
