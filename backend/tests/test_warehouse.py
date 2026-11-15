"""Warehouse business rules: exact stock, atomic movements and retry protection."""
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from backend import models
from backend.migrations import initialize_database

BASE = '/api/warehouse/materials'


@pytest.fixture(autouse=True)
def warehouse_manager(client):
    client.post('/register/', json={'username': 'warehouse-manager', 'password': 'test-only', 'full_name': 'Kierownik testowy'})
    assert client.post('/login/', json={'username': 'warehouse-manager', 'password': 'test-only'}).status_code == 200


def material(client, quantity='10', **fields):
    response = client.post(BASE, json=dict(name='S235', unit='kg', opening_quantity=quantity,
                                          minimum_quantity='2', **fields))
    assert response.status_code == 201, response.text
    return response.json()


def move(client, item, kind, quantity, **fields):
    return client.post(f"{BASE}/{item['id']}/movements", json={
        'kind': kind, 'quantity': quantity, **fields})


def stock(client, item):
    return next(row for row in client.get(BASE).json() if row['id'] == item['id'])


def history(client, item):
    return client.get(f"{BASE}/{item['id']}/movements").json()


def test_receipt_issue_threshold_and_immutable_history(client):
    item = material(client)
    assert move(client, item, 'receipt', '2,5', note='Dostawa nr 1').status_code == 201
    assert move(client, item, 'issue', '11', note='ZL/001').status_code == 201
    current = stock(client, item)
    assert Decimal(current['quantity']) == Decimal('1.5') and current['is_low_stock']
    entries = history(client, item)
    assert [e['kind'] for e in entries] == ['issue', 'receipt', 'opening']
    assert [Decimal(e['balance_after']) for e in entries] == [Decimal('1.5'), Decimal('12.5'), Decimal('10')]
    assert entries[0]['note'] == 'ZL/001' and Decimal(entries[0]['quantity']) == -11
    assert entries[0]['created_at']
    result = client.patch(f"{BASE}/{item['id']}/minimum", json={'minimum_quantity': '1,25'})
    assert result.status_code == 200 and not result.json()['is_low_stock']
    assert history(client, item) == entries  # Threshold edits never rewrite stock movements.


def test_insufficient_stock_is_atomic_and_replenishment_is_possible(client):
    item = material(client, '3')
    before = history(client, item)
    response = move(client, item, 'issue', '3.000001')
    assert response.status_code == 409
    assert history(client, item) == before and Decimal(stock(client, item)['quantity']) == 3
    assert move(client, item, 'issue', '3').status_code == 201
    assert Decimal(stock(client, item)['quantity']) == 0
    assert stock(client, item)['is_low_stock']
    assert move(client, item, 'receipt', '4').status_code == 201
    assert not stock(client, item)['is_low_stock']


def test_decimal_movements_do_not_accumulate_float_errors(client):
    item = material(client, '0')
    assert history(client, item) == []
    for quantity in ('0,1', '0.2'):
        assert move(client, item, 'receipt', quantity).status_code == 201
    assert move(client, item, 'issue', '0.3').status_code == 201
    assert Decimal(stock(client, item)['quantity']) == 0
    assert move(client, item, 'issue', '0.000001').status_code == 409


def test_same_movement_retry_is_idempotent_but_key_reuse_is_rejected(client):
    item = material(client)
    request_id = str(uuid4())
    first = move(client, item, 'issue', '4', request_id=request_id, note='ZL/008')
    repeated = move(client, item, 'issue', '4', request_id=request_id, note='ZL/008')
    assert first.status_code == repeated.status_code == 201 and first.json() == repeated.json()
    assert Decimal(stock(client, item)['quantity']) == 6 and len(history(client, item)) == 2
    assert move(client, item, 'receipt', '4', request_id=request_id, note='ZL/008').status_code == 409
    assert move(client, item, 'issue', '5', request_id=request_id, note='ZL/008').status_code == 409
    assert len(history(client, item)) == 2


def test_material_identity_uses_normalized_name_and_unit(client):
    first = client.post(BASE, json={'name': '  Stal   S235 ', 'unit': ' KG ', 'opening_quantity': '2,5'})
    assert first.status_code == 201 and first.json()['name'] == 'Stal S235' and first.json()['unit'] == 'kg'
    assert client.post(BASE, json={'name': 'stal S235', 'unit': 'kilogramy'}).status_code == 409
    assert client.post(BASE, json={'name': 'Stal S235', 'unit': 'm²'}).status_code == 201
    assert len(client.get(BASE).json()) == 2  # Different units are separate, never implicitly converted.


