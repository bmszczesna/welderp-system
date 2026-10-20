"""All API tests use a fresh in-memory database and separate upload storage."""
import os
from pathlib import Path

os.environ["DATABASE_URL"] = "sqlite://"
os.environ["UPLOAD_DIR"] = str(Path(__file__).resolve().parents[2] / ".test-artifacts" / "uploads")
os.environ["GEMINI_API_KEY"] = ""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend import database
from backend.main import app


@pytest.fixture
def db_engine(monkeypatch):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    monkeypatch.setattr(database, "engine", engine)
    monkeypatch.setattr(database, "SessionLocal", sessionmaker(bind=engine, expire_on_commit=False))
    yield engine
    engine.dispose()


@pytest.fixture
def client(db_engine):
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def order_payload():
    return {"order_number": "ZL/TEST", "client_name": "Klient testowy",
            "date_received": "2026-10-20T00:00:00", "date_deadline": "2026-10-25T00:00:00",
            "parts": [{"client_key": "part-1", "part_name": "Rama", "material": "S235",
                       "steps": [{"step_name": "Spawanie"}]}]}
