"""Regression coverage for undoing a completed operation outside production."""
import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session


@pytest.fixture
def completed_operation(client, order_payload):
    created = client.post('/orders/', json=order_payload)
    assert created.status_code == 201
    order_id = created.json()['order_id']
    order = client.get(f'/orders/{order_id}').json()
    step_id = order['parts'][0]['steps'][0]['id']
    assert client.put(f'/orders/{order_id}/status?status=IN_PROGRESS').status_code == 200
    assert client.put(f'/steps/{step_id}/production-record', json={
        'is_completed': True, 'completed_by': 'Operator Testowy',
        'production_notes': 'Zachowaj uwagi przy cofnięciu wykonania',
        'material_used': 'S235', 'material_quantity': '2,4', 'material_unit': 'kg',
    }).status_code == 200
    return client.get(f'/orders/{order_id}').json()


def assert_undone(order, original):
    step, before = order['parts'][0]['steps'][0], original['parts'][0]['steps'][0]
    assert step['is_completed'] is False
    assert step['completed_at'] is None and step['completed_by'] is None
    for field in ('id', 'production_notes', 'material_used', 'material_quantity', 'material_unit'):
        assert step[field] == before[field]


def test_undo_and_redo_during_production(client, completed_operation):
    order = completed_operation
    step_id = order['parts'][0]['steps'][0]['id']
    assert client.put(f'/steps/{step_id}/completion', json={'is_completed': False}).status_code == 200
    after = client.get(f"/orders/{order['id']}").json()
    assert after['status'] == 'IN_PROGRESS'
    assert_undone(after, order)
    assert client.put(f'/steps/{step_id}/completion', json={'is_completed': True}).status_code == 200
    step = client.get(f"/orders/{order['id']}").json()['parts'][0]['steps'][0]
    assert step['is_completed'] is True
    assert step['completed_at'] >= order['parts'][0]['steps'][0]['completed_at']
    assert step['production_notes'] == order['parts'][0]['steps'][0]['production_notes']


@pytest.mark.parametrize('approved', [False, True])
def test_undo_in_quality_returns_to_production_and_invalidates_approval(client, completed_operation, approved):
    order = completed_operation
    order_id, step_id = order['id'], order['parts'][0]['steps'][0]['id']
    assert client.put(f'/orders/{order_id}/status?status=QUALITY_CHECK').status_code == 200
    if approved:
        assert client.put(f'/orders/{order_id}/approve').status_code == 200
    result = client.put(f'/steps/{step_id}/completion', json={'is_completed': False})
    assert result.status_code == 200
    assert result.json()['order_status'] == 'IN_PROGRESS' and result.json()['is_approved'] is False
    after = client.get(f'/orders/{order_id}').json()
    assert_undone(after, order)
    assert after['needs_correction'] is True and after['completed_at'] is None
    history = client.get(f'/orders/{order_id}/history').json()
    assert history[-1]['from_status'] == 'QUALITY_CHECK' and history[-1]['to_status'] == 'IN_PROGRESS'
    assert f'operacji {step_id}' in history[-1]['reason']
    assert client.put(f'/steps/{step_id}/completion', json={'is_completed': False}).status_code == 200
    assert client.get(f'/orders/{order_id}/history').json() == history
    assert client.put(f'/orders/{order_id}/approve').status_code == 400
    assert client.put(f'/orders/{order_id}/status?status=COMPLETED').status_code == 400


