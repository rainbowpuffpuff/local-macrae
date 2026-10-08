"""Pull the Jungwirth group publication list from the IOCB site API.

The /en/publications page is a Vue archive that calls /en/api/publications?page=N
(10 items per page, page starts at 0). We page until an empty payload.
"""
import csv
import html
import json
import re
import sys
import time
import urllib.request
from pathlib import Path

API = "https://jungwirth.group.uochb.cz/en/api/publications"
OUT = Path(__file__).resolve().parent.parent / "data"

# entries the site lists without a link; DOI checked against Crossref (journal/pages match)
DOI_FIXES = {
    "Molecular Dynamics Simulations of Atmospheric Oxidants at the Air−Water Interface:": "10.1021/jp051361+",
}


def get_page(page):
    req = urllib.request.Request(
        f"{API}?page={page}",
        headers={"Accept": "application/json", "User-Agent": "iocb-chat literature fetcher"},
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)["payload"]


def strip_tags(s):
    return html.unescape(re.sub(r"<[^>]+>", "", s or "")).strip()


def parse_info(info):
    # e.g. "<i>Soft Matter</i> <strong>22</strong> (33): 5630–5639 (2026)"
    journal = re.search(r"<i>(.*?)</i>", info or "")
    volume = re.search(r"<strong>(.*?)</strong>", info or "")
    year = re.search(r"\((\d{4})\)\s*$", strip_tags(info))
    volume = strip_tags(volume.group(1)) if volume else ""
    if year:
        year = int(year.group(1))
    elif re.fullmatch(r"(19|20)\d\d", volume) and volume != "1970":
        # "Molecular Physics 2022: e2146541" style, year sits where the volume goes
        year = int(volume)
    else:
        # the site uses 1970 for Early View papers with no issue yet
        year = None
    return {
        "journal": strip_tags(journal.group(1)) if journal else "",
        "volume": volume,
        "year": year,
        "citation": strip_tags(info),
    }


def doi_from_link(link):
    m = re.search(r"doi\.org/(10\.\S+)", link or "", re.I)
    return m.group(1).strip() if m else ""


def main():
    rows, page = [], 0
    while True:
        items = get_page(page)
        if not items:
            break
        for it in items:
            authors = it.get("authors") or []
            title = strip_tags(it.get("name"))
            doi = doi_from_link(it.get("link")) or next(
                (d for t, d in DOI_FIXES.items() if title.startswith(t)), "")
            rows.append({
                "title": title,
                **parse_info(it.get("info")),
                "doi": doi,
                "link": it.get("link") or "",
                "authors": "; ".join(a["name"] for a in authors),
                "group_authors": "; ".join(a["name"] for a in authors if a.get("isGroupAuthor")),
                "n_authors": len(authors),
            })
        print(f"page {page}: {len(items)} (total {len(rows)})", file=sys.stderr)
        page += 1
        time.sleep(0.5)

    OUT.mkdir(exist_ok=True)
    (OUT / "group_publications.json").write_text(json.dumps(rows, ensure_ascii=False, indent=1))
    with open(OUT / "group_publications.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)
    write_markdown(rows, OUT / "group_publications.md")
    print(f"wrote {len(rows)} publications to {OUT}", file=sys.stderr)


def write_markdown(rows, path):
    lines = [
        "# Pavel Jungwirth group publications",
        "",
        f"Source: {API} (IOCB site, {len(rows)} entries, covers 2004 onward).",
        "Includes papers by any group member, not only ones with P. Jungwirth as author.",
        "",
    ]
    years = sorted({r["year"] for r in rows if r["year"]}, reverse=True)
    for year in years + [None]:
        group = [r for r in rows if r["year"] == year]
        if not group:
            continue
        lines += [f"## {year or 'Early view / no year'} ({len(group)})", ""]
        for r in group:
            ref = f"[{r['doi']}](https://doi.org/{r['doi']})" if r["doi"] else (r["link"] or "no DOI")
            lines.append(f"1. **{r['title']}**. {r['authors']}. *{r['citation']}*. {ref}")
        lines.append("")
    path.write_text("\n".join(lines))


if __name__ == "__main__":
    main()
