from conftest import login


def post(client, url, data=None, **kw):
    data = dict(data or {})
    data.setdefault("csrf_token", "tok")
    return client.post(url, data=data, **kw)


def test_public_pages_render(client):
    for path in ["/", "/?q=train+an+image+model", "/equipment/eq_fdm_printer", "/requests", "/bookings", "/healthz"]:
        assert client.get(path).status_code == 200, path


def test_security_headers(client):
    r = client.get("/")
    assert "default-src 'self'" in r.headers["Content-Security-Policy"]
    assert r.headers["X-Content-Type-Options"] == "nosniff"
    assert r.headers["X-Frame-Options"] == "DENY"


def test_accessibility_landmarks(client):
    html = client.get("/").get_data(as_text=True)
    assert 'lang="en"' in html and 'href="#main"' in html and '<main id="main"' in html
    assert '<label for="q">' in html


def test_csrf_required(client):
    assert client.post("/switch-user", data={"user_id": "f_rao"}).status_code == 400
    assert post(client, "/switch-user", {"user_id": "f_rao"}).status_code == 302


def test_switch_user_rejects_open_redirect(client):
    r = post(client, "/switch-user", {"user_id": "f_rao", "next": "//evil.example"})
    assert r.headers["Location"] == "/"


def test_role_gating(client):
    assert client.get("/allocate").status_code == 403
    login(client, "f_iyer")
    assert client.get("/allocate").status_code == 200
    assert client.get("/insights").status_code == 200


def test_unknown_equipment_404(client):
    assert client.get("/equipment/eq_nope").status_code == 404


def test_full_flow_request_approve_book(client, service):
    login(client, "s_arjun")
    r = post(client, "/requests/suggest", {"description": "Measure nitrate absorbance in lake water samples"})
    assert r.status_code == 200 and "Spectrophotometer" in r.get_data(as_text=True)
    r = post(client, "/requests", {
        "project_title": "Lake study", "description": "Measure nitrate absorbance in lake water samples",
        "deadline": "2026-12-20", "guide_id": "f_khan", "project_type": "research",
        "equipment_id": "eq_uvvis", "hours_eq_uvvis": "3",
    })
    assert r.status_code == 302
    request = next(r for r in service.repo.requests_for_student("s_arjun") if r.project_title == "Lake study")

    login(client, "f_khan")
    page = client.get("/requests").get_data(as_text=True)
    assert "Lake study" in page and "AI brief" in page
    assert post(client, f"/requests/{request.id}/decide", {"decision": "approve"}).status_code == 302

    login(client, "s_arjun")
    r = post(client, "/equipment/eq_uvvis/book", {"request_id": request.id, "date": "2026-11-30", "start_hour": "10", "hours": "2"})
    assert r.status_code == 302
    assert "UV-Vis" in client.get("/bookings").get_data(as_text=True)


def test_invalid_booking_shows_errors(client):
    login(client, "s_meera")
    r = post(client, "/equipment/eq_dso/book", {"request_id": "rq_hand", "date": "bad", "start_hour": "10", "hours": "2"})
    assert r.status_code == 400 and "valid date" in r.get_data(as_text=True)


def test_faculty_cannot_book(client):
    login(client, "f_rao")
    r = post(client, "/equipment/eq_dso/book", {"request_id": "rq_hand", "date": "2026-11-30", "start_hour": "10", "hours": "2"})
    assert r.status_code == 403


def test_allocation_page_and_apply(client, service):
    login(client, "a_lab")
    page = client.get("/allocate?equipment=eq_fdm_printer").get_data(as_text=True)
    assert "Swap proposed" in page and "Equivalent machine" in page
    assert post(client, "/allocate/eq_fdm_printer/apply").status_code == 302
    assert service.repo.waitlist("eq_fdm_printer") == []


def test_checklist(client):
    r = post(client, "/equipment/eq_laser_cutter/checklist", {"project": "cut acrylic base"})
    assert r.status_code == 200 and "safety training" in r.get_data(as_text=True)


def test_rate_limit(app, client):
    app.extensions["ai_limiter"].limit = 1
    assert client.get("/?q=gpu").status_code == 200
    assert client.get("/?q=gpu").status_code == 429


def test_global_rate_limit_caps_all_clients(app):
    app.extensions["ai_global_limiter"].limit = 2
    for i in range(3):
        c = app.test_client()
        with c.session_transaction() as s:
            s["_csrf"] = f"tok{i}"
        status = c.get("/?q=gpu").status_code
    assert status == 429


