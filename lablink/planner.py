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
    """One planned lab session on a machine."""
    equipment_id: str
    start: datetime
    end: datetime

    @property
    def hours(self) -> int:
        """Length of the session in whole hours."""
        return int((self.end - self.start).total_seconds() // 3600)


@dataclass
class PlanItem:
    """The plan for one equipment need: its sessions, status and a note explaining it."""
    equipment_id: str
    hours_needed: int
    hours_booked: int
    sessions: list[Session] = field(default_factory=list)
    status: str = "ready"  # ready | partial | blocked | done
    note: str = ""

    @property
    def hours_planned(self) -> int:
        """Total hours across the planned sessions."""
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
    """Plan the remaining hours of every equipment need on `request` for `student`."""
    planner = _ProjectPlanner(request, student, catalogue, bookings, now)
    return [planner.plan_need(need.equipment_id, need.hours) for need in request.needs if need.equipment_id in catalogue]


class _ProjectPlanner:
    """Holds the planning state: bookings grow as sessions are planned, so later needs see earlier ones."""

    def __init__(
        self,
        request: AccessRequest,
        student: User,
        catalogue: dict[str, Equipment],
        bookings: list[Booking],
        now: datetime,
    ) -> None:
        self.request, self.student, self.catalogue, self.now = request, student, catalogue, now
        self.working = [b for b in bookings if b.status == BookingStatus.CONFIRMED]
        self.mine = [b for b in self.working if b.user_id == student.id]
        self.earliest = now + timedelta(hours=1)
        self.cutoff = deadline_cutoff(request)

    def plan_need(self, equipment_id: str, hours_needed: int) -> PlanItem:
        """Plan one need: done, blocked by a rule, or as many sessions as fit before the deadline."""
        primary = self.catalogue[equipment_id]
        booked = sum(b.hours for b in self.mine if b.request_id == self.request.id and b.equipment_id == primary.id)
        item = PlanItem(primary.id, hours_needed, booked)
        if hours_needed - booked <= 0:
            item.status, item.note = "done", "Already booked in full."
            return item
        problems = access_problems(self.student, primary, self.request)
        if problems:
            item.status, item.note = "blocked", " ".join(p.message for p in problems)
            return item
        stop_reason = self._fill_sessions(item, primary, hours_needed - booked)
        self._finish(item, primary, stop_reason)
        return item

    def _equivalents(self, primary: Equipment) -> list[Equipment]:
        """The requested machine first, then same-category machines the student is trained for."""
        return [primary] + [
            e
            for e in sorted(self.catalogue.values(), key=lambda e: e.id)
            if e.category == primary.category
            and e.id != primary.id
            and not (e.requires_training and e.category not in self.student.trainings)
        ]

    def _fill_sessions(self, item: PlanItem, primary: Equipment, remaining: int) -> str:
        """Add sessions until the hours are covered. Returns why planning stopped early ("" if it didn't)."""
        candidates = self._equivalents(primary)
        while remaining > 0:
            length = min(SESSION_HOURS, remaining)
            session, capped = self._first_session(candidates, length)
            if session is None:
                if capped:
                    return f"The {WEEKLY_CAP_HOURS}-hour weekly fair-use cap leaves no more time before the deadline."
                return "No free slot before the deadline on this or an equivalent machine."
            self._reserve(session)
            item.sessions.append(session)
            remaining -= length
        return ""

    def _first_session(self, candidates: list[Equipment], length: int) -> tuple[Session | None, bool]:
        """Earliest valid session; an equivalent machine only if the requested one has no room.
        Also reports whether the weekly cap ruled out any otherwise-free slot."""
        capped = False
        for eq in candidates:
            session, eq_capped = self._first_session_on(eq, length)
            capped = capped or eq_capped
            if session is not None:
                return session, capped
        return None, capped

    def _first_session_on(self, eq: Equipment, length: int) -> tuple[Session | None, bool]:
        eq_bookings = [b for b in self.working if b.equipment_id == eq.id]
        capped = False
        for slot in candidate_windows(length, self.earliest, self.cutoff):
            if free_units(eq_bookings, eq.units, slot.start, slot.end) <= 0:
                continue
            if _overlaps_any(slot.start, slot.end, self.mine):
                continue
            if hours_in_week(self.mine, self.student.id, slot.start.date()) + length > WEEKLY_CAP_HOURS:
                capped = True
                continue
            return Session(eq.id, slot.start, slot.end), capped
        return None, capped

    def _reserve(self, session: Session) -> None:
        """Record a planned session so later sessions and needs can't clash with it."""
        virtual = Booking(
            id=f"plan_{len(self.working)}",
            equipment_id=session.equipment_id,
            user_id=self.student.id,
            request_id=self.request.id,
            start=session.start,
            end=session.end,
            created_at=self.now,
        )
        self.working.append(virtual)
        self.mine.append(replace(virtual))

    @staticmethod
    def _finish(item: PlanItem, primary: Equipment, stop_reason: str) -> None:
        item.sessions.sort(key=lambda session: (session.start, session.equipment_id))
        if not stop_reason:
            item.status = "ready"
            if any(s.equipment_id != primary.id for s in item.sessions):
                item.note = "Some sessions use an equivalent machine in another department."
        else:
            item.status = "partial" if item.sessions else "blocked"
            item.note = stop_reason
