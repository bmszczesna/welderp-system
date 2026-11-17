"""Consumption, reservations and the stock ledger form one transaction."""
from copy import deepcopy
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend import models

BASE = '/api/warehouse'


def read_part(client, order):
    return client.get(f"/orders/{order['id']}").json()['parts'][0]


def stock(client, mid):
    return next(item for item in client.get(BASE + '/materials').json() if item['id'] == mid)


def history(client, mid):
    return client.get(f'{BASE}/materials/{mid}/movements').json()


def save(client, part, quantity, **extra):
    return client.put(f"/parts/{part['id']}/material-usage", json={
        'material_quantity': quantity, 'material_unit': 'kg', **extra})


@pytest.fixture
def active(client, order_payload):
    response = client.post('/orders/', json=order_payload)
    assert response.status_code == 201, response.text
    order = client.get(f"/orders/{response.json()['order_id']}").json()
    assert client.put(f"/orders/{order['id']}/status?status=IN_PROGRESS").status_code == 200
    return order


def test_save_retry_and_corrections_account_only_the_difference(client, active):
    part = read_part(client, active)
    mid = part['warehouse_material_id']
    for target, balance, reserved, entries in [('2.4', '997.6', '7.6', 1), ('2.4', '997.6', '7.6', 1),
                                              ('2', '998', '8', 2), ('3.25', '996.75', '6.75', 3),
                                              ('0', '1000', '10', 4)]:
        result = save(client, part, target)
        assert result.status_code == 200, result.text
        current = read_part(client, active)
        assert Decimal(current['issued_quantity']) == Decimal(target)
        assert Decimal(current['reserved_quantity']) == Decimal(reserved)
        assert Decimal(stock(client, mid)['quantity']) == Decimal(balance)
        assert len(history(client, mid)) == entries
    movements = history(client, mid)
    assert {row['kind'] for row in movements} == {'receipt', 'issue'}
    assert sum(Decimal(row['quantity']) for row in movements) == 0
    assert all(row['order_part_id'] == part['id'] for row in movements)


def test_stale_form_cannot_overwrite_newer_usage_but_exact_retry_succeeds(client, active):
    part = read_part(client, active)
    assert save(client, part, '2', expected_version=0).status_code == 200
    assert save(client, part, '2', expected_version=0).status_code == 200
    assert save(client, part, '3', expected_version=1).status_code == 200
    result = save(client, part, '2', expected_version=0)
    assert result.status_code == 409
    current = read_part(client, active)
    assert current['material_quantity'] == 3 and current['material_usage_version'] == 2
    assert len(history(client, part['warehouse_material_id'])) == 2


def test_manual_issue_updates_report_and_is_not_deducted_again(client, active):
    part = read_part(client, active)
    mid = part['warehouse_material_id']
    payload = {'kind': 'issue', 'quantity': '4', 'order_part_id': part['id'], 'request_id': 'manual-usage'}
    for _ in range(2):
        assert client.post(f'{BASE}/materials/{mid}/movements', json=payload).status_code == 201
    current = read_part(client, active)
    assert current['material_quantity'] == 4 and current['material_usage_version'] == 1
    assert save(client, current, '4').status_code == 200
    assert len(history(client, mid)) == 1
    assert save(client, current, '5').status_code == 200
    assert Decimal(stock(client, mid)['quantity']) == 995
    assert save(client, current, '2').status_code == 200
    assert Decimal(read_part(client, active)['issued_quantity']) == 2
    # A later manual issue uses the corrected net amount and keeps the report aligned.
    assert client.post(f'{BASE}/materials/{mid}/movements', json={
        'kind': 'issue', 'quantity': '3', 'order_part_id': part['id']}).status_code == 201
    assert read_part(client, active)['material_quantity'] == 5
    # Only consumption corrections can create a linked return.
    assert client.post(f'{BASE}/materials/{mid}/movements', json={
        'kind': 'receipt', 'quantity': '3', 'order_part_id': part['id']}).status_code == 422


def test_legacy_report_is_not_deducted_until_explicit_save(client, active, db_engine):
    part = read_part(client, active)
    with Session(db_engine) as db:
        legacy = db.get(models.OrderPart, part['id'])
        legacy.material_quantity, legacy.material_unit = 2.4, 'kg'
        db.commit()
    assert Decimal(stock(client, part['warehouse_material_id'])['quantity']) == 1000
    assert save(client, part, '2.4').status_code == 200
    assert Decimal(stock(client, part['warehouse_material_id'])['quantity']) == Decimal('997.6')
    assert len(history(client, part['warehouse_material_id'])) == 1


