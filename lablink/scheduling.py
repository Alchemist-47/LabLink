"""Availability and slot search.

Labs are open OPEN_HOUR–CLOSE_HOUR, Monday to Saturday. Bookings are whole hours.
A piece of equipment with N units can host N overlapping confirmed bookings.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta

from .models import Booking, Equipment
from .repository import count_overlapping

OPEN_HOUR = 9
CLOSE_HOUR = 18
OPEN_WEEKDAYS = frozenset(range(6))  # Monday=0 … Saturday=5
MAX_BOOKING_HOURS = CLOSE_HOUR - OPEN_HOUR


@dataclass(frozen=True)
class Slot:
    start: datetime
    end: datetime

    @property
    def hours(self) -> int:
        return int((self.end - self.start).total_seconds() // 3600)


def is_valid_window(start: datetime, end: datetime) -> bool:
    """True if [start, end) is on whole hours, within one open day."""
    return (
        start < end
        and start.date() == end.date()
        and start.weekday() in OPEN_WEEKDAYS
        and start.minute == start.second == 0
        and end.minute == end.second == 0
        and start.hour >= OPEN_HOUR
        and end.hour <= CLOSE_HOUR
        and (end.hour > start.hour)
    )


def free_units(bookings: Iterable[Booking], units: int, start: datetime, end: datetime) -> int:
    return max(0, units - count_overlapping(bookings, start, end))


def _next_whole_hour(moment: datetime) -> datetime:
    rounded = moment.replace(minute=0, second=0, microsecond=0)
    return rounded if rounded == moment else rounded + timedelta(hours=1)


def candidate_windows(hours: int, earliest: datetime, latest: datetime) -> Iterator[Slot]:
    """Yield every valid `hours`-long window that starts at or after `earliest`
    and ends at or before `latest`, in chronological order."""
    if hours < 1 or hours > MAX_BOOKING_HOURS:
        return
    cursor = _next_whole_hour(earliest)
    day = cursor.date()
    while datetime.combine(day, time(OPEN_HOUR)) < latest:
        if day.weekday() in OPEN_WEEKDAYS:
            first = max(OPEN_HOUR, cursor.hour if day == cursor.date() else OPEN_HOUR)
            for hour in range(first, CLOSE_HOUR - hours + 1):
                start = datetime.combine(day, time(hour))
                end = start + timedelta(hours=hours)
                if end > latest:
                    return
                yield Slot(start, end)
        day += timedelta(days=1)


def find_free_slots(
    bookings: list[Booking],
    units: int,
    hours: int,
    earliest: datetime,
    latest: datetime,
    limit: int = 5,
) -> list[Slot]:
    slots: list[Slot] = []
    for slot in candidate_windows(hours, earliest, latest):
        if free_units(bookings, units, slot.start, slot.end) > 0:
            slots.append(slot)
            if len(slots) >= limit:
                break
    return slots


def spread_slots(slots: list[Slot], limit: int = 5) -> list[Slot]:
    """Prefer at most one suggestion per day so options are genuinely different."""
    seen: set[date] = set()
    picked: list[Slot] = []
    for slot in slots:
        if slot.start.date() not in seen:
            seen.add(slot.start.date())
            picked.append(slot)
        if len(picked) >= limit:
            break
    return picked


def day_grid(bookings: list[Booking], units: int, day: date) -> list[tuple[int, int]]:
    """(hour, free units) for each opening hour of `day`; empty if the lab is closed."""
    if day.weekday() not in OPEN_WEEKDAYS:
        return []
    grid = []
    for hour in range(OPEN_HOUR, CLOSE_HOUR):
        start = datetime.combine(day, time(hour))
        grid.append((hour, free_units(bookings, units, start, start + timedelta(hours=1))))
    return grid


def utilisation(bookings: list[Booking], equipment: Equipment, since: datetime, until: datetime) -> float:
    """Booked unit-hours divided by available unit-hours over [since, until)."""
    capacity_hours = 0
    day = since.date()
    while day < until.date():
        if day.weekday() in OPEN_WEEKDAYS:
            capacity_hours += (CLOSE_HOUR - OPEN_HOUR) * equipment.units
        day += timedelta(days=1)
    if capacity_hours == 0:
        return 0.0
    booked = sum(
        b.hours
        for b in bookings
        if b.status == "confirmed" and b.start >= since and b.end <= until
    )
    return min(1.0, booked / capacity_hours)