@pytest.mark.parametrize('bad', [-1, True, 'NaN', 'Infinity', '0.0000001', '1000000000', ''])
def test_bad_amounts_never_change_stock_or_history(client, bad):
    item = material(client)
    before = history(client, item)
    assert move(client, item, 'receipt', bad).status_code == 422
    assert client.patch(f"{BASE}/{item['id']}/minimum", json={'minimum_quantity': bad}).status_code == 422
    assert client.post(BASE, json={'name': 'Inny', 'unit': 'kg', 'opening_quantity': bad}).status_code == 422
    assert history(client, item) == before and Decimal(stock(client, item)['quantity']) == 10
    assert len(client.get(BASE).json()) == 1


@pytest.mark.parametrize('payload', [
    {'name': '   ', 'unit': 'kg'}, {'name': 'Stal', 'unit': 'tonnes'},
    {'name': 'Stal', 'unit': ''}, {'name': 'Stal', 'unit': None},
    {'name': 'Stal', 'unit': 'kg', 'quantity': 100},
])
def test_incomplete_or_unknown_material_fields_are_rejected(client, payload):
    assert client.post(BASE, json=payload).status_code == 422
    assert client.get(BASE).json() == []


def test_zero_issue_unknown_direction_and_oversized_note_are_rejected(client):
    item = material(client)
    assert move(client, item, 'issue', 0).status_code == 422
    assert move(client, item, 'other', 1).status_code == 422
    assert move(client, item, 'receipt', 1, note='x' * 501).status_code == 422
    assert Decimal(stock(client, item)['quantity']) == 10 and len(history(client, item)) == 1


def test_stock_upper_bound_is_enforced_atomically(client):
    item = material(client, '999999999.999999')
    assert move(client, item, 'receipt', '0.000001').status_code == 409
    assert Decimal(stock(client, item)['quantity']) == Decimal('999999999.999999')
    assert len(history(client, item)) == 1


def test_missing_material_and_history_limit(client):
    assert client.get(f'{BASE}/999/movements').status_code == 404
    assert client.post(f'{BASE}/999/movements', json={'kind': 'receipt', 'quantity': 1}).status_code == 404
    assert client.patch(f'{BASE}/999/minimum', json={'minimum_quantity': 1}).status_code == 404
    item = material(client)
    move(client, item, 'receipt', 1)
    assert len(client.get(f"{BASE}/{item['id']}/movements?limit=1").json()) == 1
    assert client.get(f"{BASE}/{item['id']}/movements?limit=0").status_code == 422


def test_failed_commit_rolls_back_stock_and_movement(client, monkeypatch):
    item = material(client)
    before = history(client, item)
    def fail(session):
        raise RuntimeError('forced commit failure')
    with monkeypatch.context() as patch:
        patch.setattr(Session, 'commit', fail)
        with pytest.raises(RuntimeError, match='forced commit failure'):
            move(client, item, 'issue', 2)
    assert Decimal(stock(client, item)['quantity']) == 10 and history(client, item) == before


def test_production_usage_is_separate_from_manual_stock(client, order_payload):
    item = material(client)
    order_id = client.post('/orders/', json=order_payload).json()['order_id']
    order = client.get(f'/orders/{order_id}').json()
    client.put(f'/orders/{order_id}/status?status=IN_PROGRESS')
    assert client.put(f"/parts/{order['parts'][0]['id']}/material-usage", json={
        'material_quantity': 2, 'material_unit': 'kg'}).status_code == 200
    assert Decimal(stock(client, item)['quantity']) == 10
    assert move(client, item, 'issue', 2, note=order['order_number']).status_code == 201
    assert Decimal(stock(client, item)['quantity']) == 8


def test_additive_warehouse_tables_preserve_old_orders():
    engine = create_engine('sqlite://')
    models.Base.metadata.create_all(engine)
    with engine.begin() as connection:
        connection.execute(text('DROP TABLE warehouse_movements'))
        connection.execute(text('DROP TABLE warehouse_materials'))
        connection.execute(text("INSERT INTO orders(id,order_number,client_name) VALUES (1,'LEGACY','Klient')"))
        before = connection.execute(text('SELECT * FROM orders')).all()
    initialize_database(engine)
    initialize_database(engine)
    with engine.connect() as connection:
        assert connection.execute(text('SELECT * FROM orders')).all() == before
        assert connection.execute(text('SELECT count(*) FROM warehouse_materials')).scalar_one() == 0
        assert connection.execute(text('SELECT count(*) FROM warehouse_movements')).scalar_one() == 0
    engine.dispose()
