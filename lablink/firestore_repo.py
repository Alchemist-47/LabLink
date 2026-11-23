"""Firestore-backed repository used in production (Cloud Run).

Authentication uses Application Default Credentials, so no key file is needed:
on Cloud Run the service account is picked up automatically.
"""

from __future__ import annotations

from google.cloud import firestore

from .models import (
    AccessRequest,
    Booking,
    BookingStatus,
    Equipment,
    User,
    booking_from_dict,
    equipment_from_dict,
    request_from_dict,
    to_dict,
    user_from_dict,
)
from .repository import Repository, count_overlapping

USERS = "users"
EQUIPMENT = "equipment"
REQUESTS = "requests"
BOOKINGS = "bookings"


class FirestoreRepository(Repository):
    def __init__(self, project: str | None = None, database: str | None = None, client=None) -> None:
        """`client` lets tests inject a fake; production builds a real client from ADC."""
        if client is None:
            kwargs = {}
            if project:
                kwargs["project"] = project
            if database:
                kwargs["database"] = database
            client = firestore.Client(**kwargs)
        self._db = client

    # Users ---------------------------------------------------------------
    def list_users(self) -> list[User]:
        return [user_from_dict(d.to_dict()) for d in self._db.collection(USERS).stream()]

    def get_user(self, user_id: str) -> User | None:
        snap = self._db.collection(USERS).document(user_id).get()
        return user_from_dict(snap.to_dict()) if snap.exists else None

    # Equipment -----------------------------------------------------------
    def list_equipment(self) -> list[Equipment]:
        items = [equipment_from_dict(d.to_dict()) for d in self._db.collection(EQUIPMENT).stream()]
        return sorted(items, key=lambda e: (e.department, e.name))

    def get_equipment(self, equipment_id: str) -> Equipment | None:
        snap = self._db.collection(EQUIPMENT).document(equipment_id).get()
        return equipment_from_dict(snap.to_dict()) if snap.exists else None

    # Requests ------------------------------------------------------------
    def list_requests(self) -> list[AccessRequest]:
        items = [request_from_dict(d.to_dict()) for d in self._db.collection(REQUESTS).stream()]
        return sorted(items, key=lambda r: r.created_at, reverse=True)

    def get_request(self, request_id: str) -> AccessRequest | None:
        snap = self._db.collection(REQUESTS).document(request_id).get()
        return request_from_dict(snap.to_dict()) if snap.exists else None

    def save_request(self, request: AccessRequest) -> None:
        self._db.collection(REQUESTS).document(request.id).set(to_dict(request))

    # Bookings ------------------------------------------------------------
    def list_bookings(self, equipment_id: str | None = None) -> list[Booking]:
        query = self._db.collection(BOOKINGS)
        if equipment_id:
            query = query.where(filter=firestore.FieldFilter("equipment_id", "==", equipment_id))
        items = [booking_from_dict(d.to_dict()) for d in query.stream()]
        return sorted(items, key=lambda b: b.start)

    def get_booking(self, booking_id: str) -> Booking | None:
        snap = self._db.collection(BOOKINGS).document(booking_id).get()
        return booking_from_dict(snap.to_dict()) if snap.exists else None

    def save_booking(self, booking: Booking) -> None:
        self._db.collection(BOOKINGS).document(booking.id).set(to_dict(booking))

    def add_booking_if_capacity(self, booking: Booking, capacity: int) -> bool:
        query = self._db.collection(BOOKINGS).where(
            filter=firestore.FieldFilter("equipment_id", "==", booking.equipment_id)
        )
        ref = self._db.collection(BOOKINGS).document(booking.id)

        @firestore.transactional
        def _txn(transaction: firestore.Transaction) -> bool:
            existing = [booking_from_dict(s.to_dict()) for s in transaction.get(query)]
            if count_overlapping(existing, booking.start, booking.end) >= capacity:
                return False
            booking.status = BookingStatus.CONFIRMED
            transaction.set(ref, to_dict(booking))
            return True

        return _txn(self._db.transaction())

    # Seeding -------------------------------------------------------------
    def is_empty(self) -> bool:
        return not list(self._db.collection(EQUIPMENT).limit(1).stream())

    def clear(self) -> None:
        """Delete all documents, in batches of up to 400 (Firestore allows 500 writes per batch)."""
        for collection in (USERS, EQUIPMENT, REQUESTS, BOOKINGS):
            refs = [snap.reference for snap in self._db.collection(collection).stream()]
            for start in range(0, len(refs), 400):
                batch = self._db.batch()
                for ref in refs[start : start + 400]:
                    batch.delete(ref)
                batch.commit()

    def load(self, users, equipment, requests, bookings) -> None:
        batch = self._db.batch()
        for collection, items in (
            (USERS, users),
            (EQUIPMENT, equipment),
            (REQUESTS, requests),
            (BOOKINGS, bookings),
        ):
            for item in items:
                batch.set(self._db.collection(collection).document(item.id), to_dict(item))
        batch.commit()
