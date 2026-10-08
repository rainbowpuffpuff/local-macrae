"""agent-runner: run agents (and scripts) as flows through Harbor, across Claude accounts, with traces."""

from __future__ import annotations

import argparse
import getpass
import json
import sys
import time
from pathlib import Path

from . import events


def _vars(pairs: list[str]) -> dict:
    out = {}
    for p in pairs or []:
        k, _, v = p.partition("=")
        try:
            out[k] = json.loads(v)
        except ValueError:
            out[k] = v
    return out


def cmd_flow(args) -> int:
    from . import flows

    if args.flow_cmd == "run":
        path = Path(args.file).expanduser().resolve()
        if args.detach:
            print(flows.start_detached(path, _vars(args.var)))
            return 0
        status = flows.run_flow_blocking(path, _vars(args.var), run_id=args.run_id)
        print(status)
        return 0 if status == "ok" else 1
    if args.flow_cmd == "check":
        spec, _ = flows.load_flow(Path(args.file).expanduser())
        steps, deps, problems = flows.analyze(spec)
        for p in problems:
            print("✗", p)
        if not problems:
            kinds = {s["id"]: flows.step_kind(s) + (" ×each" if "foreach" in s else "") for s in steps}
            for i, lvl in enumerate(flows.levels(deps)):
                print(f"level {i}: " + ", ".join(f"{n} ({kinds[n]})" for n in lvl))
        return 1 if problems else 0
    if args.flow_cmd == "list":
        for r in flows.list_runs()[: args.n]:
            steps = r.get("steps", {})
            done = sum(1 for v in steps.values() if v.get("status") in ("ok", "failed", "skipped", "cancelled"))
            print(f"{r['id']:48} {r.get('status', '?'):10} {done}/{len(steps)} steps  "
                  f"{time.strftime('%m-%d %H:%M', time.localtime(r.get('started') or 0))}")
        return 0
    if args.flow_cmd == "show":
        r = next((x for x in flows.list_runs() if x["id"].startswith(args.run_id)), None)
        if not r:
            print("no such run", file=sys.stderr)
            return 1
        if args.json:
            print(json.dumps(r, indent=1, default=str))
            return 0
        print(f"{r['id']}  {r.get('status')}  {r.get('dir')}")
        for k in r.get("order", []):
            s = r["steps"][k]
            print(f"  {k:24} {s.get('status', ''):16} acct={s.get('account') or '-':12} "
                  f"reward={s.get('reward')} attempt={s.get('attempt', '')} {s.get('error') or s.get('note') or ''}"[:220])
        return 0
    if args.flow_cmd == "cancel":
        ok = flows.cancel_run(args.run_id)
        print("cancelling" if ok else "not running")
        return 0 if ok else 1
    return 2


def cmd_run(args) -> int:
    from . import flows

    p = flows.write_single_flow(instruction=args.instruction or "", paths=args.path or [], agent=args.agent,
                                model=args.model or "", account=args.account, attempts=args.attempts,
                                workdir=str(Path(args.workdir).expanduser().resolve()), task=args.task or "",
                                dataset=args.dataset or "", retry=args.retry, until=args.until or "")
    if args.detach:
        print(flows.start_detached(p))
        return 0
    status = flows.run_flow_blocking(p)
    print(status)
    return 0 if status == "ok" else 1


