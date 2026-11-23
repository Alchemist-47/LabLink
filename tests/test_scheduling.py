from datetime import date, datetime

from lablink.models import Booking
from lablink.scheduling import (
    candidate_windows,
    day_grid,
    find_free_slots,
    free_units,
    is_valid_window,
    spread_slots,
)

MON = date(2026, 11, 23)


def bk(i, start, end):
    return Booking(f"b{i}", "eq", "u", "r", start, end, start)


def test_valid_window_rules():
    assert is_valid_window(datetime(2026, 11, 23, 9), datetime(2026, 11, 23, 18))
    assert not is_valid_window(datetime(2026, 11, 23, 8), datetime(2026, 11, 23, 10))  # before opening
    assert not is_valid_window(datetime(2026, 11, 23, 17), datetime(2026, 11, 23, 19))  # after closing
    assert not is_valid_window(datetime(2026, 11, 29, 10), datetime(2026, 11, 29, 12))  # Sunday
    assert not is_valid_window(datetime(2026, 11, 23, 10, 30), datetime(2026, 11, 23, 12))  # not on the hour
    assert not is_valid_window(datetime(2026, 11, 23, 12), datetime(2026, 11, 23, 12))


def test_candidate_windows_skip_sunday_and_respect_bounds():
    windows = list(candidate_windows(9, datetime(2026, 11, 28, 9), datetime(2026, 12, 1, 0)))
    assert [w.start.date() for w in windows] == [date(2026, 11, 28), date(2026, 11, 30)]
    assert list(candidate_windows(0, datetime(2026, 11, 23, 9), datetime(2026, 11, 24))) == []
    assert list(candidate_windows(10, datetime(2026, 11, 23, 9), datetime(2026, 11, 24))) == []


def test_capacity_counts_overlaps_only():
    bookings = [bk(1, datetime(2026, 11, 23, 9), datetime(2026, 11, 23, 11))]
    assert free_units(bookings, 2, datetime(2026, 11, 23, 10), datetime(2026, 11, 23, 12)) == 1
    assert free_units(bookings, 1, datetime(2026, 11, 23, 11), datetime(2026, 11, 23, 12)) == 1


def test_find_free_slots_avoids_full_hours():
    bookings = [bk(1, datetime(2026, 11, 23, 9), datetime(2026, 11, 23, 15))]
    slots = find_free_slots(bookings, 1, 2, datetime(2026, 11, 23, 9), datetime(2026, 11, 24, 0))
    assert slots[0].start == datetime(2026, 11, 23, 15)
    assert all(s.end.hour <= 18 for s in slots)


def test_spread_slots_one_per_day():
    slots = find_free_slots([], 1, 1, datetime(2026, 11, 23, 9), datetime(2026, 11, 26, 0), limit=50)
    picked = spread_slots(slots, 5)
    assert len({s.start.date() for s in picked}) == len(picked) == 3


def test_day_grid_closed_sunday():
    assert day_grid([], 1, date(2026, 11, 29)) == []
    grid = day_grid([bk(1, datetime(2026, 11, 23, 9), datetime(2026, 11, 23, 10))], 2, MON)
    assert grid[0] == (9, 1) and grid[1] == (10, 2) and len(grid) == 9
