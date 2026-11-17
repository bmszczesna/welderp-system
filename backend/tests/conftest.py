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
def order_payload(client, db_engine):
    from decimal import Decimal
    from sqlalchemy.orm import Session
    from backend import models
    if client.get('/auth/me').status_code != 200:
        client.post('/register/', json={'username':'order-test-owner','password':'test','full_name':'Kierownik testowy'})
        client.post('/login/', json={'username':'order-test-owner','password':'test'})
    with Session(db_engine) as db:
        item=models.WarehouseMaterial(name='S235 (testy)',name_key='s235 (testy)',unit='kg',
                                      quantity=Decimal('1000'),minimum_quantity=Decimal('0'))
        db.add(item)
        db.commit()
        item_id=item.id
    return {"order_number": "ZL/TEST", "client_name": "Klient testowy",
            "date_received": "2026-10-20T00:00:00", "date_deadline": "2026-10-25T00:00:00",
            "parts": [{"client_key": "part-1", "part_name": "Rama", "material": "S235",
                       "warehouse_material_id":item_id,"planned_quantity":"10",
                       "steps": [{"step_name": "Spawanie"}]}]}


@pytest.fixture
def finish_production(client):
    """Prepare a valid quality handoff through the production and warehouse APIs."""
    def finish(order_id):
        order = client.get(f'/orders/{order_id}').json()
        if order['status'] == 'IN_QUEUE':
            assert client.put(f'/orders/{order_id}/status?status=IN_PROGRESS').status_code == 200
        for part in order['parts']:
            for step in part['steps']:
                assert client.put(f"/steps/{step['id']}/completion", json={'is_completed': True}).status_code == 200
            quantity = part['material_quantity'] if part['material_quantity'] else '1'
            result = client.put(f"/parts/{part['id']}/material-usage", json={
                'material_quantity': quantity, 'material_unit': part['planned_unit']})
            assert result.status_code == 200, result.text
        return client.get(f'/orders/{order_id}').json()
    return finish
