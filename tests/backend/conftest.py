from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from vidfactory.api.auth_deps import require_user
from vidfactory.api.main import app
from vidfactory.core.auth import hash_password
from vidfactory.database.db import get_db
from vidfactory.database.models import Base, User


@pytest.fixture()
def db_session():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine, autoflush=False, future=True)()
    yield session
    session.close()


@pytest.fixture()
def client(db_session):
    app.dependency_overrides[get_db] = lambda: db_session
    yield TestClient(app)
    app.dependency_overrides.clear()


@pytest.fixture()
def user(db_session):
    u = User(username="pilot", password_hash=hash_password("s3cret"))
    db_session.add(u)
    db_session.commit()
    return u


@pytest.fixture()
def auth_client(client, user):
    """A `client` with auth short-circuited to `user` — for tests not about auth itself."""
    app.dependency_overrides[require_user] = lambda: user
    yield client
    app.dependency_overrides.pop(require_user, None)
