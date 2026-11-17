"""A quality approval cannot bypass operations or warehouse reconciliation."""
from datetime import timedelta
from decimal import Decimal

import pytest
from sqlalchemy.orm import Session

from backend import auth, models


@pytest.fixture
def production_order(client, db_engine):
    # Authenticate with a real server-side session; password hashing is unrelated
    # to this fixture's production/warehouse checks.
    token = 'production-handoff-test-session'
    with Session(db_engine) as db:
        user = models.User(username='handoff-owner', full_name='Kierownik',
                           hashed_password='unused', role='manager')
        db.add(user)
        db.flush()
        db.add(models.UserSession(user_id=user.id, token_hash=auth.token_hash(token),
                                 expires_at=auth.now() + timedelta(hours=1)))
        db.commit()
    client.cookies.set(auth.COOKIE, token)
    material = client.post('/api/warehouse/materials', json={
        'name': 'S235 handoff', 'unit': 'kg', 'opening_quantity': '100'}).json()
    result = client.post('/orders/', json={
        'order_number': 'HANDOFF/1', 'client_name': 'Klient',
        'date_received': '2026-11-01T00:00:00', 'date_deadline': '2026-12-01T00:00:00',
        'parts': [{'part_name': name, 'material': material['name'],
                   'warehouse_material_id': material['id'], 'planned_quantity': '10',
                   'steps': [{'step_name': 'Cięcie'}, {'step_name': 'Spawanie'}]}
                  for name in ('Rama', 'Wspornik')],
    })
    assert result.status_code == 201, result.text
    return client.get(f"/orders/{result.json()['order_id']}").json()


def move(client, order, status):
    return client.put(f"/orders/{order['id']}/status?status={status}")


def record_production(client, order, *, consume=True):
    assert move(client, order, 'IN_PROGRESS').status_code == 200
    for part in order['parts']:
        for step in part['steps']:
            assert client.put(f"/steps/{step['id']}/completion", json={'is_completed': True}).status_code == 200
        if consume:
            result = client.put(f"/parts/{part['id']}/material-usage", json={
                'material_quantity': '2.123456', 'material_unit': 'kg'})
            assert result.status_code == 200, result.text


def snapshot(client, order):
    mid = order['parts'][0]['warehouse_material_id']
    return (client.get(f"/orders/{order['id']}").json(),
            client.get('/api/warehouse/materials').json(),
            client.get(f'/api/warehouse/materials/{mid}/movements').json())


def test_cannot_skip_production_from_queue(client, production_order):
    before = snapshot(client, production_order)
    for status in ('QUALITY_CHECK', 'COMPLETED'):
        assert move(client, production_order, status).status_code == 400
        assert snapshot(client, production_order) == before


def test_every_operation_of_every_part_is_required(client, production_order):
    record_production(client, production_order)
    last_step = production_order['parts'][-1]['steps'][-1]
    assert client.put(f"/steps/{last_step['id']}/completion", json={'is_completed': False}).status_code == 200
    before = snapshot(client, production_order)
    result = move(client, production_order, 'QUALITY_CHECK')
    assert result.status_code == 400
    assert 'Wspornik' in result.json()['detail'] and 'Spawanie' in result.json()['detail']
    assert snapshot(client, production_order) == before


@pytest.mark.parametrize('quantity', [None, '0', '2'])
def test_empty_zero_or_unreconciled_legacy_consumption_cannot_pass(client, production_order, db_engine, quantity):
    record_production(client, production_order, consume=False)
    with Session(db_engine) as db:
        # Even completed legacy step-level production records do not debit stock.
        for original in production_order['parts']:
            part = db.get(models.OrderPart, original['id'])
            part.material_quantity = float(quantity) if quantity is not None else None
            part.material_unit = 'kg' if quantity is not None else None
            for step in part.steps:
                step.material_quantity, step.material_unit = 2, 'kg'
        db.commit()
    before = snapshot(client, production_order)
    assert move(client, production_order, 'QUALITY_CHECK').status_code == 400
    assert snapshot(client, production_order) == before


