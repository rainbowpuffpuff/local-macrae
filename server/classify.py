"""Tool call → TraceEvent type and a short human title (contract: "Mapping rules for trajectory tool calls")."""

from __future__ import annotations

import json
import re
import shlex
from dataclasses import dataclass
from typing import Any, Optional

from . import catalog

DETAIL_MAX = 600
TITLE_MAX = 90

PAPER_PATH_RE = re.compile(r"(^|[/\s\"'])papers?(/|[\s\"']|$)|paper|\.pdf\b|doi|10\.\d{4,9}[/_]|publication|article",
                           re.I)
PAPER_URL_RE = re.compile(r"doi\.org|arxiv|chemrxiv|biorxiv|pubs\.|/doi/|sciencedirect|springer|wiley|nature\.com|"
                          r"rsc\.org|aip\.org|aps\.org|pnas|pubmed|ncbi\.nlm|openalex|crossref|semanticscholar|"
                          r"\.pdf\b|uochb\.cz", re.I)
SEARCH_CMD_RE = re.compile(r"\brag\b[^|;&]*\bsearch\b|search_papers", re.I)
INSTALL_CMD_RE = re.compile(r"\b(pip3?|uv|conda|mamba|micromamba|apt(-get)?|npm|brew|dnf|yum)\s+(\S+\s+)?"
                            r"(install|add|create|sync)\b", re.I)
CALC_PROGRAMS = ["xtb", "crest", "psi4", "openmm", "pyscf", "orca", "gmx", "gromacs", "cp2k", "lammps", "rdkit",
                 "ase", "scipy", "numpy", "mdtraj", "packmol", "obabel"]
CALC_CMD_RE = re.compile(r"\b(python[0-9.]*|" + "|".join(CALC_PROGRAMS) + r")\b", re.I)
TRIVIAL_CMD_RE = re.compile(r"^\s*(which|type|command -v)\b|--version\b|\s-V\s*$", re.I)
PAPER_READ_CMD_RE = re.compile(r"^\s*(cat|head|tail|less|more|pdftotext|pdfinfo|grep|rg|sed|awk|ls|jq|wc)\b", re.I)
DECIMAL_RE = re.compile(r"(?<![\w.])[-+]?\d+\.\d{3,}(?:[eE][-+]?\d+)?")
UNIT_RE = re.compile(r"\b(kcal|kj|hartree|eh|ev|energy|enthalpy|entropy|gradient|rmsd|density|temperature|"
                     r"angstrom|bohr|ps|ns|fs|mol)\b|Å", re.I)
PAST = {"run": "Ran", "running": "Ran", "compute": "Computed", "computing": "Computed", "calculate": "Calculated",
        "calculating": "Calculated", "execute": "Executed", "check": "Checked", "install": "Installed",
        "create": "Created", "test": "Tested", "list": "Listed", "search": "Searched", "read": "Read",
        "build": "Built", "optimize": "Optimized", "simulate": "Simulated", "verify": "Verified",
        "show": "Showed", "print": "Printed", "extract": "Extracted", "convert": "Converted", "write": "Wrote",
        "generate": "Generated", "set": "Set", "setup": "Set up", "make": "Made", "find": "Found",
        "count": "Counted", "parse": "Parsed", "plot": "Plotted", "analyze": "Analyzed", "view": "Viewed",
        "pull": "Pulled", "copy": "Copied", "collect": "Collected", "prepare": "Prepared", "fetch": "Fetched",
        "download": "Downloaded", "summarize": "Summarized"}


@dataclass
class Classified:
    type: str
    title: str
    detail: str = ""
    citation: Optional[dict] = None
    needs_output: bool = False  # don't emit before the call's output is known (Bash: calc depends on output)


def clip(s: Any, n: int = DETAIL_MAX) -> str:
    s = "" if s is None else (s if isinstance(s, str) else json.dumps(s, ensure_ascii=False, default=str))
    s = s.strip()
    return s if len(s) <= n else s[: n - 1].rstrip() + "…"


def one_line(s: str, n: int = TITLE_MAX) -> str:
    return clip(re.sub(r"\s+", " ", s or ""), n)


