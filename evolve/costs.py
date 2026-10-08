"""What a run cost, for metrics and for the distiller.

The backend owns the real numbers (server/costs.py; `GET /api/runs/{id}` → "costs"). evolve uses them when the
backend is importable and returns them; otherwise it estimates the same way the contract describes:
LLM = Claude Code's own total_cost_usd per trial (else tokens × PRICES), compute = Modal sandbox seconds × RATES.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any, Optional

if TYPE_CHECKING:
    from .runs import Run

# USD per million tokens: input, output, cache read, cache write (5-minute). Source: Anthropic pricing as cached in
# the claude-api reference, 2026-10-06. Unknown models fall back to "default".
PRICES: dict[str, tuple[float, float, float, float]] = {
    "claude-fable-5-1": (10.0, 50.0, 0.25, 12.5),
    "claude-fable-5": (10.0, 50.0, 1.0, 12.5),
    "claude-opus-5-5": (4.0, 20.0, 0.20, 5.0),
    "claude-opus-5": (5.0, 25.0, 0.50, 6.25),
    "claude-opus-4-8": (5.0, 25.0, 0.50, 6.25),
    "claude-opus-4-7": (5.0, 25.0, 0.50, 6.25),
    "claude-opus-4-6": (5.0, 25.0, 0.50, 6.25),
    "claude-sonnet-5-5": (2.0, 10.0, 0.20, 2.5),
    "claude-sonnet-5": (2.0, 10.0, 0.20, 2.5),
    "claude-sonnet-4-6": (3.0, 15.0, 0.30, 3.75),
    "claude-haiku-5-5": (0.10, 0.50, 0.01, 0.125),
    "claude-haiku-4-5": (1.0, 5.0, 0.10, 1.25),
    "default": (3.0, 15.0, 0.30, 3.75),
}

# Modal, USD per second. Source: modal.com/pricing (CPU per physical core, memory per GiB, GPUs per device);
# check before quoting. Hardware names follow the planner's vocabulary: cpu-<cores> | gpu-<type>[-<count>].
RATES = {
    "cpu_core": 0.0000131,
    "mem_gib": 0.00000222,
    "gpu": {"t4": 0.000164, "l4": 0.000222, "a10g": 0.000306, "l40s": 0.000542, "a100": 0.000583,
            "a100-40gb": 0.000583, "a100-80gb": 0.000694, "h100": 0.001097, "h200": 0.001261, "b200": 0.001736},
}
DEFAULT_HARDWARE = "cpu-1"  # Harbor's default Modal sandbox: 1 core, 2 GiB


def price(model: str) -> tuple[float, float, float, float]:
    m = (model or "").lower().split("/")[-1]
    m = re.sub(r"-\d{8}$", "", m)
    if m in PRICES:
        return PRICES[m]
    for k in sorted(PRICES, key=len, reverse=True):
        if k != "default" and m.startswith(k):
            return PRICES[k]
    return PRICES["default"]


def llm_usd(model: str, tokens: dict) -> float:
    p_in, p_out, p_cr, p_cw = price(model)
    return (int(tokens.get("in") or 0) * p_in + int(tokens.get("out") or 0) * p_out
            + int(tokens.get("cache_read") or tokens.get("cache") or 0) * p_cr
            + int(tokens.get("cache_write") or 0) * p_cw) / 1e6


def hardware_spec(hw: str) -> dict:
    """{"cores", "mem_gib", "gpu", "gpus"} for a planner hardware name."""
    hw = (hw or DEFAULT_HARDWARE).lower().strip()
    m = re.fullmatch(r"cpu-(\d+)", hw)
    if m:
        cores = int(m.group(1))
        return {"cores": cores, "mem_gib": 2.0 * cores, "gpu": "", "gpus": 0}
    m = re.fullmatch(r"gpu-([a-z0-9]+(?:-\d+gb)?)(?:-(\d+))?", hw)
    if m and m.group(1) in RATES["gpu"]:
        n = int(m.group(2) or 1)
        return {"cores": 4 * n, "mem_gib": 16.0 * n, "gpu": m.group(1), "gpus": n}
    return hardware_spec(DEFAULT_HARDWARE)


def compute_usd(seconds: float, hw: str) -> float:
    s = hardware_spec(hw)
    per_s = s["cores"] * RATES["cpu_core"] + s["mem_gib"] * RATES["mem_gib"]
    if s["gpu"]:
        per_s += s["gpus"] * RATES["gpu"][s["gpu"]]
    return max(0.0, seconds) * per_s


def _from_server(run: "Run") -> Optional[dict]:
    from .runs import _server
    srv = _server()
    if srv is None:
        return None
    try:
        r = srv[1].get_run(run.id)
    except Exception:
        return None
    c = r.get("costs") if isinstance(r, dict) else None
    if isinstance(c, dict) and isinstance(c.get("total_usd"), (int, float)):
        return dict(c, source="server")
    return None


def estimate(run: "Run") -> dict:
    tokens = {"in": 0, "out": 0, "cache": 0}
    llm = comp = 0.0
    by_step: dict[str, dict[str, Any]] = {}
    for s in run.agent_steps:
        s_llm = s_comp = s_secs = 0.0
        model = ""
        for tr in s.trials:
            model = tr.model or model
            tokens["in"] += tr.tokens.get("in", 0)
            tokens["out"] += tr.tokens.get("out", 0)
            tokens["cache"] += tr.tokens.get("cache_read", 0)
            s_llm += tr.cost_usd if tr.cost_usd is not None else llm_usd(tr.model, tr.tokens)
            secs = tr.seconds or 0.0
            s_secs += secs
            if (s.environment or "modal") == "modal":
                s_comp += compute_usd(secs, run.hardware)
        if not s.trials and s.seconds and (s.environment or "") == "modal":
            s_secs = s.seconds
            s_comp = compute_usd(s.seconds, run.hardware)
        llm += s_llm
        comp += s_comp
        by_step[s.key] = {"llm_usd": round(s_llm, 4), "compute_usd": round(s_comp, 4), "seconds": round(s_secs, 1),
                          "model": model, "hardware": run.hardware or DEFAULT_HARDWARE}
    return {"llm_usd": round(llm, 4), "compute_usd": round(comp, 4), "total_usd": round(llm + comp, 4),
            "tokens": tokens, "by_step": by_step, "wall_s": run.wall_s, "source": "evolve-estimate"}


def run_costs(run: "Run") -> dict:
    return _from_server(run) or estimate(run)
