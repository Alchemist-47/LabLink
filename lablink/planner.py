"""Plan & book: turn a project request into concrete sessions (pure, deterministic).

For every piece of equipment the project needs, the planner books the remaining
hours as sessions of up to SESSION_HOURS, each at the earliest time that:

* has a free unit (taking the sessions already planned into account),
* doesn't overlap the student's other bookings or planned sessions,
* keeps the student within the weekly fair-use cap (later weeks are used if needed),
* ends before the project deadline.

If the requested machine is full, an equivalent machine (same category, any
department) is used, but only one the student is allowed to use. Items that the
student can't book yet (approval pending, training missing) are reported with the
reason instead of being skipped silently.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta

from .models import AccessRequest, Booking, BookingStatus, Equipment, User
from .policy import WEEKLY_CAP_HOURS, access_problems, deadline_cutoff, hours_in_week
from .scheduling import candidate_windows, free_units

SESSION_HOURS = 4


@dataclass(frozen=True)
class Session:
    equipment_id: str
    start: datetime
    end: datetime

    @property
    def hours(self) -> int:
        return int((self.end - self.start).total_seconds() // 3600)


@dataclass
class PlanItem:
    equipment_id: str
    hours_needed: int
    hours_booked: int
    sessions: list[Session] = field(default_factory=list)
    status: str = "ready"  # ready | partial | blocked | done
    note: str = ""

    @property
    def hours_planned(self) -> int:
        return sum(s.hours for s in self.sessions)


def _overlaps_any(start: datetime, end: datetime, bookings: list[Booking]) -> bool:
    return any(b.overlaps(start, end) for b in bookings)


def plan_project(
    request: AccessRequest,
    student: User,
    catalogue: dict[str, Equipment],
    bookings: list[Booking],
    now: datetime,
) -> list[PlanItem]:
    confirmed = [b for b in bookings if b.status == BookingStatus.CONFIRMED]
    working = list(confirmed)  # grows as sessions are planned
    mine = [b for b in confirmed if b.user_id == student.id]
    earliest = now + timedelta(hours=1)
    cutoff = deadline_cutoff(request)
    items: list[PlanItem] = []

    for need in request.needs:
        primary = catalogue.get(need.equipment_id)
        if primary is None:
            continue
        booked = sum(b.hours for b in mine if b.request_id == request.id and b.equipment_id == primary.id)
        item = PlanItem(primary.id, need.hours, booked)
        remaining = need.hours - booked
        if remaining <= 0:
            item.status, item.note = "done", "Already booked in full."
            items.append(item)
            continue
        problems = access_problems(student, primary, request)
        if problems:
            item.status, item.note = "blocked", " ".join(p.message for p in problems)
            items.append(item)
            continue

        candidates = [primary] + [
            e
            for e in sorted(catalogue.values(), key=lambda e: e.id)
            if e.category == primary.category
            and e.id != primary.id
            and not (e.requires_training and e.category not in student.trainings)
        ]
        stop_reason = ""
        while remaining > 0:
            length = min(SESSION_HOURS, remaining)
            best: Session | None = None
            capped = False
            # Requested machine first; an equivalent machine only if it has no room before the deadline.
            for eq in candidates:
                if best is not None:
                    break
                eq_bookings = [b for b in working if b.equipment_id == eq.id]
                for slot in candidate_windows(length, earliest, cutoff):
                    if free_units(eq_bookings, eq.units, slot.start, slot.end) <= 0:
                        continue
                    if _overlaps_any(slot.start, slot.end, mine):
                        continue
                    if hours_in_week(mine, student.id, slot.start.date()) + length > WEEKLY_CAP_HOURS:
                        capped = True
                        continue
                    best = Session(eq.id, slot.start, slot.end)
                    break
            if best is None:
                stop_reason = (
                    f"The {WEEKLY_CAP_HOURS}-hour weekly fair-use cap leaves no more time before the deadline."
                    if capped
                    else "No free slot before the deadline on this or an equivalent machine."
                )
                break
            virtual = Booking(
                id=f"plan_{len(working)}",
                equipment_id=best.equipment_id,
                user_id=student.id,
                request_id=request.id,
                start=best.start,
                end=best.end,
                created_at=now,
            )
            working.append(virtual)
            mine.append(replace(virtual))
            item.sessions.append(best)
            remaining -= length

        item.sessions.sort(key=lambda session: (session.start, session.equipment_id))
        if remaining <= 0:
            item.status = "ready"
            if any(s.equipment_id != primary.id for s in item.sessions):
                item.note = "Some sessions use an equivalent machine in another department."
        else:
            item.status = "partial" if item.sessions else "blocked"
            item.note = stop_reason
        items.append(item)
    return items
