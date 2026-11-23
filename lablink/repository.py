"""Storage abstraction.

`Repository` defines the operations the app needs. `MemoryRepository` is used for
tests and local development; `FirestoreRepository` (firestore_repo.py) is used on
Cloud Run. Both implement `add_booking_if_capacity` atomically so two students
can never book the last unit of a machine at the same time.
"""

from __future__ import annotations

import secrets
import threading
from abc import ABC, abstractmethod
from collections.abc import Iterable
from datetime import datetime

from .models import (
    AccessRequest,
    Booking,
    BookingStatus,
    Equipment,
    RequestStatus,
    User,
)


def new_id(prefix: str) -> str:
    """A short random id with a readable prefix, e.g. `bk_3f9a1c2b`."""
    return f"{prefix}_{secrets.token_hex(6)}"


class Repository(ABC):
    """Storage interface. MemoryRepository and FirestoreRepository both implement it
    and pass the same contract tests."""

    @abstractmethod
    def list_users(self) -> list[User]: ...

    @abstractmethod
    def get_user(self, user_id: str) -> User | None: ...

    @abstractmethod
    def list_equipment(self) -> list[Equipment]: ...

    @abstractmethod
    def get_equipment(self, equipment_id: str) -> Equipment | None: ...

    @abstractmethod
    def list_requests(self) -> list[AccessRequest]: ...

    @abstractmethod
    def get_request(self, request_id: str) -> AccessRequest | None: ...

    @abstractmethod
    def save_request(self, request: AccessRequest) -> None: ...

    @abstractmethod
    def list_bookings(self, equipment_id: str | None = None) -> list[Booking]: ...

    @abstractmethod
    def get_booking(self, booking_id: str) -> Booking | None: ...

    @abstractmethod
    def save_booking(self, booking: Booking) -> None: ...

    @abstractmethod
    def add_booking_if_capacity(self, booking: Booking, capacity: int) -> bool:
        """Atomically store a confirmed booking if fewer than `capacity` confirmed
        bookings overlap it. Returns False (and stores nothing) otherwise."""

    @abstractmethod
    def is_empty(self) -> bool: ...

    @abstractmethod
    def clear(self) -> None:
        """Delete every user, equipment item, request and booking."""

    @abstractmethod
    def load(
        self,
        users: Iterable[User],
        equipment: Iterable[Equipment],
        requests: Iterable[AccessRequest],
        bookings: Iterable[Booking],
    ) -> None: ...

    # Convenience queries shared by both backends -------------------------

    def requests_for_student(self, student_id: str) -> list[AccessRequest]:
        """Requests submitted by this student."""
        return [r for r in self.list_requests() if r.student_id == student_id]

    def requests_for_guide(self, guide_id: str) -> list[AccessRequest]:
        """Requests that name this faculty member as guide."""
        return [r for r in self.list_requests() if r.guide_id == guide_id]

    def approved_requests_for(self, student_id: str) -> list[AccessRequest]:
        """This student's approved requests."""
        return [
            r
            for r in self.requests_for_student(student_id)
            if r.status == RequestStatus.APPROVED
        ]

    def bookings_for_user(self, user_id: str) -> list[Booking]:
        """All of this user's bookings, any status."""
        return [b for b in self.list_bookings() if b.user_id == user_id]

    def confirmed_bookings(self, equipment_id: str) -> list[Booking]:
        """Bookings that currently hold capacity on a machine."""
        return [
            b for b in self.list_bookings(equipment_id) if b.status == BookingStatus.CONFIRMED
        ]

    def waitlist(self, equipment_id: str | None = None) -> list[Booking]:
        """Waitlisted bookings, oldest first, optionally for one machine."""
        return [
            b for b in self.list_bookings(equipment_id) if b.status == BookingStatus.WAITLISTED
        ]


def count_overlapping(bookings: Iterable[Booking], start: datetime, end: datetime) -> int:
    """Number of confirmed bookings that overlap [start, end)."""
    return sum(
        1 for b in bookings if b.status == BookingStatus.CONFIRMED and b.overlaps(start, end)
    )


class MemoryRepository(Repository):
    """Thread-safe in-memory storage, used for the demo and tests."""
    def __init__(self) -> None:
        self._users: dict[str, User] = {}
        self._equipment: dict[str, Equipment] = {}
        self._requests: dict[str, AccessRequest] = {}
        self._bookings: dict[str, Booking] = {}
        self._lock = threading.Lock()

    def list_users(self) -> list[User]:
        return list(self._users.values())

    def get_user(self, user_id: str) -> User | None:
        return self._users.get(user_id)

    def list_equipment(self) -> list[Equipment]:
        return sorted(self._equipment.values(), key=lambda e: (e.department, e.name))

    def get_equipment(self, equipment_id: str) -> Equipment | None:
        return self._equipment.get(equipment_id)

    def list_requests(self) -> list[AccessRequest]:
        return sorted(self._requests.values(), key=lambda r: r.created_at, reverse=True)

    def get_request(self, request_id: str) -> AccessRequest | None:
        return self._requests.get(request_id)

    def save_request(self, request: AccessRequest) -> None:
        self._requests[request.id] = request

    def list_bookings(self, equipment_id: str | None = None) -> list[Booking]:
        items = list(self._bookings.values())
        if equipment_id:
            items = [b for b in items if b.equipment_id == equipment_id]
        return sorted(items, key=lambda b: b.start)

    def get_booking(self, booking_id: str) -> Booking | None:
        return self._bookings.get(booking_id)

    def save_booking(self, booking: Booking) -> None:
        self._bookings[booking.id] = booking

    def add_booking_if_capacity(self, booking: Booking, capacity: int) -> bool:
        with self._lock:
            existing = self.list_bookings(booking.equipment_id)
            if count_overlapping(existing, booking.start, booking.end) >= capacity:
                return False
            booking.status = BookingStatus.CONFIRMED
            self._bookings[booking.id] = booking
            return True

    def is_empty(self) -> bool:
        return not self._equipment

    def clear(self) -> None:
        with self._lock:
            for store in (self._users, self._equipment, self._requests, self._bookings):
                store.clear()

    def load(self, users, equipment, requests, bookings) -> None:
        for u in users:
            self._users[u.id] = u
        for e in equipment:
            self._equipment[e.id] = e
        for r in requests:
            self._requests[r.id] = r
        for b in bookings:
            self._bookings[b.id] = b
