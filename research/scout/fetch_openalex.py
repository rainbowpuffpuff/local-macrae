import json, sys, time, urllib.request, urllib.parse
pubs = json.load(open(sys.argv[1])); out = sys.argv[2]
recent = [p for p in pubs if (p.get('year') or 0) >= 2020 and p.get('doi')]
def inv(ix):
    if not ix: return ""
    pos = {}
    for w, ps in ix.items():
        for i in ps: pos[i] = w
    return " ".join(pos[i] for i in sorted(pos))
res = {}
t0 = time.time()
for i in range(0, len(recent), 40):
    chunk = recent[i:i+40]
    f = "doi:" + "|".join(p['doi'].lower() for p in chunk)
    url = "https://api.openalex.org/works?per-page=50&mailto=scout@example.org&filter=" + urllib.parse.quote(f, safe=':|/')
    data = json.load(urllib.request.urlopen(url, timeout=60))
    for w in data['results']:
        doi = (w.get('doi') or '').replace('https://doi.org/', '').lower()
        res[doi] = {"openalex": w['id'], "abstract": inv(w.get('abstract_inverted_index')),
                    "cited_by": w.get('cited_by_count'), "topics": [t['display_name'] for t in (w.get('topics') or [])[:3]],
                    "oa_url": (w.get('best_oa_location') or {}).get('landing_page_url')}
rows = []
for p in recent:
    r = res.get(p['doi'].lower(), {})
    rows.append({**{k: p[k] for k in ('title','journal','year','doi','authors')}, **r})
json.dump(rows, open(out, 'w'), indent=1, ensure_ascii=False)
print(f"{len(recent)} recent, {len(res)} found in OpenAlex, {sum(1 for r in rows if r.get('abstract'))} with abstract, {time.time()-t0:.1f}s")