@pytest.mark.parametrize('reason', ['material', 'correction', 'queue'])
def test_undo_in_queue_preserves_material_and_correction_flags(client, completed_operation, reason):
    order = completed_operation
    order_id, part_id = order['id'], order['parts'][0]['id']
    step_id = order['parts'][0]['steps'][0]['id']
    if reason == 'material':
        assert client.put(f'/parts/{part_id}/material-missing?missing=true').status_code == 200
    elif reason == 'correction':
        assert client.put(f'/orders/{order_id}/status?status=QUALITY_CHECK').status_code == 200
        assert client.put(f'/orders/{order_id}/needs-correction').status_code == 200
    else:
        assert client.put(f'/orders/{order_id}/status?status=IN_QUEUE').status_code == 200
    history = client.get(f'/orders/{order_id}/history').json()
    assert client.put(f'/steps/{step_id}/completion', json={'is_completed': False}).status_code == 200
    after = client.get(f'/orders/{order_id}').json()
    assert after['status'] == 'IN_QUEUE'
    assert after['needs_correction'] == (reason == 'correction')
    assert after['parts'][0]['material_missing'] == (reason == 'material')
    assert_undone(after, order)
    assert client.get(f'/orders/{order_id}/history').json() == history
    assert client.put(f'/steps/{step_id}/completion', json={'is_completed': True}).status_code == 400


@pytest.mark.parametrize('status', ['COMPLETED', 'ARCHIVED'])
@pytest.mark.parametrize('completed', [False, True])
def test_finished_orders_remain_read_only(client, completed_operation, status, completed):
    order = completed_operation
    order_id, step_id = order['id'], order['parts'][0]['steps'][0]['id']
    assert client.put(f'/orders/{order_id}/status?status=QUALITY_CHECK').status_code == 200
    assert client.put(f'/orders/{order_id}/approve').status_code == 200
    assert client.put(f'/orders/{order_id}/status?status=COMPLETED').status_code == 200
    if status == 'ARCHIVED':
        assert client.put(f'/orders/{order_id}/status?status=ARCHIVED').status_code == 200
    before = client.get(f'/orders/{order_id}').json()
    assert client.put(f'/steps/{step_id}/completion', json={'is_completed': completed}).status_code == 400
    assert client.put(f'/steps/{step_id}/toggle').status_code == 400
    assert client.get(f'/orders/{order_id}').json() == before


@pytest.mark.parametrize('status', ['IN_QUEUE', 'QUALITY_CHECK'])
def test_cannot_mark_new_completion_outside_production(client, order_payload, status):
    created = client.post('/orders/', json=order_payload).json()
    order_id = created['order_id']
    order = client.get(f'/orders/{order_id}').json()
    step_id = order['parts'][0]['steps'][0]['id']
    if status == 'QUALITY_CHECK':
        assert client.put(f'/orders/{order_id}/status?status=QUALITY_CHECK').status_code == 200
    assert client.put(f'/steps/{step_id}/completion', json={'is_completed': True}).status_code == 400
    assert client.get(f'/orders/{order_id}').json()['parts'][0]['steps'][0]['is_completed'] is False


def test_legacy_toggle_can_undo_quality_completion(client, completed_operation):
    order = completed_operation
    order_id, step_id = order['id'], order['parts'][0]['steps'][0]['id']
    assert client.put(f'/orders/{order_id}/status?status=QUALITY_CHECK').status_code == 200
    assert client.put(f'/orders/{order_id}/approve').status_code == 200
    result = client.put(f'/steps/{step_id}/toggle')
    assert result.status_code == 200 and result.json()['is_completed'] is False
    assert result.json()['order_status'] == 'IN_PROGRESS' and result.json()['is_approved'] is False
    assert_undone(client.get(f'/orders/{order_id}').json(), order)


def test_undo_rolls_back_status_approval_and_step_on_database_error(client, completed_operation, monkeypatch):
    order = completed_operation
    order_id, step_id = order['id'], order['parts'][0]['steps'][0]['id']
    assert client.put(f'/orders/{order_id}/status?status=QUALITY_CHECK').status_code == 200
    assert client.put(f'/orders/{order_id}/approve').status_code == 200
    before = client.get(f'/orders/{order_id}').json()
    history = client.get(f'/orders/{order_id}/history').json()
    def fail(session):
        raise IntegrityError('UPDATE', {}, Exception('forced failure'))
    with monkeypatch.context() as patch:
        patch.setattr(Session, 'commit', fail)
        assert client.put(f'/steps/{step_id}/completion', json={'is_completed': False}).status_code == 400
    assert client.get(f'/orders/{order_id}').json() == before
    assert client.get(f'/orders/{order_id}/history').json() == history
