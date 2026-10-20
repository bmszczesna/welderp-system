"""Integration of main's part-level material usage with the validated order API."""
import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from backend import models
from backend.migrations import initialize_database


def create(client, payload):
    result = client.post('/orders/', json=payload)
    assert result.status_code == 201, result.text
    return client.get(f"/orders/{result.json()['order_id']}").json()


def edit(order):
    return {'order_number': order['order_number'], 'client_name': 'Zmieniony klient',
            'date_deadline': order['date_deadline'], 'operator': order['operator'],
            'parts': [{'id': p['id'], 'part_name': p['part_name'], 'material': p['material'],
                       'operator': p['operator'], 'steps': [
                           {'id': s['id'], 'step_name': s['step_name'], 'operator': s['operator']}
                           for s in p['steps']]} for p in order['parts']]}


def test_part_amount_create_edit_and_read_contract(client, order_payload):
    order_payload['parts'][0].update(material_quantity='1,25', material_unit=' KG ')
    order = create(client, order_payload)
    part = order['parts'][0]
    assert part['material_quantity'] == 1.25 and part['material_unit'] == 'kg'
    assert client.get('/orders/').json()[0]['parts'] == order['parts']
    assert client.put(f"/orders/{order['id']}", json=edit(order)).status_code == 200
    after = client.get(f"/orders/{order['id']}").json()['parts'][0]
    assert after == part  # Omitting material consumption from structural edits preserves it.
    payload = edit(order)
    payload['parts'][0].update(material_quantity='2,75', material_unit='m²')
    assert client.put(f"/orders/{order['id']}", json=payload).status_code == 200
    part = client.get(f"/orders/{order['id']}").json()['parts'][0]
    assert part['material_quantity'] == 2.75 and part['material_unit'] == 'm2'


def test_part_usage_and_checkbox_preserve_legacy_step_records(client, order_payload):
    order = create(client, order_payload)
    order_id, part_id = order['id'], order['parts'][0]['id']
    step_id = order['parts'][0]['steps'][0]['id']
    assert client.put(f'/orders/{order_id}/status?status=IN_PROGRESS').status_code == 200
    assert client.put(f'/steps/{step_id}/production-record', json={
        'is_completed': True, 'material_used': 'S235', 'material_quantity': '2,5',
        'material_unit': 'kg', 'production_notes': 'Nie usuwaj dawnego wpisu',
    }).status_code == 200
    original = client.get(f'/orders/{order_id}').json()['parts'][0]['steps']
    result = client.put(f'/parts/{part_id}/material-usage', json={
        'material_quantity': '1,25', 'material_unit': ' KG '})
    assert result.status_code == 200 and result.json()['material_quantity'] == 1.25
    part = client.get(f'/orders/{order_id}').json()['parts'][0]
    assert part['steps'] == original and part['material_unit'] == 'kg'
    assert client.put(f'/orders/{order_id}/status?status=QUALITY_CHECK').status_code == 200
    assert client.put(f'/orders/{order_id}/approve').status_code == 200
    assert client.put(f'/steps/{step_id}/completion', json={'is_completed': False}).status_code == 200
    after = client.get(f'/orders/{order_id}').json()
    assert after['status'] == 'IN_PROGRESS' and after['is_approved'] is False
    assert after['parts'][0]['material_quantity'] == 1.25
    assert after['parts'][0]['steps'][0]['production_notes'] == original[0]['production_notes']
    assert client.put(f'/parts/{part_id}/material-usage', json={
        'material_quantity': None, 'material_unit': None}).status_code == 200
    part = client.get(f'/orders/{order_id}').json()['parts'][0]
    assert part['material_quantity'] is None and part['material_unit'] is None
    assert part['steps'][0]['production_notes'] == original[0]['production_notes']


@pytest.mark.parametrize('fields', [
    {'material_quantity': -1, 'material_unit': 'kg'},
    {'material_quantity': 'NaN', 'material_unit': 'kg'},
    {'material_quantity': 'Infinity', 'material_unit': 'kg'},
    {'material_quantity': True, 'material_unit': 'kg'},
    {'material_quantity': 1}, {'material_unit': 'kg'},
    {'material_quantity': 1, 'material_unit': 'unknown'},
])
def test_invalid_part_amount_is_rejected_on_create_and_production(client, order_payload, fields):
    bad = dict(order_payload['parts'][0], **fields)
    assert client.post('/orders/', json=dict(order_payload, parts=[bad])).status_code == 422
    assert client.get('/orders/').json() == []
    order = create(client, order_payload)
    assert client.put(f"/orders/{order['id']}/status?status=IN_PROGRESS").status_code == 200
    assert client.put(f"/parts/{order['parts'][0]['id']}/material-usage", json=fields).status_code == 422
    assert client.get(f"/orders/{order['id']}").json()['parts'][0]['material_quantity'] is None


