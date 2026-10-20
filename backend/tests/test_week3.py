from copy import deepcopy

import pytest
from sqlalchemy import inspect, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from backend import settings
from backend.migrations import initialize_database

PDF = b'%PDF-1.4\n1 0 obj<</Type/Catalog>>endobj\n%%EOF'


def create(client, payload):
    response = client.post('/orders/', json=payload)
    assert response.status_code == 201, response.text
    return client.get(f"/orders/{response.json()['order_id']}").json()


def edit(order):
    return {'order_number': order['order_number'], 'client_name': order['client_name'],
            'date_deadline': order['date_deadline'], 'operator': order['operator'],
            'parts': [{'id': p['id'], 'client_key': f"part-{p['id']}", 'part_name': p['part_name'],
                       'material': p['material'], 'operator': p['operator'],
                       'steps': [{'id': s['id'], 'step_name': s['step_name'], 'operator': s['operator']}
                                 for s in p['steps']]} for p in order['parts']]}


def upload(client, part_id, name='rysunek.pdf', content=PDF, mime='application/pdf'):
    return client.post(f'/parts/{part_id}/upload-pdf', files={'file': (name, content, mime)})


def test_health_and_static_pages(client):
    assert client.get('/health').json() == {'status': 'ok', 'database': 'ok'}
    for url in ['/', '/index.html', '/frontend/js/api.js', '/archive.html']:
        assert client.get(url).status_code == 200


@pytest.mark.parametrize('field,value', [('order_number', '  '), ('client_name', ''),
                                        ('parts', []), ('date_deadline', None)])
def test_incomplete_order_has_no_writes(client, order_payload, field, value):
    order_payload[field] = value
    assert client.post('/orders/', json=order_payload).status_code == 422
    assert client.get('/orders/').json() == []


@pytest.mark.parametrize('field,value', [('part_name', ' '), ('steps', [])])
def test_incomplete_part(client, order_payload, field, value):
    order_payload['parts'][0][field] = value
    assert client.post('/orders/', json=order_payload).status_code == 422
    assert client.get('/orders/').json() == []


def test_blank_step_and_reversed_dates(client, order_payload):
    order_payload['parts'][0]['steps'][0]['step_name'] = ' '
    assert client.post('/orders/', json=order_payload).status_code == 422
    order_payload['parts'][0]['steps'][0]['step_name'] = 'Cięcie'
    order_payload['date_deadline'] = '2026-10-19T00:00:00'
    assert client.post('/orders/', json=order_payload).status_code == 422


def test_timezone_and_same_day(client, order_payload):
    order_payload.update(date_received='2026-10-20T12:00:00+02:00', date_deadline='2026-10-20T23:00:00+02:00')
    assert create(client, order_payload)['date_received'] == '2026-10-20T10:00:00'


@pytest.mark.parametrize('unit,canonical', [(' KG ', 'kg'), ('kilogramy', 'kg'),
                                         ('szt.', 'szt'), ('m²', 'm2'), ('MB', 'm')])
def test_material_normalization(client, order_payload, unit, canonical):
    order_payload['parts'][0]['steps'][0].update(material_quantity='1,25', material_unit=unit)
    step = create(client, order_payload)['parts'][0]['steps'][0]
    assert step['material_quantity'] == 1.25 and step['material_unit'] == canonical


def test_quantity_requires_a_material_and_zero_is_valid(client, order_payload):
    order_payload['parts'][0]['material'] = None
    order_payload['parts'][0]['steps'][0].update(material_quantity=0, material_unit='kg')
    assert client.post('/orders/', json=order_payload).status_code == 422
    assert client.get('/orders/').json() == []
    order_payload['parts'][0]['material'] = 'S235'
    assert create(client, order_payload)['parts'][0]['steps'][0]['material_quantity'] == 0


def test_normalization_is_shared_by_edit_and_production(client, order_payload):
    order = create(client, order_payload)
    payload = edit(order)
    payload['parts'][0]['steps'][0].update(material_quantity='2,75', material_unit='szt.')
    assert client.put(f"/orders/{order['id']}", json=payload).status_code == 200
    step = client.get(f"/orders/{order['id']}").json()['parts'][0]['steps'][0]
    assert step['material_quantity'] == 2.75 and step['material_unit'] == 'szt'
    assert client.put(f"/orders/{order['id']}/status?status=IN_PROGRESS").status_code == 200
    result = client.put(f"/steps/{step['id']}/production-record", json={
        'is_completed': False, 'material_quantity': '3,5', 'material_unit': ' KG '})
    assert result.status_code == 200
    assert result.json()['material_quantity'] == 3.5 and result.json()['material_unit'] == 'kg'
    assert client.put(f"/steps/{step['id']}/production-record", json={
        'is_completed': False, 'material_quantity': -1, 'material_unit': 'kg'}).status_code == 422


