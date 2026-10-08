#!/usr/bin/env python3
"""Stands in for `harbor exec … -e modal` in the v2 end-to-end check: a Modal sandbox, as seen from the backend.

- The -p folders are uploaded to the "sandbox" (<trial>/artifacts/app/<name>, i.e. /app/<name>).
- `claude` there is the real image wrapper, tasks/common/claude_live.py, around tests/e2e_v2/fake_claude.py, with the
  --ae vars as its environment: it forwards the stream-json live to MACRAE_LIVE_URL/api/live/<run>/<step>.
- Like Modal, nothing reaches the trial folder until the agent is done: claude-code.txt, the ATIF trajectory.json and
  result.json are written at the end (the backend can only show the agent's work live through the wrapper).

Each call is logged to $FAKE_HARBOR_LOG as {"argv", "ae": {name: value or *** for the token}}.
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]


def opt(argv, *names, many=False):
    out = [argv[i + 1] for i, a in enumerate(argv) if a in names and i + 1 < len(argv)]
    return out if many else (out[-1] if out else None)


def iso(t):
    return datetime.fromtimestamp(t, timezone.utc).isoformat().replace("+00:00", "Z")


def atif(lines, instruction, t_start):
    """Harbor's ATIF trajectory from the stream-json lines [(t, obj)] (what Harbor writes after the trial)."""
    steps = [{"step_id": 1, "timestamp": iso(t_start), "source": "user", "message": instruction}]
    by_id = {}
    session, model = "", ""
    for t, d in lines:
        session = session or d.get("session_id") or ""
        if d.get("type") == "system":
            model = d.get("model") or model
        msg = d.get("message") if isinstance(d.get("message"), dict) else {}
        if d.get("type") == "assistant":
            for b in msg.get("content") or []:
                st = {"step_id": len(steps) + 1, "timestamp": iso(t), "source": "agent", "message": "",
                      "model_name": msg.get("model") or model}
                u = msg.get("usage") or {}
                st["metrics"] = {"prompt_tokens": u.get("input_tokens", 0) + u.get("cache_read_input_tokens", 0)
                                 + u.get("cache_creation_input_tokens", 0),
                                 "completion_tokens": u.get("output_tokens", 0),
                                 "cached_tokens": u.get("cache_read_input_tokens", 0),
                                 "extra": {"cache_creation_input_tokens": u.get("cache_creation_input_tokens", 0)}}
                if b.get("type") == "text":
                    st["message"] = b["text"]
                elif b.get("type") == "tool_use":
                    st["tool_calls"] = [{"tool_call_id": b["id"], "function_name": b["name"], "arguments": b["input"]}]
                    st["observation"] = {"results": []}
                    by_id[b["id"]] = st
                steps.append(st)
        elif d.get("type") == "user":
            for b in msg.get("content") or []:
                if isinstance(b, dict) and b.get("type") == "tool_result" and b.get("tool_use_id") in by_id:
                    r = {"source_call_id": b["tool_use_id"], "content": b.get("content")}
                    if b.get("is_error"):
                        r["is_error"] = True
                    by_id[b["tool_use_id"]]["observation"]["results"].append(r)
    final = next((d for _, d in lines if d.get("type") == "result"), {})
    u = final.get("usage") or {}
    return {"schema_version": "ATIF-v1.7", "session_id": session,
            "agent": {"name": "claude-code", "version": "e2e", "model_name": model},
            "steps": steps,
            "final_metrics": {"total_prompt_tokens": u.get("input_tokens", 0) + u.get("cache_read_input_tokens", 0)
                              + u.get("cache_creation_input_tokens", 0),
                              "total_completion_tokens": u.get("output_tokens", 0),
                              "total_cached_tokens": u.get("cache_read_input_tokens", 0),
                              "total_cost_usd": final.get("total_cost_usd"),
                              "extra": {"total_cache_creation_input_tokens": u.get("cache_creation_input_tokens", 0)}}}


