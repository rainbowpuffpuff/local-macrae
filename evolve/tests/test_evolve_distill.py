"""distill(): rule-based (no key) and Claude (a fake anthropic SDK), the store, tools, idempotency."""
import importlib
import json
import sys
import types

import pytest

import evolve
from evolve import rules, runs, store, tools
from evolve.tests.trace_fixtures import A, B, C, D, clone_run

D_MOD = importlib.import_module("evolve.distill")  # `evolve.distill` the attribute is the function


def by_text(lessons, needle):
    return next(x for x in lessons if needle in x["lesson"])


def test_rules_on_the_small_calc_retry(traces):
    r = runs.load(A)
    cands = rules.extract(r)
    ratio = by_text(cands, "with only the ion charge scaled by 0.75")
    assert ratio["kind"] == "avoid" and "attempt 2 passed after fixing it" in ratio["lesson"]
    seq = int(ratio["evidence"][0].rsplit("/", 1)[1])
    ev = next(e for e in r.events if e["seq"] == seq)
    assert ev["step"] == "calc" and ev["title"].startswith("Check failed")
    mod = by_text(cands, "pyscf")
    assert "/opt/calc/bin/python run_calc.py" in mod["lesson"] and mod["kind"] == "setting"
    ev = next(e for e in r.events if e["seq"] == int(mod["evidence"][0].rsplit("/", 1)[1]))
    assert ev["step"] == "calc" and ev["type"] in ("calc", "error", "status")
    assert all(c["task_id"] == "small-calc" and c["run_id"] == A for c in cands)


def test_rules_on_the_bff_run(traces):
    cands = rules.extract(runs.load(C))
    gmx = by_text(cands, "`gmx` is not on PATH")
    assert "micromamba install" in gmx["lesson"] and "gromacs" in gmx["lesson"]
    to = by_text(cands, "hit its time limit")
    assert "30.1 min on cpu-8" in to["lesson"]
    assert "--steps 20000" in to["lesson"] and "--steps 100000" in to["lesson"]
    assert any("Run 64 short MD trajectories" in c["lesson"] for c in cands)


def test_infra_failures_teach_nothing(traces):
    assert rules.extract(runs.load(D)) == []
    assert evolve.distill(D) == []
    assert D in store.registry()  # but the run counts as distilled


def test_distill_writes_contract_records_and_is_idempotent(traces):
    home, _ = traces
    first = evolve.distill(A)
    assert first and all(set(x) >= {"task_id", "lesson", "evidence", "kind", "created"} for x in first)
    lines = (home / "evolve" / "lessons.jsonl").read_text().splitlines()
    rec = json.loads(lines[0])
    assert rec["task_id"] == "small-calc" and rec["kind"] in store.KINDS and isinstance(rec["created"], float)
    assert all(e.startswith(A + "/") for e in rec["evidence"])
    again = evolve.distill(A)
    assert sorted(x["id"] for x in again) == sorted(x["id"] for x in first)
    assert len((home / "evolve" / "lessons.jsonl").read_text().splitlines()) == len(lines)
    reg = store.registry()[A]
    assert reg["source"] == "rules" and set(reg["lessons"]) == {x["id"] for x in first}
    record = json.loads((home / "evolve" / "distill" / f"{A}.json").read_text())
    assert record["candidates"] and record["source"] == "rules"


def test_passing_scripts_become_tools(traces):
    home, _ = traces
    evolve.distill(A)
    m = tools.manifest("small-calc")
    t = next(x for x in m["tools"] if x["name"] == "run_calc.py")
    assert t["run_id"] == A and t["step"] == "calc" and "/opt/calc/bin/python run_calc.py" in t["command"]
    assert t["description"].startswith("Ion–water binding")
    body = (home / "evolve" / "tools" / "small-calc" / "run_calc.py").read_text()
    assert "ECC" in body
    # attempt 1 (rejected by the check) is not where it came from: attempt 2's copy is
    a2 = home / "jobs" / A / "calc-a2" / "calc-a2__Ru7Lm" / "artifacts" / "app" / "calc" / "run_calc.py"
    assert body == a2.read_text()
    # BFF: the three scripts of the passing attempt
    evolve.distill(C)
    names = {x["name"] for x in tools.listing("bff-charges")}
    assert names == {"run_md.py", "fit_surrogate.py", "run_mcmc.py"}


def test_a_repeat_reinforces_instead_of_duplicating(traces):
    home, _ = traces
    evolve.distill(A)
    n = len(store.load())
    later = clone_run(home, A, "20261009-090000-small-calc-aaaa")
    evolve.distill(later)
    xs = store.load()
    assert len(xs) == n  # same problems → same lessons
    mod = by_text(xs, "pyscf")
    assert mod["hits"] == 2 and set(mod["runs"]) == {A, later}
    assert any(e.startswith(later) for e in mod["evidence"])
    tool = next(t for t in tools.manifest("small-calc")["tools"] if t["name"] == "run_calc.py")
    assert set(tool["runs"]) == {A, later} and tool["versions"] == 1


