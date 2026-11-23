from datetime import datetime

from conftest import NOW, login

from lablink.calendar_export import booking_to_ics, escape_text, fold
from lablink.models import Booking, BookingStatus, Equipment


def _confirmed_booking(service, user_id="s_rahul", booking_id="bk_p2"):
    b = service.repo.get_booking(booking_id)
    assert b.status == BookingStatus.CONFIRMED and b.user_id == user_id
    return b


def test_ics_download_for_own_booking(client, service):
    b = _confirmed_booking(service)
    login(client, "s_rahul")
    r = client.get(f"/bookings/{b.id}.ics")
    assert r.status_code == 200 and r.mimetype == "text/calendar"
    assert f'filename="lablink-{b.id}.ics"' in r.headers["Content-Disposition"]
    body = r.get_data(as_text=True)
    assert body.startswith("BEGIN:VCALENDAR\r\n") and body.endswith("END:VCALENDAR\r\n")
    assert "SUMMARY:LabLink: Ultimaker S5 3D Printer" in body
    assert "TRIGGER:-PT15M" in body and f"UID:{b.id}@lablink" in body
    # Campus time 09:00 IST is 03:30 UTC
    assert f"DTSTART:{b.start:%Y%m%d}T033000Z" in body


def test_coordinator_can_export_any_booking(client, service):
    b = _confirmed_booking(service)
    login(client, "a_lab")
    assert client.get(f"/bookings/{b.id}.ics").status_code == 200


def test_other_students_cannot_export(client, service):
    b = _confirmed_booking(service)
    login(client, "s_meera")
    assert client.get(f"/bookings/{b.id}.ics").status_code == 403


def test_unknown_and_waitlisted_bookings_are_not_exportable(client, service):
    login(client, "s_ananya")
    assert client.get("/bookings/bk_nope.ics").status_code == 404
    assert service.repo.get_booking("bk_w1").status == BookingStatus.WAITLISTED
    assert client.get("/bookings/bk_w1.ics").status_code == 404
    assert client.get("/bookings/..%2Fetc.ics").status_code == 404


def test_bookings_page_offers_calendar_link_for_confirmed_only(client):
    login(client, "s_ananya")
    page = client.get("/bookings").get_data(as_text=True)
    assert "/bookings/bk_w1.ics" not in page  # waitlisted
    login(client, "s_rahul")
    assert "Add to calendar" in client.get("/bookings").get_data(as_text=True)


def test_text_escaping():
    assert escape_text("a,b;c\\d\ne") == "a\\,b\\;c\\\\d\\ne"


def test_long_lines_are_folded_to_75_octets():
    line = "DESCRIPTION:" + "é" * 80
    folded = fold(line)
    parts = folded.split("\r\n ")
    assert len(parts) > 1 and all(len(p.encode("utf-8")) <= 75 for p in parts)
    assert "".join(parts) == line


def test_special_characters_survive_in_event():
    eq = Equipment("eq_x", "Scope, 4-ch; fast", "Oscilloscope", "ECE", "Lab 2\nWing B", 1, "d")
    b = Booking("bk_x", "eq_x", "s", "rq", datetime(2026, 11, 24, 10), datetime(2026, 11, 24, 12), NOW)
    body = booking_to_ics(b, eq, "Hand, v2", NOW)
    assert "SUMMARY:LabLink: Scope\\, 4-ch\\; fast" in body and "Lab 2\\nWing B" in body
