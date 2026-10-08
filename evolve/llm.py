"""One structured Claude call (Anthropic Python SDK), used by distill and reusable by the backend's planner.

    data, usage = llm.call_json(system, user, schema)      # raises LLMError on any failure
    usage == {"model", "in", "out", "cache_read", "cache_write", "usd", "seconds", "request_id"}

Only when ANTHROPIC_API_KEY is set (config.llm_enabled()); callers fall back to rules on LLMError. The response is
constrained to the JSON schema (output_config.format). Opus/Sonnet 5.x requests opt into server-side refusal
fallbacks ("default" routing), retried once without them if the API rejects the parameter.
"""

from __future__ import annotations

import json
import time
from typing import Any, Optional

from . import config, costs

FALLBACK_BETA = "server-side-fallback-2026-07-01"
FALLBACK_MODELS = ("claude-fable-5-1", "claude-opus-5-5", "claude-opus-5", "claude-sonnet-5-5")


class LLMError(Exception):
    pass


def _client(timeout: float):
    try:
        import anthropic
    except ImportError as e:
        raise LLMError("the anthropic package is not installed (pip install -r evolve/requirements.txt)") from e
    return anthropic, anthropic.Anthropic(timeout=timeout, max_retries=2)


def call_json(system: str, user: str, schema: dict, model: Optional[str] = None, max_tokens: int = 16000,
              effort: str = "medium", timeout: float = 180.0) -> tuple[dict, dict]:
    if not config.llm_enabled():
        raise LLMError("no ANTHROPIC_API_KEY (or MACRAE_EVOLVE_LLM=0)")
    model = model or config.model()
    anthropic, client = _client(timeout)
    req: dict[str, Any] = dict(
        model=model, max_tokens=max_tokens, system=system,
        messages=[{"role": "user", "content": user}],
        output_config={"effort": effort, "format": {"type": "json_schema", "schema": schema}},
    )
    use_fallbacks = model in FALLBACK_MODELS
    t0 = time.time()
    try:
        try:
            if use_fallbacks:
                resp = client.beta.messages.create(**req, betas=[FALLBACK_BETA], fallbacks="default")
            else:
                resp = client.beta.messages.create(**req)
        except anthropic.BadRequestError:
            if not use_fallbacks:
                raise
            resp = client.beta.messages.create(**req)  # an endpoint without the fallback beta
    except anthropic.APIStatusError as e:
        raise LLMError(f"Claude API {getattr(e, 'status_code', '?')}: {str(getattr(e, 'message', e))[:300]}") from e
    except anthropic.APIConnectionError as e:
        raise LLMError(f"Claude API unreachable: {e}") from e
    except Exception as e:  # a bad request shape, an SDK mismatch: never break the caller
        raise LLMError(f"Claude call failed: {type(e).__name__}: {e}") from e
    seconds = time.time() - t0
    u = getattr(resp, "usage", None)
    served = str(getattr(resp, "model", "") or model)
    tokens = {"in": int(getattr(u, "input_tokens", 0) or 0), "out": int(getattr(u, "output_tokens", 0) or 0),
              "cache_read": int(getattr(u, "cache_read_input_tokens", 0) or 0),
              "cache_write": int(getattr(u, "cache_creation_input_tokens", 0) or 0)}
    usage = {"model": served, **tokens, "usd": round(costs.llm_usd(served, tokens), 5), "seconds": round(seconds, 1),
             "request_id": str(getattr(resp, "_request_id", "") or "")}
    stop = getattr(resp, "stop_reason", None)
    if stop == "refusal":
        raise LLMError("Claude declined the request (refusal)")
    if stop == "max_tokens":
        raise LLMError("the answer was cut off (max_tokens)")
    text = next((getattr(b, "text", "") for b in getattr(resp, "content", None) or []
                 if getattr(b, "type", "") == "text"), "")
    try:
        data = json.loads(text)
    except ValueError as e:
        raise LLMError(f"the answer is not JSON: {text[:200]!r}") from e
    if not isinstance(data, dict):
        raise LLMError("the answer is not a JSON object")
    return data, usage