def short_path(p: str) -> str:
    p = str(p or "").strip()
    for pre in ("/app/", "/workspace/", "/root/", "/home/agent/"):
        if p.startswith(pre):
            p = p[len(pre):]
    parts = [x for x in p.split("/") if x]
    return "/".join(parts[-2:]) if len(parts) > 2 else (p or "?")


def first_sentence(text: str, n: int = TITLE_MAX) -> str:
    t = re.sub(r"[#*`>_]+", "", text or "").strip()
    t = re.sub(r"\s+", " ", t)
    m = re.match(r"(.+?[.!?:])(\s|$)", t)
    return one_line(m.group(1) if m and len(m.group(1)) >= 12 else t, n)


def past_tense(desc: str) -> str:
    desc = one_line(desc, 70).rstrip(".")
    if not desc:
        return "Ran a command"
    w, _, rest = desc.partition(" ")
    if w.lower() in PAST:
        return (PAST[w.lower()] + (" " + rest if rest else "")).strip()
    if w.lower().endswith("ed"):
        return desc[0].upper() + desc[1:]
    return "Ran " + desc[0].lower() + desc[1:]


def program_of(cmd: str) -> str:
    low = cmd.lower()
    for p in CALC_PROGRAMS:
        if re.search(rf"\b{re.escape(p)}\b", low):
            return "gromacs" if p == "gmx" else p
    m = re.search(r"\bpython[0-9.]*\b", low)
    if m:
        return "python"
    try:
        return shlex.split(cmd)[0].rsplit("/", 1)[-1]
    except (ValueError, IndexError):
        return ""


def command_label(cmd: str) -> str:
    """'python3 run_md.py --ps 10' → 'run_md.py', 'python -m mod' → 'mod', 'python -c …' → 'a Python snippet'."""
    try:
        toks = shlex.split(cmd)
    except ValueError:
        toks = cmd.split()
    for i, t in enumerate(toks):
        if re.fullmatch(r"(\S*/)?python[0-9.]*", t):
            nxt = toks[i + 1] if i + 1 < len(toks) else ""
            if nxt == "-c":
                return "a Python snippet"
            if nxt == "-m" and i + 2 < len(toks):
                return toks[i + 2]
            if nxt.endswith(".py"):
                return nxt.rsplit("/", 1)[-1]
            break
    return one_line(cmd, 60)


def looks_computed(output: str) -> bool:
    """'any command whose output contains numbers from a computation'."""
    if not output:
        return False
    n = len(DECIMAL_RE.findall(output[:5000]))
    return n >= 3 or (n >= 1 and bool(UNIT_RE.search(output[:5000])))


def search_query(cmd: str) -> str:
    try:
        toks = shlex.split(cmd)
    except ValueError:
        toks = cmd.split()
    for i, t in enumerate(toks):
        if t == "search" and i + 1 < len(toks):
            rest = [x for x in toks[i + 1:] if not x.startswith("-")]
            if rest:
                return rest[0]
    return ""


def _citation_from(*texts: str) -> Optional[dict]:
    for t in texts:
        c = catalog.citation_for_doi(catalog.find_doi(t or ""))
        if c:
            return c
    return None


def _dur(seconds: Optional[float]) -> str:
    if seconds is None or seconds < 0.05:
        return ""  # unknown, or timestamps too coarse to tell
    return f"{seconds:.1f} s" if seconds < 60 else f"{seconds / 60:.1f} min"