@pytest.mark.parametrize('status', ['IN_QUEUE', 'QUALITY_CHECK', 'COMPLETED', 'ARCHIVED'])
def test_part_usage_requires_active_production(client, order_payload, status):
    order = create(client, order_payload)
    order_id, part_id = order['id'], order['parts'][0]['id']
    if status != 'IN_QUEUE':
        assert client.put(f'/orders/{order_id}/status?status=QUALITY_CHECK').status_code == 200
        if status in ('COMPLETED', 'ARCHIVED'):
            assert client.put(f'/orders/{order_id}/approve').status_code == 200
            assert client.put(f'/orders/{order_id}/status?status=COMPLETED').status_code == 200
        if status == 'ARCHIVED':
            assert client.put(f'/orders/{order_id}/status?status=ARCHIVED').status_code == 200
    before = client.get(f'/orders/{order_id}').json()
    assert client.put(f'/parts/{part_id}/material-usage', json={
        'material_quantity': 1, 'material_unit': 'kg'}).status_code == 400
    assert client.get(f'/orders/{order_id}').json() == before


def test_part_usage_requires_material_and_allows_zero(client, order_payload):
    order_payload['parts'][0]['material'] = None
    order = create(client, order_payload)
    order_id, part_id = order['id'], order['parts'][0]['id']
    assert client.put(f'/orders/{order_id}/status?status=IN_PROGRESS').status_code == 200
    assert client.put(f'/parts/{part_id}/material-usage', json={
        'material_quantity': 0, 'material_unit': 'kg'}).status_code == 422
    payload = edit(order)
    payload['parts'][0]['material'] = 'S235'
    assert client.put(f'/orders/{order_id}', json=payload).status_code == 200
    assert client.put(f'/parts/{part_id}/material-usage', json={
        'material_quantity': 0, 'material_unit': 'kg'}).status_code == 200
    assert client.put(f'/parts/{part_id}/material-missing').status_code == 200
    assert client.put(f'/parts/{part_id}/material-usage', json={
        'material_quantity': 1, 'material_unit': 'kg'}).status_code == 400


def test_part_usage_write_is_rolled_back_on_database_error(client, order_payload, monkeypatch):
    order = create(client, order_payload)
    assert client.put(f"/orders/{order['id']}/status?status=IN_PROGRESS").status_code == 200
    before = client.get(f"/orders/{order['id']}").json()
    def fail(session):
        raise IntegrityError('UPDATE', {}, Exception('forced failure'))
    with monkeypatch.context() as patch:
        patch.setattr(Session, 'commit', fail)
        assert client.put(f"/parts/{order['parts'][0]['id']}/material-usage", json={
            'material_quantity': 1, 'material_unit': 'kg'}).status_code == 400
    assert client.get(f"/orders/{order['id']}").json() == before


def test_old_material_migration_does_not_mix_units_or_erase_history():
    engine = create_engine('sqlite://')
    models.Base.metadata.create_all(engine)
    with engine.begin() as connection:
        connection.execute(text('ALTER TABLE order_parts DROP COLUMN material_quantity'))
        connection.execute(text('ALTER TABLE order_parts DROP COLUMN material_unit'))
        connection.execute(text("INSERT INTO orders(id, order_number) VALUES (1, 'LEGACY')"))
        for part_id in (1, 2, 3):
            connection.execute(text("INSERT INTO order_parts(id,part_name,material,order_id,position) VALUES (:id,'Rama','S235',1,0)"), {'id': part_id})
        records = [(1, 1, 'S235', 2.5, ' KG '), (2, 1, 'Drut SG2', .35, 'kg'),
                   (3, 1, None, 1.5, 'kg'), (4, 2, 'S235', 2, 'm'),
                   (5, 2, 'S235', 3, 'kg'), (6, 3, 'S235', 7, 'unknown')]
        for step_id, part_id, material, quantity, unit in records:
            connection.execute(text('''INSERT INTO process_steps
                (id,part_id,step_name,material_used,material_quantity,material_unit,position,production_notes)
                VALUES (:id,:part,'Cięcie',:material,:quantity,:unit,0,'Zachowaj dawną notatkę')'''),
                dict(id=step_id, part=part_id, material=material, quantity=quantity, unit=unit))
        before = connection.execute(text('SELECT * FROM process_steps ORDER BY id')).all()
    initialize_database(engine)
    with engine.begin() as connection:
        assert connection.execute(text('SELECT id,material_quantity,material_unit FROM order_parts ORDER BY id')).all() == [
            (1, 4.0, 'kg'), (2, None, None), (3, None, None)]
        assert connection.execute(text('SELECT * FROM process_steps ORDER BY id')).all() == before
        connection.execute(text('UPDATE order_parts SET material_quantity=NULL,material_unit=NULL WHERE id=1'))
    initialize_database(engine)
    with engine.connect() as connection:
        assert connection.execute(text('SELECT material_quantity FROM order_parts WHERE id=1')).scalar_one() is None
    engine.dispose()
