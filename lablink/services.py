"""Business logic. Routes stay thin; every permission check lives here."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field, replace
from datetime import date, datetime, timedelta

from .ai import LabAssistant, Match
from .fairness import hours_used, plan_allocation
from .models import (
    AccessRequest,
    AllocationDecision,
    Booking,
    BookingStatus,
    Equipment,
    EquipmentNeed,
    RequestStatus,
    Role,
    User,
)
from .planner import PlanItem, plan_project
from .policy import access_problems, check_booking, hours_in_week, slot_problems
from .repository import Repository, new_id
from .scheduling import (
    CLOSE_HOUR,
    day_grid,
    find_free_slots,
    free_units,
    is_valid_window,
    spread_slots,
    utilisation,
)
from .seed import build_demo_data
from .validation import BookingForm, RequestForm


class PermissionDenied(Exception):
    pass


class NotFound(Exception):
    pass


@dataclass
class BookingResult:
    status: str  # "confirmed" | "waitlisted" | "unavailable"
    booking: Booking | None = None
    slots: list = field(default_factory=list)
    alternatives: list[tuple[Equipment, int]] = field(default_factory=list)
    message: str = ""
    can_waitlist: bool = True


class LabService:
    def __init__(self, repo: Repository, ai: LabAssistant, clock: Callable[[], datetime]):
        self.repo = repo
        self.ai = ai
        self.clock = clock

    # Helpers ---------------------------------------------------------------
    def now(self) -> datetime:
        return self.clock()

    def equipment_map(self) -> dict[str, Equipment]:
        return {e.id: e for e in self.repo.list_equipment()}

    def user_map(self) -> dict[str, User]:
        return {u.id: u for u in self.repo.list_users()}

    def faculty(self) -> list[User]:
        return [u for u in self.repo.list_users() if u.role == Role.FACULTY]

    def require_equipment(self, equipment_id: str) -> Equipment:
        eq = self.repo.get_equipment(equipment_id)
        if eq is None:
            raise NotFound("Equipment not found")
        return eq

    # Discovery -------------------------------------------------------------
    def search(
        self, query: str, department: str = "", category: str = ""
    ) -> tuple[list[tuple[Equipment, str, str]], str]:
        """Returns ([(equipment, reason, tier)], source)."""
        catalogue = self.repo.list_equipment()
        if department:
            catalogue = [e for e in catalogue if e.department == department]
        if category:
            catalogue = [e for e in catalogue if e.category == category]
        if not query:
            return [(e, "", "") for e in catalogue], ""
        matches, source = self.ai.search(query, catalogue)
        by_id = {e.id: e for e in catalogue}
        return [(by_id[m.equipment_id], m.reason, m.tier) for m in matches if m.equipment_id in by_id], source

    def free_now(self, equipment: Equipment) -> int:
        now = self.now().replace(minute=0, second=0, microsecond=0)
        return free_units(self.repo.confirmed_bookings(equipment.id), equipment.units, now, now + timedelta(hours=1))

    def week_grid(self, equipment: Equipment, start: date, days: int = 7) -> list[tuple[date, list[tuple[int, int]]]]:
        bookings = self.repo.confirmed_bookings(equipment.id)
        out = []
        for i in range(days):
            day = start + timedelta(days=i)
            out.append((day, day_grid(bookings, equipment.units, day)))
        return out

    # Project-based access requests -------------------------------------------
    def suggest_needs(self, description: str, student: User | None = None) -> tuple[list[Match], str]:
        department = student.department if student else ""
        return self.ai.suggest_needs(description, self.repo.list_equipment(), department)

    def create_request(self, student: User, form: RequestForm) -> AccessRequest:
        if student.role != Role.STUDENT:
            raise PermissionDenied("Only students can submit access requests")
        request = AccessRequest(
            id=new_id("rq"),
            student_id=student.id,
            guide_id=form.guide_id,
            project_title=form.project_title,
            description=form.description,
            deadline=form.deadline,
            needs=[EquipmentNeed(e, h) for e, h in form.needs],
            created_at=self.now(),
            project_type=form.project_type,
        )
        request.ai_summary = "\n".join(self._brief(request, student))
        self.repo.save_request(request)
        return request

    def _brief(self, request: AccessRequest, student: User) -> list[str]:
        days_left = (request.deadline - self.now().date()).days
        used = hours_used(self.repo.list_bookings(), student.id, self.now())
        return self.ai.summarise_request(request, student, self.equipment_map(), used, days_left)

    def brief_for(self, request: AccessRequest) -> list[str]:
        """Faculty brief; generated lazily for seeded/legacy requests and cached on the record."""
        if not request.ai_summary:
            student = self.repo.get_user(request.student_id)
            if student is None:
                return []
            request.ai_summary = "\n".join(self._brief(request, student))
            self.repo.save_request(request)
        return request.ai_summary.splitlines()

    def visible_requests(self, user: User) -> list[AccessRequest]:
        if user.role == Role.STUDENT:
            return self.repo.requests_for_student(user.id)
        if user.role == Role.FACULTY:
            return self.repo.requests_for_guide(user.id)
        return self.repo.list_requests()

    def decide_request(self, actor: User, request_id: str, approve: bool, note: str) -> AccessRequest:
        request = self.repo.get_request(request_id)
        if request is None:
            raise NotFound("Request not found")
        if not (actor.role == Role.ADMIN or (actor.role == Role.FACULTY and request.guide_id == actor.id)):
            raise PermissionDenied("Only the project's faculty guide can decide this request")
        if request.status != RequestStatus.PENDING:
            raise PermissionDenied("This request has already been decided")
        request.status = RequestStatus.APPROVED if approve else RequestStatus.REJECTED
        request.decision_note = note
        self.repo.save_request(request)
        return request

    # Booking ---------------------------------------------------------------
    def bookable_requests(self, student: User, equipment_id: str) -> list[AccessRequest]:
        """The student's live (pending or approved) projects that include this equipment.
        Whether a pending one may book is decided by policy.check_booking."""
        return [
            r
            for r in self.repo.requests_for_student(student.id)
            if r.needs_equipment(equipment_id) and r.status != RequestStatus.REJECTED
        ]

    def hours_this_week(self, student: User) -> int:
        return hours_in_week(self.repo.list_bookings(), student.id, self.now().date())

    def book(self, student: User, equipment_id: str, form: BookingForm, join_waitlist: bool) -> BookingResult:
        equipment = self.require_equipment(equipment_id)
        request = self.repo.get_request(form.request_id)
        if student.role != Role.STUDENT:
            raise PermissionDenied("Only students can book equipment")
        access = access_problems(student, equipment, request)
        if access and access[0].code == "ownership":
            raise PermissionDenied(access[0].message)
        start, end = form.start, form.start + timedelta(hours=form.hours)
        if not is_valid_window(start, end):
            return BookingResult(
                "unavailable",
                message=f"Labs are open Monday–Saturday, 09:00–{CLOSE_HOUR}:00. Choose a slot within opening hours.",
                slots=self._suggest_slots(equipment, form.hours, student_id=student.id),
            )
        problems = access + slot_problems(student, request, start, end, self.repo.list_bookings())
        if problems:
            return BookingResult(
                "blocked",
                message=" ".join(p.message for p in problems),
                slots=[] if access else self._suggest_slots(
                    equipment, form.hours, before=request.deadline, student_id=student.id
                ),
                can_waitlist=False,
            )
        for b in self.repo.bookings_for_user(student.id):
            if (
                b.status == BookingStatus.CONFIRMED
                and b.equipment_id != equipment_id
                and b.overlaps(start, end)
            ):
                other = self.repo.get_equipment(b.equipment_id)
                other_name = other.name if other else b.equipment_id
                return BookingResult(
                    "unavailable",
                    message=(
                        f"Schedule clash: you already hold a confirmed slot on {other_name} "
                        f"from {b.start:%a %d %b, %H:%M} to {b.end:%H:%M}. "
                        "Students cannot book multiple stations concurrently."
                    ),
                    slots=self._suggest_slots(equipment, form.hours, before=request.deadline, student_id=student.id),
                    can_waitlist=False,
                )
        booking = Booking(
            id=new_id("bk"),
            equipment_id=equipment_id,
            user_id=student.id,
            request_id=request.id,
            start=start,
            end=end,
            created_at=self.now(),
        )
        if self.repo.add_booking_if_capacity(booking, equipment.units):
            return BookingResult("confirmed", booking, message="Booking confirmed.")
        if join_waitlist:
            booking.status = BookingStatus.WAITLISTED
            self.repo.save_booking(booking)
            return BookingResult(
                "waitlisted",
                booking,
                message="You're on the waitlist. Faculty will run fair allocation and you'll get a slot before your deadline where possible.",
            )
        return BookingResult(
            "unavailable",
            message="That slot is full. Pick a suggested slot, an equivalent machine, or join the waitlist.",
            slots=self._suggest_slots(equipment, form.hours, before=request.deadline, student_id=student.id),
            alternatives=self._alternatives(equipment, start, end),
        )

    def _suggest_slots(
        self, equipment: Equipment, hours: int, before: date | None = None, student_id: str | None = None
    ):
        now = self.now()
        latest = now + timedelta(days=21)
        if before:
            latest = min(latest, datetime.combine(before, datetime.min.time()) + timedelta(hours=CLOSE_HOUR))
        slots = find_free_slots(
            self.repo.confirmed_bookings(equipment.id), equipment.units, hours, now + timedelta(hours=1), latest, limit=40
        )
        if student_id:
            user_bookings = [
                b for b in self.repo.bookings_for_user(student_id) if b.status == BookingStatus.CONFIRMED
            ]
            slots = [s for s in slots if not any(b.overlaps(s.start, s.end) for b in user_bookings)]
        return spread_slots(slots, 5)

    def _alternatives(self, equipment: Equipment, start: datetime, end: datetime) -> list[tuple[Equipment, int]]:
        out = []
        for alt in self.repo.list_equipment():
            if alt.category == equipment.category and alt.id != equipment.id:
                free = free_units(self.repo.confirmed_bookings(alt.id), alt.units, start, end)
                if free:
                    out.append((alt, free))
        return out

    def cancel_booking(self, user: User, booking_id: str) -> Booking:
        booking = self.repo.get_booking(booking_id)
        if booking is None:
            raise NotFound("Booking not found")
        if booking.user_id != user.id and user.role != Role.ADMIN:
            raise PermissionDenied("You can only cancel your own bookings")
        booking.status = BookingStatus.CANCELLED
        self.repo.save_booking(booking)
        return booking

    # Plan & book -------------------------------------------------------------
    def _own_live_request(self, student: User, request_id: str) -> AccessRequest:
        request = self.repo.get_request(request_id)
        if request is None:
            raise NotFound("Request not found")
        if student.role != Role.STUDENT or request.student_id != student.id:
            raise PermissionDenied("You can only plan your own projects")
        if request.status == RequestStatus.REJECTED:
            raise PermissionDenied("This project request was rejected")
        return request

    def plan_for(self, student: User, request_id: str) -> tuple[AccessRequest, list[PlanItem]]:
        request = self._own_live_request(student, request_id)
        items = plan_project(request, student, self.equipment_map(), self.repo.list_bookings(), self.now())
        return request, items

    def book_plan(self, student: User, request_id: str) -> tuple[int, int]:
        """Recompute the plan server-side and book every session. Returns (booked, failed).
        Each booking re-checks the rules and capacity, so a slot taken in the meantime fails
        cleanly instead of double-booking."""
        request, items = self.plan_for(student, request_id)
        catalogue = self.equipment_map()
        booked = failed = 0
        for item in items:
            for session in item.sessions:
                equipment = catalogue[session.equipment_id]
                problems = check_booking(
                    student, equipment, request, session.start, session.end, self.repo.list_bookings()
                )
                # The planner may use an equivalent machine that isn't listed on the request.
                problems = [p for p in problems if not (p.code == "ownership" and session.equipment_id != item.equipment_id)]
                clash = any(
                    b.status == BookingStatus.CONFIRMED and b.overlaps(session.start, session.end)
                    for b in self.repo.bookings_for_user(student.id)
                )
                booking = Booking(
                    id=new_id("bk"),
                    equipment_id=session.equipment_id,
                    user_id=student.id,
                    request_id=request.id,
                    start=session.start,
                    end=session.end,
                    created_at=self.now(),
                    note="Booked from project plan",
                )
                if not problems and not clash and self.repo.add_booking_if_capacity(booking, equipment.units):
                    booked += 1
                else:
                    failed += 1
        return booked, failed

    # Fair allocation -------------------------------------------------------
    def contested_equipment(self) -> list[tuple[Equipment, int]]:
        catalogue = self.equipment_map()
        counts: dict[str, int] = {}
        for b in self.repo.waitlist():
            counts[b.equipment_id] = counts.get(b.equipment_id, 0) + 1
        return [(catalogue[e], n) for e, n in sorted(counts.items(), key=lambda t: -t[1]) if e in catalogue]

    def _require_planner(self, actor: User) -> None:
        if actor.role not in (Role.FACULTY, Role.ADMIN):
            raise PermissionDenied("Only faculty and the lab coordinator can allocate equipment")

    def plan(self, actor: User, equipment_id: str) -> list[AllocationDecision]:
        self._require_planner(actor)
        equipment = self.require_equipment(equipment_id)
        requests = {r.id: r for r in self.repo.list_requests()}
        decisions = plan_allocation(
            equipment,
            self.repo.waitlist(equipment_id),
            requests,
            self.repo.list_equipment(),
            self.repo.list_bookings(),
            self.now(),
        )
        explanations = self.ai.explain_plan(decisions, self.user_map(), self.equipment_map())
        for d in decisions:
            d.explanation = explanations.get(d.booking_id, d.reason)
        return decisions

    def apply_plan(self, actor: User, equipment_id: str) -> int:
        """Recompute the plan server-side (never trust a submitted plan) and apply it."""
        self._require_planner(actor)
        decisions = plan_allocation(
            self.require_equipment(equipment_id),
            self.repo.waitlist(equipment_id),
            {r.id: r for r in self.repo.list_requests()},
            self.repo.list_equipment(),
            self.repo.list_bookings(),
            self.now(),
        )
        catalogue = self.equipment_map()
        applied = 0
        for d in decisions:
            if d.start is None or d.equipment_id is None:
                continue
            if d.outcome == "swap" and not self._move_holder(d, catalogue):
                continue
            booking = self.repo.get_booking(d.booking_id)
            if booking is None or booking.status != BookingStatus.WAITLISTED:
                continue
            updated = replace(booking, equipment_id=d.equipment_id, start=d.start, end=d.end,
                              note=f"Fair allocation ({d.outcome}), score {d.score}")
            if self.repo.add_booking_if_capacity(updated, catalogue[d.equipment_id].units):
                applied += 1
        return applied

    def _move_holder(self, d: AllocationDecision, catalogue: dict[str, Equipment]) -> bool:
        held = self.repo.get_booking(d.swap_booking_id or "")
        if held is None or held.status != BookingStatus.CONFIRMED or d.swap_start is None:
            return False
        original = replace(held)
        held.status = BookingStatus.CANCELLED  # release the slot while re-placing
        self.repo.save_booking(held)
        moved = replace(original, equipment_id=d.swap_equipment_id, start=d.swap_start, end=d.swap_end,
                        note="Moved by fair allocation to free a slot for a more urgent project")
        if self.repo.add_booking_if_capacity(moved, catalogue[d.swap_equipment_id].units):
            return True
        self.repo.save_booking(original)  # roll back
        return False

    # Demo ------------------------------------------------------------------
    def reset_demo(self, actor: User) -> None:
        """Restore the demo campus (fresh dates, live 3D-printer conflict). Coordinator only."""
        if actor.role != Role.ADMIN:
            raise PermissionDenied("Only the lab coordinator can reset the demo")
        self.repo.clear()
        self.repo.load(*build_demo_data(self.now()))

    # Utilisation -----------------------------------------------------------
    def utilisation_stats(self, days: int = 14) -> list[dict]:
        now = self.now()
        since = (now - timedelta(days=days)).replace(hour=0, minute=0, second=0, microsecond=0)
        until = now.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)
        waiting: dict[str, int] = {}
        for b in self.repo.waitlist():
            waiting[b.equipment_id] = waiting.get(b.equipment_id, 0) + 1
        stats = []
        for eq in self.repo.list_equipment():
            bookings = self.repo.list_bookings(eq.id)
            stats.append(
                {
                    "id": eq.id,
                    "name": eq.name,
                    "department": eq.department,
                    "units": eq.units,
                    "utilisation": round(100 * utilisation(bookings, eq, since, until)),
                    "upcoming_hours": sum(
                        b.hours for b in bookings if b.status == BookingStatus.CONFIRMED and b.start >= now
                    ),
                    "waitlisted": waiting.get(eq.id, 0),
                }
            )
        return sorted(stats, key=lambda s: (-s["waitlisted"], -s["utilisation"]))
