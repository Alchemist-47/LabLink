"""FairShare: deterministic, explainable allocation of contested equipment.

When more teams want a machine than it can serve, waitlisted bookings are ranked
by a transparent priority score (0–100):

    urgency   (0–40)  nearer project deadline           -> higher
    project   (0–20)  capstone/research > club > course -> higher
    fairness  (0–25)  fewer hours already used          -> higher (prevents hogging)
    waiting   (0–15)  longer time on the waitlist       -> higher

Each team, highest score first, is placed in the first feasible slot before its
deadline, trying in order:
  1. the requested machine (the originally requested time if it is free),
  2. an equivalent machine in another department (same category),
  3. a *swap*: a lower-priority team with a later deadline that holds a slot
     before this deadline is offered a different slot, freeing theirs,
  4. otherwise the team is deferred to the earliest slot after its deadline.

Scores and placements are pure code, so outcomes are reproducible and testable.
Gemini only explains them. Nothing changes until faculty or the lab
coordinator applies the plan.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, time, timedelta

from .models import (
    AccessRequest,
    AllocationDecision,
    Booking,
    BookingStatus,
    Equipment,
    ProjectType,
)
from .scheduling import CLOSE_HOUR, candidate_windows, free_units, is_valid_window

URGENCY_WEIGHT = 40.0
PROJECT_WEIGHT = 20.0
FAIRNESS_WEIGHT = 25.0
WAITING_WEIGHT = 15.0
URGENCY_HORIZON_DAYS = 30
USAGE_CAP_HOURS = 20
WAITING_CAP_DAYS = 7
USAGE_LOOKBACK_DAYS = 30
PLANNING_HORIZON_DAYS = 21
SWAP_MARGIN = 10.0  # a swap needs a clearly higher priority, not a marginal one

PROJECT_FACTOR = {
    ProjectType.CAPSTONE: 1.0,
    ProjectType.RESEARCH: 0.9,
    ProjectType.CLUB: 0.5,
    ProjectType.COURSEWORK: 0.25,
}


@dataclass(frozen=True)
class Score:
    """A waitlisted booking's priority: urgency + project type + fairness + waiting time (max 100)."""
    total: float
    urgency: float
    project: float
    fairness: float
    waiting: float

    def breakdown(self) -> dict[str, float]:
        """The four score parts, rounded, for display and explanations."""
        return {
            "urgency": self.urgency,
            "project": self.project,
            "fairness": self.fairness,
            "waiting": self.waiting,
        }


def hours_used(bookings: list[Booking], user_id: str, now: datetime) -> int:
    """Confirmed hours a student has had or holds (recent past + future), all equipment."""
    since = now - timedelta(days=USAGE_LOOKBACK_DAYS)
    return sum(
        b.hours
        for b in bookings
        if b.user_id == user_id and b.status == BookingStatus.CONFIRMED and b.end >= since
    )


def priority_score(request: AccessRequest, waited_since: datetime, used_hours: int, now: datetime) -> Score:
    """Score one request: nearer deadline, higher-stakes project, fewer hours used and longer wait all raise it."""
    days_left = (request.deadline - now.date()).days
    urgency = URGENCY_WEIGHT * max(0.0, 1 - max(days_left, 0) / URGENCY_HORIZON_DAYS)
    project = PROJECT_WEIGHT * PROJECT_FACTOR[request.project_type]
    fairness = FAIRNESS_WEIGHT * (1 - min(used_hours, USAGE_CAP_HOURS) / USAGE_CAP_HOURS)
    waited_days = max(0.0, (now - waited_since).total_seconds() / 86400)
    waiting = WAITING_WEIGHT * min(waited_days / WAITING_CAP_DAYS, 1.0)
    return Score(
        total=round(urgency + project + fairness + waiting, 1),
        urgency=round(urgency, 1),
        project=round(project, 1),
        fairness=round(fairness, 1),
        waiting=round(waiting, 1),
    )


def deadline_cutoff(request: AccessRequest, horizon: datetime) -> datetime:
    """Latest moment a session may end for this request: closing time on its deadline, capped at `horizon`."""
    return min(datetime.combine(request.deadline, time(CLOSE_HOUR)), horizon)


