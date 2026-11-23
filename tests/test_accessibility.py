"""Automated accessibility checks (WCAG 2.1 AA essentials) on every page and role.

Uses only the standard-library HTML parser. Complements the manual axe-core audit:
these run on every push, so regressions are caught in CI.
"""

from __future__ import annotations

import re
from collections import Counter
from html.parser import HTMLParser
from pathlib import Path

import pytest
from conftest import login

STUDENT_PAGES = ["/", "/?q=print+an+enclosure", "/equipment/eq_fdm_printer", "/equipment/eq_uvvis",
                 "/requests", "/bookings", "/requests/rq_drone/plan"]
FACULTY_PAGES = ["/requests", "/allocate", "/insights"]
ADMIN_PAGES = ["/requests", "/bookings", "/allocate?equipment=eq_fdm_printer", "/insights"]
LABELLABLE = {"input", "select", "textarea"}
NOT_LABELLED_INPUTS = {"hidden", "submit", "button", "reset", "image"}


class Page(HTMLParser):
    """Collects the facts the checks need from one HTML page."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.elements: list[tuple[str, dict[str, str]]] = []
        self.label_for: set[str] = set()
        self.ids: list[str] = []
        self.h1 = 0
        self.lang = ""
        self.text_stack: list[list[str]] = []  # accessible-name text for open <a>/<button>/<label>
        self.named: list[tuple[str, dict[str, str], str]] = []
        self.label_depth = 0
        self.wrapped_controls: set[int] = set()

    def handle_starttag(self, tag, attrs):
        a = {k: (v or "") for k, v in attrs}
        self.elements.append((tag, a))
        if "id" in a:
            self.ids.append(a["id"])
        if tag == "html":
            self.lang = a.get("lang", "")
        if tag == "h1":
            self.h1 += 1
        if tag == "label":
            self.label_depth += 1
            if a.get("for"):
                self.label_for.add(a["for"])
        if tag in LABELLABLE and self.label_depth:
            self.wrapped_controls.add(len(self.elements) - 1)
        if tag in ("a", "button"):
            self.text_stack.append([])
        if tag == "img" and self.text_stack:
            self.text_stack[-1].append(a.get("alt", ""))

    def handle_endtag(self, tag):
        if tag == "label":
            self.label_depth = max(0, self.label_depth - 1)
        if tag in ("a", "button") and self.text_stack:
            text = " ".join(self.text_stack.pop()).strip()
            opening = next(e for e in reversed(self.elements) if e[0] == tag)
            self.named.append((tag, opening[1], text))

    def handle_data(self, data):
        for frame in self.text_stack:
            frame.append(data)


def parse(html: str) -> Page:
    page = Page()
    page.feed(html)
    return page


def problems(html: str) -> list[str]:
    page = parse(html)
    found = []
    if not page.lang:
        found.append("missing <html lang>")
    if page.h1 != 1:
        found.append(f"expected exactly one <h1>, found {page.h1}")
    mains = [a for t, a in page.elements if t == "main"]
    if len(mains) != 1 or mains[0].get("id") != "main":
        found.append("need exactly one <main id='main'>")
    if not any(t == "a" and a.get("href") == "#main" for t, a in page.elements):
        found.append("missing skip link to #main")
    dupes = [i for i, n in Counter(page.ids).items() if n > 1]
    if dupes:
        found.append(f"duplicate ids: {dupes}")
    for index, (tag, a) in enumerate(page.elements):
        if tag in LABELLABLE and a.get("type", "text") not in NOT_LABELLED_INPUTS:
            labelled = (
                a.get("id") in page.label_for
                or a.get("aria-label")
                or a.get("aria-labelledby")
                or index in page.wrapped_controls
            )
            if not labelled:
                found.append(f"unlabelled <{tag} name={a.get('name')!r}>")
        if tag == "img" and "alt" not in a:
            found.append(f"<img src={a.get('src')!r}> has no alt")
        if a.get("tabindex", "0").lstrip("-").isdigit() and int(a.get("tabindex", "0")) > 0:
            found.append(f"positive tabindex on <{tag}>")
        if tag == "th" and a.get("scope") not in ("col", "row"):
            found.append("<th> without scope")
    for tag, a, text in page.named:
        if not (text or a.get("aria-label") or a.get("aria-labelledby")):
            found.append(f"<{tag}> with no accessible name ({a.get('href') or a.get('type')})")
    tables = html.count("<table")
    if tables and html.count("<caption") < tables:
        found.append("table without <caption>")
    return found


def check(client, path, method="get", **kw):
    r = getattr(client, method)(path, **kw)
    assert r.status_code in (200, 400, 401, 403, 404, 409), (path, r.status_code)
    assert problems(r.get_data(as_text=True)) == [], path


@pytest.mark.parametrize("path", STUDENT_PAGES)
def test_student_pages(client, path):
    login(client, "s_ananya")
    check(client, path)


@pytest.mark.parametrize("path", FACULTY_PAGES)
def test_faculty_pages(client, path):
    login(client, "f_iyer")
    check(client, path)


@pytest.mark.parametrize("path", ADMIN_PAGES)
def test_coordinator_pages(client, path):
    login(client, "a_lab")
    check(client, path)


def test_result_and_error_states(client):
    login(client, "s_meera")
    check(client, "/requests/suggest", method="post",
          data={"csrf_token": "tok", "description": "EMG prosthetic hand with printed finger joints"})
    check(client, "/equipment/eq_fdm_printer/book", method="post",  # full slot → alternatives offered
          data={"csrf_token": "tok", "request_id": "rq_hand", "date": "2026-11-24", "start_hour": "10", "hours": "2"})
    check(client, "/equipment/eq_dso/book", method="post",  # validation errors
          data={"csrf_token": "tok", "request_id": "rq_hand", "date": "bad", "start_hour": "10", "hours": "2"})
    check(client, "/equipment/does_not_exist")


def test_checker_catches_real_problems():
    bad = '<html><body><h1>a</h1><h1>b</h1><input name="x"><img src="p.png"><a href="/x"></a></body></html>'
    found = problems(bad)
    assert any("lang" in f for f in found) and any("exactly one <h1>" in f for f in found)
    assert any("unlabelled" in f for f in found) and any("no alt" in f for f in found)
    assert any("accessible name" in f for f in found) and any("skip link" in f for f in found)


# --- Colour contrast of the design tokens (WCAG 2.1 AA: 4.5:1 for normal text) -------

def _tokens() -> dict[str, str]:
    css = (Path(__file__).resolve().parents[1] / "lablink" / "static" / "style.css").read_text(encoding="utf-8")
    root = css[css.index(":root") : css.index("}", css.index(":root"))]
    return dict(re.findall(r"--([a-z0-9-]+):\s*(#[0-9a-fA-F]{6})", root))


def _luminance(hex_colour: str) -> float:
    channels = [int(hex_colour[i : i + 2], 16) / 255 for i in (1, 3, 5)]
    linear = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in channels]
    return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]


def contrast(a: str, b: str) -> float:
    la, lb = sorted((_luminance(a), _luminance(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


@pytest.mark.parametrize(
    "foreground, background",
    [
        ("ink", "butter"), ("ink", "paper"), ("ink-soft", "butter"), ("ink-soft", "paper"),
        ("link", "butter"), ("link", "paper"), ("ink", "yellow"),
        *[("ink", d) for d in ("cs", "ece", "mech", "bio", "des", "oth")],
        *[("ink", f"{d}-bg") for d in ("cs", "ece", "mech", "bio", "des", "oth")],
        *[("ink-soft", f"{d}-bg") for d in ("cs", "ece", "mech", "bio", "des", "oth")],
    ],
)
def test_design_token_contrast(foreground, background):
    tokens = _tokens()
    ratio = contrast(tokens[foreground], tokens[background])
    assert ratio >= 4.5, f"--{foreground} on --{background} is {ratio:.2f}:1"


def test_text_on_dark_bars_has_contrast():
    assert contrast("#ffffff", _tokens()["ink"]) >= 4.5  # demo bar text
