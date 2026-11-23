from datetime import datetime, timedelta

from lablink.fairness import hours_used, plan_allocation, priority_score
from lablink.models import (
    AccessRequest,
    Booking,
    BookingStatus,
    Equipment,
    EquipmentNeed,
    ProjectType,
)

NOW = datetime(2026, 11, 23, 8, 0)  # Monday
PRINTER = Equipment("eq_p", "Printer", "3D Printer", "Mech", "M1", 1, "fdm")
RESIN = Equipment("eq_r", "Resin", "3D Printer", "Design", "D1", 1, "sla")


def req(rid, deadline_days, kind=ProjectType.CAPSTONE):
    return AccessRequest(rid, f"s_{rid}", "f", rid, "desc", NOW.date() + timedelta(days=deadline_days),
                         [EquipmentNeed("eq_p", 6)], NOW - timedelta(days=1), kind)


def wait(bid, user, rid, hours, days_waiting=1, eq="eq_p"):
    start = datetime(2026, 11, 23, 10)
    return Booking(bid, eq, user, rid, start, start + timedelta(hours=hours),
                   NOW - timedelta(days=days_waiting), BookingStatus.WAITLISTED)


def confirmed(bid, user, rid, day, start_h, end_h, eq="eq_p"):
    d = NOW.date() + timedelta(days=day)
    return Booking(bid, eq, user, rid, datetime.combine(d, datetime.min.time()) + timedelta(hours=start_h),
                   datetime.combine(d, datetime.min.time()) + timedelta(hours=end_h), NOW - timedelta(days=5))


def test_score_components():
    urgent = priority_score(req("a", 2), NOW, 0, NOW)
    relaxed = priority_score(req("b", 25), NOW, 0, NOW)
    assert urgent.urgency > relaxed.urgency
    coursework = priority_score(req("c", 2, ProjectType.COURSEWORK), NOW, 0, NOW)
    assert urgent.project == 20 and coursework.project == 5
    heavy_user = priority_score(req("d", 2), NOW, 20, NOW)
    assert heavy_user.fairness == 0 and urgent.fairness == 25
    waited = priority_score(req("e", 2), NOW - timedelta(days=10), 0, NOW)
    assert waited.waiting == 15
    assert 0 <= relaxed.total <= 100


def test_overdue_deadline_gets_max_urgency():
    assert priority_score(req("a", -1), NOW, 0, NOW).urgency == 40


def test_hours_used_ignores_waitlist_and_old_bookings():
    old = confirmed("o", "s_x", "r", -40, 9, 17)
    recent = confirmed("n", "s_x", "r", -2, 9, 12)
    waiting = wait("w", "s_x", "r", 5)
    assert hours_used([old, recent, waiting], "s_x", NOW) == 3


def test_higher_priority_gets_requested_slot_first():
    requests = {"a": req("a", 2), "b": req("b", 20, ProjectType.COURSEWORK)}
    plan = plan_allocation(PRINTER, [wait("wb", "s_b", "b", 6, 5), wait("wa", "s_a", "a", 6)], requests, [PRINTER], [], NOW)
    assert [d.student_id for d in plan] == ["s_a", "s_b"]
    assert plan[0].outcome == "allocated" and plan[0].start == datetime(2026, 11, 23, 10)
    assert plan[1].start >= plan[0].end or plan[1].start.date() != plan[0].start.date()


def test_alternative_equipment_when_requested_is_full():
    requests = {"a": req("a", 1)}
    full = [confirmed("c0", "s_z", "z", 0, 9, 18), confirmed("c1", "s_z", "z", 1, 9, 18)]
    plan = plan_allocation(PRINTER, [wait("wa", "s_a", "a", 4)], requests, [PRINTER, RESIN], full, NOW)
    assert plan[0].outcome == "alternative" and plan[0].equipment_id == "eq_r"


def test_swap_with_lower_priority_holder():
    requests = {"a": req("a", 1), "z": req("z", 20, ProjectType.COURSEWORK)}
    full = [confirmed("c0", "s_z", "z", 0, 9, 18), confirmed("c1", "s_z", "z", 1, 9, 18)]
    plan = plan_allocation(PRINTER, [wait("wa", "s_a", "a", 4)], requests, [PRINTER], full, NOW)
    d = plan[0]
    assert d.outcome == "swap" and d.swap_booking_id == "c0" and d.swap_user_id == "s_z"
    assert d.start == datetime(2026, 11, 23, 9)
    assert d.swap_start.date() > NOW.date() + timedelta(days=1)  # moved after the busy days
    assert d.swap_end <= datetime.combine(requests["z"].deadline, datetime.min.time()) + timedelta(hours=18)


def test_no_swap_against_higher_priority_holder():
    requests = {"a": req("a", 1, ProjectType.COURSEWORK), "z": req("z", 20, ProjectType.CAPSTONE)}
    full = [confirmed("c0", "s_z", "z", 0, 9, 18), confirmed("c1", "s_z", "z", 1, 9, 18)]
    # Give the waiting student heavy usage so their score is clearly lower.
    history = [confirmed(f"h{i}", "s_a", "a", -i - 1, 9, 17) for i in range(3)]
    plan = plan_allocation(PRINTER, [wait("wa", "s_a", "a", 4)], requests, [PRINTER], full + history, NOW)
    assert plan[0].outcome == "deferred" and plan[0].start is not None


def test_plan_is_deterministic():
    requests = {"a": req("a", 3), "b": req("b", 3)}
    waits = [wait("wa", "s_a", "a", 6), wait("wb", "s_b", "b", 6)]
    first = plan_allocation(PRINTER, waits, requests, [PRINTER], [], NOW)
    second = plan_allocation(PRINTER, list(reversed(waits)), requests, [PRINTER], [], NOW)
    assert [(d.booking_id, d.start) for d in first] == [(d.booking_id, d.start) for d in second]


def test_planned_slots_never_exceed_capacity():
    requests = {f"r{i}": req(f"r{i}", 5) for i in range(6)}
    waits = [wait(f"w{i}", f"s_{i}", f"r{i}", 5) for i in range(6)]
    plan = plan_allocation(PRINTER, waits, requests, [PRINTER], [], NOW)
    slots = [(d.start, d.end) for d in plan if d.equipment_id == "eq_p" and d.start]
    for i, (s1, e1) in enumerate(slots):
        for s2, e2 in slots[i + 1:]:
            assert e1 <= s2 or e2 <= s1


def test_unknown_request_is_skipped():
    plan = plan_allocation(PRINTER, [wait("wx", "s_x", "missing", 2)], {}, [PRINTER], [], NOW)
    assert plan == []