def first_fit(
    booked: list[Booking],
    units: int,
    hours: int,
    earliest: datetime,
    latest: datetime,
    preferred: datetime | None = None,
) -> tuple[datetime, datetime] | None:
    """Earliest window with a free unit; tries `preferred` first if given."""
    if preferred is not None:
        end = preferred + timedelta(hours=hours)
        if (
            is_valid_window(preferred, end)
            and earliest <= preferred
            and end <= latest
            and free_units(booked, units, preferred, end) > 0
        ):
            return preferred, end
    for slot in candidate_windows(hours, earliest, latest):
        if free_units(booked, units, slot.start, slot.end) > 0:
            return slot.start, slot.end
    return None


class _Planner:
    def __init__(self, equipment, catalogue, all_bookings, requests, now):
        self.equipment = equipment
        self.now = now
        self.earliest = now + timedelta(hours=1)
        self.horizon = now + timedelta(days=PLANNING_HORIZON_DAYS)
        self.requests = requests
        self.all_bookings = all_bookings
        self.alternatives = [e for e in catalogue if e.category == equipment.category and e.id != equipment.id]
        self.units = {e.id: e.units for e in [equipment, *self.alternatives]}
        # Working copy of confirmed bookings so placements in this plan don't collide.
        self.booked: dict[str, list[Booking]] = {
            eq_id: [b for b in all_bookings if b.equipment_id == eq_id and b.status == BookingStatus.CONFIRMED]
            for eq_id in self.units
        }
        self._score_cache: dict[str, Score] = {}

    def score_for(self, booking: Booking) -> Score | None:
        """Cached priority score for a booking, or None if its request no longer exists."""
        request = self.requests.get(booking.request_id)
        if request is None:
            return None
        if booking.id not in self._score_cache:
            used = hours_used(self.all_bookings, booking.user_id, self.now)
            self._score_cache[booking.id] = priority_score(request, booking.created_at, used, self.now)
        return self._score_cache[booking.id]

    def place(self, booking: Booking, eq_id: str, start: datetime, end: datetime) -> None:
        """Reserve a slot in this plan's working copy so later decisions can't reuse it."""
        self.booked[eq_id].append(replace(booking, equipment_id=eq_id, start=start, end=end,
                                          status=BookingStatus.CONFIRMED))

    def decide(self, item: Booking, score: Score) -> AllocationDecision:
        """Try, in order: the requested machine, an equivalent machine, a fair swap; else defer."""
        cutoff = deadline_cutoff(self.requests[item.request_id], self.horizon)
        return (
            self.on_requested(item, score, cutoff)
            or self.on_alternative(item, score, cutoff)
            or self.try_swap(item, score, cutoff)
            or self.defer(item, score)
        )

    def on_requested(self, item: Booking, score: Score, cutoff: datetime) -> AllocationDecision | None:
        """Fit the booking on the requested machine before the deadline, preferring its original time."""
        eq = self.equipment
        fit = first_fit(self.booked[eq.id], eq.units, item.hours, self.earliest, cutoff, item.start)
        if not fit:
            return None
        self.place(item, eq.id, *fit)
        return AllocationDecision(
            item.id, item.user_id, "", score.total, score.breakdown(), "allocated",
            eq.id, *fit, reason="Fits on the requested equipment before the project deadline.",
        )

    def on_alternative(self, item: Booking, score: Score, cutoff: datetime) -> AllocationDecision | None:
        """Fit the booking on an equivalent machine (same category, any department) before the deadline."""
        for alt in self.alternatives:
            fit = first_fit(self.booked[alt.id], alt.units, item.hours, self.earliest, cutoff)
            if fit:
                self.place(item, alt.id, *fit)
                return AllocationDecision(
                    item.id, item.user_id, "", score.total, score.breakdown(), "alternative",
                    alt.id, *fit,
                    reason=f"Requested equipment is full before the deadline; equivalent {alt.name} in {alt.department} is free.",
                )
        return None

    def defer(self, item: Booking, score: Score) -> AllocationDecision:
        """No room before the deadline: offer the earliest later slot, if any."""
        eq = self.equipment
        fit = first_fit(self.booked[eq.id], eq.units, item.hours, self.earliest, self.horizon)
        if fit:
            self.place(item, eq.id, *fit)
        return AllocationDecision(
            item.id, item.user_id, "", score.total, score.breakdown(), "deferred",
            equipment_id=eq.id if fit else None,
            start=fit[0] if fit else None,
            end=fit[1] if fit else None,
            reason="No capacity before the deadline on this or equivalent equipment; "
            + ("earliest slot after the deadline offered." if fit else "no slot in the next three weeks."),
        )

    def try_swap(self, item: Booking, score: Score, cutoff: datetime) -> AllocationDecision | None:
        """Find a lower-priority holder before `cutoff` who can move elsewhere."""
        for _, _, held, holder_request in sorted(self._swap_candidates(item, score, cutoff), key=lambda c: (c[0], c[1])):
            decision = self._swap_with(item, score, held, holder_request)
            if decision:
                return decision
        return None

    def _swap_candidates(self, item: Booking, score: Score, cutoff: datetime) -> list[tuple]:
        """Holders whose slot fits `item`, who score at least SWAP_MARGIN lower and have a later deadline."""
        my_deadline = self.requests[item.request_id].deadline
        candidates = []
        for held in self.booked[self.equipment.id]:
            if held.user_id == item.user_id or held.start < self.earliest or held.end > cutoff or held.hours < item.hours:
                continue
            holder_request = self.requests.get(held.request_id)
            holder_score = self.score_for(held)
            if holder_request is None or holder_score is None:
                continue
            if holder_score.total + SWAP_MARGIN > score.total or holder_request.deadline <= my_deadline:
                continue
            candidates.append((holder_score.total, held.start, held, holder_request))
        return candidates

    def _swap_with(
        self, item: Booking, score: Score, held: Booking, holder_request: AccessRequest
    ) -> AllocationDecision | None:
        """Give `held`'s slot to `item` if the holder can be moved before their own deadline."""
        eq_id = self.equipment.id
        new_slot = (held.start, held.start + timedelta(hours=item.hours))
        without = [b for b in self.booked[eq_id] if b.id != held.id]
        trial = [*without, replace(held, start=new_slot[0], end=new_slot[1], id=item.id)]
        holder_cutoff = deadline_cutoff(holder_request, self.horizon)
        for move_eq in [eq_id, *[a.id for a in self.alternatives]]:
            pool = trial if move_eq == eq_id else self.booked[move_eq]
            moved = first_fit(pool, self.units[move_eq], held.hours, self.earliest, holder_cutoff)
            if moved:
                self.booked[eq_id] = without
                self.place(item, eq_id, *new_slot)
                self.place(held, move_eq, *moved)
                return AllocationDecision(
                    booking_id=item.id, student_id=item.user_id, project_title="",
                    score=score.total, score_breakdown=score.breakdown(), outcome="swap",
                    equipment_id=eq_id, start=new_slot[0], end=new_slot[1],
                    reason="Swap proposed with a lower-priority team whose deadline is later.",
                    swap_booking_id=held.id, swap_user_id=held.user_id,
                    swap_equipment_id=move_eq, swap_start=moved[0], swap_end=moved[1],
                )
        return None


def plan_allocation(
    equipment: Equipment,
    waitlist: list[Booking],
    requests: dict[str, AccessRequest],
    catalogue: list[Equipment],
    all_bookings: list[Booking],
    now: datetime,
) -> list[AllocationDecision]:
    """Build a fair allocation plan for every waitlisted booking on `equipment`.
    Highest priority first, so earlier decisions take the best slots."""
    planner = _Planner(equipment, catalogue, all_bookings, requests, now)
    scored = [(planner.score_for(item), item) for item in waitlist]
    ranked = sorted(
        ((score, item) for score, item in scored if score is not None),
        key=lambda t: (-t[0].total, t[1].created_at, t[1].id),
    )
    decisions: list[AllocationDecision] = []
    for score, item in ranked:
        decision = planner.decide(item, score)
        decision.project_title = requests[item.request_id].project_title
        decisions.append(decision)
    return decisions
