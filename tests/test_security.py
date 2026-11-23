"""Security behaviour: write rate limits and sign-in mode (DEMO_MODE off)."""

import pytest
from conftest import NOW

from lablink import create_app
from lablink.ai import LabAssistant
from lablink.repository import MemoryRepository

IAP = "X-Goog-Authenticated-User-Email"


def make_client(**config):
    base = {"TESTING": True, "SECRET_KEY": "t", "AI_RATE_LIMIT": 1000}
    app = create_app(repo=MemoryRepository(), assistant=LabAssistant(None), clock=lambda: NOW, config={**base, **config})
    c = app.test_client()
    with c.session_transaction() as s:
        s["_csrf"] = "tok"
    return app, c


# --- Write rate limits ---------------------------------------------------------------

def test_form_submissions_are_rate_limited_per_browser():
    _, c = make_client(WRITE_RATE_LIMIT=3)
    codes = [c.post("/switch-user", data={"csrf_token": "tok", "user_id": "s_rahul"}).status_code for _ in range(4)]
    assert codes == [302, 302, 302, 429]


def test_reads_are_not_rate_limited():
    _, c = make_client(WRITE_RATE_LIMIT=1)
    assert all(c.get("/").status_code == 200 for _ in range(5))


def test_global_write_cap_applies_across_browsers():
    app, _ = make_client(WRITE_GLOBAL_RATE_LIMIT=2)
    codes = []
    for i in range(3):
        c = app.test_client()
        with c.session_transaction() as s:
            s["_csrf"] = f"t{i}"
        codes.append(c.post("/switch-user", data={"csrf_token": f"t{i}", "user_id": "s_rahul"}).status_code)
    assert codes == [302, 302, 429]


# --- Sign-in mode (DEMO_MODE off) -------------------------------------------------------

@pytest.fixture
def signed_in_app():
    return make_client(DEMO_MODE=False)


def test_every_page_requires_sign_in(signed_in_app):
    _, c = signed_in_app
    r = c.get("/")
    assert r.status_code == 401 and "Sign in required" in r.get_data(as_text=True)
    assert c.get("/requests").status_code == 401
    assert c.get("/healthz").status_code == 200  # uptime checks still work


def test_identity_header_selects_the_user(signed_in_app):
    _, c = signed_in_app
    page = c.get("/requests", headers={IAP: "accounts.google.com:Meera@LabLink.example.edu"}).get_data(as_text=True)
    assert "Signed in as Meera Pillai" in page and "Viewing as" not in page


def test_unknown_or_malformed_identity_is_rejected(signed_in_app):
    _, c = signed_in_app
    assert c.get("/", headers={IAP: "accounts.google.com:stranger@evil.example"}).status_code == 401
    assert c.get("/", headers={IAP: "not-an-email"}).status_code == 401


def test_role_switching_and_reset_are_disabled(signed_in_app):
    _, c = signed_in_app
    headers = {IAP: "accounts.google.com:lab@lablink.example.edu"}
    assert c.post("/switch-user", data={"csrf_token": "tok", "user_id": "a_lab"}, headers=headers).status_code == 404
    assert c.post("/demo/reset", data={"csrf_token": "tok"}, headers=headers).status_code == 404


def test_permissions_follow_the_signed_in_identity(signed_in_app):
    _, c = signed_in_app
    student = {IAP: "accounts.google.com:ananya@lablink.example.edu"}
    coordinator = {IAP: "accounts.google.com:lab@lablink.example.edu"}
    assert c.get("/allocate", headers=student).status_code == 403
    assert c.get("/allocate", headers=coordinator).status_code == 200
