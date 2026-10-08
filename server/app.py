"""macrae backend: FastAPI app with the HTTP API from CONTRACT.md (prefix /api).

Run:  uvicorn server.app:app --host 0.0.0.0 --port 8080     (or: python -m server)
"""

from __future__ import annotations

import hmac
import logging
import os
import re
import time
from contextlib import asynccontextmanager
from typing import Any, Optional

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from pydantic import BaseModel, Field
from starlette.background import BackgroundTask

from . import (bridges, capabilities, catalog, config, costs, events, evolution, forge, launch, live, planner,
               rawtrace, runs, safety, traces)
from .classify import clip, first_sentence

log = logging.getLogger("macrae.server")

MAX_INPUTS = 20
MAX_INPUT_LEN = 500
# Task inputs end up in flow templates, which may render them into shell commands (`run:` steps).
# Allow what DOIs, titles and short queries need; refuse shell metacharacters.
SAFE_INPUT_RE = re.compile(r"^[^\"'`$\\;|&<>\n\r\x00]*$")


def _int_env(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, "") or default)
    except ValueError:
        return default


# ── models ──────────────────────────────────────────────────────────────────


class StartBody(BaseModel):
    inputs: Optional[dict[str, Any]] = None


class SearchBody(BaseModel):
    query: str = Field("", max_length=4000)
    k: int = Field(6, ge=1, le=50)


class ToolSearchBody(BaseModel):
    query: str = Field("", max_length=4000)


class ToolStartBody(BaseModel):
    task_id: str = Field("", max_length=200)
    inputs: Optional[dict[str, Any]] = None


class ToolStatusBody(BaseModel):
    run_id: str = Field("", max_length=200)


class ForgeBody(BaseModel):
    name: str = Field(..., min_length=2, max_length=60)
    why: str = Field("", max_length=1000)
    kind: str = ""
    task_id: str = Field("", max_length=80)
    force: bool = False


# ── app ─────────────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(_: FastAPI):
    if config.auth_disabled():
        log.warning("MACRAE_AUTH_DISABLED is set: every route is open. Never do this on a public host.")
    elif not config.tool_secret():
        log.error("MACRAE_TOOL_SECRET is not set: every route except /api/health will answer 503")
    log.info("runs: %s  tasks: %s  index: %s", config.runs_dir(), config.tasks_file(), config.index_dir())
    log.info("live url: %s  planner: %s (%s)", live.public_url(None) or "from the Worker's X-Macrae-Origin",
             planner.model_name(), "key set" if planner.api_key() else "no ANTHROPIC_API_KEY: task defaults")
    evolution.start_watcher()
    safety.start_watchdog()
    yield
    safety.stop_watchdog()
    evolution.stop_watcher()


app = FastAPI(title="macrae backend", version="1.0", docs_url="/api/docs", openapi_url="/api/openapi.json",
              redoc_url=None, lifespan=lifespan)

if config.cors_origins():
    app.add_middleware(CORSMiddleware, allow_origins=config.cors_origins(), allow_methods=["GET", "POST"],
                       allow_headers=["Content-Type", "X-Macrae-Secret"])


@app.exception_handler(Exception)
async def _unhandled(request: Request, exc: Exception) -> JSONResponse:
    log.exception("unhandled error on %s %s", request.method, request.url.path)
    return JSONResponse({"detail": f"internal error: {type(exc).__name__}"}, status_code=500)


def require_secret(x_macrae_secret: Optional[str] = Header(default=None)) -> None:
    if config.auth_disabled():
        return
    secret = config.tool_secret()
    if not secret:
        raise HTTPException(503, "server misconfigured: MACRAE_TOOL_SECRET is not set")
    if not x_macrae_secret or not hmac.compare_digest(x_macrae_secret.encode(), secret.encode()):
        raise HTTPException(401, "missing or wrong X-Macrae-Secret")


auth = [Depends(require_secret)]
# v3: raw traces (zip, Harbor jobs, trajectories), the manuscript edit stream, notes-to-self, figures
app.include_router(rawtrace.router, dependencies=auth)

# costs for people: price table, estimates before a task starts, `cost` on every chat answer (server/cost_api.py)
from . import cost_api  # noqa: E402

cost_api.install(app, auth)

