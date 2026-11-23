"""Server-side validation of every form input. Nothing from a form is trusted."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

from .models import ProjectType
from .scheduling import MAX_BOOKING_HOURS

ID_PATTERN = re.compile(r"^[a-z]{1,3}_[a-z0-9_]{1,40}$")
MAX_TITLE = 120
MAX_TEXT = 1500
MAX_QUERY = 300
MAX_NEEDS = 6
MAX_NEED_HOURS = 40


class ValidationError(ValueError):
    def __init__(self, errors: list[str]):
        super().__init__("; ".join(errors))
        self.errors = errors


def clean_text(value: str | None, max_len: int) -> str:
    text = (value or "").replace("\x00", "").strip()
    text = re.sub(r"\s+", " ", text)
    return text[:max_len]


def valid_id(value: str | None) -> str | None:
    return value if value and ID_PATTERN.fullmatch(value) else None


@dataclass
class RequestForm:
    project_title: str
    description: str
    deadline: date
    guide_id: str
    project_type: ProjectType
    needs: list[tuple[str, int]] = field(default_factory=list)


def parse_request_form(form, today: date, equipment_ids: set[str], faculty_ids: set[str]) -> RequestForm:
    errors: list[str] = []
    title = clean_text(form.get("project_title"), MAX_TITLE)
    description = clean_text(form.get("description"), MAX_TEXT)
    if len(title) < 3:
        errors.append("Project title must be at least 3 characters.")
    if len(description) < 20:
        errors.append("Describe the project in at least 20 characters.")

    deadline = None
    try:
        deadline = date.fromisoformat(form.get("deadline", ""))
        if deadline <= today:
            errors.append("Deadline must be in the future.")
        elif deadline > today + timedelta(days=365):
            errors.append("Deadline must be within a year.")
    except ValueError:
        errors.append("Enter a valid deadline date.")

    guide_id = form.get("guide_id", "")
    if guide_id not in faculty_ids:
        errors.append("Choose a faculty guide from the list.")

    try:
        project_type = ProjectType(form.get("project_type", ""))
    except ValueError:
        errors.append("Choose a project type.")
        project_type = ProjectType.COURSEWORK

    needs: list[tuple[str, int]] = []
    for eq_id in form.getlist("equipment_id")[:MAX_NEEDS]:
        if eq_id not in equipment_ids:
            errors.append("Unknown equipment selected.")
            continue
        try:
            hours = int(form.get(f"hours_{eq_id}", "0"))
        except ValueError:
            hours = 0
        if not 1 <= hours <= MAX_NEED_HOURS:
            errors.append(f"Hours for each item must be between 1 and {MAX_NEED_HOURS}.")
            continue
        if eq_id not in {n[0] for n in needs}:
            needs.append((eq_id, hours))
    if not needs:
        errors.append("Select at least one piece of equipment.")

    if errors:
        raise ValidationError(errors)
    return RequestForm(title, description, deadline, guide_id, project_type, needs)


@dataclass
class BookingForm:
    request_id: str
    start: datetime
    hours: int


def parse_booking_form(form, now: datetime) -> BookingForm:
    errors: list[str] = []
    request_id = valid_id(form.get("request_id"))
    if not request_id:
        errors.append("Choose the approved project this booking is for.")
    start = None
    try:
        day = date.fromisoformat(form.get("date", ""))
        hour = int(form.get("start_hour", ""))
        start = datetime(day.year, day.month, day.day, hour)
        if start < now:
            errors.append("Choose a time in the future.")
        elif start > now + timedelta(days=60):
            errors.append("Bookings can be made up to 60 days ahead.")
    except ValueError:
        errors.append("Choose a valid date and start time.")
    try:
        hours = int(form.get("hours", ""))
        if not 1 <= hours <= MAX_BOOKING_HOURS:
            raise ValueError
    except ValueError:
        errors.append(f"Duration must be 1–{MAX_BOOKING_HOURS} hours.")
        hours = 0
    if errors:
        raise ValidationError(errors)
    return BookingForm(request_id, start, hours)
