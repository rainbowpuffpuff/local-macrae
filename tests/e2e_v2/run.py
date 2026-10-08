#!/usr/bin/env python3
"""The v2 end-to-end check: the real backend (uvicorn server.app) + the real agent_runner engine + the real
small-calc flow + the real live wrapper (tasks/common/claude_live.py), with a fake `harbor` (a Modal sandbox that
only syncs its files when the trial ends), a fake `claude` (slow stream-json) and a stub Anthropic Messages API for
the planner's real SDK call.

    python tests/e2e_v2/run.py [--keep DIR] [--json report.json]

Two runs of small-calc, each watched through the HTTP API every 0.3 s exactly like the page does. It checks:
  1. the plan events come first (seq 1 "Planning…", seq 2 the decision, with the planner's token cost);
  2. agent tool calls appear in GET /api/runs/{id}/events while the agent step is still running, before Harbor has
     written any trial file (so they came through POST /api/live), and are not repeated when the trajectory arrives;
  3. GET /api/runs/{id} costs grow while the run goes (LLM $, compute $, tokens);
  4. after run 1, evolve distills lessons (GET /api/evolution) whose evidence points at real events of run 1;
  5. run 2's agent instruction (what Harbor got with -i) contains those lessons, its planner saw them, and the
     scripts folder (tools_dir) was uploaded as a second path;
  6. /api/live refuses posts without the run's token.
Exit 0 when every check passes; the report (JSON) has the evidence.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import socket
import stat
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
SECRET = "e2e-secret"
TOOL_TYPES = {"read", "search", "calc", "write", "status", "cite"}


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


# ── a stub of the Anthropic Messages API (the planner's call goes through the real SDK) ──────────────────────────


class Stub(BaseHTTPRequestHandler):
    calls: list[dict] = []

    def log_message(self, *a):
        pass

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers.get("content-length") or 0)) or b"{}")
        prompt = json.dumps(body.get("messages"))
        learned = "LESSONS FROM EARLIER RUNS" in prompt
        Stub.calls.append({"path": self.path, "beta": self.headers.get("anthropic-beta"), "model": body.get("model"),
                           "has_schema": bool((body.get("output_config") or {}).get("format")),
                           "fallbacks": body.get("fallbacks"), "lessons_in_prompt": learned, "prompt": prompt})
        decision = {
            "plan": [{"name": "Prepare", "detail": "pick the group papers to cite", "where": "backend"},
                     {"name": "DFT scan", "detail": "PySCF B3LYP ion–water scan, CP-corrected minimum", "where": "modal"},
                     {"name": "Check", "detail": "verify result.json and the citations", "where": "backend"}],
            "hardware": "cpu-4", "budget_usd": 1.5,
            "params": [{"name": "scan_step_angstrom", "value": "0.2"}] if learned else [],
            "why": ("An earlier run lost time on the system python (pyscf missing); with that lesson a coarser 0.2 Å "
                    "scan on 4 cores fits the budget.") if learned else
                   "A single-molecule DFT scan: 4 CPU cores are plenty and a GPU would only add cost."}
        out = {"id": f"msg_stub_{len(Stub.calls)}", "type": "message", "role": "assistant",
               "model": body.get("model") or "claude-sonnet-5-5",
               "content": [{"type": "text", "text": json.dumps(decision)}], "stop_reason": "end_turn",
               "stop_sequence": None, "usage": {"input_tokens": 1830, "output_tokens": 290,
                                                "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0}}
        data = json.dumps(out).encode()
        self.send_response(200)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


# ── HTTP helpers ─────────────────────────────────────────────────────────────


class Api:
    def __init__(self, base: str):
        self.base = base

    def req(self, method: str, path: str, body=None, headers=None, secret=True):
        h = {"Content-Type": "application/json", **(headers or {})}
        if secret:
            h["X-Macrae-Secret"] = SECRET
        data = json.dumps(body).encode() if body is not None else None
        r = urllib.request.Request(self.base + path, data=data, headers=h, method=method)
        try:
            with urllib.request.urlopen(r, timeout=30) as resp:
                return resp.status, json.loads(resp.read() or b"null")
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read() or b"null")

    def get(self, path, **kw):
        code, body = self.req("GET", path, **kw)
        if code != 200:
            raise RuntimeError(f"GET {path} → {code}: {body}")
        return body


def watch(api: Api, run_id: str, home: Path, timeout: float = 240) -> dict:
    """Poll like the page: events (?after) and the run every ~0.3 s. Each event is stamped with what was true when
    it arrived: the agent step's status and whether Harbor had written any trial file yet."""
    events, samples, after, t0 = [], [], 0, time.time()
    jobs = home / "jobs" / run_id
    while time.time() - t0 < timeout:
        body = api.get(f"/api/runs/{run_id}/events?after={after}")
        run = api.get(f"/api/runs/{run_id}")
        calc = next((s for s in run.get("steps") or [] if s["key"] == "calc"), {})
        trial_files = sorted(str(p.relative_to(jobs)) for p in jobs.glob("*/*/agent/*")) if jobs.is_dir() else []
        for e in body["events"]:
            assert e["seq"] == after + 1, (e["seq"], after)
            after = e["seq"]
            events.append(dict(e, _arrived=round(time.time() - t0, 2), _calc_status=calc.get("status"),
                               _trial_files=len(trial_files)))
        c = run.get("costs") or {}
        samples.append({"t": round(time.time() - t0, 2), "status": run["status"], "calc": calc.get("status"),
                        "total_usd": c.get("total_usd"), "llm_usd": c.get("llm_usd"),
                        "compute_usd": c.get("compute_usd"), "tokens": (c.get("tokens") or {}).get("in", 0)
                        + (c.get("tokens") or {}).get("out", 0) + (c.get("tokens") or {}).get("cache", 0),
                        "phases": c.get("phases"), "events": after})
        if body["done"]:
            return {"events": events, "samples": samples, "run": run}
        time.sleep(0.3)
    raise TimeoutError(f"run {run_id} did not finish in {timeout} s")


