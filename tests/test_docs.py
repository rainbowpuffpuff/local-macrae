"""Checks on the documentation: README.md, docs/*.md and docs/architecture.html.

The architecture page is public, so it must hold no secrets and no account names; its links and diagram labels
must hold up. The Markdown docs must only link to files and headings that exist.
"""
import re
from html.parser import HTMLParser
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
ARCH = ROOT / "docs" / "architecture.html"
MARKDOWN = [ROOT / "README.md", ROOT / "docs" / "DEMO.md", ROOT / "docs" / "TRACES.md"]

# Strings that must never appear in the public architecture page: the Cloudflare/Modal account name (it is part of
# the live URL), and shapes of real keys.
ACCOUNT_NAMES = ["acalincarol"]
SECRET_PATTERNS = [
    r"sk-ant-[A-Za-z0-9_-]{8,}",          # Anthropic API key
    r"\bsk_[0-9a-f]{20,}",                # ElevenLabs API key
    r"\bak-[A-Za-z0-9]{16,}", r"\bas-[A-Za-z0-9]{16,}",   # Modal token id / secret
    r"\bgh[pousr]_[A-Za-z0-9]{20,}",      # GitHub token
    r"\bAKIA[0-9A-Z]{16}\b",              # AWS access key
    r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[a-z]{2,}",   # any e-mail address
]
VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "source", "track", "wbr"}


def test_docs_exist():
    for p in MARKDOWN + [ARCH]:
        assert p.is_file() and p.stat().st_size > 2000, p


@pytest.mark.parametrize("path", MARKDOWN + [ARCH], ids=lambda p: p.name)
def test_no_secrets(path):
    text = path.read_text(encoding="utf-8")
    for pat in SECRET_PATTERNS:
        found = re.findall(pat, text)
        assert not found, f"{path.name}: looks like a secret or address: {found[:3]}"


def test_architecture_has_no_account_names():
    text = ARCH.read_text(encoding="utf-8").lower()
    for name in ACCOUNT_NAMES:
        assert name not in text


