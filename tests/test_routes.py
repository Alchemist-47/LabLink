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
