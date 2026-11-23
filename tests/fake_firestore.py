"""A minimal in-memory stand-in for google.cloud.firestore.Client.

It implements only the calls FirestoreRepository makes, and stores documents as
deep-copied dicts so tests catch anything that isn't Firestore-serialisable.
"""

from __future__ import annotations

import copy
import threading
from collections import defaultdict

_OPS = {"==": lambda a, b: a == b}


class Snapshot:
    def __init__(self, data, reference=None):
        self._data = data
        self.reference = reference

    @property
    def exists(self):
        return self._data is not None

    def to_dict(self):
        return copy.deepcopy(self._data)


class DocRef:
    def __init__(self, store, collection, doc_id):
        self._store, self._collection, self.id = store, collection, doc_id

    def get(self):
        return Snapshot(self._store[self._collection].get(self.id))

    def set(self, data):
        self._store[self._collection][self.id] = copy.deepcopy(data)

    def delete(self):
        self._store[self._collection].pop(self.id, None)


class Query:
    def __init__(self, store, collection, filters=(), max_results=None):
        self._store, self._collection = store, collection
        self._filters, self._limit = tuple(filters), max_results

    def where(self, *, filter):  # noqa: A002 - mirrors the Firestore API
        return Query(self._store, self._collection, (*self._filters, filter), self._limit)

    def limit(self, n):
        return Query(self._store, self._collection, self._filters, n)

    def stream(self):
        docs = [
            Snapshot(d, DocRef(self._store, self._collection, doc_id))
            for doc_id, d in list(self._store[self._collection].items())
            if all(_OPS[f.op_string](d.get(f.field_path), f.value) for f in self._filters)
        ]
        return iter(docs[: self._limit] if self._limit is not None else docs)


class Collection(Query):
    def __init__(self, store, name):
        super().__init__(store, name)

    def document(self, doc_id):
        return DocRef(self._store, self._collection, doc_id)


class Batch:
    def __init__(self):
        self._writes = []

    def set(self, ref, data):
        self._writes.append((ref, data))

    def delete(self, ref):
        self._writes.append((ref, None))

    def commit(self):
        for ref, data in self._writes:
            if data is None:
                ref.delete()
            else:
                ref.set(data)


class Transaction(Batch):
    def get(self, query):
        return query.stream()


class FakeFirestore:
    def __init__(self):
        self.store = defaultdict(dict)
        self.lock = threading.Lock()  # stands in for Firestore's transaction isolation

    def collection(self, name):
        return Collection(self.store, name)

    def batch(self):
        return Batch()

    def transaction(self):
        return Transaction()


def fake_transactional(client: FakeFirestore):
    """Replacement for firestore.transactional: run the function, then commit, atomically."""

    def decorator(fn):
        def run(transaction):
            with client.lock:
                result = fn(transaction)
                transaction.commit()
                return result

        return run

    return decorator