def test_runs_that_used_lessons_record_the_outcome(traces):
    home, _ = traces
    with store.locked():
        xs = store.load()
        for i in ("La1b2c3", "Ld4e5f6", "L0a9b8c"):
            xs.append({"id": i, "task_id": "small-calc", "lesson": f"lesson {i} about something specific",
                       "kind": "do", "evidence": [], "created": 1.0, "updated": 1.0, "hits": 1, "used": 0,
                       "used_ok": 0, "status": "active"})
        store.save(xs)
    evolve.distill(B)
    used = {x["id"]: x for x in store.load() if x["id"].startswith("L") and x["id"] in ("La1b2c3", "Ld4e5f6")}
    assert all(x["used"] == 1 and x["used_ok"] == 1 for x in used.values())
    evolve.distill(B, force=True)  # redoing a run doesn't count its use twice
    assert store.load()[0]["used"] == 1


def test_lessons_used_by_failing_runs_retire():
    xs = [{"id": "L000001", "task_id": "t", "lesson": "x", "used": 0, "used_ok": 0, "status": "active"}]
    for _ in range(2):
        assert store.record_use(xs, ["L000001"], ok=False) == []
    assert store.record_use(xs, ["L000001"], ok=False) == ["L000001"]
    assert xs[0]["status"] == "retired" and store.active(xs, "t") == []


def test_unfinished_runs_are_not_distilled(home):
    rid = "20261009-100000-small-calc-run"
    d = home / "runs" / rid
    d.mkdir()
    (d / "state.json").write_text(json.dumps({"id": rid, "status": "running", "started": 1.0, "steps": {},
                                              "order": [], "vars": {"task_id": "small-calc"}}))
    assert evolve.distill(rid) == [] and rid not in store.registry()


def test_pending_and_background(traces):
    assert set(evolve.pending()) == {A, B, C, D}
    th = evolve.distill_in_background(A)
    th.join(30)
    assert A in store.registry() and A not in evolve.pending()
    out = evolve.distill_pending()
    assert set(out) == {B, C, D} and evolve.pending() == []


# ── the Claude path, with a fake SDK ────────────────────────────────────────


class FakeAnthropic(types.ModuleType):
    """Just enough of the anthropic package: Anthropic().beta.messages.create and the error classes."""

    def __init__(self, answer, fail_with=None, reject_fallbacks=False, stop="end_turn"):
        super().__init__("anthropic")
        self.calls = []
        mod = self

        class APIStatusError(Exception):
            def __init__(self, message, status_code=500):
                super().__init__(message)
                self.message, self.status_code = message, status_code

        class BadRequestError(APIStatusError):
            pass

        class APIConnectionError(Exception):
            pass

        self.APIStatusError, self.BadRequestError, self.APIConnectionError = APIStatusError, BadRequestError, \
            APIConnectionError

        class Messages:
            def create(self, **kw):
                mod.calls.append(kw)
                if fail_with:
                    raise fail_with(mod)
                if reject_fallbacks and "fallbacks" in kw:
                    raise BadRequestError("fallbacks: unknown parameter", 400)
                text = json.dumps(answer(kw) if callable(answer) else answer)
                return types.SimpleNamespace(
                    model=kw["model"], stop_reason=stop, _request_id="req_test",
                    content=[types.SimpleNamespace(type="thinking", thinking=""),
                             types.SimpleNamespace(type="text", text=text)],
                    usage=types.SimpleNamespace(input_tokens=12000, output_tokens=900, cache_read_input_tokens=0,
                                                cache_creation_input_tokens=0))

        class Anthropic:
            def __init__(self, **kw):
                self.beta = types.SimpleNamespace(messages=Messages())

        self.Anthropic = Anthropic


def answer_for(kw):
    digest = kw["messages"][0]["content"]
    ev = next(ln.split(" ")[0] for ln in digest.splitlines() if ln.startswith(A + "/calc/") and "error" in ln)
    return {"summary": "Two avoidable mistakes cost a whole retry.",
            "lessons": [
                {"lesson": "Run run_calc.py with /opt/calc/bin/python; the system python3 has no pyscf.",
                 "kind": "do", "evidence": [ev], "confidence": 0.9},
                {"lesson": "Scale only the ion charge by 0.75 in the ECC Coulomb term, never the water charges.",
                 "kind": "avoid", "evidence": ["made/up/7"], "confidence": 1.7},
                {"lesson": "x", "kind": "do", "evidence": [], "confidence": 0.5},  # too short: dropped
            ],
            "reinforces": ["Lnot000"],
            "tools": [{"file": "run_calc.py", "description": "PySCF ion–water scan + CP + ECC Coulomb",
                       "reusable": True}]}