def classify_command(cmd: str, output: Optional[str], description: str = "",
                     seconds: Optional[float] = None) -> Classified:
    """A shell command (Bash tool call or a flow script step)."""
    cmd = (cmd or "").strip()
    shown = one_line(cmd, 60)
    out = output or ""
    detail = clip(f"$ {cmd}" + (f"\n→ {out.strip()}" if out.strip() else ""))
    if SEARCH_CMD_RE.search(cmd):
        q = search_query(cmd)
        return Classified("search", f"Searched papers for “{one_line(q, 60)}”" if q else "Searched the papers",
                          detail)
    if INSTALL_CMD_RE.search(cmd):
        return Classified("status", one_line(past_tense(description)) if description else f"Installed: {shown}",
                          detail)
    if PAPER_READ_CMD_RE.match(cmd) and PAPER_PATH_RE.search(cmd):
        return Classified("read", past_tense(description) if description else f"Read {shown}", detail,
                          _citation_from(cmd, out[:3000]))
    calc_by_cmd = bool(CALC_CMD_RE.search(cmd)) and not TRIVIAL_CMD_RE.search(cmd)
    if calc_by_cmd or (output is not None and looks_computed(out)):
        prog = program_of(cmd)
        what = past_tense(description) if description else f"Ran {command_label(cmd)}"
        extra = ", ".join(x for x in (prog, _dur(seconds)) if x)
        return Classified("calc", one_line(what, 70) + (f" ({extra})" if extra else ""), detail)
    title = past_tense(description) if description else f"Ran {shown}"
    return Classified("status", one_line(title), detail, needs_output=output is None)


def _args(a: Any) -> dict:
    if isinstance(a, dict):
        return a
    if isinstance(a, str):
        try:
            v = json.loads(a)
            return v if isinstance(v, dict) else {"input": a}
        except ValueError:
            return {"input": a}
    return {}


def classify_tool(name: str, arguments: Any, output: Optional[str], seconds: Optional[float] = None) -> Classified:
    """One trajectory tool call. `output` None = not known yet (call still running)."""
    a = _args(arguments)
    n = (name or "").strip()
    low = n.lower()
    out = output or ""
    out_clip = clip(out, 400)

    if low in ("bash", "shell", "run_command", "execute_command", "terminal", "bashoutput") or low.endswith("__bash"):
        cmd = str(a.get("command") or a.get("cmd") or a.get("input") or "")
        c = classify_command(cmd, output, str(a.get("description") or ""), seconds)
        if c.type in ("calc", "status") and output is None:
            c.needs_output = True  # the output decides calc vs status, and the title wants a duration
        return c

    if "search_papers" in low or re.search(r"(^|_)rag(_|$)", low) or low.endswith("search_papers"):
        q = str(a.get("query") or a.get("q") or a.get("input") or "")
        return Classified("search", f"Searched papers for “{one_line(q, 60)}”" if q else "Searched the papers",
                          out_clip)

    if low in ("read", "view", "read_file", "notebookread", "str_replace_based_edit_tool") and \
            str(a.get("command") or "view") == "view":
        path = str(a.get("file_path") or a.get("path") or a.get("notebook_path") or "")
        if PAPER_PATH_RE.search(path):
            return Classified("read", f"Read {short_path(path)}", out_clip, _citation_from(path, out[:3000]))
        return Classified("status", f"Opened {short_path(path)}", out_clip)

    if low in ("grep", "glob", "ls", "list_files", "search_files", "find"):
        pat = str(a.get("pattern") or a.get("glob") or a.get("query") or "")
        path = str(a.get("path") or a.get("include") or "")
        target = f"{pat} {path}"
        if PAPER_PATH_RE.search(target):
            verb = "Listed" if low in ("glob", "ls", "list_files") else "Searched"
            where = short_path(path) if path else "papers"
            title = f"{verb} {where} for “{one_line(pat, 50)}”" if verb == "Searched" else \
                f"Listed {one_line(pat or where, 60)}"
            return Classified("read", title, out_clip, _citation_from(target))
        if low in ("glob", "ls", "list_files"):
            return Classified("status", f"Listed {one_line(pat or path or '.', 60)}", out_clip)
        return Classified("status", f"Searched code for “{one_line(pat, 50)}”", out_clip)

    if low in ("webfetch", "fetch", "web_fetch"):
        url = str(a.get("url") or "")
        host = re.sub(r"^https?://", "", url)
        if PAPER_URL_RE.search(url):
            return Classified("read", f"Read {one_line(host, 70)}", clip(f"{url}\n{a.get('prompt') or ''}"),
                              _citation_from(url))
        return Classified("status", f"Fetched {one_line(host, 70)}", clip(url))

    if low in ("websearch", "web_search"):
        q = str(a.get("query") or "")
        return Classified("search", f"Searched the web for “{one_line(q, 60)}”", out_clip)

    if low in ("write", "write_file", "create_file"):
        path = str(a.get("file_path") or a.get("path") or "")
        return Classified("write", f"Wrote {short_path(path)}", clip(a.get("content") or a.get("text") or ""))

    if low in ("edit", "multiedit", "notebookedit", "str_replace", "edit_file", "apply_patch") or \
            low == "str_replace_based_edit_tool":
        path = str(a.get("file_path") or a.get("path") or a.get("notebook_path") or "")
        new = a.get("new_string") or a.get("new_source") or a.get("new_str") or \
            "\n".join(str(e.get("new_string", "")) for e in a.get("edits") or [] if isinstance(e, dict))
        return Classified("write", f"Edited {short_path(path)}", clip(new))

    if low == "todowrite":
        todos = [t for t in a.get("todos") or [] if isinstance(t, dict)]
        items = [str(t.get("content") or t.get("activeForm") or "") for t in todos]
        return Classified("think", one_line("Planned: " + "; ".join(i for i in items if i)) if items else "Planned",
                          clip("\n".join(f"- [{t.get('status', '')}] {i}" for t, i in zip(todos, items))))

    if low in ("task", "agent"):
        return Classified("think", one_line(f"Delegated: {a.get('description') or a.get('prompt') or ''}"),
                          clip(a.get("prompt") or ""))

    return Classified("status", f"Used {n or 'a tool'}", clip(json.dumps(a, ensure_ascii=False, default=str)))