@pytest.mark.parametrize('fields', [
    {'material_quantity': -1, 'material_unit': 'kg'},
    {'material_quantity': 'NaN', 'material_unit': 'kg'},
    {'material_quantity': 'Infinity', 'material_unit': 'kg'},
    {'material_quantity': True, 'material_unit': 'kg'},
    {'material_quantity': 1}, {'material_unit': 'kg'},
    {'material_quantity': 1, 'material_unit': 'unknown'},
])
def test_invalid_material_does_not_write(client, order_payload, fields):
    order_payload['parts'][0]['steps'][0].update(fields)
    response = client.post('/orders/', json=order_payload)
    assert response.status_code == 422, response.text
    assert client.get('/orders/').json() == []


def test_duplicate_order_on_create_and_edit(client, order_payload):
    first = create(client, order_payload)
    assert client.post('/orders/', json=order_payload).status_code == 400
    order_payload['order_number'] = 'INNY'
    second = create(client, order_payload)
    payload = edit(second)
    payload['order_number'] = first['order_number']
    assert client.put(f"/orders/{second['id']}", json=payload).status_code == 400
    assert client.get(f"/orders/{second['id']}").json()['order_number'] == 'INNY'


def test_same_named_parts_use_unique_mapping(client, order_payload):
    other = deepcopy(order_payload['parts'][0])
    other['client_key'] = 'part-2'
    order_payload['parts'].append(other)
    response = client.post('/orders/', json=order_payload)
    parts = response.json()['parts']
    assert [p['client_key'] for p in parts] == ['part-1', 'part-2']
    assert parts[0]['id'] != parts[1]['id']
    assert upload(client, parts[1]['id']).status_code == 201
    order = client.get(f"/orders/{response.json()['order_id']}").json()
    assert order['parts'][0]['pdfs'] == [] and len(order['parts'][1]['pdfs']) == 1


def test_pdf_details_list_and_download(client, order_payload):
    order = create(client, order_payload)
    part_id = order['parts'][0]['id']
    first, second = upload(client, part_id).json(), upload(client, part_id).json()
    assert first['url'] != second['url']
    detail = client.get(f"/orders/{order['id']}").json()
    listed = client.get('/orders/').json()[0]
    assert detail['parts'][0]['pdfs'] == listed['parts'][0]['pdfs']
    assert client.get(first['url']).content == client.get(second['url']).content == PDF


@pytest.mark.parametrize('name,content,mime', [
    ('bad.txt', PDF, 'application/pdf'), ('bad.pdf', b'not PDF', 'application/pdf'),
    ('empty.pdf', b'', 'application/pdf'), ('bad.pdf', PDF, 'text/plain'),
])
def test_invalid_pdf_is_cleaned_up(client, order_payload, name, content, mime):
    order = create(client, order_payload)
    before = set(settings.UPLOAD_DIR.iterdir())
    assert upload(client, order['parts'][0]['id'], name, content, mime).status_code == 400
    assert set(settings.UPLOAD_DIR.iterdir()) == before
    assert client.get(f"/orders/{order['id']}").json()['parts'][0]['pdfs'] == []


def test_pdf_limit_missing_part_and_safe_name(client, order_payload, monkeypatch):
    order = create(client, order_payload)
    part_id = order['parts'][0]['id']
    result = upload(client, part_id, '../../rysunek.pdf')
    assert result.status_code == 201 and result.json()['file_name'] == 'rysunek.pdf'
    assert '..' not in result.json()['url']
    monkeypatch.setattr(settings, 'MAX_PDF_BYTES', 10)
    assert upload(client, part_id).status_code == 413
    assert upload(client, 999999).status_code == 404