@pytest.fixture
def claude(traces, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")

    def install(**kw):
        fake = FakeAnthropic(kw.pop("answer", answer_for), **kw)
        monkeypatch.setitem(sys.modules, "anthropic", fake)
        return fake
    return install


def test_claude_distill_request_and_answer(claude):
    fake = claude()
    out = evolve.distill(A)
    kw = fake.calls[0]
    assert kw["model"] == "claude-opus-5-5"
    assert kw["fallbacks"] == "default" and kw["betas"] == ["server-side-fallback-2026-07-01"]
    assert kw["output_config"]["format"]["type"] == "json_schema"
    assert kw["output_config"]["format"]["schema"]["required"] == ["summary", "lessons", "reinforces", "tools"]
    digest = kw["messages"][0]["content"]
    assert f"# Run {A} of task small-calc" in digest
    assert "check after attempt 1 (exit 1)" in digest and "cites [4]" in digest
    assert "ModuleNotFoundError: No module named 'pyscf'" in digest
    assert "### run_calc.py" in digest  # the script it may keep
    assert {x["source"] for x in out} == {"claude"} | ({"rules"} if any(x["key"] == "baseline" for x in out) else set())
    venv = by_text(out, "/opt/calc/bin/python")
    assert venv["kind"] == "do" and venv["evidence"][0].startswith(A + "/calc/")
    ecc = by_text(out, "Scale only the ion charge")
    assert ecc["evidence"] == [f"{A}/-/{runs.load(A).events[-1]['seq']}"]  # unknown id → the run's last event
    assert ecc["confidence"] == 1.0
    assert not any(x["lesson"] == "x" for x in out)
    reg = store.registry()[A]
    assert reg["source"] == "claude" and reg["usage"]["in"] == 12000 and reg["usage"]["usd"] == pytest.approx(0.066)
    assert next(t for t in tools.listing("small-calc"))["description"] == "PySCF ion–water scan + CP + ECC Coulomb"


def test_claude_reinforces_existing_lessons(claude):
    claude(answer={"summary": "", "lessons": [], "reinforces": [], "tools": []})
    evolve.distill(C, use_llm=False)
    lid = by_text(store.load(), "`gmx` is not on PATH")["id"]
    claude(answer={"summary": "same again", "lessons": [], "reinforces": [lid], "tools": []})
    home = runs.config.runner_home()
    later = clone_run(home, C, "20261009-090000-bff-charges-bbbb")
    evolve.distill(later)
    x = next(x for x in store.load() if x["id"] == lid)
    assert x["hits"] == 2 and later in x["runs"]


def test_unwanted_scripts_are_not_kept(claude):
    claude(answer={"summary": "", "lessons": [], "reinforces": [],
                   "tools": [{"file": "run_calc.py", "description": "one-off", "reusable": False}]})
    evolve.distill(A)
    assert tools.listing("small-calc") == []


@pytest.mark.parametrize("mode", ["api_error", "refusal", "not_json"])
def test_claude_failures_fall_back_to_the_rules(claude, mode):
    if mode == "api_error":
        claude(fail_with=lambda m: m.APIStatusError("overloaded", 529))
    elif mode == "refusal":
        claude(stop="refusal")
    else:
        claude(answer="not an object")
    out = evolve.distill(A)
    assert out and all(x["source"] == "rules" for x in out)
    reg = store.registry()[A]
    assert reg["source"] == "rules" and reg["llm_error"]


def test_rejected_fallbacks_are_retried_without(claude):
    fake = claude(reject_fallbacks=True)
    evolve.distill(A)
    assert "fallbacks" in fake.calls[0] and "fallbacks" not in fake.calls[1]
    assert store.registry()[A]["source"] == "claude"


def test_no_key_never_calls_claude(traces, monkeypatch):
    fake = FakeAnthropic(answer_for)
    monkeypatch.setitem(sys.modules, "anthropic", fake)
    evolve.distill(A)
    assert fake.calls == []
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    monkeypatch.setenv("MACRAE_EVOLVE_LLM", "0")
    evolve.distill(C)
    assert fake.calls == []


def test_digest_stays_bounded(traces):
    r = runs.load(C)
    text = D_MOD.digest(r, [], tools.candidates(r))
    assert len(text) < D_MOD.MAX_DIGEST * 1.5
    assert "AgentTimeoutError" in text and "--steps 100000" in text and f"{C}/fit/" in text