# kill switch, daily spend cap, rate limits, request checks, secret scrub (server/safety.py)
safety.install(app, auth)


@app.get("/api/health")
def health() -> dict:
    try:
        papers, chunks = bridges.index_stats()
    except Exception as e:  # health must always answer
        log.warning("index stats failed: %s", e)
        papers, chunks = 0, 0
    return {"ok": True, "papers": papers, "chunks": chunks, "modal": config.modal_configured()}


# ── tasks ───────────────────────────────────────────────────────────────────


@app.get("/api/tasks", dependencies=auth)
def list_tasks() -> dict:
    return {"tasks": catalog.load_tasks()}


def _resolve_inputs(task: dict, given: Optional[dict]) -> dict:
    """Declared inputs only, defaults filled in, values checked (they may reach a shell through the flow)."""
    given = dict(given or {})
    declared = {i["name"]: i for i in task.get("inputs") or []}
    unknown = sorted(set(given) - set(declared))
    if unknown:
        raise HTTPException(400, f"unknown inputs {unknown}; task {task['id']} takes {sorted(declared) or 'none'}")
    if len(given) > MAX_INPUTS:
        raise HTTPException(400, "too many inputs")
    out: dict[str, Any] = {}
    for name, spec in declared.items():
        v = given.get(name, spec.get("default", ""))
        if v is None or (isinstance(v, str) and not v.strip()):
            v = spec.get("default", "")
        if isinstance(v, bool) or not isinstance(v, (str, int, float)):
            raise HTTPException(400, f"input {name} must be a string or number")
        v = v.strip() if isinstance(v, str) else v
        s = str(v)
        if len(s) > MAX_INPUT_LEN:
            raise HTTPException(400, f"input {name} is longer than {MAX_INPUT_LEN} characters")
        if not SAFE_INPUT_RE.match(s):
            raise HTTPException(400, f"input {name} contains characters that are not allowed (quotes, $, ;, |, &, <, >, \\)")
        out[name] = safety.check_input(name, spec, v)
    return out


def _check_capacity() -> None:
    limit = _int_env("MACRAE_MAX_ACTIVE_RUNS", 4)
    if limit <= 0:
        return
    active = [r for r in runs.list_runs(limit=50) if r["status"] == "running" and r.get("task_id")]
    if len(active) >= limit:
        raise HTTPException(429, f"{len(active)} task runs are already going; try again when one finishes")


def _start(task: dict, given: Optional[dict], origin: Optional[str] = None) -> str:
    inputs = _resolve_inputs(task, given)
    if config.draining():
        raise HTTPException(503, "the backend is restarting (an update or a restart); try again in a few minutes")
    _check_capacity()
    safety.check_start(task)
    try:
        run_id = launch.start(task, inputs, origin)
    except bridges.Unavailable as e:
        log.error("%s", e)
        raise HTTPException(503, "task runner is not available on this server") from e
    except (KeyError, ValueError, FileNotFoundError) as e:
        raise HTTPException(400, f"could not start {task['id']}: {e}") from e
    except HTTPException:
        raise
    except Exception as e:
        log.exception("starting %s failed", task["id"])
        raise HTTPException(500, f"could not start {task['id']}: {type(e).__name__}: {e}") from e
    runs.save_sidecar(run_id, task, inputs)
    log.info("started task %s as run %s (inputs %s)", task["id"], run_id, inputs)
    return run_id


@app.post("/api/tasks/{task_id}/start", dependencies=auth)
def start_task(task_id: str, request: Request, body: Optional[StartBody] = None) -> dict:
    task = catalog.find_task(task_id)
    if not task:
        raise HTTPException(404, f"no task {task_id!r}")
    return {"run_id": _start(task, body.inputs if body else None, request.headers.get("x-macrae-origin"))}


# ── runs ────────────────────────────────────────────────────────────────────


@app.get("/api/runs", dependencies=auth)
def list_runs() -> dict:
    return {"runs": runs.list_runs(limit=50)}