@pytest.mark.parametrize('action', ['approve', 'complete'])
@pytest.mark.parametrize('corruption', ['step', 'missing', 'zero', 'quantity', 'unit'])
def test_approval_and_completion_recheck_existing_quality_records(
        client, production_order, db_engine, action, corruption):
    record_production(client, production_order)
    assert move(client, production_order, 'QUALITY_CHECK').status_code == 200
    with Session(db_engine) as db:
        order = db.get(models.Order, production_order['id'])
        order.is_approved = action == 'complete'
        part = order.parts[-1]
        if corruption == 'step':
            part.steps[-1].is_completed = False
        elif corruption == 'missing':
            part.material_quantity, part.material_unit = None, None
        elif corruption == 'zero':
            part.material_quantity = 0
        elif corruption == 'quantity':
            part.material_quantity = 3
        elif corruption == 'unit':
            part.material_unit = 'm'
        db.commit()
    before = snapshot(client, production_order)
    result = (client.put(f"/orders/{production_order['id']}/approve") if action == 'approve'
              else move(client, production_order, 'COMPLETED'))
    assert result.status_code == 400
    assert snapshot(client, production_order) == before


def test_successful_pipeline_deducts_actual_usage_once_and_releases_only_unused_stock(client, production_order):
    record_production(client, production_order)
    assert move(client, production_order, 'QUALITY_CHECK').status_code == 200
    assert move(client, production_order, 'COMPLETED').status_code == 400  # Approval is still required.
    assert client.put(f"/orders/{production_order['id']}/approve").status_code == 200
    before_stock = snapshot(client, production_order)[1][0]
    assert Decimal(before_stock['quantity']) == Decimal('95.753088')
    assert Decimal(before_stock['reserved_quantity']) == Decimal('15.753088')
    assert move(client, production_order, 'COMPLETED').status_code == 200
    completed, materials, movements = snapshot(client, production_order)
    assert completed['completed_at'] is not None
    assert all(Decimal(p['reserved_quantity']) == 0 for p in completed['parts'])
    assert Decimal(materials[0]['quantity']) == Decimal('95.753088')
    assert Decimal(materials[0]['available_quantity']) == Decimal('95.753088')
    assert len(movements) == 3  # Opening balance and one issue per part.
    assert move(client, production_order, 'COMPLETED').status_code == 200
    assert move(client, production_order, 'ARCHIVED').status_code == 200
    assert snapshot(client, production_order)[1:] == (materials, movements)


def test_manual_warehouse_issues_are_valid_consumption(client, production_order):
    record_production(client, production_order, consume=False)
    for part in production_order['parts']:
        result = client.post(f"/api/warehouse/materials/{part['warehouse_material_id']}/movements", json={
            'kind': 'issue', 'quantity': '3', 'order_part_id': part['id']})
        assert result.status_code == 201, result.text
    assert move(client, production_order, 'QUALITY_CHECK').status_code == 200
    assert client.put(f"/orders/{production_order['id']}/approve").status_code == 200
    assert move(client, production_order, 'COMPLETED').status_code == 200
    assert Decimal(snapshot(client, production_order)[1][0]['quantity']) == 94


@pytest.mark.parametrize('missing', ['parts', 'steps'])
def test_legacy_empty_structure_cannot_pass_quality(client, production_order, db_engine, missing):
    record_production(client, production_order)
    with Session(db_engine) as db:
        order = db.get(models.Order, production_order['id'])
        if missing == 'parts':
            # No issues are attached to deleted parts in this legacy fixture.
            for movement in db.query(models.WarehouseMovement).all():
                movement.order_part_id = None
            order.parts = []
        else:
            order.parts[-1].steps = []
        db.commit()
    before = snapshot(client, production_order)
    assert move(client, production_order, 'QUALITY_CHECK').status_code == 400
    assert snapshot(client, production_order) == before
