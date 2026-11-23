"""Domain models for LabLink.

All datetimes are naive and expressed in campus local time (see config.CAMPUS_TZ).
Models are plain dataclasses so they serialise cleanly to Firestore documents.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import date, datetime
from enum import StrEnum
from typing import Any


class Role(StrEnum):
    """Who is using LabLink: student, faculty guide, or lab coordinator (admin)."""
    STUDENT = "student"
    FACULTY = "faculty"
    ADMIN = "admin"


class RequestStatus(StrEnum):
    """Lifecycle of a project access request."""
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"


class ProjectType(StrEnum):
    """Kind of project; it sets the project part of the fairness score."""
    CAPSTONE = "capstone"
    RESEARCH = "research"
    CLUB = "club"
    COURSEWORK = "coursework"


class RiskLevel(StrEnum):
    """Safety risk of a machine, shown on its page and in faculty briefs."""
    LOW = "low"
    MODERATE = "moderate"
    HIGH = "high"


class BookingStatus(StrEnum):
    """Confirmed bookings hold capacity; waitlisted ones wait for fair allocation."""
    CONFIRMED = "confirmed"
    WAITLISTED = "waitlisted"
    CANCELLED = "cancelled"


@dataclass(frozen=True)
class User:
    """A student, faculty member or coordinator, with the safety trainings they have completed."""
    id: str
    name: str
    role: Role
    department: str
    email: str = ""  # used to match a signed-in identity when DEMO_MODE is off
    trainings: tuple[str, ...] = ()  # equipment categories this person is safety-trained on


@dataclass(frozen=True)
class Equipment:
    """A bookable machine type with its unit count, lab location and access rules."""
    id: str
    name: str
    category: str
    department: str
    lab: str
    units: int
    description: str
    tags: tuple[str, ...] = ()
    requires_training: bool = False
    risk_level: RiskLevel = RiskLevel.LOW
    requires_approval: bool = False  # high-demand/high-risk: faculty guide must approve the project first

    def search_text(self) -> str:
        """Lower-cased text used by the keyword fallback search."""
        return " ".join(
            [self.name, self.category, self.department, self.lab, self.description, *self.tags]
        ).lower()


@dataclass
class EquipmentNeed:
    """Hours of one machine that a project request asks for."""
    equipment_id: str
    hours: int


@dataclass
class AccessRequest:
    """A project-based request for access to one or more pieces of equipment."""

    id: str
    student_id: str
    guide_id: str
    project_title: str
    description: str
    deadline: date
    needs: list[EquipmentNeed]
    created_at: datetime
    project_type: ProjectType = ProjectType.COURSEWORK
    status: RequestStatus = RequestStatus.PENDING
    ai_summary: str = ""
    decision_note: str = ""

    def needs_equipment(self, equipment_id: str) -> bool:
        """True if this request asks for the given machine."""
        return any(n.equipment_id == equipment_id for n in self.needs)


@dataclass
class Booking:
    """A time slot on one machine for one student's project."""
    id: str
    equipment_id: str
    user_id: str
    request_id: str
    start: datetime
    end: datetime
    created_at: datetime
    status: BookingStatus = BookingStatus.CONFIRMED
    note: str = ""

    @property
    def hours(self) -> int:
        """Length of the booking in whole hours."""
        return int((self.end - self.start).total_seconds() // 3600)

    def overlaps(self, start: datetime, end: datetime) -> bool:
        """True if this booking shares any time with [start, end)."""
        return self.start < end and start < self.end


@dataclass
class AllocationDecision:
    """One line of a fair-allocation plan produced by the scheduler."""

    booking_id: str
    student_id: str
    project_title: str
    score: float
    score_breakdown: dict[str, float]
    outcome: str  # "allocated" | "alternative" | "swap" | "deferred"
    equipment_id: str | None = None
    start: datetime | None = None
    end: datetime | None = None
    reason: str = ""
    explanation: str = field(default="")
    # For "swap": the confirmed booking that would move, and where it moves to.
    swap_booking_id: str | None = None
    swap_user_id: str | None = None
    swap_equipment_id: str | None = None
    swap_start: datetime | None = None
    swap_end: datetime | None = None


def to_dict(obj: Any) -> dict[str, Any]:
    """Serialise a dataclass into JSON/Firestore friendly primitives."""
    return _encode(asdict(obj))


def _encode(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: _encode(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_encode(v) for v in value]
    if isinstance(value, datetime):
        return value.isoformat(timespec="minutes")
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, StrEnum):
        return value.value
    return value


def user_from_dict(d: dict[str, Any]) -> User:
    """Rebuild a User from its stored (Firestore) form."""
    return User(
        id=d["id"],
        name=d["name"],
        role=Role(d["role"]),
        department=d["department"],
        email=d.get("email", ""),
        trainings=tuple(d.get("trainings", ())),
    )


def equipment_from_dict(d: dict[str, Any]) -> Equipment:
    """Rebuild Equipment from its stored (Firestore) form."""
    return Equipment(
        id=d["id"],
        name=d["name"],
        category=d["category"],
        department=d["department"],
        lab=d["lab"],
        units=int(d["units"]),
        description=d["description"],
        tags=tuple(d.get("tags", ())),
        requires_training=bool(d.get("requires_training", False)),
        risk_level=RiskLevel(d.get("risk_level", "low")),
        requires_approval=bool(d.get("requires_approval", False)),
    )


def request_from_dict(d: dict[str, Any]) -> AccessRequest:
    """Rebuild an AccessRequest from its stored (Firestore) form."""
    return AccessRequest(
        id=d["id"],
        student_id=d["student_id"],
        guide_id=d["guide_id"],
        project_title=d["project_title"],
        description=d["description"],
        deadline=date.fromisoformat(d["deadline"]),
        needs=[EquipmentNeed(n["equipment_id"], int(n["hours"])) for n in d["needs"]],
        created_at=datetime.fromisoformat(d["created_at"]),
        project_type=ProjectType(d.get("project_type", "coursework")),
        status=RequestStatus(d["status"]),
        ai_summary=d.get("ai_summary", ""),
        decision_note=d.get("decision_note", ""),
    )


def booking_from_dict(d: dict[str, Any]) -> Booking:
    """Rebuild a Booking from its stored (Firestore) form."""
    return Booking(
        id=d["id"],
        equipment_id=d["equipment_id"],
        user_id=d["user_id"],
        request_id=d["request_id"],
        start=datetime.fromisoformat(d["start"]),
        end=datetime.fromisoformat(d["end"]),
        created_at=datetime.fromisoformat(d["created_at"]),
        status=BookingStatus(d["status"]),
        note=d.get("note", ""),
    )