@app.get("/api/runs/{run_id}", dependencies=auth)
def get_run(run_id: str) -> dict:
    st = runs.read_state(run_id)
    if st is None:
        raise HTTPException(404, f"no run {run_id!r}")
    r = runs.summarize(st, with_extra=True)
    run_dir = runs.run_dir(run_id)
    plan = planner.load(run_dir)
    try:
        r["costs"] = costs.run_costs(st, plan)
        if r["status"] in runs.TERMINAL and not (run_dir / "costs.json").is_file():
            costs.save(run_dir, r["costs"])
    except Exception as e:  # the run view must answer even if a trial file is odd
        log.warning("costs for %s failed: %s", run_id, e)
        r["costs"] = None
    r["plan"] = {k: plan.get(k) for k in ("status", "plan", "hardware", "params", "budget_usd", "why", "model",
                                          "cost_usd", "error")} if plan else None
    r["live"] = live.has_live(run_dir)
    return r


# ── live agent output (the claude wrapper in the Modal image) ──────────────


@app.post("/api/live/{run_id}/{step}")
async def live_ingest(run_id: str, step: str, request: Request,
                      x_macrae_live: Optional[str] = Header(default=None)) -> JSONResponse:
    """No X-Macrae-Secret: the run's own token (X-Macrae-Live) authorizes posting to this run only."""
    n = request.headers.get("content-length")
    if n and n.isdigit() and int(n) > live.MAX_BODY:
        return JSONResponse({"detail": "batch too large"}, status_code=413)
    body = await request.body()
    try:
        res = await run_in_threadpool(live.ingest, run_id, step, x_macrae_live, body,
                                      request.headers.get("content-type", ""), dict(request.headers))
    except live.LiveError as e:
        return JSONResponse({"detail": e.detail}, status_code=e.status)
    return JSONResponse(res)


# ── evolution ───────────────────────────────────────────────────────────────


@app.get("/api/evolution", dependencies=auth)
def evolution_metrics() -> dict:
    """evolve.metrics(): per task, run-over-run outcome/time/cost and lessons. Empty (available: false) without
    the evolve package, so the page can show "no data yet" instead of an error."""
    try:
        data = evolution.metrics()
    except bridges.Unavailable as e:
        return {"by_task": {}, "available": False, "detail": str(e)[:300]}
    except Exception as e:
        log.exception("evolve.metrics failed")
        return {"by_task": {}, "available": False, "detail": f"evolve.metrics failed: {type(e).__name__}: {e}"[:300]}
    data.setdefault("available", True)
    return data


# ── capabilities (v3: CREATE / TEST / INSTALL / EVOLVE, authority fixed) ─────


@app.get("/api/capabilities", dependencies=auth)
def capability_ledger(window_h: Optional[float] = Query(None, gt=0, le=24 * 365), all: bool = False) -> dict:
    """Installed capabilities, the ledger (gap → create → test → install → use, rejected) of the session window,
    dusk (its first event) and dawn (now), and the fixed authority."""
    try:
        return capabilities.payload(window_h, all_events=all)
    except bridges.Unavailable as e:
        from . import policy
        return {"capabilities": [], "ledger": [], "dusk": None, "dawn": None, "authority": policy.describe(),
                "forges": [], "available": False, "detail": str(e)[:300]}


@app.post("/api/capabilities/forge", dependencies=auth)
def capability_forge(body: ForgeBody, request: Request) -> dict:
    """Build a capability by hand (the same forge a gap starts). 409 when it is installed or being built."""
    res = forge.start(body.name, body.why or f"requested by an operator for {body.task_id or 'any task'}",
                      kind=body.kind, task_id=body.task_id, origin=request.headers.get("x-macrae-origin"),
                      force=body.force)
    if res["status"] in ("installed", "in-progress", "busy", "gave-up", "off"):
        raise HTTPException(409, res)
    if res["status"] == "invalid":
        raise HTTPException(400, res)
    return res


def _benchmark() -> Any:
    try:
        return bridges._import("evolve.benchmark")
    except bridges.Unavailable as e:
        raise HTTPException(503, "the evolve package is not available on this server") from e


@app.post("/api/benchmark/{mode}", dependencies=auth)
def benchmark_start(mode: str) -> dict:
    """Start the dusk (nothing learned) or dawn (everything installed) benchmark suite."""
    if mode not in ("dusk", "dawn"):
        raise HTTPException(404, "mode must be dusk or dawn")
    if config.draining():
        raise HTTPException(503, "the backend is restarting (an update or a restart); try again in a few minutes")
    try:
        rec = _benchmark().start(mode)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    rec["warnings"] = []
    if mode == "dawn":
        going = [f for f in capabilities.payload().get("forges", []) if f.get("status") == "running"]
        if going:
            rec["warnings"].append(f"{len(going)} forge run(s) still going: "
                                   f"{', '.join(f['name'] for f in going)} won't be in this dawn")
    return rec


