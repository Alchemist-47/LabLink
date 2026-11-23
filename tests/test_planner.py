from datetime import datetime, time

import pytest

from lablink.models import BookingStatus
from lablink.planner import SESSION_HOURS, plan_project
from lablink.policy import WEEKLY_CAP_HOURS, hours_in_week
from lablink.scheduling import is_valid_window
from lablink.seed import build_demo_data

NOW = datetime(2026, 11, 23, 8)  # Monday 08:00


@pytest.fixture
def demo():
    users, equipment, requests, bookings = build_demo_data(NOW)
    return ({u.id: u for u in users}, {e.id: e for e in equipment}, {r.id: r for r in requests}, bookings)


def plan(demo, request_id, user_id, extra=()):
    users, equipment, requests, bookings = demo
    return plan_project(requests[request_id], users[user_id], equipment, [*bookings, *extra], NOW)


def all_sessions(items):
    return [s for i in items for s in i.sessions]


@pytest.mark.parametrize("rid, uid", [("rq_drone", "s_ananya"), ("rq_hand", "s_meera"), ("rq_biosensor", "s_arjun")])
def test_every_session_obeys_the_rules(demo, rid, uid):
    users, equipment, requests, bookings = demo
    items = plan(demo, rid, uid)
    sessions = all_sessions(items)
    cutoff = datetime.combine(requests[rid].deadline, time(18))
    confirmed = [b for b in bookings if b.status == BookingStatus.CONFIRMED]
    for s in sessions:
        assert is_valid_window(s.start, s.end) and s.end <= cutoff and s.start > NOW
        assert 1 <= s.hours <= SESSION_HOURS
        same = [b for b in confirmed if b.equipment_id == s.equipment_id and b.overlaps(s.start, s.end)]
        assert len(same) < equipment[s.equipment_id].units  # a unit is free
        assert not any(b.user_id == uid and b.overlaps(s.start, s.end) for b in confirmed)  # no own clash
    for i, a in enumerate(sessions):  # planned sessions don't overlap each other
        assert all(not (a.start < b.end and b.start < a.end) for b in sessions[i + 1:])
    own = [b for b in confirmed if b.user_id == uid]
    for s in sessions:
        week = [x for x in sessions if x.start.isocalendar()[:2] == s.start.isocalendar()[:2]]
        assert hours_in_week(own, uid, s.start.date()) + sum(x.hours for x in week) <= WEEKLY_CAP_HOURS


def test_blocked_items_explain_why(demo):
    items = {i.equipment_id: i for i in plan(demo, "rq_biosensor", "s_arjun")}
    uvvis = items["eq_uvvis"]
    assert uvvis.status == "blocked" and not uvvis.sessions
    assert "approve" in uvvis.note and "safety training" in uvvis.note
    assert items["eq_esp32"].status == "ready"  # open equipment is planned while the request is pending


def test_requested_machine_first_then_equivalent(demo):
    items = {i.equipment_id: i for i in plan(demo, "rq_drone", "s_ananya")}
    printer = items["eq_fdm_printer"]
    assert printer.status == "ready" and printer.hours_planned == 6
    assert "eq_fdm_printer" in {s.equipment_id for s in printer.sessions}  # requested machine used where it has room
    assert [s.start for s in printer.sessions] == sorted(s.start for s in printer.sessions)  # shown in time order
    assert {s.equipment_id for s in printer.sessions} == {"eq_fdm_printer", "eq_resin_printer"}
    assert "equivalent" in printer.note


def test_booked_hours_reduce_what_is_planned(demo):
    items = {i.equipment_id: i for i in plan(demo, "rq_hand", "s_meera")}
    dso = items["eq_dso"]
    assert dso.hours_booked == 2 and dso.hours_planned == 1


def test_weekly_cap_limits_the_plan(demo):
    users, equipment, requests, bookings = demo
    request = requests["rq_drone"]
    request.needs[1].hours = 30  # far more Jetson time than the weekly cap allows before Thursday
    items = {i.equipment_id: i for i in plan(demo, "rq_drone", "s_ananya")}
    jetson = items["eq_jetson"]
    assert jetson.status == "partial" and "weekly fair-use cap" in jetson.note


def test_fully_booked_items_are_done(demo):
    users, equipment, requests, bookings = demo
    requests["rq_hand"].needs[1].hours = 2  # exactly what Meera already booked on the DSO
    items = {i.equipment_id: i for i in plan(demo, "rq_hand", "s_meera")}
    assert items["eq_dso"].status == "done" and not items["eq_dso"].sessions


def test_no_slot_before_deadline_is_explained(demo):
    users, equipment, requests, bookings = demo
    requests["rq_hand"].deadline = NOW.date()  # deadline today: only today's remaining hours
    for b in bookings:  # fill both oscilloscope units all day
        if b.equipment_id == "eq_dso":
            b.status = BookingStatus.CANCELLED
    from lablink.models import Booking
    fillers = [Booking(f"f{u}", "eq_dso", f"other{u}", "rq_x", NOW.replace(hour=9), NOW.replace(hour=18), NOW) for u in range(2)]
    items = {i.equipment_id: i for i in plan(demo, "rq_hand", "s_meera", extra=fillers)}
    assert items["eq_dso"].status == "blocked" and "No free slot" in items["eq_dso"].note


def test_plan_is_deterministic(demo):
    first = [(s.equipment_id, s.start) for s in all_sessions(plan(demo, "rq_drone", "s_ananya"))]
    second = [(s.equipment_id, s.start) for s in all_sessions(plan(demo, "rq_drone", "s_ananya"))]
    assert first == second
