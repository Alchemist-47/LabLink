from datetime import date, datetime

import pytest
from werkzeug.datastructures import MultiDict

from lablink.validation import (
    ValidationError,
    clean_text,
    parse_booking_form,
    parse_request_form,
    valid_id,
)

TODAY = date(2026, 11, 23)


def request_form(**overrides):
    data = {
        "project_title": "Drone",
        "description": "A quadcopter that inspects rooftops with vision.",
        "deadline": "2026-12-10",
        "guide_id": "f_rao",
        "project_type": "capstone",
        "equipment_id": "eq_a",
        "hours_eq_a": "5",
    }
    data.update(overrides)
    return MultiDict(data)


def test_valid_request_form():
    form = parse_request_form(request_form(), TODAY, {"eq_a"}, {"f_rao"})
    assert form.needs == [("eq_a", 5)] and form.deadline == date(2026, 12, 10)


@pytest.mark.parametrize(
    "overrides",
    [
        {"project_title": "x"},
        {"description": "short"},
        {"deadline": "2026-11-01"},
        {"deadline": "not-a-date"},
        {"guide_id": "f_unknown"},
        {"project_type": "hacking"},
        {"equipment_id": "eq_evil"},
        {"hours_eq_a": "0"},
        {"hours_eq_a": "abc"},
    ],
)
def test_invalid_request_forms(overrides):
    with pytest.raises(ValidationError):
        parse_request_form(request_form(**overrides), TODAY, {"eq_a"}, {"f_rao"})


def test_booking_form():
    now = datetime(2026, 11, 23, 8)
    form = parse_booking_form(MultiDict({"request_id": "rq_a", "date": "2026-11-24", "start_hour": "10", "hours": "3"}), now)
    assert form.start == datetime(2026, 11, 24, 10) and form.hours == 3
    with pytest.raises(ValidationError):
        parse_booking_form(MultiDict({"request_id": "rq_a", "date": "2026-11-20", "start_hour": "10", "hours": "3"}), now)
    with pytest.raises(ValidationError):
        parse_booking_form(MultiDict({"request_id": "../etc", "date": "2026-11-24", "start_hour": "10", "hours": "30"}), now)


def test_text_and_id_cleaning():
    assert clean_text("  a \n\n b\x00 ", 10) == "a b"
    assert clean_text("x" * 50, 5) == "xxxxx"
    assert valid_id("rq_abc123") == "rq_abc123"
    assert valid_id("rq_abc'; drop") is None
    assert valid_id(None) is None
