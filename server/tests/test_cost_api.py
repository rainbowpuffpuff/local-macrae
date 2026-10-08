"""server/cost_api.py and the cost helpers it uses: the cited price table, estimates from past runs, and the `cost`
stamped on chat answers."""

import json
import time

import pytest
from conftest import write_state

from server import cost_api, costs, runs


@pytest.fixture(autouse=True)
def _fresh():
    cost_api.reset_cache()
    yield
    cost_api.reset_cache()


# ── the price table ──────────────────────────────────────────────────────────


def test_current_list_prices():
    # platform.claude.com/docs/en/about-claude/pricing, 2026-10-08: (input, output, cache hit, 5-min cache write)
    assert costs.PRICES["claude-opus-5-5"] == (4.0, 20.0, 0.20, 5.0)
    assert costs.PRICES["claude-sonnet-5-5"] == (2.0, 10.0, 0.10, 2.5)  # cache hits 0.05× input on the 5.5 models
    assert costs.PRICES["claude-haiku-5-5"] == (0.10, 0.50, 0.01, 0.125)
    tk = {"in": 1_000_000, "out": 1_000_000, "cache_read": 1_000_000, "cache_write": 1_000_000}
    assert costs.llm_usd("claude-sonnet-5-5", tk) == pytest.approx(2 + 10 + 0.10 + 2.5)
    # modal.com/pricing, Sandbox rates per physical core / GiB, GPUs per device
    assert costs.COMPUTE_RATES["cpu_core_s"] == 0.00003942 and costs.COMPUTE_RATES["mem_gib_s"] == 0.00000667
    assert costs.COMPUTE_RATES["gpu_s"]["h100"] == 0.001097


def test_haiku_long_prompt_tier_applies_per_request_only():
    big = {"in": 150_000, "out": 1_000}
    assert costs.llm_usd("claude-haiku-5-5", big, one_request=True) == pytest.approx((150_000 * 0.5 + 1000 * 2.5) / 1e6)
    assert costs.llm_usd("claude-haiku-5-5", big) == pytest.approx((150_000 * 0.1 + 1000 * 0.5) / 1e6)
    small = {"in": 50_000, "cache_read": 49_000, "out": 10}
    assert costs.llm_usd("claude-haiku-5-5", small, one_request=True) == costs.llm_usd("claude-haiku-5-5", small)


def test_price_table_cites_its_sources(client):
    t = client.get("/api/costs/prices").json()
    assert t["as_of"] == costs.PRICES_AS_OF == "2026-10-08"
    assert t["sources"]["anthropic"]["url"].startswith("https://platform.claude.com/")
    assert t["sources"]["modal"]["url"] == "https://modal.com/pricing"
    opus = next(m for m in t["llm"]["models"] if m["id"] == "claude-opus-5-5")
    assert opus["output"] == 20.0 and opus["cache_write_1h"] == 8.0
    assert next(m for m in t["llm"]["models"] if m["id"] == "claude-haiku-5-5")["long_prompt"]["above_tokens"] == 100_000
    assert t["compute"]["cpu_core_s"] == 0.00003942
    assert {h["id"] for h in t["compute"]["hardware"]} >= {"cpu-2", "gpu-a10g"}
    assert t["backend"]["instance"]["name"] == "standard-1" and t["backend"]["usd_per_hour"] > 0
    assert t["voice"]["usd_per_min"] == 0.08
    # source comment in the module itself, as the job asked
    src = (costs.__file__ and open(costs.__file__).read())
    for url in ("https://platform.claude.com/docs/en/about-claude/pricing", "https://modal.com/pricing"):
        assert url in src


def test_prices_need_the_secret(client):
    client.headers.pop("X-Macrae-Secret")
    assert client.get("/api/costs/prices").status_code == 401
    assert client.get("/api/costs/estimates").status_code == 401


# ── answers ─────────────────────────────────────────────────────────────────


def test_answer_cost_backend_only_and_with_llm():
    c = costs.answer_cost(2.0)
    assert c["llm_usd"] == 0 and c["model"] == "" and c["compute"] == "backend"
    assert c["compute_usd"] == pytest.approx(2.0 * (0.5 * 0.000020 + 4 * 0.0000025 + 8 * 0.00000007))
    c = costs.answer_cost(1.0, "claude-sonnet-5-5-20261001", {"input_tokens": 3000, "output_tokens": 400,
                                                              "cache_read_input_tokens": 10_000})
    assert c["model"] == "claude-sonnet-5-5" and c["tokens"]["in"] == 3000 and c["tokens"]["cache_read"] == 10_000
    assert c["llm_usd"] == pytest.approx((3000 * 2 + 400 * 10 + 10_000 * 0.1) / 1e6)
    assert c["total_usd"] == pytest.approx(c["llm_usd"] + c["compute_usd"])


def test_search_answer_carries_its_cost(client, fake_modules):
    body = client.post("/api/search", json={"query": "ion pairing", "k": 2}).json()
    assert len(body["passages"]) == 2
    c = body["cost"]
    assert c["llm_usd"] == 0 and c["compute_usd"] > 0 and c["seconds"] >= 0 and c["total_usd"] == c["compute_usd"]


