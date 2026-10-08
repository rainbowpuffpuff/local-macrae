"""Pick the newest group papers and fetch their abstracts from OpenAlex. Prints JSON for the flow.

One folder per paper under $FLOW_RUN_DIR/papers/<n>/paper/paper.json, so each agent gets only its paper.
"""
import argparse
import json
import os
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def openalex(doi):
    url = "https://api.openalex.org/works/" + urllib.parse.quote(f"https://doi.org/{doi}", safe="")
    req = urllib.request.Request(url, headers={"User-Agent": "local-macrae agent-runner example"})
    with urllib.request.urlopen(req, timeout=30) as r:
        w = json.load(r)
    inv = w.get("abstract_inverted_index") or {}
    words = sorted((i, word) for word, idx in inv.items() for i in idx)
    return {
        "abstract": " ".join(word for _, word in words) or None,
        "topics": [t.get("display_name") for t in (w.get("topics") or [])][:5],
        "referenced_works": len(w.get("referenced_works") or []),
        "cited_by_count": w.get("cited_by_count"),
        "openalex_id": w.get("id"),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=3)
    ap.add_argument("--author", default="Jungwirth")
    a = ap.parse_args()
    pubs = json.loads((ROOT / "data/group_publications.json").read_text())
    pubs = [p for p in pubs if p.get("doi") and a.author.lower() in json.dumps(p.get("authors", "")).lower()]
    pubs.sort(key=lambda p: p.get("year") or 0, reverse=True)
    out = []
    base = Path(os.environ.get("FLOW_RUN_DIR", "/tmp")) / "papers"
    for i, p in enumerate(pubs[: a.n]):
        d = base / str(i) / "paper"
        d.mkdir(parents=True, exist_ok=True)
        try:
            extra = openalex(p["doi"])
        except Exception as e:  # keep going; the agent is told when the abstract is missing
            extra = {"abstract": None, "openalex_error": str(e)}
        (d / "paper.json").write_text(json.dumps({**p, **extra}, indent=1, ensure_ascii=False))
        out.append({"doi": p["doi"], "title": p.get("title"), "dir": str(d)})
    print(json.dumps(out))


if __name__ == "__main__":
    main()
