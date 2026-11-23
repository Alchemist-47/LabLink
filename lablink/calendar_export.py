"""Calendar export: download a confirmed booking as an .ics file (RFC 5545).

Times are stored in campus-local time and exported in UTC, so every calendar app
shows them correctly. Each event carries a 15-minute reminder.
"""

from __future__ import annotations

from datetime import UTC, datetime

from flask import Blueprint, Response, abort, current_app

from .models import Booking, BookingStatus, Equipment, Role

bp = Blueprint("calendar", __name__)

REMINDER_MINUTES = 15
MAX_LINE_OCTETS = 75


def escape_text(value: str) -> str:
    """Escape a TEXT value (RFC 5545 §3.3.11)."""
    return (
        value.replace("\\", "\\\\")
        .replace(";", "\\;")
        .replace(",", "\\,")
        .replace("\r\n", "\\n")
        .replace("\n", "\\n")
        .replace("\r", "\\n")
    )


def fold(line: str) -> str:
    """Fold lines longer than 75 octets (RFC 5545 §3.1), never splitting a UTF-8 character."""
    out: list[str] = []
    current = ""
    for ch in line:
        limit = MAX_LINE_OCTETS if not out else MAX_LINE_OCTETS - 1  # continuation lines start with a space
        if len((current + ch).encode("utf-8")) > limit:
            out.append(current)
            current = ch
        else:
            current += ch
    out.append(current)
    return "\r\n ".join(out)


def _utc(moment: datetime) -> str:
    from . import CAMPUS_TZ

    return moment.replace(tzinfo=CAMPUS_TZ).astimezone(UTC).strftime("%Y%m%dT%H%M%SZ")


def booking_to_ics(booking: Booking, equipment: Equipment, project_title: str, now: datetime) -> str:
    """One confirmed booking as an RFC 5545 calendar file, in UTC, with a 15-minute reminder."""
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//LabLink//Lab bookings//EN",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        "BEGIN:VEVENT",
        f"UID:{booking.id}@lablink",
        f"DTSTAMP:{_utc(now)}",
        f"DTSTART:{_utc(booking.start)}",
        f"DTEND:{_utc(booking.end)}",
        f"SUMMARY:{escape_text(f'LabLink: {equipment.name}')}",
        f"LOCATION:{escape_text(f'{equipment.lab}, {equipment.department}')}",
        f"DESCRIPTION:{escape_text(f'Project: {project_title}. Bring your files and follow the lab SOP.')}",
        "BEGIN:VALARM",
        "ACTION:DISPLAY",
        f"DESCRIPTION:{escape_text(f'Lab session on {equipment.name} in {REMINDER_MINUTES} minutes')}",
        f"TRIGGER:-PT{REMINDER_MINUTES}M",
        "END:VALARM",
        "END:VEVENT",
        "END:VCALENDAR",
    ]
    return "\r\n".join(fold(line) for line in lines) + "\r\n"


@bp.get("/bookings/<booking_id>.ics")
def booking_ics(booking_id: str):
    """Download a confirmed booking as .ics. Only its owner or the lab coordinator may."""
    from .routes import current_user
    from .validation import valid_id

    service = current_app.extensions["lablink"]
    booking = service.repo.get_booking(valid_id(booking_id) or "")
    if booking is None or booking.status != BookingStatus.CONFIRMED:
        abort(404)
    me = current_user()
    if booking.user_id != me.id and me.role != Role.ADMIN:
        abort(403, description="You can only export your own bookings.")
    equipment = service.repo.get_equipment(booking.equipment_id)
    if equipment is None:
        abort(404)
    request = service.repo.get_request(booking.request_id)
    body = booking_to_ics(booking, equipment, request.project_title if request else "Lab work", service.now())
    return Response(
        body,
        mimetype="text/calendar",
        headers={"Content-Disposition": f'attachment; filename="lablink-{booking.id}.ics"'},
    )