def test_edit_preserves_identity_pdf_and_missing_flag(client, order_payload):
    order = create(client, order_payload)
    part_id = order['parts'][0]['id']
    pdf = upload(client, part_id).json()
    assert client.put(f'/parts/{part_id}/material-missing').status_code == 200
    payload = edit(order)
    payload['client_name'] = 'Poprawiony klient'
    assert client.put(f"/orders/{order['id']}", json=payload).status_code == 200
    part = client.get(f"/orders/{order['id']}").json()['parts'][0]
    assert part['id'] == part_id and part['steps'][0]['id'] == order['parts'][0]['steps'][0]['id']
    assert part['material_missing'] is True and part['pdfs'][0]['id'] == pdf['id']
    assert client.get(pdf['url']).content == PDF


def test_edit_keeps_production_record_and_step_order(client, order_payload):
    order_payload['parts'][0]['steps'].append({'step_name': 'Kontrola'})
    order = create(client, order_payload)
    assert client.put(f"/orders/{order['id']}/status?status=IN_PROGRESS").status_code == 200
    step_id = order['parts'][0]['steps'][0]['id']
    result = client.put(f'/steps/{step_id}/production-record', json={
        'is_completed': True, 'production_notes': 'Wykonano', 'material_quantity': '2,5',
        'material_unit': 'KG', 'completed_by': 'Operator'})
    assert result.status_code == 200
    payload = edit(order)
    payload['parts'][0]['steps'].reverse()
    assert client.put(f"/orders/{order['id']}", json=payload).status_code == 200
    steps = client.get(f"/orders/{order['id']}").json()['parts'][0]['steps']
    assert [s['step_name'] for s in steps] == ['Kontrola', 'Spawanie']
    assert steps[1]['id'] == step_id and steps[1]['is_completed'] is True
    assert steps[1]['material_quantity'] == 2.5 and steps[1]['material_unit'] == 'kg'
    assert steps[1]['production_notes'] == 'Wykonano' and steps[1]['completed_at'] is not None


def test_foreign_and_duplicate_ids_are_rejected(client, order_payload):
    first = create(client, order_payload)
    order_payload['order_number'] = 'INNY'
    second = create(client, order_payload)
    payload = edit(first)
    payload['parts'][0]['id'] = second['parts'][0]['id']
    assert client.put(f"/orders/{first['id']}", json=payload).status_code == 400
    payload = edit(first)
    payload['parts'][0]['steps'][0]['id'] = second['parts'][0]['steps'][0]['id']
    assert client.put(f"/orders/{first['id']}", json=payload).status_code == 400
    payload = edit(first)
    payload['parts'].append(deepcopy(payload['parts'][0]))
    assert client.put(f"/orders/{first['id']}", json=payload).status_code == 422
    assert client.get(f"/orders/{first['id']}").json() == first


def test_removed_part_removes_its_pdf(client, order_payload):
    other = deepcopy(order_payload['parts'][0])
    other['client_key'] = 'part-2'
    order_payload['parts'].append(other)
    order = create(client, order_payload)
    pdf = upload(client, order['parts'][1]['id']).json()
    payload = edit(order)
    payload['parts'].pop()
    assert client.put(f"/orders/{order['id']}", json=payload).status_code == 200
    assert client.get(pdf['url']).status_code == 404


def test_material_rollback_and_history(client, order_payload):
    order = create(client, order_payload)
    order_id, part_id = order['id'], order['parts'][0]['id']
    assert client.put(f'/orders/{order_id}/status?status=IN_PROGRESS').status_code == 200
    assert client.put(f'/parts/{part_id}/material-missing?missing=true').json()['order_status'] == 'IN_QUEUE'
    for status in ['IN_PROGRESS', 'QUALITY_CHECK', 'COMPLETED', 'ARCHIVED']:
        assert client.put(f'/orders/{order_id}/status?status={status}').status_code == 400
    assert client.put(f'/parts/{part_id}/material-missing?missing=false').status_code == 200
    assert client.get(f'/orders/{order_id}').json()['status'] == 'IN_QUEUE'
    assert client.put(f'/orders/{order_id}/status?status=IN_PROGRESS').status_code == 200
    events = client.get(f'/orders/{order_id}/history').json()
    assert [e['to_status'] for e in events] == ['IN_QUEUE', 'IN_PROGRESS', 'IN_QUEUE', 'IN_PROGRESS']
    assert 'Brak materiału' in events[2]['reason']


