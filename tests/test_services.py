import threading
from datetime import datetime, timedelta

import pytest
from conftest import NOW

from lablink.models import BookingStatus, ProjectType, RequestStatus
from lablink.services import PermissionDenied
from lablink.validation import BookingForm, RequestForm


def user(service, uid):
    return service.repo.get_user(uid)


def test_seeded_demo_has_contested_printer(service):
    contested = dict((e.id, n) for e, n in service.contested_equipment())
    assert contested["eq_fdm_printer"] == 4


def test_student_cannot_book_without_approved_request(service):
    form = BookingForm("rq_biosensor", NOW + timedelta(days=1, hours=2), 2)  # pending request
    with pytest.raises(PermissionDenied):
        service.book(user(service, "s_arjun"), "eq_uvvis", form, False)
    with pytest.raises(PermissionDenied):  # someone else's approved request
        service.book(user(service, "s_arjun"), "eq_jetson", BookingForm("rq_drone", NOW + timedelta(hours=26), 2), False)


def test_booking_confirms_then_conflicts_then_waitlists(service):
    meera = user(service, "s_meera")
    start = datetime(2026, 11, 30, 10)  # next Monday: free
    first = service.book(meera, "eq_dso", BookingForm("rq_hand", start, 2), False)
    assert first.status == "confirmed"
    second = service.book(meera, "eq_dso", BookingForm("rq_hand", start, 2), False)
    assert second.status == "confirmed"  # DSO has two units
    third = service.book(meera, "eq_dso", BookingForm("rq_hand", start, 2), False)
    assert third.status == "unavailable" and third.slots
    fourth = service.book(meera, "eq_dso", BookingForm("rq_hand", start, 2), True)
    assert fourth.status == "waitlisted" and fourth.booking.status == BookingStatus.WAITLISTED


def test_outside_opening_hours_is_rejected(service):
    result = service.book(user(service, "s_meera"), "eq_dso", BookingForm("rq_hand", datetime(2026, 11, 29, 10), 2), False)
    assert result.status == "unavailable" and "open" in result.message


def test_concurrent_bookings_never_overbook(service):
    meera = user(service, "s_meera")
    start = datetime(2026, 12, 1, 10)
    results = []

    def attempt():
        results.append(service.book(meera, "eq_dso", BookingForm("rq_hand", start, 2), False).status)

    threads = [threading.Thread(target=attempt) for _ in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert results.count("confirmed") == 2


def test_request_lifecycle_and_guide_permissions(service):
    form = RequestForm("Line follower", "A line following robot for the club.", NOW.date() + timedelta(days=10),
                       "f_khan", ProjectType.CLUB, [("eq_esp32", 4)])
    request = service.create_request(user(service, "s_ananya"), form)
    assert request.status == RequestStatus.PENDING and len(service.brief_for(request)) == 3
    with pytest.raises(PermissionDenied):
        service.decide_request(user(service, "f_rao"), request.id, True, "")  # not the guide
    with pytest.raises(PermissionDenied):
        service.decide_request(user(service, "s_ananya"), request.id, True, "")
    approved = service.decide_request(user(service, "f_khan"), request.id, True, "Go ahead")
    assert approved.status == RequestStatus.APPROVED
    with pytest.raises(PermissionDenied):
        service.decide_request(user(service, "f_khan"), request.id, False, "")  # already decided


def test_faculty_cannot_create_requests(service):
    form = RequestForm("X project", "Some description long enough.", NOW.date() + timedelta(days=5),
                       "f_khan", ProjectType.CLUB, [("eq_esp32", 4)])
    with pytest.raises(PermissionDenied):
        service.create_request(user(service, "f_khan"), form)


def test_students_cannot_plan(service):
    with pytest.raises(PermissionDenied):
        service.plan(user(service, "s_ananya"), "eq_fdm_printer")


def test_apply_plan_resolves_conflict_without_overbooking(service):
    admin = user(service, "a_lab")
    plan = service.plan(admin, "eq_fdm_printer")
    assert {d.outcome for d in plan} >= {"allocated", "alternative", "swap"}
    assert all(d.explanation for d in plan)
    applied = service.apply_plan(admin, "eq_fdm_printer")
    assert applied == 4
    assert service.repo.waitlist("eq_fdm_printer") == []
    for eq_id in ("eq_fdm_printer", "eq_resin_printer"):
        confirmed = service.repo.confirmed_bookings(eq_id)
        for i, a in enumerate(confirmed):
            for b in confirmed[i + 1:]:
                assert not a.overlaps(b.start, b.end), (a, b)
    moved = [b for b in service.repo.bookings_for_user("s_rahul") if "Moved" in b.note]
    assert len(moved) == 1


def test_cancel_only_own_booking(service):
    with pytest.raises(PermissionDenied):
        service.cancel_booking(user(service, "s_meera"), "bk_p1")
    assert service.cancel_booking(user(service, "s_rahul"), "bk_p1").status == BookingStatus.CANCELLED


def test_student_cannot_book_concurrent_overlapping_slots_on_different_equipment(service):
    meera = user(service, "s_meera")
    monday = datetime(2026, 11, 30, 10)  # 10:00 - 12:00

    # Meera books DSO from 10:00 to 12:00
    first = service.book(meera, "eq_dso", BookingForm("rq_hand", monday, 2), False)
    assert first.status == "confirmed"

    # Attempt 1: Booking 3D printer overlapping from 11:00 to 13:00 (1 hour overlap) is blocked
    clash_slot = service.book(meera, "eq_fdm_printer", BookingForm("rq_hand", datetime(2026, 11, 30, 11), 2), False)
    assert clash_slot.status == "unavailable"
    assert "Schedule clash" in clash_slot.message
    assert "Keysight" in clash_slot.message
    assert clash_slot.can_waitlist is False

    # Attempt 2: Even with join_waitlist=True, the clashing slot cannot be waitlisted
    clash_waitlist = service.book(meera, "eq_fdm_printer", BookingForm("rq_hand", datetime(2026, 11, 30, 11), 2), True)
    assert clash_waitlist.status == "unavailable"
    assert "Schedule clash" in clash_waitlist.message
    assert clash_waitlist.can_waitlist is False

    # Suggested slots should not include times clashing with Meera's existing booking
    for s in clash_slot.slots:
        assert not (s.start.date() == monday.date() and s.start.hour in (10, 11))

    # Adjacent non-overlapping slot (12:00 to 14:00) is allowed
    adjacent = service.book(meera, "eq_fdm_printer", BookingForm("rq_hand", datetime(2026, 11, 30, 12), 2), False)
    assert adjacent.status == "confirmed"


def test_utilisation_stats_shape(service):
    stats = service.utilisation_stats()
    assert stats[0]["id"] == "eq_fdm_printer" and stats[0]["waitlisted"] == 4
    assert all(0 <= s["utilisation"] <= 100 for s in stats)
