"""python -m evolve distill|context|metrics|export|lessons|hints|add|watch|fixtures|check-flows"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from pathlib import Path

import evolve
from evolve import config


def _print(obj) -> None:
    print(json.dumps(obj, indent=1, ensure_ascii=False, default=str))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m evolve", description="learn from agent traces")
    sub = ap.add_subparsers(dest="cmd", required=True)
    d = sub.add_parser("distill", help="turn finished runs into lessons")
    d.add_argument("runs", nargs="*", help="run ids or run folders (default: every finished run not yet distilled)")
    d.add_argument("--force", action="store_true", help="distill again even if done before")
    d.add_argument("--no-llm", action="store_true", help="rule-based extractor only (no Claude call)")
    c = sub.add_parser("context", help="the lessons block a new run of TASK gets")
    c.add_argument("task_id")
    c.add_argument("-k", type=int, default=8)
    c.add_argument("--vars", action="store_true", help="print the flow vars {lessons, tools_dir} as JSON")
    sub.add_parser("metrics", help="the Evolution panel's data (GET /api/evolution)")
    e = sub.add_parser("export", help="trajectories as JSONL for fine-tuning")
    e.add_argument("out", nargs="?", default="evolve-dataset")
    ls = sub.add_parser("lessons", help="list stored lessons")
    ls.add_argument("task_id", nargs="?", default="")
    ls.add_argument("--all", action="store_true", help="include retired lessons")
    h = sub.add_parser("hints", help="past runs of TASK for the planner")
    h.add_argument("task_id")
    a = sub.add_parser("add", help="add a lesson by hand (task * = every task)")
    a.add_argument("task_id")
    a.add_argument("text")
    a.add_argument("--kind", default="do", choices=["do", "avoid", "setting", "tool"])
    w = sub.add_parser("watch", help="distill every run as it finishes (if the server doesn't call distill)")
    w.add_argument("--every", type=float, default=30.0)
    w.add_argument("--no-llm", action="store_true")
    fx = sub.add_parser("fixtures", help="install the 4 sample runs (tests' fixtures) into an AGENT_RUNNER_HOME")
    fx.add_argument("home", help="e.g. /tmp/demo-home, then AGENT_RUNNER_HOME=/tmp/demo-home python -m evolve distill")
    f = sub.add_parser("check-flows", help="check the lesson-injection hook in flow files")
    f.add_argument("files", nargs="*", default=[str(p) for p in sorted((config.REPO_ROOT / "tasks" / "flows")
                                                                       .glob("*.yaml"))])
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    if args.cmd == "distill":
        use_llm = False if args.no_llm else None
        if not args.runs:
            _print(evolve.distill_pending(use_llm=use_llm))
            return 0
        out = {}
        for r in args.runs:
            try:
                out[r] = evolve.distill(r, force=args.force, use_llm=use_llm)
            except FileNotFoundError as ex:
                print(f"error: {ex}", file=sys.stderr)
                return 2
        _print(out)
    elif args.cmd == "context":
        if args.vars:
            _print(evolve.flow_vars(args.task_id, args.k))
        else:
            print(evolve.context(args.task_id, args.k) or f"(no lessons for {args.task_id} yet)")
    elif args.cmd == "metrics":
        _print(evolve.metrics())
    elif args.cmd == "export":
        out = evolve.export_dataset(args.out)
        m = json.loads((out / "manifest.json").read_text())
        print(f"{out}/all.jsonl: {m['all']} trajectories; {out}/sft.jsonl: {m['sft']} with reward >= 1")
    elif args.cmd == "lessons":
        for x in evolve.lessons(args.task_id, include_retired=args.all):
            st = "" if x.get("status", "active") == "active" else f" [{x['status']}]"
            print(f"[{x['id']}] {x['task_id']} {x['kind']:7} hits={x.get('hits', 1)} "
                  f"used={x.get('used', 0)}/{x.get('used_ok', 0)} ok{st}  {x['lesson']}")
    elif args.cmd == "hints":
        _print(evolve.hints(args.task_id))
    elif args.cmd == "add":
        _print(evolve.add_lesson(args.task_id, args.text, args.kind))
    elif args.cmd == "watch":
        print(f"watching {config.runs_dir()} every {args.every:g} s (Ctrl-C to stop)", flush=True)
        try:
            while True:
                done = evolve.distill_pending(use_llm=False if args.no_llm else None)
                for rid, n in done.items():
                    print(f"{time.strftime('%H:%M:%S')} {rid}: {n} lesson(s)", flush=True)
                time.sleep(args.every)
        except KeyboardInterrupt:
            return 0
    elif args.cmd == "fixtures":
        from evolve.tests.trace_fixtures import install
        home = Path(args.home).expanduser().resolve()
        (home / "runs").mkdir(parents=True, exist_ok=True)
        for rid in install(home):
            print(home / "runs" / rid)
    elif args.cmd == "check-flows":
        problems = evolve.check_flows([Path(p) for p in args.files])
        for p in problems:
            print("✗", p)
        if not problems:
            print(f"ok: {len(args.files)} flow(s) inject lessons and tools")
        return 1 if problems else 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
