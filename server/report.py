"""The run's trace as one self-contained HTML page (GET /api/runs/{id}/report.html, and report.html in trace.zip).

No scripts and no external files: it opens offline from the zip, and the Worker's Content-Security-Policy (which allows
no inline scripts) doesn't get in its way. Expanding a tool call or a log uses <details>. Light and dark follow the
system, with the page's colours (web/style.css).

`render(data)` takes what traces.gather() returns (already redacted).
"""

from __future__ import annotations

import html
import json
import re
import time
from typing import Any, Optional

ICONS = {"read": "📄", "search": "🔎", "calc": "🧮", "write": "✍️", "think": "💭", "result": "✅", "error": "⚠️",
         "status": "•", "cite": "📚", "plan": "🧭"}
ARGS_MAX = 6000
OUTPUT_MAX = 8000
TEXT_MAX = 12000
# flow vars that are engine plumbing or shown in their own section, not the run's inputs (as trace.HIDDEN_START_VARS)
PLUMBING_VARS = {"lessons", "tools_dir", "plan", "plan_why", "python", "environment", "task_id", "task_title"}


def e(s: Any) -> str:
    return html.escape("" if s is None else str(s), quote=True)


def clip(s: Any, n: int) -> str:
    s = "" if s is None else str(s)
    return s if len(s) <= n else s[:n] + f"\n… ({len(s) - n:,} more characters in the zip)"


def when(t: Any) -> str:
    try:
        return time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime(float(t)))
    except (TypeError, ValueError):
        return "—"


def dur(s: Any) -> str:
    try:
        s = float(s)
    except (TypeError, ValueError):
        return "—"
    if s < 60:
        return f"{s:.1f} s" if s < 10 else f"{s:.0f} s"
    if s < 3600:
        return f"{int(s // 60)} min {int(s % 60):02d} s"
    return f"{int(s // 3600)} h {int(s % 3600 // 60):02d} min"


def offset(t: Any, t0: Optional[float]) -> str:
    try:
        d = max(0.0, float(t) - float(t0))
    except (TypeError, ValueError):
        return ""
    return f"+{int(d // 3600)}:{int(d % 3600 // 60):02d}:{int(d % 60):02d}" if d >= 3600 else \
        f"+{int(d // 60):02d}:{int(d % 60):02d}"


def usd(v: Any) -> str:
    try:
        v = float(v)
    except (TypeError, ValueError):
        return "—"
    if v == 0:
        return "$0.00"
    return f"${v:.4f}" if v < 0.01 else f"${v:.3f}" if v < 1 else f"${v:,.2f}"


def num(v: Any) -> str:
    try:
        return f"{int(v):,}"
    except (TypeError, ValueError):
        return "—"


def size(n: Any) -> str:
    try:
        n = float(n)
    except (TypeError, ValueError):
        return ""
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return ""


def pre(s: Any, n: int = TEXT_MAX, cls: str = "") -> str:
    return f'<pre class="{cls}">{e(clip(s, n))}</pre>' if s not in (None, "") else ""


