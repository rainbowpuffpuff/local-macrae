#!/usr/bin/env python3
"""Stands in for the real Claude Code CLI behind tasks/common/claude_live.py in the v2 end-to-end check.

Invoked like Harbor invokes it (`claude --verbose --output-format=stream-json --print`, instruction on stdin), it
prints stream-json lines slowly (FAKE_CLAUDE_DELAY seconds apart), as a small-calc agent would, and writes the files
the step's check wants into $FAKE_APP_DIR/calc (the sandbox's /app/calc).

Without lessons in the instruction it first runs the scan with the system python (ModuleNotFoundError: pyscf), then
with the venv's python: the mistake evolve's rules learn from. With lessons it goes straight to the venv, and it
copies run_calc.py from /app/small-calc/ when the server handed it the earlier run's scripts (tools_dir).
"""

import json
import os
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1] / "tasks" / "tests"))
from fake_harbor import CALC_RESULT  # noqa: E402  (a result.json that passes tasks/checks.py calc)

SESSION = "e2e-session-" + os.environ.get("MACRAE_RUN_ID", "x")[-4:]
MODEL = "claude-opus-5-5"
DELAY = float(os.environ.get("FAKE_CLAUDE_DELAY", "0.8"))
RUN_CALC = '"""Ion–water scan with PySCF (B3LYP/def2-SVP), CP-corrected def2-TZVP at the minimum."""\nprint("scan")\n'
EXPLANATION = (("The sodium ion binds one water molecule by about 23 kcal/mol in the gas phase. " * 12)
               + "The group scales ionic charges by 0.75 to include electronic polarization [2].\n")


def emit(obj, delay=DELAY):
    sys.stdout.write(json.dumps(obj) + "\n")
    sys.stdout.flush()
    time.sleep(delay)


n_msg = 0
cache = 6000


def assistant(block):
    global n_msg, cache
    n_msg += 1
    cache += 1500
    emit({"type": "assistant", "session_id": SESSION, "parent_tool_use_id": None,
          "message": {"id": f"msg_{n_msg:02d}", "model": MODEL, "role": "assistant", "content": [block],
                      "usage": {"input_tokens": 400, "output_tokens": 180, "cache_read_input_tokens": cache,
                                "cache_creation_input_tokens": 900}}})


n_tool = 0


def tool(name, args, output, secs=None, error=False):
    global n_tool
    n_tool += 1
    tid = f"toolu_e2e_{n_tool:02d}"
    assistant({"type": "tool_use", "id": tid, "name": name, "input": args})
    if secs:
        time.sleep(secs)
    res = {"type": "tool_result", "tool_use_id": tid, "content": output}
    if error:
        res["is_error"] = True
    emit({"type": "user", "session_id": SESSION, "message": {"role": "user", "content": [res]}})


def main():
    instruction = sys.stdin.read()
    log = os.environ.get("FAKE_CLAUDE_LOG")
    if log:
        with open(log, "a") as f:
            f.write(json.dumps({"argv": sys.argv[1:], "instruction": instruction,
                                "live_env": {k: ("***" if k == "MACRAE_LIVE_TOKEN" else os.environ.get(k))
                                             for k in ("MACRAE_LIVE_URL", "MACRAE_LIVE_TOKEN", "MACRAE_RUN_ID",
                                                       "MACRAE_STEP")}}) + "\n")
    app = Path(os.environ["FAKE_APP_DIR"])
    calc = app / "calc"
    learned = "LESSONS FROM EARLIER RUNS" in instruction
    tools = app / "small-calc" / "run_calc.py"
    t0 = time.time()

    emit({"type": "system", "subtype": "init", "session_id": SESSION, "model": MODEL, "tools": ["Bash", "Read"]})
    assistant({"type": "text", "text": "I'll read the task parameters and the references first."})
    tool("Read", {"file_path": "/app/calc/task.json"}, (calc / "task.json").read_text()[:2000]
         if (calc / "task.json").is_file() else "{}")
    tool("Bash", {"command": "python3 -m venv /opt/calc && /opt/calc/bin/pip install -q pyscf numpy",
                  "description": "Install PySCF in a venv"}, "", secs=1.0)
    if learned and tools.is_file():
        tool("Bash", {"command": "cp /app/small-calc/run_calc.py /app/calc/run_calc.py",
                      "description": "Reuse run_calc.py from an earlier passing run"}, "")
        (calc / "run_calc.py").write_text(tools.read_text())
    else:
        tool("Write", {"file_path": "/app/calc/run_calc.py", "content": RUN_CALC}, "File created successfully")
        (calc / "run_calc.py").write_text(RUN_CALC)
    if not learned:
        tool("Bash", {"command": "cd /app/calc && python3 run_calc.py | tee output.log", "description": "Run the scan"},
             "Traceback (most recent call last):\n  File \"run_calc.py\", line 2\nModuleNotFoundError: No module "
             "named 'pyscf'", error=True)
    (calc / "output.log").write_text("r=2.2 E=-23.4\n")
    (calc / "result.json").write_text(json.dumps(CALC_RESULT))
    tool("Bash", {"command": "cd /app/calc && /opt/calc/bin/python run_calc.py | tee output.log",
                  "description": "Run the scan with the venv's python"},
         "r_min 2.23 A  E_int -23.412 kcal/mol (CP-corrected)\nwall 41.2 s", secs=1.5)
    tool("Write", {"file_path": "/app/calc/explanation.md", "content": EXPLANATION[:200]}, "File created successfully")
    (calc / "explanation.md").write_text(EXPLANATION)
    assistant({"type": "text", "text": "Done: result.json, explanation.md, run_calc.py and output.log are in /app/calc."})
    emit({"type": "result", "subtype": "success", "session_id": SESSION, "is_error": False, "num_turns": n_msg,
          "duration_ms": int((time.time() - t0) * 1000), "total_cost_usd": round(0.012 * n_msg, 4),
          "usage": {"input_tokens": 400 * n_msg, "output_tokens": 180 * n_msg, "cache_read_input_tokens": cache,
                    "cache_creation_input_tokens": 900 * n_msg}}, delay=0)
    return 0


if __name__ == "__main__":
    sys.exit(main())
