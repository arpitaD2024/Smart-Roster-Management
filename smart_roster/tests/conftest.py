import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from smart_roster.backend.db import Base, get_db
from smart_roster.backend.main import app


@pytest.fixture
def test_engine(tmp_path):
    engine = create_engine(
        f"sqlite:///{(tmp_path / 'test-roster.db').as_posix()}",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(bind=engine)
    yield engine
    Base.metadata.drop_all(bind=engine)
    engine.dispose()


@pytest.fixture
def db_session(test_engine):
    session_factory = sessionmaker(bind=test_engine, autoflush=False, autocommit=False)
    session = session_factory()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture
def client(test_engine):
    session_factory = sessionmaker(bind=test_engine, autoflush=False, autocommit=False)

    def override_get_db():
        session = session_factory()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()