@app.get("/api/benchmark", dependencies=auth)
def benchmark_list() -> dict:
    b = _benchmark()
    return {"benchmarks": [b.summary(r) for r in b.list_benchmarks()[:10]], "suite": b.suite()}


@app.get("/api/benchmark/{bench_id}", dependencies=auth)
def benchmark_get(bench_id: str) -> dict:
    b = _benchmark()
    rec = b.load(bench_id) if re.match(r"^[A-Za-z0-9._-]{1,120}$", bench_id) else None
    if not rec:
        raise HTTPException(404, f"no benchmark {bench_id!r}")
    return b.summary(rec)


@app.get("/api/dawn-report", dependencies=auth)
def dawn_report(dusk: Optional[str] = None, dawn: Optional[str] = None) -> dict:
    """{"dusk", "dawn", "delta", "capabilities_installed_between", "runs", "ready"}: the latest dusk benchmark
    against the latest dawn after it (or the given ids)."""
    for v in (dusk, dawn):
        if v is not None and not re.match(r"^[A-Za-z0-9._-]{1,120}$", v):
            raise HTTPException(400, "bad benchmark id")
    return _benchmark().report(dusk, dawn)


@app.get("/api/runs/{run_id}/events", dependencies=auth)
def run_events(run_id: str, after: int = Query(0, ge=0)) -> dict:
    res = events.poll(run_id, after)
    if res is None:
        raise HTTPException(404, f"no run {run_id!r}")
    return res


# ── downloadable proof: trace.zip and the HTML report (server/traces.py, server/report.py) ──


def _trace_files(run_id: str) -> "traces.RunFiles":
    try:
        rf = traces.locate(run_id)
    except LookupError as e:
        raise HTTPException(503, str(e)) from e
    if rf is None:
        raise HTTPException(404, f"no run {run_id!r}")
    return rf


@app.get("/api/runs/{run_id}/trace.zip", dependencies=auth)
def run_trace_zip(run_id: str) -> FileResponse:
    """Everything the run left (Harbor job folders, logs, state, live events, results) + report.html, as one zip."""
    rf = _trace_files(run_id)
    path = traces.build_zip(rf)
    return FileResponse(path, media_type="application/zip", filename=f"macrae-trace-{run_id}.zip",
                        headers={"Cache-Control": "no-store"}, background=BackgroundTask(path.unlink, missing_ok=True))


@app.get("/api/runs/{run_id}/report.html", dependencies=auth)
def run_report(run_id: str, download: bool = False) -> HTMLResponse:
    """The run's trace as one self-contained HTML page (no scripts, nothing external): open it or save it."""
    rf = _trace_files(run_id)
    disp = "attachment" if download else "inline"
    return HTMLResponse(traces.report_html(rf), headers={
        "Content-Disposition": f'{disp}; filename="macrae-report-{run_id}.html"', "Cache-Control": "no-store"})


# ── search ──────────────────────────────────────────────────────────────────


@app.post("/api/search", dependencies=auth)
def search(body: SearchBody) -> dict:
    q = body.query.strip()
    if not q:
        return {"passages": []}
    try:
        return {"passages": bridges.search(q[:1000], k=body.k)}
    except bridges.Unavailable as e:
        log.error("%s", e)
        raise HTTPException(503, "paper search is not available on this server") from e


# ── ElevenLabs server tools ─────────────────────────────────────────────────

NO_PAPERS = ("The group's paper index has nothing on this. Say that the papers available here don't cover it, "
             "and don't answer from general knowledge.")