def test_middleware_prices_llm_usage_and_keeps_a_cost_the_route_set(env, client):
    from server.app import app
    from server.cost_api import CHAT_PATHS

    @app.post("/api/ask")  # a priced path with no real route (/api/chat has one)
    def chat(body: dict) -> dict:
        if body.get("own"):
            return {"answer": "x", "cost": {"total_usd": 1.23}}
        return {"answer": "Ions pair [1].", "model": "claude-opus-5-5",
                "usage": {"input_tokens": 1000, "output_tokens": 100}}

    try:
        assert {"/api/chat", "/api/ask"} <= CHAT_PATHS
        c = client.post("/api/ask", json={}).json()["cost"]
        assert c["model"] == "claude-opus-5-5" and c["llm_usd"] == pytest.approx((1000 * 4 + 100 * 20) / 1e6)
        assert client.post("/api/ask", json={"own": True}).json()["cost"] == {"total_usd": 1.23}
    finally:
        app.router.routes[:] = [r for r in app.router.routes if getattr(r, "path", "") != "/api/ask"]
    # errors and other routes pass through untouched
    assert "cost" not in client.post("/api/search", json={"query": "x", "k": 0}).json()
    assert "cost" not in client.get("/api/tasks").json()


# ── estimates ───────────────────────────────────────────────────────────────


def _finished_run(env, run_id, task_id, status, total, llm, wall, started, hardware="cpu-2"):
    d = env.runs / run_id
    write_state(d, status=status, started=started, finished=started + wall, steps={}, order=[])
    (d / "costs.json").write_text(json.dumps({"total_usd": total, "llm_usd": llm, "compute_usd": total - llm,
                                              "wall_s": wall, "hardware": hardware}))
    runs.save_sidecar(run_id, {"id": task_id, "title": task_id}, {})


def test_no_past_runs_gives_the_hourly_rate(client):
    e = client.get("/api/tasks/small-calc/estimate").json()
    assert e["basis"] == "none" and e["n"] == 0 and e["usd"] is None
    assert e["usd_per_hour"] == pytest.approx(costs.hardware_rate(costs.DEFAULT_HARDWARE) * 3600, abs=1e-3)
    assert client.get("/api/tasks/nope/estimate").status_code == 404


def test_estimate_from_past_runs_prefers_successes(env, client):
    now = time.time() - 10_000
    for i, (total, wall) in enumerate([(0.40, 300), (0.50, 360), (0.60, 420), (0.70, 480)]):
        _finished_run(env, f"r-ok-{i}", "small-calc", "ok", total, total * 0.9, wall, now + i)
    _finished_run(env, "r-bad", "small-calc", "failed", 5.0, 4.0, 2000, now + 10)
    _finished_run(env, "r-other", "methods-card", "ok", 0.10, 0.10, 60, now + 11)
    body = client.get("/api/costs/estimates").json()
    est = body["estimates"]
    assert body["runs"]["r-bad"]["total_usd"] == 5.0 and body["runs"]["r-ok-0"]["wall_s"] == 300  # recent-runs list
    e = est["small-calc"]
    assert e["basis"] == "past_runs" and e["n"] == 4 and e["n_ok"] == 4
    assert e["usd"] == pytest.approx(0.55) and e["seconds"] == pytest.approx(390)
    assert e["usd_low"] == pytest.approx(0.475) and e["usd_high"] == pytest.approx(0.625)  # quartiles
    assert e["success_rate"] == pytest.approx(0.8)  # 4 of the 5 newest
    assert e["runs"][0] == "r-ok-3" and "r-bad" not in e["runs"] and e["hardware"] == "cpu-2"
    assert est["methods-card"]["n"] == 1 and est["methods-card"]["usd_low"] == est["methods-card"]["usd_high"] == 0.1
    assert client.get("/api/tasks/small-calc/estimate").json() == e


def test_estimate_uses_failures_when_nothing_succeeded_and_skips_running(env, client):
    now = time.time() - 1000
    _finished_run(env, "r-f1", "small-calc", "failed", 0.2, 0.2, 100, now)
    write_state(env.runs / "r-live", status="running", started=now + 5, steps={}, order=[])
    runs.save_sidecar("r-live", {"id": "small-calc", "title": "x"}, {})
    e = client.get("/api/tasks/small-calc/estimate").json()
    assert e["n"] == 1 and e["n_ok"] == 0 and e["usd"] == 0.2 and e["success_rate"] == 0


def test_estimate_computes_and_saves_costs_for_runs_never_opened(env, client):
    now = time.time() - 500
    d = env.runs / "r-unseen"
    write_state(d, status="ok", started=now, finished=now + 50, order=["prep"],
                steps={"prep": {"kind": "run", "status": "ok", "started": now, "finished": now + 50}})
    runs.save_sidecar("r-unseen", {"id": "methods-card", "title": "m"}, {})
    e = client.get("/api/tasks/methods-card/estimate").json()
    assert e["n"] == 1 and e["usd"] == 0 and e["seconds"] == pytest.approx(50, abs=1)
    assert (d / "costs.json").is_file()
