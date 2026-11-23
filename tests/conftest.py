from datetime import datetime

import pytest

from lablink import create_app
from lablink.ai import LabAssistant
from lablink.repository import MemoryRepository

# Monday 08:00, before labs open, so "today" is fully bookable.
NOW = datetime(2026, 11, 23, 8, 0)


@pytest.fixture
def repo():
    return MemoryRepository()


@pytest.fixture
def app(repo):
    return create_app(
        repo=repo,
        assistant=LabAssistant(None),
        clock=lambda: NOW,
        config={"TESTING": True, "SECRET_KEY": "test", "AI_RATE_LIMIT": 1000},
    )


@pytest.fixture
def service(app):
    return app.extensions["lablink"]


@pytest.fixture
def client(app):
    c = app.test_client()
    with c.session_transaction() as s:
        s["_csrf"] = "tok"
    return c


def login(client, user_id):
    with client.session_transaction() as s:
        s["user_id"] = user_id