def test_full_pipeline_completion_and_archive(client, order_payload):
    order = create(client, order_payload)
    order_id, part_id = order['id'], order['parts'][0]['id']
    step_id = order['parts'][0]['steps'][0]['id']
    assert upload(client, part_id).status_code == 201
    assert client.put(f'/orders/{order_id}/status?status=IN_PROGRESS').status_code == 200
    assert client.put(f'/steps/{step_id}/production-record', json={
        'is_completed': True, 'material_quantity': '1,5', 'material_unit': ' KG ', 'completed_by': ' Operator '}).status_code == 200
    assert client.put(f'/orders/{order_id}/status?status=QUALITY_CHECK').status_code == 200
    assert client.put(f'/orders/{order_id}/status?status=COMPLETED').status_code == 400
    assert client.put(f'/orders/{order_id}/approve').status_code == 200
    assert client.put(f'/orders/{order_id}/status?status=COMPLETED').status_code == 200
    completed = client.get(f'/orders/{order_id}').json()
    assert completed['completed_at'] is not None
    assert client.put(f'/parts/{part_id}/material-missing').status_code == 400
    assert client.put(f'/orders/{order_id}', json=edit(order)).status_code == 400
    assert client.put(f'/orders/{order_id}/status?status=ARCHIVED').status_code == 200
    assert client.get(f'/orders/{order_id}').json()['completed_at'] == completed['completed_at']


def test_atomic_update_on_database_error(client, order_payload, monkeypatch):
    order = create(client, order_payload)
    pdf = upload(client, order['parts'][0]['id']).json()
    payload = edit(order)
    payload['client_name'] = 'Niezapisany'
    def fail(session):
        raise IntegrityError('INSERT', {}, Exception('forced failure'))
    with monkeypatch.context() as patch:
        patch.setattr(Session, 'commit', fail)
        assert client.put(f"/orders/{order['id']}", json=payload).status_code == 400
    after = client.get(f"/orders/{order['id']}").json()
    assert after['client_name'] == order['client_name'] and after['parts'][0]['id'] == order['parts'][0]['id']
    assert client.get(pdf['url']).content == PDF


def test_pdf_cleanup_on_database_error(client, order_payload, monkeypatch):
    order = create(client, order_payload)
    before = set(settings.UPLOAD_DIR.iterdir())
    def fail(session):
        raise IntegrityError('INSERT', {}, Exception('forced failure'))
    monkeypatch.setattr(Session, 'commit', fail)
    assert upload(client, order['parts'][0]['id']).status_code == 400
    assert set(settings.UPLOAD_DIR.iterdir()) == before


def test_registration_and_password_spaces(client):
    account = {'username': ' operator ', 'full_name': ' Operator Testowy ', 'password': ' hasło ze spacją '}
    assert client.post('/register/', json=account).status_code == 201
    assert client.post('/register/', json=account).status_code == 400
    assert client.post('/login/', json={'username': 'operator', 'password': account['password']}).status_code == 200
    assert client.post('/login/', json={'username': 'operator', 'password': account['password'].strip()}).status_code == 401


def test_optional_ai_does_not_block_startup(client):
    assert client.get('/health').status_code == 200
    response = client.get('/reports/ai-pdf')
    assert response.status_code == 503 and 'GEMINI_API_KEY' in response.json()['detail']


def test_additive_schema_upgrade_is_idempotent(db_engine):
    with db_engine.begin() as connection:
        connection.execute(text('CREATE TABLE orders (id INTEGER PRIMARY KEY, order_number VARCHAR, client_name VARCHAR, date_received TIMESTAMP, date_deadline TIMESTAMP, status VARCHAR, is_approved BOOLEAN, operator VARCHAR)'))
        connection.execute(text("INSERT INTO orders (id, order_number, client_name, status) VALUES (42, 'STARE', 'Klient', 'IN_QUEUE')"))
    initialize_database(db_engine)
    initialize_database(db_engine)
    with db_engine.connect() as connection:
        row = connection.execute(text('SELECT order_number, status_changed_at, completed_at FROM orders WHERE id=42')).one()
        assert row == ('STARE', None, None)
        assert connection.execute(text('SELECT COUNT(*) FROM order_status_events')).scalar() == 0
        assert 'position' in {c['name'] for c in inspect(connection).get_columns('order_parts')}


def test_openapi_contract(client):
    schema = client.get('/openapi.json').json()
    assert '/orders/{order_id}/history' in schema['paths']
    assert '/metadata/material-units' in schema['paths']
    assert schema['components']['schemas']['PartResponse']['properties']['pdfs']
    assert len([r for r in client.app.routes if getattr(r, 'path', None) == '/register/']) == 1