def wait_lessons(api: Api, run_id: str, timeout: float = 90) -> dict:
    t0 = time.time()
    while time.time() - t0 < timeout:
        evo = api.get("/api/evolution")
        ls = (evo.get("lessons") or {}).get("small-calc") or []
        rows = (evo.get("by_task") or {}).get("small-calc") or []
        if any(any(str(e).startswith(run_id + "/") for e in x.get("evidence") or []) for x in ls) and \
                any(r["run_id"] == run_id and r.get("distilled") for r in rows):
            return evo
        time.sleep(0.5)
    raise TimeoutError(f"no lessons from {run_id} after {timeout} s")


# ── the check ────────────────────────────────────────────────────────────────


def main(work: Path, report_path: Path | None = None) -> dict:
    home = work / "home"
    (home / "runs").mkdir(parents=True, exist_ok=True)
    (home / "settings.json").write_text('{"agent_image": ""}')
    bindir = work / "bin"
    bindir.mkdir(exist_ok=True)
    shim = bindir / "harbor"
    shim.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{HERE / "fake_harbor.py"}" "$@"\n')
    shim.chmod(shim.stat().st_mode | stat.S_IEXEC)
    (HERE / "fake_claude.py").chmod((HERE / "fake_claude.py").stat().st_mode | stat.S_IEXEC)
    (work / "index").mkdir(exist_ok=True)
    (work / "papers").mkdir(exist_ok=True)
    harbor_log, claude_log = work / "harbor-calls.jsonl", work / "claude-calls.jsonl"

    stub = ThreadingHTTPServer(("127.0.0.1", 0), Stub)
    threading.Thread(target=stub.serve_forever, daemon=True).start()
    port = free_port()
    base = f"http://127.0.0.1:{port}"
    env = {k: v for k, v in os.environ.items()
           if not k.startswith(("MODAL_", "AGENT_RUNNER_", "CLAUDE", "MACRAE_", "ANTHROPIC_", "FAKE_"))}
    env.update(
        PATH=f"{bindir}{os.pathsep}{env.get('PATH', '')}", PYTHONPATH=str(ROOT), AGENT_RUNNER_HOME=str(home),
        MACRAE_TOOL_SECRET=SECRET, MACRAE_LIVE_URL=base, MACRAE_INDEX_DIR=str(work / "index"),
        MACRAE_PAPERS_DIR=str(work / "papers"), MACRAE_OFFLINE="1", MACRAE_EMBEDDER="hash",
        # the planner's key + the stub API; agent steps use a (fake) subscription login, so evolve has no API key
        # and distills with its rules, as on a backend without ANTHROPIC_API_KEY
        MACRAE_PLANNER_API_KEY="sk-ant-api03-e2e", ANTHROPIC_BASE_URL=f"http://127.0.0.1:{stub.server_port}",
        AGENT_RUNNER_TOKEN_E2E="sk-ant-oat01-" + "e2e" * 20,
        FAKE_HARBOR_LOG=str(harbor_log), FAKE_CLAUDE_LOG=str(claude_log), FAKE_CLAUDE_DELAY="0.8")
    server_log = open(work / "server.log", "wb")
    server = subprocess.Popen([sys.executable, "-m", "uvicorn", "server.app:app", "--host", "127.0.0.1", "--port",
                               str(port), "--log-level", "warning"], cwd=str(ROOT), env=env, stdout=server_log,
                              stderr=subprocess.STDOUT)
    api = Api(base)
    checks: list[tuple[str, bool, object]] = []

    def check(name: str, ok: bool, evidence: object) -> None:
        checks.append((name, bool(ok), evidence))
        print(f"{'PASS' if ok else 'FAIL'}  {name}\n      {json.dumps(evidence, ensure_ascii=False, default=str)[:900]}")

    try:
        for _ in range(100):
            try:
                if api.get("/api/health", secret=False)["ok"]:
                    break
            except Exception:
                time.sleep(0.2)
        runs = []
        for n in (1, 2):
            code, body = api.req("POST", "/api/tasks/small-calc/start", {"inputs": {"ion": "Na+"}})
            assert code == 200, body
            rid = body["run_id"]
            print(f"\n── run {n}: {rid}")
            if n == 1:
                # /api/live: the run's token, never the shared secret
                st, _ = api.req("POST", f"/api/live/{rid}/calc", {"stream": "x", "offset": 0, "lines": ["{}"]})
                st2, _ = api.req("POST", f"/api/live/{rid}/calc", {"stream": "x", "offset": 0, "lines": ["{}"]},
                                 headers={"X-Macrae-Live": "wrong"}, secret=False)
                st3, _ = api.req("POST", "/api/live/20990101-000000-nope-0000/calc",
                                 {"stream": "x", "offset": 0, "lines": ["{}"]}, headers={"X-Macrae-Live": "x"},
                                 secret=False)
                check("/api/live refuses the shared secret, a wrong token and an unknown run",
                      (st, st2, st3) == (401, 401, 404), {"secret_only": st, "wrong_token": st2, "unknown_run": st3})
            w = watch(api, rid, home)
            w["run_id"] = rid
            runs.append(w)
            ev = w["events"]
            print(f"   {len(ev)} events, status {w['run']['status']}, cost ${w['run']['costs']['total_usd']:.4f}")
            for e in ev:
                print(f"   #{e['seq']:>2} +{e['_arrived']:>5}s  [{e['_calc_status'] or '-':>8}] {e['step'] or '-':>8} "
                      f"{e['type']:>6}  {e['title'][:80]}"
                      + (f"   cost ${e['cost']['usd']:.4f}" if e.get("cost") else "")
                      + (f"   {e['elapsed_s']} s" if e.get("elapsed_s") is not None else ""))
            check(f"run {n} finished ok", w["run"]["status"] == "ok",
                  {"status": w["run"]["status"], "steps": [(s["key"], s["status"]) for s in w["run"]["steps"]]})

            # 1. plan first
            check(f"run {n}: the plan events are first (seq 1 planning, seq 2 the decision with its cost)",
                  ev[0]["type"] == "plan" and ev[1]["type"] == "plan" and ev[1].get("plan", {}).get("source") ==
                  "planner" and (ev[1].get("cost") or {}).get("usd", 0) > 0 and ev[2]["title"].startswith("Started"),
                  {"seq1": ev[0]["title"], "seq2": ev[1]["title"], "cost": ev[1].get("cost"),
                   "plan": {k: ev[1].get("plan", {}).get(k) for k in ("hardware", "params", "budget_usd", "source",
                                                                        "planner_model", "lessons_used")},
                   "seq3": ev[2]["title"]})

            # 2. live tool calls before the step ends, before any trial file exists
            agent = [e for e in ev if e["step"] == "calc" and e["type"] in TOOL_TYPES | {"think"}
                     and not e["title"].startswith(("Started", "Check", "Finished", "Attempt"))]
            early = [e for e in agent if e["_calc_status"] == "running" and e["_trial_files"] == 0]
            calls = [json.loads(ln) for ln in claude_log.read_text().splitlines()]
            check(f"run {n}: agent tool calls stream in while `calc` is running, before Harbor wrote any trial file",
                  len(early) >= 5 and len(early) == len(agent),
                  {"agent_events": len(agent), "seen_while_running_before_trial_files": len(early),
                   "first": [f"#{e['seq']} +{e['_arrived']}s {e['type']} {e['title']}" for e in early[:6]],
                   "step_finished_at": next((f"#{e['seq']} +{e['_arrived']}s" for e in ev
                                             if e["title"].startswith("Finished calc")), None),
                   "live_file": f"runs/{rid}/live/calc.jsonl: "
                                f"{len((home / 'runs' / rid / 'live' / 'calc.jsonl').read_text().splitlines())} lines",
                   "claude_env": calls[-1]["live_env"]})
            titles = [(e["type"], e["title"]) for e in agent]
            check(f"run {n}: the trajectory that arrives at the end repeats none of them",
                  len(titles) == len(set(titles)) and not any(e["_trial_files"] for e in agent),
                  {"unique": len(set(titles)), "total": len(titles),
                   "trial_files_now": sorted(str(p.relative_to(home)) for p in
                                             (home / "jobs" / rid).glob("*/*/agent/*"))})

            # 3. costs grow
            live = [s for s in w["samples"] if s["calc"] == "running" and s["total_usd"] is not None]
            totals = [s["total_usd"] for s in live]
            llm = [s["llm_usd"] for s in live]
            check(f"run {n}: costs grow while the agent runs (GET /api/runs/{{id}} costs)",
                  len(set(totals)) >= 4 and totals == sorted(totals) and llm[-1] > llm[0] and
                  live[-1]["compute_usd"] > live[0]["compute_usd"],
                  {"samples_while_running": len(live),
                   "total_usd": [totals[0], totals[len(totals) // 2], totals[-1]],
                   "llm_usd": [llm[0], llm[-1]], "compute_usd": [live[0]["compute_usd"], live[-1]["compute_usd"]],
                   "tokens": [live[0]["tokens"], live[-1]["tokens"]],
                   "final": {k: w["run"]["costs"][k] for k in ("llm_usd", "compute_usd", "total_usd", "wall_s",
                                                               "phases", "hardware")}})
            costed = [e for e in ev if e.get("cost")]
            check(f"run {n}: event `cost` is each event's own share (sums to the run's LLM cost), totals are separate",
                  abs(sum(e["cost"]["usd"] for e in costed) - w["run"]["costs"]["llm_usd"]) < 1e-3 and
                  not any(e.get("cost") for e in ev if e["type"] == "result"),
                  {"sum_event_cost": round(sum(e["cost"]["usd"] for e in costed), 6),
                   "run_llm_usd": w["run"]["costs"]["llm_usd"],
                   "run_end_totals": ev[-1].get("totals")})

            if n == 1:
                # 4. evolve lessons from run 1
                evo = wait_lessons(api, rid)
                lessons = evo["lessons"]["small-calc"]
                ids = {e["seq"]: e for e in ev}
                cited = [(x["id"], ln) for x in lessons for ln in x.get("links") or [] if ln["run_id"] == rid]
                check("run 1 → evolve wrote lessons whose evidence is a real event of run 1 (the page's seq)",
                      cited and all(ln["seq"] in ids and (ids[ln["seq"]]["step"] or "") == ln["step"]
                                    for _, ln in cited),
                      {"lessons": [f"[{x['id']}] {x['kind']}: {x['lesson'][:160]}" for x in lessons],
                       "evidence": [f"{i} → {ln['run_id']}/{ln['step'] or '-'}/{ln['seq']}: "
                                    f"{ids.get(ln['seq'], {}).get('type')} {ids.get(ln['seq'], {}).get('title')}"
                                    for i, ln in cited],
                       "tools": evo.get("tools", {}).get("small-calc"),
                       "lessons_file": str((home / "evolve" / "lessons.jsonl").relative_to(work))})
                run1_lessons = lessons

        # 5. run 2 got them
        hc = [json.loads(ln) for ln in harbor_log.read_text().splitlines()]
        i1, i2 = (c["argv"][c["argv"].index("-i") + 1] for c in hc)
        paths2 = [hc[1]["argv"][i + 1] for i, a in enumerate(hc[1]["argv"]) if a == "-p"]
        ids1 = [x["id"] for x in run1_lessons]
        block = i2[i2.index("LESSONS FROM EARLIER RUNS"):] if "LESSONS FROM EARLIER RUNS" in i2 else ""
        check("run 2's agent instruction (harbor -i) contains the lessons evolve wrote after run 1",
              "LESSONS FROM EARLIER RUNS" not in i1 and all(f"[{i}]" in i2 for i in ids1),
              {"run1_has_block": "LESSONS FROM EARLIER RUNS" in i1, "lesson_ids": ids1, "run2_block": block[:1200]})
        check("run 2 got the earlier run's scripts (tools_dir as a second -p, named in the instruction)",
              len(paths2) == 2 and Path(paths2[1], "run_calc.py").is_file() and "/app/small-calc/" in i2,
              {"paths": [str(Path(p).relative_to(work)) if p.startswith(str(work)) else p for p in paths2]})
        check("the planner saw the lessons in run 2 (not in run 1), and its params reached the instruction",
              [c["lessons_in_prompt"] for c in Stub.calls] == [False, True] and "in 0.2 Å steps" in i2
              and "in 0.1 Å steps" in i1 and runs[1]["events"][1]["plan"]["lessons_used"] >= 1,
              {"planner_calls": [{k: c[k] for k in ("path", "beta", "model", "has_schema", "fallbacks",
                                                     "lessons_in_prompt")} for c in Stub.calls],
               "run2_plan": runs[1]["events"][1]["title"], "run2_why": runs[1]["events"][1]["plan"]["why"],
               "lessons_used": runs[1]["events"][1]["plan"]["lessons_used"],
               "scan_step_in_instruction": ["0.1 Å" if "in 0.1 Å steps" in i1 else "?",
                                            "0.2 Å" if "in 0.2 Å steps" in i2 else "?"]})
        check("the live env reached the agent through --ae (token masked here)",
              all(c["ae"].get("MACRAE_LIVE_URL") == base and c["ae"].get("MACRAE_STEP") == "calc" for c in hc),
              [c["ae"] for c in hc])

        # 6. the Evolution view after both runs
        evo = wait_lessons(api, runs[1]["run_id"])
        rows = evo["by_task"]["small-calc"]
        check("GET /api/evolution: both runs, run 2 used the lessons and was cheaper and faster",
              [r["run_id"] for r in rows] == [r["run_id"] for r in runs] and rows[0]["lessons_used"] == 0
              and rows[1]["lessons_used"] >= 1 and rows[1]["total_usd"] < rows[0]["total_usd"]
              and rows[1]["wall_s"] < rows[0]["wall_s"],
              [{k: r.get(k) for k in ("run_id", "ok", "wall_s", "total_usd", "lessons_used", "distilled",
                                      "lessons_learned")} for r in rows])
        used = {x["id"]: (x.get("used"), x.get("used_ok")) for x in evo["lessons"]["small-calc"]}
        check("distilling run 2 recorded the outcome of the lessons it used",
              any(u == (1, 1) for u in used.values()), used)
    finally:
        server.terminate()
        try:
            server.wait(10)
        except subprocess.TimeoutExpired:
            server.kill()
        stub.shutdown()
        server_log.close()
    report = {"ok": all(ok for _, ok, _ in checks), "checks": [{"name": n, "ok": ok, "evidence": e}
                                                               for n, ok, e in checks],
              "runs": [{"run_id": r["run_id"], "events": [{k: v for k, v in e.items()} for e in r["events"]],
                        "samples": r["samples"]} for r in runs]}
    if report_path:
        report_path.write_text(json.dumps(report, indent=1, ensure_ascii=False, default=str))
    print(f"\n{sum(ok for _, ok, _ in checks)}/{len(checks)} checks passed")
    return report


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--keep", help="work folder to keep (default: a temporary one, deleted)")
    ap.add_argument("--json", help="write the report here")
    a = ap.parse_args()
    work = Path(a.keep) if a.keep else Path(tempfile.mkdtemp(prefix="macrae-e2e-v2-"))
    work.mkdir(parents=True, exist_ok=True)
    try:
        rep = main(work, Path(a.json) if a.json else None)
    finally:
        if not a.keep:
            shutil.rmtree(work, ignore_errors=True)
    sys.exit(0 if rep["ok"] else 1)
