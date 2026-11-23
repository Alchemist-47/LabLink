"""Booking rules that depend on who is booking (pure functions, no I/O).

Every booking attempt, every slot the planner proposes and every "book all" goes
through `check_booking`, so the same rules apply everywhere:

* the project request must belong to the student, include the equipment and not be rejected;
* equipment marked `requires_approval` needs the project to be APPROVED by the guide,
  while ordinary equipment can be booked as soon as the request exists;
* equipment marked `requires_training` needs that safety training on the student's record;
* the slot must end before the project deadline (end of that day's opening hours);
* a student may hold at most WEEKLY_CAP_HOURS confirmed hours per calendar week (Mon–Sun),
  so nobody monopolises shared equipment.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta

from .models import AccessRequest, Booking, BookingStatus, Equipment, RequestStatus, User
from .scheduling import CLOSE_HOUR

WEEKLY_CAP_HOURS = 12


@dataclass(frozen=True)
class Problem:
    """One rule a booking breaks: a stable code plus a message for the student."""
    code: str  # ownership | approval | training | deadline | weekly_cap
    message: str


def week_start(day: date) -> date:
    """Monday of the week containing `day` (the weekly cap runs Monday to Sunday)."""
    return day - timedelta(days=day.weekday())


def deadline_cutoff(request: AccessRequest) -> datetime:
    """Closing time on the project's deadline day; no session may end after it."""
    return datetime.combine(request.deadline, time(CLOSE_HOUR))


def hours_in_week(bookings: Iterable[Booking], user_id: str, day: date, exclude_id: str | None = None) -> int:
    """Confirmed hours the student holds in the calendar week containing `day`."""
    monday = week_start(day)
    return sum(
        b.hours
        for b in bookings
        if b.user_id == user_id
        and b.status == BookingStatus.CONFIRMED
        and b.id != exclude_id
        and week_start(b.start.date()) == monday
    )


def access_problems(student: User, equipment: Equipment, request: AccessRequest | None) -> list[Problem]:
    """Rules that don't depend on the time slot: ownership, approval, training."""
    if (
        request is None
        or request.student_id != student.id
        or not request.needs_equipment(equipment.id)
        or request.status == RequestStatus.REJECTED
    ):
        return [Problem("ownership", "Choose one of your own project requests that includes this equipment.")]
    problems = []
    if equipment.requires_approval and request.status != RequestStatus.APPROVED:
        problems.append(
            Problem(
                "approval",
                f"{equipment.name} is in high demand, so your faculty guide must approve the project before you book it.",
            )
        )
    if equipment.requires_training and equipment.category not in student.trainings:
        problems.append(
            Problem(
                "training",
                f"{equipment.name} needs {equipment.category} safety training. "
                "Ask the lab coordinator to record your training, then book again.",
            )
        )
    return problems


def slot_problems(
    student: User,
    request: AccessRequest,
    start: datetime,
    end: datetime,
    bookings: Iterable[Booking],
    exclude_id: str | None = None,
) -> list[Problem]:
    """Rules about the slot itself: deadline and weekly fair-use cap."""
    problems = []
    if end > deadline_cutoff(request):
        problems.append(
            Problem("deadline", f"This slot ends after your project deadline ({request.deadline:%a %d %b}).")
        )
    held = hours_in_week(bookings, student.id, start.date(), exclude_id)
    new_total = held + int((end - start).total_seconds() // 3600)
    if new_total > WEEKLY_CAP_HOURS:
        problems.append(
            Problem(
                "weekly_cap",
                f"This would bring you to {new_total} lab hours in the week of {week_start(start.date()):%d %b}. "
                f"The fair-use limit is {WEEKLY_CAP_HOURS} hours per week, so other students get a turn.",
            )
        )
    return problems


def check_booking(
    student: User,
    equipment: Equipment,
    request: AccessRequest | None,
    start: datetime,
    end: datetime,
    bookings: Iterable[Booking],
) -> list[Problem]:
    """Every rule this booking would break (access, deadline, weekly cap), in one list."""
    problems = access_problems(student, equipment, request)
    if request is None or (problems and problems[0].code == "ownership"):
        return problems
    return problems + slot_problems(student, request, start, end, list(bookings))