def test_usage_cannot_take_another_orders_reservation(client, active, order_payload):
    part = read_part(client, active)
    payload = deepcopy(order_payload)
    payload['order_number'] = 'OTHER'
    payload['parts'][0]['planned_quantity'] = '990'
    result = client.post('/orders/', json=payload)
    assert result.status_code == 201
    other = client.get(f"/orders/{result.json()['order_id']}").json()
    assert save(client, part, '11').status_code == 409
    assert read_part(client, active)['material_quantity'] is None
    assert save(client, part, '10').status_code == 200
    assert Decimal(read_part(client, other)['reserved_quantity']) == 990
    assert Decimal(stock(client, part['warehouse_material_id'])['quantity']) == 990


def test_correction_restores_own_reservation_before_waiting_order(client, active, order_payload, db_engine):
    part = read_part(client, active)
    with Session(db_engine) as db:
        db.get(models.WarehouseMaterial, part['warehouse_material_id']).quantity = Decimal('10')
        db.commit()
    waiting_payload = deepcopy(order_payload)
    waiting_payload['order_number'] = 'WAITING'
    result = client.post('/orders/', json=waiting_payload)
    waiting = client.get(f"/orders/{result.json()['order_id']}").json()
    assert save(client, part, '6').status_code == 200
    assert save(client, part, '4').status_code == 200
    assert Decimal(read_part(client, active)['reserved_quantity']) == 6
    assert Decimal(read_part(client, waiting)['reserved_quantity']) == 0
    assert Decimal(read_part(client, waiting)['shortage_quantity']) == 10


def test_over_consumption_uses_only_free_stock_and_forecast_uses_net_returns(client, active):
    part = read_part(client, active)
    mid = part['warehouse_material_id']
    assert save(client, part, '12').status_code == 200
    assert Decimal(read_part(client, active)['remaining_quantity']) == 0
    assert save(client, part, '2').status_code == 200
    demands = client.get(BASE + '/demands').json()['items']
    demand = next(row for row in demands if row['part_id'] == part['id'])
    assert Decimal(demand['remaining_quantity']) == 8
    assert Decimal(stock(client, mid)['quantity']) == 998


@pytest.mark.parametrize('quantity,unit', [('1', 'm2'), ('0.0000001', 'kg'), ('1000000000', 'kg')])
def test_usage_rejects_wrong_unit_or_unsupported_precision(client, active, quantity, unit):
    part = read_part(client, active)
    assert save(client, part, quantity, material_unit=unit).status_code == 422
    assert client.put(f"/orders/{active['id']}", json={
        'order_number': active['order_number'], 'client_name': active['client_name'],
        'date_deadline': active['date_deadline'], 'parts': [{
            'id': part['id'], 'part_name': part['part_name'], 'material': part['material'],
            'material_quantity': quantity, 'material_unit': unit,
            'steps': [{'id': step['id'], 'step_name': step['step_name']} for step in part['steps']]}]
    }).status_code == 422
    assert read_part(client, active) == part
    assert history(client, part['warehouse_material_id']) == []


def test_clear_usage_returns_material_and_preserves_steps(client, active):
    part = read_part(client, active)
    assert save(client, part, '0.123456').status_code == 200
    assert client.put(f"/parts/{part['id']}/material-usage", json={
        'material_quantity': None, 'material_unit': None}).status_code == 200
    current = read_part(client, active)
    assert current['material_quantity'] is None and current['material_unit'] is None
    assert Decimal(current['issued_quantity']) == 0
    assert current['steps'] == part['steps']
    assert Decimal(stock(client, part['warehouse_material_id'])['quantity']) == 1000


def test_inventory_write_requires_login_and_accepts_operator(client, active):
    part = read_part(client, active)
    client.cookies.clear()
    assert save(client, part, '1').status_code == 401
    assert client.post('/register/', json={'username': 'worker', 'password': 'test', 'full_name': 'Operator'}).status_code == 201
    assert client.post('/login/', json={'username': 'worker', 'password': 'test'}).status_code == 200
    assert client.get('/auth/me').json()['role'] == 'operator'
    assert save(client, part, '1').status_code == 200


def test_commit_failure_rolls_back_stock_ledger_report_and_reservation(client, active, db_engine, monkeypatch):
    from sqlalchemy.exc import IntegrityError
    part = read_part(client, active)
    mid = part['warehouse_material_id']
    before = stock(client, mid)
    def fail(_):
        raise IntegrityError('UPDATE', {}, Exception('forced failure'))
    with monkeypatch.context() as patch:
        patch.setattr(Session, 'commit', fail)
        assert save(client, part, '2').status_code == 400
    assert read_part(client, active) == part
    assert stock(client, mid) == before
    assert history(client, mid) == []
    with Session(db_engine) as db:
        assert db.scalars(select(models.WarehouseMovement)).all() == []
