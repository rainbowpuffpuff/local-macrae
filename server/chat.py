"""Typed chat with Jarvis: a normal conversation that draws on the group's papers when they help.

Every message retrieves passages from the RAG index; Claude answers like a capable assistant, citing [n] only for
claims that come from those passages, and says which parts are from the papers. It never refuses just because the
papers don't cover something. Conversation memory is kept per anonymous session id (in memory, last turns only).
"""

from __future__ import annotations

import logging
import os
import threading
import time
from collections import OrderedDict
from typing import Any

from . import bridges, costs

log = logging.getLogger("macrae.chat")

DEFAULT_MODEL = "claude-sonnet-5-5"
MAX_TURNS = 12            # remembered user+assistant messages per session
MAX_SESSIONS = 2000
_lock = threading.Lock()
_sessions: "OrderedDict[str, list[dict]]" = OrderedDict()

SYSTEM = """You are Jarvis, the research assistant of the Pavel Jungwirth group at the Institute of Organic \
Chemistry and Biochemistry (IOCB) in Prague. The group works on ions and water, interfaces, charge scaling (ECC), \
force fields (including Bayesian force-field learning), membranes and biomolecules, with molecular dynamics and \
quantum chemistry. Pavel Jungwirth, who led the group, passed away in July 2026; the group continues his research.

Talk like a warm, sharp colleague: answer greetings and small talk naturally, answer general science questions \
from your own knowledge, explain, brainstorm, follow up on earlier turns. Keep answers concise (a few short \
paragraphs at most) unless asked for depth.

Each user message comes with numbered passages retrieved from the group's papers. Use them whenever they are \
relevant: cite claims that come from a passage with its marker exactly as given, e.g. [2], and you may name the \
paper's first author and year. Never invent citations or cite a passage for something it doesn't say. If the \
passages don't cover the question, just answer helpfully from general knowledge without citations, and when it \
matters say briefly that this part isn't from the group's papers. Never reply that you found nothing; never refuse \
only because the papers lack the answer. You can also offer the tasks on the page (methods card for a paper, a \
small live calculation on Modal) when they fit."""


def model_name() -> str:
    return os.environ.get("MACRAE_CHAT_MODEL", "").strip() or DEFAULT_MODEL


def api_key() -> str:
    return (os.environ.get("MACRAE_CHAT_API_KEY") or os.environ.get("ANTHROPIC_API_KEY") or "").strip()


def _history(session: str) -> list[dict]:
    with _lock:
        h = _sessions.get(session)
        if h is None:
            h = []
            _sessions[session] = h
            while len(_sessions) > MAX_SESSIONS:
                _sessions.popitem(last=False)
        else:
            _sessions.move_to_end(session)
        return list(h)


def _remember(session: str, user: str, answer: str) -> None:
    with _lock:
        h = _sessions.setdefault(session, [])
        h.extend([{"role": "user", "content": user}, {"role": "assistant", "content": answer}])
        del h[:-MAX_TURNS]


def _passages(message: str) -> tuple[str, list[dict]]:
    try:
        ctx, cits = bridges.search_with_context(message[:1000], k=6)
        return ctx or "", cits or []
    except Exception as e:  # index missing or search error: chat still works
        log.warning("chat retrieval unavailable: %s", e)
        return "", []


def reply(session: str, message: str) -> dict[str, Any]:
    """One chat turn. Returns {answer, citations, model, cost: {usd, tokens}, seconds}."""
    t0 = time.time()
    message = message.strip()[:4000]
    ctx, cits = _passages(message)
    if ctx:
        user = (f"Passages from the group's papers (cite with their markers only if relevant):\n\n{ctx}\n\n"
                f"---\nUser message:\n{message}")
    else:
        user = f"(No passages from the group's papers matched this message.)\n\nUser message:\n{message}"
    msgs = _history(session) + [{"role": "user", "content": user}]

    import anthropic  # lazy: the server starts without it

    model = model_name()
    client = anthropic.Anthropic(api_key=api_key(), timeout=float(os.environ.get("MACRAE_CHAT_TIMEOUT", "") or 60),
                                 max_retries=1)
    resp = client.messages.create(model=model, max_tokens=1500, system=SYSTEM, messages=msgs)
    answer = "".join(getattr(b, "text", "") for b in resp.content if getattr(b, "type", "") == "text").strip()
    if not answer:
        answer = "Sorry, I lost my train of thought there. Could you say that again?"
    usage = resp.usage.to_dict() if hasattr(resp.usage, "to_dict") else dict(resp.usage or {})
    tokens = costs.norm_usage(usage)
    used = str(resp.model or model)
    usd = costs.llm_usd(used, tokens)
    _remember(session, message, answer)
    # only return the sources the answer actually cites, in their original numbering
    cited = [c for c in cits if c.get("key") and c["key"] in answer]
    return {"answer": answer, "citations": cited, "model": used,
            "cost": {"usd": round(usd, 6), "tokens": costs.public_tokens(tokens)},
            "seconds": round(time.time() - t0, 2)}
