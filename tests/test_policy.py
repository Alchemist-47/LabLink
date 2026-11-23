from datetime import date, datetime, timedelta

from lablink.models import AccessRequest, Booking, BookingStatus, Equipment, EquipmentNeed, RequestStatus, Role, User
from lablink.policy import (
    WEEKLY_CAP_HOURS,
    access_problems,
    check_booking,
    hours_in_week,
    slot_problems,
    week_start,
)

NOW = datetime(2026, 11, 23, 8)  # Monday
STUDENT = User("s_x", "X", Role.STUDENT, "CS", trainings=("3D Printer",))
PRINTER = Equipment("eq_p", "Printer", "3D Printer", "Mech", "M1", 1, "d", requires_training=True, requires_approval=True)
LASER = Equipment("eq_l", "Laser", "Laser Cutter", "Mech", "M1", 1, "d", requires_training=True)
SCOPE = Equipment("eq_s", "Scope", "Oscilloscope", "ECE", "E1", 2, "d")


def request(status=RequestStatus.APPROVED, owner="s_x", deadline_days=10, needs=("eq_p", "eq_l", "eq_s")):
    return AccessRequest("rq_1", owner, "f", "P", "desc", NOW.date() + timedelta(days=deadline_days),
                         [EquipmentNeed(e, 4) for e in needs], NOW, status=status)


def booking(bid, day, start_h, end_h, user="s_x", status=BookingStatus.CONFIRMED):
    d = datetime.combine(day, datetime.min.time())
    return Booking(bid, "eq_s", user, "rq_1", d + timedelta(hours=start_h), d + timedelta(hours=end_h), NOW, status)


def codes(problems):
    return [p.code for p in problems]


def test_week_start_is_monday():
    assert week_start(date(2026, 11, 29)) == date(2026, 11, 23)
    assert week_start(date(2026, 11, 23)) == date(2026, 11, 23)


def test_hours_in_week_counts_only_own_confirmed_bookings_that_week():
    bookings = [
        booking("a", date(2026, 11, 24), 9, 13),
        booking("b", date(2026, 11, 28), 9, 11),
        booking("c", date(2026, 11, 30), 9, 13),  # next week
        booking("d", date(2026, 11, 25), 9, 13, user="other"),
        booking("e", date(2026, 11, 25), 9, 13, status=BookingStatus.WAITLISTED),
    ]
    assert hours_in_week(bookings, "s_x", date(2026, 11, 26)) == 6
    assert hours_in_week(bookings, "s_x", date(2026, 11, 26), exclude_id="a") == 2


def test_ownership_problems():
    assert codes(access_problems(STUDENT, PRINTER, None)) == ["ownership"]
    assert codes(access_problems(STUDENT, PRINTER, request(owner="someone"))) == ["ownership"]
    assert codes(access_problems(STUDENT, PRINTER, request(needs=("eq_s",)))) == ["ownership"]
    assert codes(access_problems(STUDENT, PRINTER, request(status=RequestStatus.REJECTED))) == ["ownership"]


def test_approval_needed_only_for_gated_equipment():
    pending = request(status=RequestStatus.PENDING)
    assert codes(access_problems(STUDENT, PRINTER, pending)) == ["approval"]
    assert access_problems(STUDENT, SCOPE, pending) == []
    assert access_problems(STUDENT, PRINTER, request()) == []


def test_training_needed_for_equipment_that_requires_it():
    assert codes(access_problems(STUDENT, LASER, request())) == ["training"]
    trained = User("s_x", "X", Role.STUDENT, "CS", trainings=("Laser Cutter",))
    assert access_problems(trained, LASER, request()) == []


def test_deadline_rule():
    start = datetime(2026, 11, 26, 10)
    assert codes(slot_problems(STUDENT, request(deadline_days=2), start, start + timedelta(hours=2), [])) == ["deadline"]
    assert slot_problems(STUDENT, request(deadline_days=3), start, start + timedelta(hours=2), []) == []


def test_weekly_cap():
    held = [booking("a", date(2026, 11, 24), 9, 17), booking("b", date(2026, 11, 25), 9, 11)]  # 10 h
    ok_start = datetime(2026, 11, 26, 9)
    assert slot_problems(STUDENT, request(), ok_start, ok_start + timedelta(hours=2), held) == []
    over = slot_problems(STUDENT, request(), ok_start, ok_start + timedelta(hours=3), held)
    assert codes(over) == ["weekly_cap"] and str(WEEKLY_CAP_HOURS) in over[0].message
    next_week = datetime(2026, 11, 30, 9)
    assert slot_problems(STUDENT, request(), next_week, next_week + timedelta(hours=8), held) == []


def test_check_booking_reports_every_problem_at_once():
    pending = request(status=RequestStatus.PENDING, deadline_days=1)
    start = datetime(2026, 11, 26, 9)
    untrained = User("s_x", "X", Role.STUDENT, "CS")
    problems = check_booking(untrained, PRINTER, pending, start, start + timedelta(hours=2), [])
    assert set(codes(problems)) == {"approval", "training", "deadline"}


def test_check_booking_stops_at_ownership():
    start = datetime(2026, 11, 26, 9)
    assert codes(check_booking(STUDENT, PRINTER, None, start, start + timedelta(hours=1), [])) == ["ownership"]
