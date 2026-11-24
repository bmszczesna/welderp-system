from datetime import timedelta

import pytest
from sqlalchemy.orm import Session

from backend import auth, models


def register(client, username, password='test-password', requested_role=None):
    payload = {
        'username': username,
        'password': password,
        'full_name': username.title(),
    }
    if requested_role is not None:
        payload['role'] = requested_role
    return client.post('/register/', json=payload)


def login(client, username, password='test-password'):
    return client.post('/login/', json={'username': username, 'password': password})


def test_registration_assigns_manager_then_operator_without_client_role_input(client):
    assert register(client, 'first-user').status_code == 201
    manager = login(client, 'first-user')
    assert manager.status_code == 200
    assert manager.json()['user']['role'] == 'manager'

    assert register(client, 'role-attempt', requested_role='manager').status_code == 422
    assert register(client, 'second-user').status_code == 201
    operator = login(client, 'second-user')
    assert operator.status_code == 200
    assert operator.json()['user']['role'] == 'operator'
    assert client.get('/auth/me').json()['role'] == 'operator'


def test_duplicate_registration_does_not_replace_the_original_password(client):
    assert register(client, 'existing-user').status_code == 201
    duplicate = client.post('/register/', json={
        'username': 'existing-user',
        'password': 'replacement-password',
        'full_name': 'Different Name',
    })

    assert duplicate.status_code == 400
    assert login(client, 'existing-user').status_code == 200
    assert login(client, 'existing-user', 'replacement-password').status_code == 401


@pytest.mark.parametrize(('username', 'password'), [
    ('missing-user', 'test-password'),
    ('existing-user', 'wrong-password'),
])
def test_failed_login_does_not_create_an_authenticated_session(client, username, password):
    if username == 'existing-user':
        assert register(client, username).status_code == 201

    response = login(client, username, password)

    assert response.status_code == 401
    assert 'welderp_session' not in response.cookies
    assert client.get('/auth/me').status_code == 401


def test_login_cookie_is_http_only_and_session_token_is_not_in_json(client):
    assert register(client, 'cookie-user').status_code == 201

    response = login(client, 'cookie-user')

    assert response.status_code == 200
    assert set(response.json()) == {'message', 'user'}
    assert set(response.json()['user']) == {'id', 'username', 'full_name', 'role'}
    assert 'welderp_session=' in response.headers['set-cookie']
    assert 'httponly' in response.headers['set-cookie'].lower()
    assert client.get('/auth/me').json()['username'] == 'cookie-user'


def test_logout_revokes_the_server_side_session(client):
    assert register(client, 'logout-user').status_code == 201
    assert login(client, 'logout-user').status_code == 200
    assert client.get('/auth/me').status_code == 200

    response = client.post('/logout/')

    assert response.status_code == 200
    assert response.json()['message'] == 'Wylogowano'
    assert client.get('/auth/me').status_code == 401


def test_expired_session_and_forged_ui_cookie_do_not_authenticate(client, db_engine):
    assert register(client, 'expired-user').status_code == 201
    assert login(client, 'expired-user').status_code == 200
    with Session(db_engine) as db:
        session = db.query(models.UserSession).one()
        session.expires_at = auth.now() - timedelta(seconds=1)
        db.commit()

    assert client.get('/auth/me').status_code == 401
    client.cookies.set('logged_in', 'true')
    assert client.get('/auth/me').status_code == 401


def test_warehouse_endpoints_require_a_manager_role(client):
    material = {'name': 'S235 API auth', 'unit': 'kg', 'opening_quantity': '10'}
    assert client.get('/api/warehouse/materials').status_code == 401
    assert client.post('/api/warehouse/materials', json=material).status_code == 401

    assert register(client, 'manager-user').status_code == 201
    assert login(client, 'manager-user').json()['user']['role'] == 'manager'
    assert client.get('/api/warehouse/materials').status_code == 200
    created = client.post('/api/warehouse/materials', json=material)
    assert created.status_code == 201

    assert register(client, 'operator-user').status_code == 201
    assert login(client, 'operator-user').json()['user']['role'] == 'operator'
    assert client.get('/metadata/material-catalog').status_code == 200
    assert client.get('/api/warehouse/materials').status_code == 403
    assert client.post('/api/warehouse/materials', json={
        'name': 'Nieuprawniony materiał',
        'unit': 'kg',
        'opening_quantity': '10',
    }).status_code == 403