def cmd_accounts(args) -> int:
    from . import pool, tokens

    if args.acc_cmd == "set":
        tok = sys.stdin.read() if not sys.stdin.isatty() else getpass.getpass(f"token for {args.account}: ")
        tokens.store_token(args.account, tok)
        pool.clear_cooldown(args.account)
        print(f"stored login for {args.account}")
        return 0
    if args.acc_cmd == "clear":
        tokens.clear_token(args.account)
        print(f"cleared {args.account}")
        return 0
    cool = pool.cooldowns()
    names = tokens.list_accounts()
    if not names:
        print("no accounts. `claude setup-token` → `agent-runner accounts set NAME`, "
              "or AGENT_RUNNER_TOKEN_<NAME> / ANTHROPIC_API_KEY in the environment")
    for a in names:
        c = cool.get(a)
        kind = (tokens.credential(a) or ("none", ""))[0]
        extra = f"  cooling until {time.strftime('%H:%M', time.localtime(c['until']))} ({c['reason']})" if c else ""
        print(f"{a:16} {kind:8} busy {pool.active_leases(a)}{extra}")
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="agent-runner", description=__doc__)
    sub = p.add_subparsers(dest="cmd", required=True)

    f = sub.add_parser("flow", help="run / check / list / show / cancel flows")
    fs = f.add_subparsers(dest="flow_cmd", required=True)
    fr = fs.add_parser("run")
    fr.add_argument("file")
    fr.add_argument("--var", action="append", default=[], help="key=value (value may be JSON)")
    fr.add_argument("--detach", action="store_true", help="run in the background, print the run id")
    fr.add_argument("--run-id")
    fc = fs.add_parser("check")
    fc.add_argument("file")
    fl = fs.add_parser("list")
    fl.add_argument("-n", type=int, default=20)
    fsh = fs.add_parser("show")
    fsh.add_argument("run_id")
    fsh.add_argument("--json", action="store_true", help="full state as JSON (for other programs/agents)")
    fx = fs.add_parser("cancel")
    fx.add_argument("run_id")

    r = sub.add_parser("run", help="one Harbor run (exec on paths, or a task/dataset)")
    r.add_argument("-i", "--instruction")
    r.add_argument("-p", "--path", action="append")
    r.add_argument("--task")
    r.add_argument("--dataset")
    r.add_argument("-a", "--agent", default="claude-code")
    r.add_argument("-m", "--model")
    r.add_argument("--account", default="auto")
    r.add_argument("-k", "--attempts", type=int, default=1)
    r.add_argument("--retry", type=int, default=0)
    r.add_argument("--until")
    r.add_argument("--workdir", default=".")
    r.add_argument("--detach", action="store_true")

    a = sub.add_parser("accounts", help="Claude logins the runner can use")
    acs = a.add_subparsers(dest="acc_cmd")
    acs_set = acs.add_parser("set", help="store a `claude setup-token` token in the keyring")
    acs_set.add_argument("account")
    acs_clr = acs.add_parser("clear")
    acs_clr.add_argument("account")
    acs.add_parser("list")

    im = sub.add_parser("image", help="the Docker image agent steps run in")
    ims = im.add_subparsers(dest="image_cmd", required=True)
    ims.add_parser("build", help="(re)build it with the latest Claude Code")
    ims.add_parser("status")

    rt = sub.add_parser("retro", help="retrospective helpers")
    rts = rt.add_subparsers(dest="retro_cmd", required=True)
    rg = rts.add_parser("gather", help="collect runs, events, sessions and source into a folder")
    rg.add_argument("--out", required=True)
    rg.add_argument("--days", type=float, default=7)
    rg.add_argument("--sessions-dir")

    sub.add_parser("examples", help="list the example flows")

    args = p.parse_args(argv)
    events.log("cli", cmd=args.cmd, sub=getattr(args, "flow_cmd", None) or getattr(args, "acc_cmd", None))

    if args.cmd == "flow":
        return cmd_flow(args)
    if args.cmd == "run":
        return cmd_run(args)
    if args.cmd == "accounts":
        return cmd_accounts(args)
    if args.cmd == "image":
        from . import harbor
        from .config import load_settings
        tag = load_settings()["agent_image"]
        if args.image_cmd == "build":
            return harbor.build_image(tag)
        print(f"{tag}: {'present' if harbor.image_exists(tag) else 'missing (run: agent-runner image build)'}")
        return 0
    if args.cmd == "retro":
        from . import retro
        out = retro.gather(Path(args.out).expanduser(), args.days,
                           Path(args.sessions_dir).expanduser() if args.sessions_dir else None)
        print(out)
        return 0
    if args.cmd == "examples":
        from .flows import list_examples
        for e in list_examples():
            print(e)
        return 0
    return 2


if __name__ == "__main__":
    sys.exit(main())