@app.post("/api/tools/search_papers", dependencies=auth)
def tool_search_papers(body: ToolSearchBody) -> dict:
    q = body.query.strip()
    if not q:
        return {"answer_context": "No question was given. Ask the user what they want to know.", "citations": []}
    try:
        ctx, cits = bridges.search_with_context(q[:1000], k=6)
    except bridges.Unavailable as e:
        log.error("%s", e)
        return {"answer_context": "Paper search is offline right now. Tell the user you can't look at the papers "
                                  "at the moment.", "citations": []}
    except Exception:
        log.exception("search_papers failed")
        return {"answer_context": "Paper search failed. Tell the user to try again in a moment.", "citations": []}
    return {"answer_context": safety.fence(ctx) if ctx else NO_PAPERS, "citations": safety.clean_citations(cits)}


@app.post("/api/tools/start_task", dependencies=auth)
def tool_start_task(body: ToolStartBody, request: Request) -> dict:
    """Lenient for the voice agent: unknown task or a refusal comes back as a message, not an HTTP error."""
    task = catalog.find_task(body.task_id, fuzzy=True)
    if not task:
        names = ", ".join(f"{t['id']} ({t['title']})" for t in catalog.load_tasks()) or "none"
        return {"run_id": "", "message": f"There is no task called “{body.task_id}”. Available tasks: {names}."}
    try:
        run_id = _start(task, body.inputs, request.headers.get("x-macrae-origin"))
    except HTTPException as e:
        return {"run_id": "", "message": f"Could not start “{task['title']}”: {e.detail}"}
    return {"run_id": run_id,
            "message": f"Started “{task['title']}” (run {run_id}). It runs on Modal and usually takes a few "
                       f"minutes; the page shows each step live. Ask me for its status any time."}


def _ago(seconds: float) -> str:
    seconds = max(0.0, seconds)
    if seconds < 90:
        return f"{seconds:.0f} seconds"
    if seconds < 5400:
        return f"{seconds / 60:.0f} minutes"
    return f"{seconds / 3600:.1f} hours"


def status_summary(run: dict, recent: list[dict]) -> str:
    leaves = [s for s in run["steps"] if s.get("fanout") is None]
    done = [s for s in leaves if s["status"] in ("ok", "failed", "skipped", "cancelled")]
    ok = [s for s in leaves if s["status"] == "ok"]
    failed = [s for s in leaves if s["status"] in ("failed", "cancelled")]
    now = time.time()
    title = run["title"]
    started = float(run.get("started") or now)
    if run["status"] == "running":
        active = [s["key"] for s in leaves if s["status"] not in ("ok", "failed", "skipped", "cancelled", "queued")]
        parts = [f"“{title}” has been running for {_ago(now - started)}"]
        if leaves:
            parts.append(f"{len(done)} of {len(leaves)} steps done")
        if active:
            parts.append("working on " + ", ".join(active[:3]))
        last = next((e for e in reversed(recent) if e["type"] != "status"), recent[-1] if recent else None)
        s = "; ".join(parts) + "."
        if last:
            s += f" Latest: {last['title']}."
        return s
    took = _ago(float(run.get("finished") or now) - started)
    if run["status"] == "ok":
        res = next((e for e in reversed(recent) if e["type"] == "result" and e.get("step")), None)
        s = f"“{title}” finished in {took}: {len(ok)} of {len(leaves)} steps succeeded."
        if res and res.get("detail"):
            s += " Result: " + first_sentence(res["detail"], 200)
        return s
    if run["status"] == "cancelled":
        return f"“{title}” was cancelled after {took}."
    where = failed[0] if failed else None
    err = (where or {}).get("error") or run.get("error") or ""
    return (f"“{title}” failed after {took}" + (f" at step {where['key']}" if where else "") +
            (f": {clip(err, 200)}" if err else ".")).strip()


@app.post("/api/tools/run_status", dependencies=auth)
def tool_run_status(body: ToolStatusBody) -> dict:
    run_id = body.run_id.strip()
    if not run_id or run_id.lower() in ("latest", "last", "current"):
        run_id = runs.latest_run_id() or ""
    if not run_id:
        return {"status": "none", "summary": "No task has been run yet.", "recent": []}
    run = runs.get_run(run_id)
    if run is None:
        return {"status": "unknown", "summary": f"I can't find a run with id {run_id}.", "recent": []}
    recent = events.tail(run_id, 5) or []
    return {"status": run["status"], "summary": safety.defang(status_summary(run, recent), 1000),
            "recent": [safety.defang(e["title"], 300) for e in recent]}