def test_rate_limit_is_per_browser(app):
    app.extensions["ai_limiter"].limit = 1
    first, second = app.test_client(), app.test_client()
    for c, tok in ((first, "a"), (second, "b")):
        with c.session_transaction() as s:
            s["_csrf"] = tok
    assert first.get("/?q=gpu").status_code == 200
    assert second.get("/?q=gpu").status_code == 200  # same IP, different browser
    assert first.get("/?q=gpu").status_code == 429


def test_managed_host_enables_secure_cookies_and_hsts(monkeypatch, repo):
    from lablink import create_app
    from lablink.ai import LabAssistant

    monkeypatch.setenv("RENDER", "true")
    app = create_app(repo=repo, assistant=LabAssistant(None), config={"TESTING": True, "SECRET_KEY": "t"})
    assert app.config["SESSION_COOKIE_SECURE"] is True
    r = app.test_client().get("/healthz", headers={"X-Forwarded-Proto": "https"})
    assert "Strict-Transport-Security" in r.headers


def test_suggest_shows_no_equipment_message_for_software(client):
    login(client, "s_ananya")
    r = post(client, "/requests/suggest", {"description": "Building an LALR(1) compiler in C++ for our compilers course"})
    page = r.get_data(as_text=True)
    assert r.status_code == 200 and "No lab equipment needed" in page
    assert 'checked' not in page.split("Equipment and total hours")[1].split("</fieldset>")[0]


def test_concurrent_booking_clash_returns_409(client, service):
    login(client, "s_meera")
    monday = "2026-11-30"
    r1 = post(
        client,
        "/equipment/eq_dso/book",
        {"request_id": "rq_hand", "date": monday, "start_hour": "10", "hours": "2"},
    )
    assert r1.status_code == 302

    r2 = post(
        client,
        "/equipment/eq_fdm_printer/book",
        {"request_id": "rq_hand", "date": monday, "start_hour": "11", "hours": "2"},
    )
    assert r2.status_code == 409
    html = r2.get_data(as_text=True)
    assert "Schedule clash" in html
    assert "Join the waitlist instead" not in html


def _app_with(repo, client):
    from conftest import NOW

    from lablink import create_app
    from lablink.ai import LabAssistant

    app = create_app(repo=repo, assistant=LabAssistant(client), clock=lambda: NOW,
                     config={"TESTING": True, "SECRET_KEY": "t", "AI_RATE_LIMIT": 1000})
    c = app.test_client()
    with c.session_transaction() as s:
        s["_csrf"] = "tok"
    return c


DESAL = {"project_kind": "physical", "items": [
    {"id": "eq_fdm_printer", "tier": "needed", "evidence": "desalination prototype", "task": "fabricate the housing",
     "reason": "Print the prototype housing.", "hours": 6},
    {"id": "eq_uvvis", "tier": "consider", "evidence": "", "task": "test output water for contaminants",
     "reason": "Desalinated water should be tested for contaminants.", "hours": 3},
]}


def test_discover_groups_inferred_matches_separately(repo):
    from test_ai import FakeClient

    c = _app_with(repo, FakeClient(payload=DESAL))
    page = c.get("/?q=build+a+desalination+prototype").get_data(as_text=True)
    assert "Matched by Gemini" in page and "Also likely useful" in page
    assert page.index("Ultimaker") < page.index("Also likely useful") < page.index("UV-Vis Spectrophotometer")


def test_request_form_ticks_needed_but_not_consider(repo):
    from test_ai import FakeClient

    c = _app_with(repo, FakeClient(payload=DESAL))
    login(c, "s_rahul")
    page = post(c, "/requests/suggest", {"description": "We will build a desalination prototype for a village"}).get_data(as_text=True)
    assert 'value="eq_fdm_printer" checked' in page
    assert 'value="eq_uvvis" checked' not in page and "Worth considering:" in page


def test_ai_failure_is_shown_honestly(repo):
    from test_ai import FakeClient

    c = _app_with(repo, FakeClient(fail=True))
    login(c, "s_rahul")
    page = post(c, "/requests/suggest", {"description": "We will build a desalination prototype for a village"}).get_data(as_text=True)
    assert "AI suggestions are unavailable right now" in page or "keyword matches" in page
    search = c.get("/?q=train+an+image+classifier").get_data(as_text=True)
    assert "Gemini didn&#39;t respond in time" in search or "Gemini didn't respond in time" in search