def pretty(v: Any) -> str:
    if isinstance(v, str):
        return v
    try:
        return json.dumps(v, indent=1, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        return str(v)


def doi_url(c: dict) -> str:
    url = str(c.get("url") or "")
    if not url and c.get("doi"):
        url = "https://doi.org/" + str(c["doi"])
    return url if re.match(r"^https?://", url) else ""


# ── a small, safe Markdown subset for the manuscript ────────────────────────

def _inline(s: str) -> str:
    s = e(s)
    s = re.sub(r"`([^`]+)`", r"<code>\1</code>", s)
    s = re.sub(r"\*\*([^*]+)\*\*", r"<b>\1</b>", s)
    s = re.sub(r"(?<![*\w])\*([^*\n]+)\*(?![*\w])", r"<i>\1</i>", s)
    s = re.sub(r"\[([^\]]+)\]\((https?://[^)\s]+)\)", r'<a href="\2" rel="noopener">\1</a>', s)
    s = re.sub(r"\[(\d+(?:\s*[,–-]\s*\d+)*)\]", r'<span class="ref">[\1]</span>', s)
    return s


def markdown(text: str) -> str:
    out: list[str] = []
    para: list[str] = []
    lst: Optional[str] = None
    code: Optional[list[str]] = None

    def flush() -> None:
        nonlocal lst
        if para:
            out.append("<p>" + _inline(" ".join(para)) + "</p>")
            para.clear()
        if lst:
            out.append(f"</{lst}>")
            lst = None

    for ln in text.splitlines():
        if code is not None:
            if ln.strip().startswith("```"):
                out.append("<pre>" + e("\n".join(code)) + "</pre>")
                code = None
            else:
                code.append(ln)
            continue
        if ln.strip().startswith("```"):
            flush()
            code = []
            continue
        m = re.match(r"^(#{1,6})\s+(.*)$", ln)
        if m:
            flush()
            lvl = min(6, len(m.group(1)) + 2)
            out.append(f"<h{lvl}>{_inline(m.group(2))}</h{lvl}>")
            continue
        m = re.match(r"^\s*([-*+]|\d+[.)])\s+(.*)$", ln)
        if m:
            if para:
                out.append("<p>" + _inline(" ".join(para)) + "</p>")
                para.clear()
            kind = "ol" if m.group(1)[0].isdigit() else "ul"
            if lst != kind:
                if lst:
                    out.append(f"</{lst}>")
                out.append(f"<{kind}>")
                lst = kind
            out.append(f"<li>{_inline(m.group(2))}</li>")
            continue
        if not ln.strip():
            flush()
            continue
        if lst:
            out.append(f"</{lst}>")
            lst = None
        para.append(ln.strip())
    if code is not None:
        out.append("<pre>" + e("\n".join(code)) + "</pre>")
    flush()
    return "\n".join(out)


# ── sections ────────────────────────────────────────────────────────────────

def _tile(label: str, value: str, sub: str = "") -> str:
    return f'<div class="tile"><span>{e(label)}</span><b>{e(value)}</b>{f"<small>{e(sub)}</small>" if sub else ""}</div>'


def summary_html(d: dict) -> str:
    r = d.get("run") or {}
    c = d.get("costs") or {}
    tk = c.get("tokens") or {}
    started, finished = r.get("started"), r.get("finished")
    wall = c.get("wall_s") if c.get("wall_s") is not None else (
        float(finished) - float(started) if started and finished else None)
    hidden = PLUMBING_VARS | {"hardware", "budget_usd"}  # the plan section shows those
    inputs = {k: v for k, v in (r.get("inputs") or {}).items() if k not in hidden and v not in ("", None)}
    task = r.get("task_id") or (r.get("inputs") or {}).get("task_id") or "—"
    rows = "".join(f"<dt>{e(k)}</dt><dd>{e(v if isinstance(v, str) else json.dumps(v, ensure_ascii=False))}</dd>"
                   for k, v in inputs.items())
    tiles = [
        _tile("Total cost", usd(c.get("total_usd")), "estimated from tokens" if c.get("estimated") else ""),
        _tile("LLM", usd(c.get("llm_usd"))),
        _tile("Compute", usd(c.get("compute_usd")), str(c.get("hardware_label") or c.get("hardware") or "")),
        _tile("Tokens", f"{num(tk.get('in'))} in · {num(tk.get('out'))} out", f"{num(tk.get('cache'))} cached"),
        _tile("Wall time", dur(wall)),
        _tile("Events", num(len(d.get("events") or []))),
    ]
    phases = c.get("phases") or {}
    total_ph = sum(float(v or 0) for v in phases.values()) or 0
    bar = ""
    if total_ph > 0:
        segs = "".join(f'<span class="ph ph-{e(k)}" style="flex:{float(v or 0):.3f}" title="{e(k)} {dur(v)}"></span>'
                       for k, v in phases.items() if float(v or 0) > 0)
        legend = " · ".join(f'<span class="key ph-{e(k)}"></span>{e(k)} {dur(v)}' for k, v in phases.items()
                            if float(v or 0) > 0)
        bar = f'<div class="phases">{segs}</div><p class="muted small">{legend}</p>'
    err = f'<div class="callout bad"><b>Error</b>{pre(r.get("error"), 3000)}</div>' if r.get("error") else ""
    notes = "".join(f'<p class="muted small">{e(n)}</p>' for n in d.get("notes") or [])
    return f"""
<section id="summary">
  <div class="meta">
    <dl class="kv">
      <dt>Run</dt><dd class="mono">{e(r.get("run_id"))}</dd>
      <dt>Task</dt><dd>{e(task)}{f' <span class="muted">(flow {e(r.get("flow"))})</span>' if r.get("flow") else ""}</dd>
      <dt>Started</dt><dd>{when(started)}</dd>
      <dt>Finished</dt><dd>{when(finished) if finished else "not yet (snapshot of a running run)"}</dd>
      {rows}
    </dl>
  </div>
  <div class="tiles">{"".join(tiles)}</div>
  {bar}{err}{notes}
</section>"""


def plan_html(d: dict) -> str:
    p = d.get("plan")
    if not p:
        return ""
    stages = p.get("plan") or []
    stages_html = "".join(f"<li>{e(s if isinstance(s, str) else pretty(s))}</li>" for s in stages)
    params = p.get("params") or {}
    params_html = "".join(f"<dt>{e(k)}</dt><dd>{e(pretty(v))}</dd>" for k, v in params.items())
    who = f"Decided by {e(p.get('model'))}" if p.get("status") == "ok" else "The task's defaults"
    lessons = p.get("lessons") or ""
    return f"""
<section id="plan"><h2>Plan</h2>
  <div class="callout">
    <p><b>{who}</b>: {e(p.get("hardware") or "default hardware")}, budget {usd(p.get("budget_usd"))}
       {f'· planner cost {usd(p.get("cost_usd"))}' if p.get("cost_usd") else ""}</p>
    {f'<p>{e(p.get("why"))}</p>' if p.get("why") else ""}
    {f'<ol class="stages">{stages_html}</ol>' if stages_html else ""}
    {f'<dl class="kv">{params_html}</dl>' if params_html else ""}
    {f'<p class="muted small">Planner: {e(p.get("error"))}</p>' if p.get("error") else ""}
    {f'<details><summary>Lessons from earlier runs it was given</summary>{pre(lessons, 8000)}</details>' if lessons.strip() else ""}
  </div>
</section>"""


def steps_html(d: dict) -> str:
    r = d.get("run") or {}
    steps = d.get("steps") or []
    by_step = (d.get("costs") or {}).get("by_step") or {}
    t0 = r.get("started")
    times = [float(x) for s in steps for x in (s.get("started"), s.get("finished")) if x]
    starts = ([float(t0)] if t0 else []) + times
    t_start = min(starts) if starts else None
    t_end = max(times + ([float(r["finished"])] if r.get("finished") else [])) if times else None
    span = (t_end - t_start) if t_start is not None and t_end is not None and t_end > t_start else None
    gantt, rows = [], []
    for s in steps:
        sc = by_step.get(s["key"]) or {}
        if span and s.get("started"):
            a = min(99.4, max(0.0, (float(s["started"]) - t_start) / span * 100))
            b = min(100.0, ((float(s.get("finished") or t_end)) - t_start) / span * 100)
            gantt.append(f'<div class="g-row"><span class="g-key mono">{e(s["key"])}</span><span class="g-track">'
                         f'<span class="g-bar st-{e(s.get("status"))}" style="left:{a:.2f}%;width:{max(0.6, b - a):.2f}%"'
                         f' title="{e(s["key"])}: {dur(float(s.get("finished") or t_end) - float(s["started"]))}">'
                         f'</span></span></div>')
        took = dur(float(s["finished"]) - float(s["started"])) if s.get("started") and s.get("finished") else "—"
        rows.append(
            f'<tr><td class="mono">{e(s["key"])}</td><td>{e(s.get("kind"))}</td>'
            f'<td><span class="pill st-{e(s.get("status"))}">{e(s.get("status"))}</span></td>'
            f'<td>{e(s.get("attempt") or "")}</td><td>{e("" if s.get("reward") is None else s.get("reward"))}</td>'
            f'<td>{took}</td><td>{e(sc.get("model") or s.get("account") or "")}</td>'
            f'<td>{e(sc.get("hardware") or "")}</td><td class="num">{usd(sc.get("llm_usd")) if sc else "—"}</td>'
            f'<td class="num">{usd(sc.get("compute_usd")) if sc else "—"}</td></tr>'
            + (f'<tr class="sub"><td></td><td colspan="9">{pre(s.get("error"), 2000, "bad")}</td></tr>'
               if s.get("error") else ""))
    plan_cost = by_step.get("plan")
    if plan_cost:
        rows.insert(0, f'<tr><td class="mono">plan</td><td>planner</td><td></td><td></td><td></td>'
                       f'<td>{dur(plan_cost.get("seconds"))}</td><td>{e(plan_cost.get("model"))}</td><td></td>'
                       f'<td class="num">{usd(plan_cost.get("llm_usd"))}</td><td class="num">—</td></tr>')
    logs = "".join(f'<details><summary>Log of <span class="mono">{e(s["key"])}</span></summary>{pre(s.get("log"), 60000)}'
                   f'</details>' for s in steps if s.get("log"))
    outputs = "".join(f'<details><summary>Output of <span class="mono">{e(s["key"])}</span></summary>'
                      f'{pre(s.get("output"), 20000)}</details>' for s in steps if s.get("output"))
    return f"""
<section id="steps"><h2>Steps</h2>
  {f'<div class="gantt">{"".join(gantt)}</div>' if gantt else ""}
  <div class="scroll"><table>
    <thead><tr><th>step</th><th>kind</th><th>status</th><th>attempt</th><th>reward</th><th>time</th>
      <th>model / account</th><th>hardware</th><th class="num">LLM</th><th class="num">compute</th></tr></thead>
    <tbody>{"".join(rows) or '<tr><td colspan="10" class="muted">No steps recorded.</td></tr>'}</tbody>
  </table></div>
  {logs}{outputs}
</section>"""


def _cite_link(c: Optional[dict]) -> str:
    if not isinstance(c, dict):
        return ""
    url = doi_url(c)
    label = f"{c.get('authors') or ''} {c.get('year') or ''}".strip() or c.get("title") or c.get("doi") or "source"
    label = re.sub(r";.*", " et al.", str(label)) if ";" in str(c.get("authors") or "") else label
    return f'<a class="cite" href="{e(url)}" rel="noopener">{e(label)}</a>' if url else f'<span class="cite">{e(label)}</span>'


def timeline_html(d: dict) -> str:
    evs = d.get("events") or []
    t0 = (d.get("run") or {}).get("started") or (evs[0].get("t") if evs else None)
    rows = []
    for ev in evs:
        typ = str(ev.get("type") or "")
        cost = ev.get("cost") or {}
        extra = []
        if cost.get("usd"):
            extra.append(usd(cost["usd"]))
        detail = ev.get("detail") or ""
        step = f' <span class="mono muted">{e(ev.get("step"))}</span>' if ev.get("step") else ""
        chips = "".join(f' <span class="chip">{e(x)}</span>' for x in extra)
        cite = " " + _cite_link(ev.get("citation")) if ev.get("citation") else ""
        body = f'<div class="ev-detail">{e(detail)}</div>' if detail else ""
        rows.append(
            f'<li class="ev ev-{e(typ)}" id="ev-{e(ev.get("seq"))}"><span class="ev-t mono">{offset(ev.get("t"), t0)}</span>'
            f'<span class="ev-i" title="{e(typ)}">{ICONS.get(typ, "•")}</span>'
            f'<div class="ev-body"><div class="ev-head"><b>{e(ev.get("title"))}</b>{step}{chips}{cite}</div>{body}</div></li>')
    return f"""
<section id="timeline"><h2>Timeline <span class="count">{len(evs)}</span></h2>
  <p class="muted small">The trace events exactly as the run view showed them (times from the run's start).</p>
  <ol class="events">{"".join(rows) or '<li class="muted">No events.</li>'}</ol>
</section>"""


def _call_label(name: str, args: Any) -> str:
    a = args if isinstance(args, dict) else {}
    for k in ("description", "file_path", "path", "pattern", "url", "query", "command", "prompt"):
        if a.get(k):
            return str(a[k]).splitlines()[0][:140]
    return pretty(args).replace("\n", " ")[:140] if args else ""


def transcript_html(items: list[dict], t0: Optional[float]) -> str:
    out = []
    n_calls = 0
    for m in items:
        if m.get("kind") == "instruction":
            out.append(f'<details class="instr"><summary>Instruction the agent got</summary>{pre(m.get("text"), 30000)}'
                       f'</details>')
        elif m.get("kind") == "text":
            out.append(f'<div class="think"><span class="ev-t mono">{offset(m.get("t"), t0)}</span>'
                       f'<div>{e(clip(m.get("text"), TEXT_MAX))}</div></div>')
        elif m.get("kind") == "call":
            n_calls += 1
            secs = f' <span class="muted">({dur(m["seconds"])})</span>' if m.get("seconds") is not None else ""
            out.append(
                f'<details class="call"><summary><span class="ev-t mono">{offset(m.get("t"), t0)}</span>'
                f'<b class="mono">{e(m.get("name"))}</b> <span class="call-label">{e(_call_label(m.get("name") or "", m.get("args")))}'
                f'</span>{secs}</summary>'
                f'<div class="io"><span class="io-k">input</span>{pre(pretty(m.get("args")), ARGS_MAX)}'
                f'<span class="io-k">output</span>'
                f'{pre(m.get("output"), OUTPUT_MAX) if m.get("output") not in (None, "") else "<p class=muted>(no output recorded)</p>"}'
                f'</div></details>')
    return "".join(out) or '<p class="muted">No transcript recorded.</p>'


def tools_html(d: dict) -> str:
    t0 = (d.get("run") or {}).get("started")
    parts = []
    for s in d.get("steps") or []:
        if not s.get("attempts") and not s.get("live"):
            continue
        blocks = []
        for att in s.get("attempts") or []:
            for tr in att.get("trials") or []:
                ar = tr.get("agent_result") or {}
                bits = [x for x in (tr.get("agent"), tr.get("model")) if x]
                if ar:
                    bits.append(f"{num(ar.get('n_input_tokens'))} in · {num(ar.get('n_output_tokens'))} out · "
                                f"{num(ar.get('n_cache_tokens'))} cached")
                    if ar.get("cost_usd") is not None:
                        bits.append(usd(ar.get("cost_usd")))
                if tr.get("started") and tr.get("finished"):
                    bits.append(dur(float(tr["finished"]) - float(tr["started"])))
                ex = tr.get("exception") or {}
                exc = (f'<div class="callout bad"><b>{e(ex.get("exception_type"))}</b>: {e(ex.get("exception_message"))}'
                       f'{pre(ex.get("exception_traceback"), 4000)}</div>') if isinstance(ex, dict) and ex else ""
                ver = tr.get("verifier_result")
                ver_html = f'<p class="small">Verifier: <span class="mono">{e(pretty(ver))}</span></p>' if ver else ""
                vfiles = "".join(f'<details><summary>verifier/{e(k)}</summary>{pre(v, 20000)}</details>'
                                 for k, v in (tr.get("verifier") or {}).items())
                n = sum(1 for m in tr.get("transcript") or [] if m.get("kind") == "call")
                if n:
                    bits.append(f"{n} tool calls")
                if tr.get("source"):
                    bits.append("from " + str(tr["source"]))
                blocks.append(
                    f'<div class="trial"><div class="trial-head"><b>Attempt {e(att.get("attempt") or "?")}</b> '
                    f'<span class="mono muted">{e(tr.get("path"))}</span><br><span class="small">{e(" · ".join(bits))}'
                    f'</span></div>{exc}{ver_html}{vfiles}{transcript_html(tr.get("transcript") or [], t0)}</div>')
        has_trial_tr = any(tr.get("transcript") for a in s.get("attempts") or [] for tr in a.get("trials") or [])
        for lv in s.get("live") or []:
            if has_trial_tr:
                continue  # the trial's own transcript has the same calls, complete
            blocks.append(f'<div class="trial"><div class="trial-head"><b>Live stream</b> <span class="mono muted">'
                          f'run/live/{e(lv.get("stream"))}</span><br><span class="small">posted by the agent while it '
                          f'ran (no trial file yet)</span></div>{transcript_html(lv.get("transcript") or [], t0)}</div>')
        parts.append(f'<h3 class="mono">{e(s["key"])}</h3>{"".join(blocks)}')
    if not parts:
        return ""
    return f"""
<section id="tools"><h2>Agent tool calls</h2>
  <p class="muted small">Every message and tool call of each agent attempt, with its input and output (long ones are cut
  here; the zip has them whole).</p>
  {"".join(parts)}
</section>"""


def checks_html(d: dict) -> str:
    rows = []
    for s in d.get("steps") or []:
        for c in s.get("checks") or []:
            if not c.get("command") and c.get("exit") is None and not c.get("output"):
                continue
            ok = c.get("exit") == 0 or c.get("passed") is True
            verdict = "passed" if ok else "failed" if c.get("exit") not in (None, 0) or c.get("passed") is False else "?"
            rows.append(
                f'<tr><td class="mono">{e(s["key"])}</td><td>{e(c.get("attempt") or "")}</td>'
                f'<td><span class="pill st-{"ok" if ok else "failed"}">{verdict}</span></td>'
                f'<td>{e("" if c.get("exit") is None else c.get("exit"))}</td>'
                f'<td>{e("" if c.get("reward") is None else c.get("reward"))}</td>'
                f'<td><code>{e(clip(c.get("command"), 400))}</code>{pre(c.get("output"), 6000)}</td></tr>')
    if not rows:
        return ""
    return f"""
<section id="checks"><h2>Checks</h2>
  <p class="muted small">The flow's <code>check:</code> run on what each attempt returned; exit 0 = reward 1.</p>
  <div class="scroll"><table><thead><tr><th>step</th><th>attempt</th><th>result</th><th>exit</th><th>reward</th>
  <th>command and output</th></tr></thead><tbody>{"".join(rows)}</tbody></table></div>
</section>"""


def results_html(d: dict) -> str:
    res = d.get("results") or []
    if not res:
        return ""
    items = "".join(f'<details{" open" if i < 3 else ""}><summary class="mono">{e(r["path"])} '
                    f'<span class="muted">{size(r.get("size"))}</span></summary>{pre(_pretty_text(r.get("text")), 30000)}'
                    f'</details>' for i, r in enumerate(res))
    return f'<section id="results"><h2>Results <span class="count">{len(res)}</span></h2>{items}</section>'


def _pretty_text(t: Any) -> str:
    s = str(t or "")
    if s.lstrip().startswith(("{", "[")):
        try:
            return json.dumps(json.loads(s), indent=1, ensure_ascii=False)
        except ValueError:
            pass
    return s


def manuscript_html(d: dict) -> str:
    ms = d.get("manuscripts") or []
    if not ms:
        return ""
    main, rest = ms[0], ms[1:]
    body = markdown(main.get("text") or "") if main.get("format") in ("md", "markdown", "txt") else \
        pre(main.get("text"), 400_000)
    others = "".join(f'<details><summary class="mono">{e(m["path"])} <span class="muted">{size(m.get("size"))}'
                     f'</span></summary>{pre(m.get("text"), 100_000)}</details>' for m in rest)
    return f"""
<section id="manuscript"><h2>Manuscript</h2>
  <p class="muted small mono">{e(main["path"])} · {size(main.get("size"))} · {when(main.get("mtime"))}</p>
  <article class="manuscript">{body}</article>
  {f'<p class="muted small">Other versions (earlier attempts or drafts):</p>{others}' if others else ""}
</section>"""


def citations_html(d: dict) -> str:
    cits = d.get("citations") or []
    if not cits:
        return ""
    cards = []
    for i, c in enumerate(cits, 1):
        url = doi_url(c)
        title = f'<a href="{e(url)}" rel="noopener">{e(c.get("title") or c.get("doi"))}</a>' if url else \
            e(c.get("title") or c.get("doi"))
        meta = " · ".join(str(x) for x in (c.get("authors"), c.get("journal"), c.get("year"),
                                            f"p. {c['page']}" if c.get("page") else None) if x)
        quote = f'<q>{e(c.get("quote"))}</q>' if c.get("quote") else ""
        doi = f'doi:{e(c.get("doi"))} · ' if c.get("doi") else ""
        cards.append(f'<li class="cite-card" id="cite-{i}"><b>{title}</b><div class="small">{e(meta)}</div>{quote}'
                     f'<div class="small muted">{doi}used in {e(", ".join(c.get("steps") or []) or "the run")} · first at '
                     f'<a href="#ev-{e(c.get("first_seq"))}">event {e(c.get("first_seq"))}</a></div></li>')
    return f'<section id="citations"><h2>Papers cited <span class="count">{len(cits)}</span></h2>' \
           f'<ol class="cites">{"".join(cards)}</ol></section>'


def files_html(d: dict) -> str:
    files = d.get("files") or []
    if not files:
        return ""
    total = sum(int(f.get("size") or 0) for f in files)
    rows = "".join(f'<tr><td class="mono">{e(f["path"])}</td><td class="num">{size(f.get("size"))}</td></tr>'
                   for f in files)
    flow = d.get("flow_yaml") or ""
    return f"""
<section id="files"><h2>Files <span class="count">{len(files)}</span></h2>
  <p class="muted small">What trace.zip contains under <code>macrae-trace-{e((d.get("run") or {}).get("run_id"))}/</code>
  ({size(total)} before compression), besides this report, run.json, events.json and manifest.json.</p>
  <details><summary>All files</summary><div class="scroll"><table><tbody>{rows}</tbody></table></div></details>
  {f'<details><summary>The flow (run/flow.yaml)</summary>{pre(flow, 100000)}</details>' if flow else ""}
</section>"""


CSS = """
:root{--bg:#fff;--surface:#f6f7fb;--text:#14182b;--text-2:#4a5068;--text-3:#7a8099;--border:#e3e5ee;
--accent:#4b57c9;--accent-soft:#4b57c91c;--good:#1f8a53;--bad:#c23b3b;--warn:#b7791f;
--mono:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;--sans:system-ui,-apple-system,"Segoe UI",Roboto,sans-serif}
@media (prefers-color-scheme:dark){:root{--bg:#0b1029;--surface:#131a3a;--text:#e8eaf6;--text-2:#b7bcd6;
--text-3:#8a90b0;--border:#252d55;--accent:#8b97ff;--accent-soft:#8b97ff22;--good:#4cc38a;--bad:#ff7676;--warn:#e0b34f}}
*{box-sizing:border-box}html{background:var(--bg);color:var(--text);font:15px/1.55 var(--sans)}
body{margin:0}header,main,footer{max-width:1100px;margin:0 auto;padding:0 20px}
header{padding-top:28px}h1{font-size:24px;margin:0 0 6px}h2{font-size:18px;margin:34px 0 10px;
border-bottom:1px solid var(--border);padding-bottom:6px}h3{font-size:15px;margin:22px 0 8px}
a{color:var(--accent)}.mono,code,pre{font-family:var(--mono);font-size:12.5px}
pre{white-space:pre-wrap;overflow-wrap:anywhere;background:var(--surface);border:1px solid var(--border);
border-radius:8px;padding:8px 10px;margin:6px 0;max-height:520px;overflow:auto}pre.bad{color:var(--bad)}
.muted{color:var(--text-3)}.small{font-size:13px}nav{position:sticky;top:0;background:var(--bg);z-index:2;
border-bottom:1px solid var(--border)}nav div{max-width:1100px;margin:0 auto;padding:8px 20px;display:flex;
gap:14px;flex-wrap:wrap;font-size:13.5px}nav a{text-decoration:none}
.pill{display:inline-block;border-radius:99px;padding:1px 9px;font-size:12px;font-weight:600;
background:var(--accent-soft);color:var(--accent)}.st-ok{background:#1f8a5322;color:var(--good)}
.st-failed,.st-crashed,.st-cancelled{background:#c23b3b22;color:var(--bad)}.st-skipped{color:var(--text-3)}
.big{font-size:14px;padding:3px 12px;vertical-align:middle}
.kv{display:grid;grid-template-columns:max-content 1fr;gap:3px 14px;margin:8px 0}.kv dt{color:var(--text-3)}
.kv dd{margin:0;overflow-wrap:anywhere}
.tiles{display:grid;grid-template-columns:repeat(auto-fill,minmax(160px,1fr));gap:10px;margin:14px 0}
.tile{background:var(--surface);border:1px solid var(--border);border-radius:10px;padding:10px 12px}
.tile span{display:block;color:var(--text-3);font-size:12px}.tile b{font-size:18px}
.tile small{display:block;color:var(--text-3)}
.phases{display:flex;height:10px;border-radius:5px;overflow:hidden;margin:6px 0;background:var(--surface)}
.ph-plan{background:#8b6fd6}.ph-setup{background:#b7791f}.ph-work{background:var(--accent)}.ph-check{background:#1f8a53}
.key{display:inline-block;width:9px;height:9px;border-radius:2px;margin-right:4px}
.callout{background:var(--accent-soft);border:1px solid var(--border);border-radius:10px;padding:10px 14px;margin:10px 0}
.callout.bad{background:#c23b3b14}.callout p{margin:4px 0}
table{border-collapse:collapse;width:100%;font-size:13.5px}th,td{text-align:left;padding:6px 8px;
border-bottom:1px solid var(--border);vertical-align:top}th{color:var(--text-3);font-weight:600}
td.num,th.num{text-align:right;white-space:nowrap}.scroll{overflow-x:auto}tr.sub td{border-bottom:0;padding-top:0}
.gantt{margin:6px 0 14px}.g-row{display:flex;align-items:center;gap:10px;margin:3px 0}
.g-key{width:160px;flex:none;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;font-size:12px}
.g-track{position:relative;flex:1;height:14px;background:var(--surface);border-radius:4px}
.g-track{overflow:hidden}.g-bar{position:absolute;top:0;bottom:0;border-radius:4px;background:var(--accent)}
.g-bar.st-ok{background:var(--good)}.g-bar.st-failed{background:var(--bad)}.g-bar.st-skipped{background:var(--border)}
details{border:1px solid var(--border);border-radius:8px;padding:4px 10px;margin:6px 0}
summary{cursor:pointer;padding:3px 0}details[open]>summary{margin-bottom:4px}
.events{list-style:none;padding:0;margin:0}.ev{display:flex;gap:10px;padding:6px 0;border-bottom:1px solid var(--border)}
.ev-t{color:var(--text-3);flex:none;width:62px;font-size:12px;padding-top:2px}.ev-i{flex:none;width:22px;text-align:center}
.ev-body{min-width:0;flex:1}.ev-detail{white-space:pre-wrap;color:var(--text-2);font-size:13px;overflow-wrap:anywhere}
.ev-error b{color:var(--bad)}.ev-result b{color:var(--good)}.ev-think .ev-head b{font-weight:500;color:var(--text-2)}
.chip{font-size:12px;color:var(--text-3);border:1px solid var(--border);border-radius:99px;padding:0 7px}
.cite{font-size:12.5px}.count{font-size:12px;color:var(--text-3);font-weight:500;margin-left:6px}
.trial{border-left:3px solid var(--accent-soft);padding-left:12px;margin:12px 0}.trial-head{margin-bottom:6px}
.think{display:flex;gap:10px;margin:6px 0;white-space:pre-wrap;color:var(--text-2)}
.call summary{display:flex;gap:8px;align-items:baseline;flex-wrap:wrap}.call-label{color:var(--text-2);
overflow-wrap:anywhere}.io-k{display:block;font-size:11px;text-transform:uppercase;letter-spacing:.06em;
color:var(--text-3);margin-top:6px}
.manuscript{background:var(--surface);border:1px solid var(--border);border-radius:10px;padding:8px 24px;
font-family:Georgia,"Times New Roman",serif;font-size:16px;line-height:1.65}.manuscript h3{font-size:22px}
.manuscript h4{font-size:18px}.manuscript h5,.manuscript h6{font-size:16px}.manuscript pre{font-size:12.5px}
.ref{color:var(--accent);font-family:var(--sans);font-size:.85em}
.cites{padding-left:22px}.cite-card{margin:10px 0}.cite-card q{display:block;color:var(--text-2);font-size:13.5px;
margin:3px 0;font-style:italic}
.stages{margin:6px 0 6px 18px;padding:0}footer{color:var(--text-3);font-size:12.5px;padding:30px 20px 40px}
@media print{nav{display:none}details{border:0}pre{max-height:none}}
"""


def render(d: dict) -> str:
    r = d.get("run") or {}
    title = r.get("title") or r.get("run_id") or "Run"
    status = str(r.get("status") or "")
    sections = [
        ("summary", "Summary", summary_html(d)), ("plan", "Plan", plan_html(d)), ("steps", "Steps", steps_html(d)),
        ("timeline", "Timeline", timeline_html(d)), ("tools", "Tool calls", tools_html(d)),
        ("checks", "Checks", checks_html(d)), ("results", "Results", results_html(d)),
        ("manuscript", "Manuscript", manuscript_html(d)), ("citations", "Citations", citations_html(d)),
        ("files", "Files", files_html(d)),
    ]
    nav = "".join(f'<a href="#{k}">{e(label)}</a>' for k, label, body in sections if body)
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="generator" content="macrae server/report.py"><meta name="robots" content="noindex">
<title>{e(title)} · {e(r.get("run_id"))} · Macrae trace</title><style>{CSS}</style></head>
<body>
<header><p class="muted small">Macrae · trace report</p>
<h1>{e(title)} <span class="pill big st-{e(status)}">{e(status or "unknown")}</span></h1></header>
<nav aria-label="Sections"><div>{nav}</div></nav>
<main>{"".join(body for _, _, body in sections)}</main>
<footer>Generated {when(d.get("generated"))} from the run's files ({"local disk" if d.get("source") == "local" else "R2"}).
Self-contained: no scripts, nothing loaded from the network. Secrets in the files are shown as ***.</footer>
</body></html>
"""
