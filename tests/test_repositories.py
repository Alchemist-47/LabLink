"""Contract tests: both storage backends must behave identically.

The Firestore backend runs against an in-memory fake client (tests/fake_firestore.py),
so these tests need no network or credentials.
"""

import threading
from dataclasses import replace
from datetime import datetime, timedelta

import pytest
from fake_firestore import FakeFirestore, fake_transactional

from lablink import create_app, firestore_repo
from lablink.ai import LabAssistant
from lablink.firestore_repo import FirestoreRepository
from lablink.models import Booking, BookingStatus, RequestStatus
from lablink.repository import MemoryRepository
from lablink.seed import build_demo_data

NOW = datetime(2026, 11, 23, 8, 0)


@pytest.fixture
def fake_client(monkeypatch):
    client = FakeFirestore()
    monkeypatch.setattr(firestore_repo.firestore, "transactional", fake_transactional(client))
    return client


@pytest.fixture(params=["memory", "firestore"])
def repo(request, fake_client):
    if request.param == "memory":
        return MemoryRepository()
    return FirestoreRepository(client=fake_client)


@pytest.fixture
def seeded(repo):
    repo.load(*build_demo_data(NOW))
    return repo


def make_booking(bid, start_hour, hours=2, status=BookingStatus.CONFIRMED, eq="eq_dso"):
    start = datetime(2026, 12, 1, start_hour)
    return Booking(bid, eq, "s_meera", "rq_hand", start, start + timedelta(hours=hours), NOW, status)


def test_empty_then_loaded(repo):
    assert repo.is_empty()
    repo.load(*build_demo_data(NOW))
    assert not repo.is_empty()


def test_reads_round_trip(seeded):
    users, equipment, requests, bookings = build_demo_data(NOW)
    assert {u.id for u in seeded.list_users()} == {u.id for u in users}
    assert seeded.get_user("f_rao").name == "Prof. Lakshmi Rao"
    assert seeded.get_equipment("eq_dso") == next(e for e in equipment if e.id == "eq_dso")
    assert seeded.get_request("rq_drone") == next(r for r in requests if r.id == "rq_drone")
    assert seeded.get_booking("bk_w1") == next(b for b in bookings if b.id == "bk_w1")


def test_missing_documents_return_none(seeded):
    assert seeded.get_user("nobody") is None
    assert seeded.get_equipment("eq_none") is None
    assert seeded.get_request("rq_none") is None
    assert seeded.get_booking("bk_none") is None


def test_ordering(seeded):
    eq = seeded.list_equipment()
    assert eq == sorted(eq, key=lambda e: (e.department, e.name))
    reqs = seeded.list_requests()
    assert [r.created_at for r in reqs] == sorted((r.created_at for r in reqs), reverse=True)
    bks = seeded.list_bookings()
    assert [b.start for b in bks] == sorted(b.start for b in bks)


def test_filtered_queries(seeded):
    assert {b.equipment_id for b in seeded.list_bookings("eq_fdm_printer")} == {"eq_fdm_printer"}
    assert len(seeded.waitlist("eq_fdm_printer")) == 4
    assert all(b.status == BookingStatus.CONFIRMED for b in seeded.confirmed_bookings("eq_fdm_printer"))
    assert {r.id for r in seeded.requests_for_guide("f_khan")} == {"rq_hand", "rq_biosensor"}
    assert {r.id for r in seeded.approved_requests_for("s_ananya")} == {"rq_drone"}


def test_save_is_upsert(seeded):
    request = seeded.get_request("rq_biosensor")
    request.status = RequestStatus.APPROVED
    seeded.save_request(request)
    assert seeded.get_request("rq_biosensor").status == RequestStatus.APPROVED
    booking = replace(seeded.get_booking("bk_w1"), note="moved")
    seeded.save_booking(booking)
    assert seeded.get_booking("bk_w1").note == "moved"
    assert len([b for b in seeded.list_bookings() if b.id == "bk_w1"]) == 1


def test_capacity_check(repo):
    # eq_dso has 2 units; waitlisted and cancelled bookings don't use capacity.
    repo.save_booking(make_booking("w", 10, status=BookingStatus.WAITLISTED))
    repo.save_booking(make_booking("c", 10, status=BookingStatus.CANCELLED))
    assert repo.add_booking_if_capacity(make_booking("a", 10), 2)
    assert repo.add_booking_if_capacity(make_booking("b", 11), 2)
    assert not repo.add_booking_if_capacity(make_booking("x", 10), 2)
    assert repo.get_booking("x") is None
    assert repo.add_booking_if_capacity(make_booking("d", 12), 2)  # touches but doesn't overlap "a"
    assert repo.get_booking("a").status == BookingStatus.CONFIRMED


def test_capacity_check_promotes_waitlisted_booking(repo):
    repo.save_booking(make_booking("w", 10, status=BookingStatus.WAITLISTED))
    waiting = repo.get_booking("w")
    assert repo.add_booking_if_capacity(waiting, 1)
    assert repo.get_booking("w").status == BookingStatus.CONFIRMED


def test_concurrent_capacity_is_atomic(repo):
    results = []

    def attempt(i):
        results.append(repo.add_booking_if_capacity(make_booking(f"t{i}", 10), 2))

    threads = [threading.Thread(target=attempt, args=(i,)) for i in range(12)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert results.count(True) == 2
    assert len(repo.confirmed_bookings("eq_dso")) == 2


def test_documents_are_plain_primitives(fake_client):
    repo = FirestoreRepository(client=fake_client)
    repo.load(*build_demo_data(NOW))
    doc = fake_client.store["bookings"]["bk_w1"]
    assert doc["start"].startswith("2026-") and doc["status"] == "waitlisted"
    assert isinstance(fake_client.store["equipment"]["eq_dso"]["tags"], list)


def test_full_allocation_flow_on_firestore(fake_client):
    app = create_app(
        repo=FirestoreRepository(client=fake_client),
        assistant=LabAssistant(None),
        clock=lambda: NOW,
        config={"TESTING": True, "SECRET_KEY": "t"},
    )
    service = app.extensions["lablink"]
    admin = service.repo.get_user("a_lab")
    assert service.apply_plan(admin, "eq_fdm_printer") == 4
    assert service.repo.waitlist("eq_fdm_printer") == []
    for eq_id in ("eq_fdm_printer", "eq_resin_printer"):
        confirmed = service.repo.confirmed_bookings(eq_id)
        for i, a in enumerate(confirmed):
            assert all(not a.overlaps(b.start, b.end) for b in confirmed[i + 1:])


def test_app_selects_firestore_from_env(monkeypatch, fake_client):
    monkeypatch.setenv("LABLINK_STORAGE", "firestore")
    monkeypatch.setattr(firestore_repo.firestore, "Client", lambda **kwargs: fake_client)
    app = create_app(assistant=LabAssistant(None), clock=lambda: NOW, config={"TESTING": True})
    assert isinstance(app.extensions["lablink"].repo, FirestoreRepository)
    assert fake_client.store["equipment"]  # seeded on first start
    # A second start must not re-seed or duplicate data.
    count = len(fake_client.store["bookings"])
    create_app(assistant=LabAssistant(None), clock=lambda: NOW, config={"TESTING": True})
    assert len(fake_client.store["bookings"]) == count


def test_clear_removes_everything(seeded):
    seeded.clear()
    assert seeded.is_empty()
    assert seeded.list_users() == [] and seeded.list_requests() == [] and seeded.list_bookings() == []
    seeded.load(*build_demo_data(NOW))  # reusable after clearing
    assert len(seeded.waitlist("eq_fdm_printer")) == 4