class _Tree(HTMLParser):
    """Collects open/close mismatches, ids, in-page links and SVG text/rect geometry."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.stack, self.errors, self.ids, self.hrefs = [], [], set(), []
        self.svgs = []        # [{"viewbox": (w, h), "boxes": [...], "texts": [...]}]
        self.groups = []      # stack of rects of the <g> elements we are in (None when the g has no box)
        self.text = None

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if a.get("id"):
            self.ids.add(a["id"])
        if tag == "a" and a.get("href", "").startswith("#"):
            self.hrefs.append(a["href"][1:])
        if tag == "svg":
            _, _, w, h = (float(v) for v in a["viewbox"].split())
            self.svgs.append({"viewbox": (w, h), "texts": []})
        if tag == "g":
            self.groups.append(None)
        if tag == "rect" and "b" in a.get("class", "").split() and self.groups and self.groups[-1] is None:
            self.groups[-1] = tuple(float(a[k]) for k in ("x", "y", "width", "height"))
        if tag == "text":
            box = next((g for g in reversed(self.groups) if g), None)
            self.text = {"x": float(a["x"]), "y": float(a["y"]), "cls": a.get("class", ""), "box": box, "s": ""}
        if tag not in VOID:
            self.stack.append((tag, self.getpos()))

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag not in VOID:
            self.stack.pop()

    def handle_endtag(self, tag):
        if tag == "g":
            self.groups.pop()
        if tag == "text" and self.text is not None:
            self.svgs[-1]["texts"].append(self.text)
            self.text = None
        if not self.stack or self.stack[-1][0] != tag:
            self.errors.append(f"</{tag}> at line {self.getpos()[0]}, open: {self.stack[-1:]}")
            return
        self.stack.pop()

    def handle_data(self, data):
        if self.text is not None:
            self.text["s"] += data


@pytest.fixture(scope="module")
def arch():
    t = _Tree()
    t.feed(ARCH.read_text(encoding="utf-8"))
    t.close()
    return t


def test_architecture_html_is_well_formed(arch):
    assert not arch.errors, arch.errors[:5]
    assert not arch.stack, arch.stack


def test_architecture_in_page_links_resolve(arch):
    missing = [h for h in arch.hrefs if h not in arch.ids]
    assert not missing, missing


def test_architecture_diagram_labels_stay_inside(arch):
    """Every label starts inside its box and inside the drawing (static check; the browser test measures widths)."""
    problems = []
    for svg in arch.svgs:
        vw, vh = svg["viewbox"]
        for t in svg["texts"]:
            if not (0 <= t["x"] < vw and 0 < t["y"] <= vh):
                problems.append(f"outside the drawing: {t['s']!r}")
            box = t["box"]
            if box and not (box[0] < t["x"] < box[0] + box[2] and box[1] < t["y"] <= box[1] + box[3]):
                problems.append(f"outside its box: {t['s']!r}")
    assert not problems, problems


MEASURE = r"""() => [...document.querySelectorAll('svg')].flatMap(svg => {
  const vb = svg.viewBox.baseVal, out = [];
  const items = [...svg.querySelectorAll('text')].map(t => {
    const g = t.closest('g'), r = g && g.querySelector(':scope > rect.b');
    return {s: t.textContent, b: t.getBBox(), r: r && r.getBBox()};
  });
  items.forEach((a, i) => {
    const {b, r} = a;
    if (b.x + b.width > vb.width) out.push('outside the drawing: ' + a.s);
    if (r && (b.x + b.width > r.x + r.width - 4 || b.y < r.y || b.y + b.height > r.y + r.height))
      out.push('overflows its box: ' + a.s);
    items.slice(i + 1).forEach(o => {
      const ix = Math.min(b.x + b.width, o.b.x + o.b.width) - Math.max(b.x, o.b.x);
      const iy = Math.min(b.y + b.height, o.b.y + o.b.height) - Math.max(b.y, o.b.y);
      if (ix > 1 && iy > 2) out.push('overlaps: ' + a.s + ' / ' + o.s);
    });
  });
  return out;
})"""


def test_architecture_diagram_labels_fit_in_a_browser():
    """Renders the page in headless Chromium and measures every label: none overflows its box or overlaps another.
    Skipped when Python Playwright and its Chromium aren't installed (pip install playwright; playwright install
    chromium)."""
    sync_api = pytest.importorskip("playwright.sync_api")
    with sync_api.sync_playwright() as p:
        try:
            browser = p.chromium.launch()
        except Exception as e:  # no browser downloaded, or the sandbox can't start it
            pytest.skip(f"no headless Chromium: {str(e).splitlines()[0]}")
        try:
            page = browser.new_page(viewport={"width": 1200, "height": 900})
            page.goto(ARCH.as_uri())
            problems = page.evaluate(MEASURE)
        finally:
            browser.close()
    assert not problems, problems


def _slug(heading: str) -> str:
    """GitHub's anchor for a heading."""
    s = re.sub(r"[`*_]", "", heading.strip().lower())
    s = re.sub(r"[^\w\- ]", "", s)
    return s.replace(" ", "-")


@pytest.mark.parametrize("path", MARKDOWN, ids=lambda p: p.name)
def test_markdown_links_resolve(path):
    text = path.read_text(encoding="utf-8")
    body = re.sub(r"```.*?```", "", text, flags=re.S)
    anchors = {_slug(m) for m in re.findall(r"^#{1,6} (.+)$", body, flags=re.M)}
    bad = []
    for target in re.findall(r"\]\(([^)\s]+)\)", body):
        if re.match(r"https?://", target):
            continue
        file, _, anchor = target.partition("#")
        dest = (path.parent / file).resolve() if file else path
        if not dest.exists():
            bad.append(target)
        elif anchor and dest == path and anchor not in anchors:
            bad.append(target)
    assert not bad, f"{path.name}: broken links {bad}"
