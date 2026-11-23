from datetime import date, datetime

from lablink.models import (
    booking_from_dict,
    equipment_from_dict,
    request_from_dict,
    to_dict,
    user_from_dict,
)
from lablink.seed import build_demo_data


def test_round_trip_matches_firestore_document_format():
    users, equipment, requests, bookings = build_demo_data(datetime(2026, 11, 23, 8))
    for items, loader in ((users, user_from_dict), (equipment, equipment_from_dict),
                          (requests, request_from_dict), (bookings, booking_from_dict)):
        for item in items:
            doc = to_dict(item)
            assert all(not isinstance(v, (date, datetime)) for v in doc.values())  # JSON/Firestore-safe
            assert loader(doc) == item


def test_seed_is_relative_to_now():
    _, _, requests, _ = build_demo_data(datetime(2027, 3, 1, 8))
    assert all(r.deadline > date(2027, 3, 1) for r in requests)
