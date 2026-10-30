import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.config import settings
from app.db.session import Base, get_db
from app.main import app
from app.models import User
from app.services.auth import hash_password
from app.services.staffing.common import ensure_state
from app.api.deps import stream_session_factory
from app.api.employees import staffing_session_factory


@pytest.fixture
def db(tmp_path, monkeypatch):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    with engine.begin() as conn:
        conn.execute(text("CREATE VIRTUAL TABLE resume_chunks_fts USING fts5(chunk_id UNINDEXED, candidate_id UNINDEXED, content)"))
    session = sessionmaker(bind=engine, expire_on_commit=False)()
    session.add_all([
        User(username="admin", password_hash=hash_password("test-admin"), role="admin"),
        User(username="hr", password_hash=hash_password("test-hr"), role="hr"),
    ])
    ensure_state(session)
    session.commit()
    monkeypatch.setattr(settings, "upload_dir", str(tmp_path))
    monkeypatch.setattr(settings, "openai_api_key", "")
    yield session
    session.close()
    engine.dispose()


@pytest.fixture
def client(db, monkeypatch):
    monkeypatch.setattr("app.main.init_db", lambda: None)
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[stream_session_factory] = lambda: lambda: db
    # Staffing services open their own sessions; without this they would write to the real database file.
    app.dependency_overrides[staffing_session_factory] = lambda: sessionmaker(bind=db.get_bind(), expire_on_commit=False)
    with TestClient(app, raise_server_exceptions=True) as client:
        yield client
    app.dependency_overrides.clear()


@pytest.fixture
def hr(client):
    response = client.post("/api/auth/login", json={"username": "hr", "password": "test-hr"})
    return {"Authorization": "Bearer " + response.json()["access_token"]}


@pytest.fixture
def admin(client):
    response = client.post("/api/auth/login", json={"username": "admin", "password": "test-admin"})
    return {"Authorization": "Bearer " + response.json()["access_token"]}