def main():
    argv = sys.argv[1:]
    ae = dict(a.split("=", 1) for a in opt(argv, "--ae", "--agent-env", many=True))
    with open(os.environ["FAKE_HARBOR_LOG"], "a") as f:
        f.write(json.dumps({"argv": argv, "t": time.time(),
                            "ae": {k: ("***" if k == "MACRAE_LIVE_TOKEN" else v) for k, v in ae.items()}}) + "\n")
    if argv[:1] != ["exec"]:
        sys.stderr.write("fake harbor: only `exec` is supported\n")
        return 2
    instruction = opt(argv, "-i") or ""
    job = Path(opt(argv, "--jobs-dir")) / opt(argv, "--job-name")
    trial = job / f"{job.name}__e2e"
    trial.mkdir(parents=True)
    (job / "config.json").write_text("{}")
    (trial / "config.json").write_text("{}")
    t_start = time.time()
    app = trial / "artifacts" / "app"  # the sandbox's /app (Harbor copies it back as artifacts/app)
    for p in opt(argv, "-p", many=True):
        shutil.copytree(p, app / Path(p).name)
    t_setup = time.time() + 0.5
    time.sleep(0.5)  # "sandbox start"

    env = dict(os.environ, **ae, MACRAE_CLAUDE_REAL=str(HERE / "fake_claude.py"), FAKE_APP_DIR=str(app))
    lines = []
    with tempfile.NamedTemporaryFile("w+", suffix=".claude-code.txt", delete=False) as sandbox_log:
        proc = subprocess.Popen([sys.executable, "-I", str(ROOT / "tasks" / "common" / "claude_live.py"), "--verbose",
                                 "--output-format=stream-json", "--print"], stdin=subprocess.PIPE,
                                stdout=subprocess.PIPE, env=env, cwd=str(app))
        proc.stdin.write(instruction.encode())
        proc.stdin.close()
        for raw in iter(proc.stdout.readline, b""):
            sandbox_log.write(raw.decode())
            try:
                lines.append((time.time(), json.loads(raw)))
            except ValueError:
                pass
        rc = proc.wait()
    t_end = time.time()
    # the trial is over: Harbor syncs the logs and writes the trial record (only now does the backend see them)
    (trial / "agent").mkdir()
    shutil.move(sandbox_log.name, trial / "agent" / "claude-code.txt")
    (trial / "agent" / "trajectory.json").write_text(json.dumps(atif(lines, instruction, t_setup)))
    final = next((d for _, d in lines if d.get("type") == "result"), {})
    u = final.get("usage") or {}
    (trial / "result.json").write_text(json.dumps({
        "task_name": job.name, "trial_name": trial.name, "started_at": iso(t_start), "finished_at": iso(t_end),
        "environment_setup": {"started_at": iso(t_start), "finished_at": iso(t_setup)},
        "agent_setup": {"started_at": iso(t_setup), "finished_at": iso(t_setup)},
        "agent_execution": {"started_at": iso(t_setup), "finished_at": iso(t_end)},
        "agent_info": {"name": "claude-code", "model_info": {"name": "claude-opus-5-5"}},
        "agent_result": {"n_input_tokens": u.get("input_tokens", 0) + u.get("cache_read_input_tokens", 0),
                         "n_cache_tokens": u.get("cache_read_input_tokens", 0),
                         "n_output_tokens": u.get("output_tokens", 0), "cost_usd": final.get("total_cost_usd")},
        "verifier_result": None, "exception_info": None if rc == 0 else {
            "exception_type": "NonZeroAgentExitCodeError", "exception_message": f"claude exited {rc}"}}))
    (job / "result.json").write_text(json.dumps({"started_at": iso(t_start), "finished_at": iso(t_end),
                                                 "n_total_trials": 1, "stats": {"n_completed_trials": 1}}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