# ── citations out of search output ──────────────────────────────────────────


def _parse_json_loose(text: str) -> Any:
    text = (text or "").strip()
    if not text:
        return None
    try:
        return json.loads(text)
    except ValueError:
        pass
    for opener in ("{", "["):
        i = text.find(opener)
        if i >= 0:
            try:
                return json.loads(text[i:])
            except ValueError:
                continue
    return None


def normalize_citation(c: Any, passage_text: str = "") -> Optional[dict]:
    if not isinstance(c, dict):
        return None
    year = c.get("year")
    try:
        year = int(year) if year not in (None, "") else None
    except (TypeError, ValueError):
        year = None
    page = c.get("page")
    try:
        page = int(page) if page not in (None, "") else None
    except (TypeError, ValueError):
        page = None
    doi = str(c.get("doi") or "")
    out = {"key": str(c.get("key") or ""), "title": str(c.get("title") or ""), "authors": str(c.get("authors") or ""),
           "year": year, "journal": str(c.get("journal") or ""), "doi": doi, "page": page,
           "url": str(c.get("url") or (f"https://doi.org/{doi}" if doi else "")),
           "quote": clip(c.get("quote") or passage_text, 240)}
    if not (out["title"] or out["doi"]):
        return None
    return out


def citations_in_output(text: str) -> list[dict]:
    """Citations from `python -m rag search` / search_papers output, if it is JSON (passages or citations)."""
    data = _parse_json_loose(text)
    if isinstance(data, dict):
        data = data.get("passages") or data.get("citations") or data.get("results") or []
    if not isinstance(data, list):
        return []
    out, seen = [], set()
    for item in data:
        if not isinstance(item, dict):
            continue
        cit = normalize_citation(item["citation"], str(item.get("text") or "")) if isinstance(
            item.get("citation"), dict) else normalize_citation(item, str(item.get("text") or ""))
        if not cit:
            continue
        k = (cit["doi"] or cit["title"], cit["page"])
        if k in seen:
            continue
        seen.add(k)
        out.append(cit)
    return out


def cite_title(c: dict) -> str:
    first = (c.get("authors") or "").split(";")[0].split(",")[0].strip()
    names = [w for w in first.split() if not re.fullmatch(r"(\w\.|\w\.-\w\.)+", w)]  # drop initials
    surname = " ".join(names) if names else first
    who = " ".join(x for x in (surname, str(c.get("year") or "")) if x)
    page = f", p. {c['page']}" if c.get("page") else ""
    return one_line(f"Cited {who}{page}: {c.get('title') or c.get('doi')}" if who else
                    f"Cited {c.get('title') or c.get('doi')}{page}")
