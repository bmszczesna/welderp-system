"""Regression tests use the isolated client fixture, never the user's database."""


def test_get_orders(client):
    response = client.get('/orders/')
    assert response.status_code == 200 and response.json() == []


def test_create_order(client, order_payload):
    response = client.post('/orders/', json=order_payload)
    assert response.status_code == 201
    order = client.get(f"/orders/{response.json()['order_id']}").json()
    assert order['order_number'] == order_payload['order_number']
    assert order['status'] == 'IN_QUEUE'


def test_get_nonexistent_order(client):
    assert client.get('/orders/999999').status_code == 404


def test_invalid_status(client, order_payload):
    order_id = client.post('/orders/', json=order_payload).json()['order_id']
    assert client.put(f'/orders/{order_id}/status?status=XYZ').status_code == 400
    assert client.get(f'/orders/{order_id}').json()['status'] == 'IN_QUEUE'


def test_add_operator(client):
    response = client.post('/operators/', params={'name': 'Operator'})
    assert response.status_code == 200
    assert client.get('/operators/').json() == [{'id': response.json()['id'], 'name': 'Operator'}]


def test_sql_injection_login(client):
    assert client.post('/register/', json={'username': 'admin', 'password': 'hasło', 'full_name': 'Administrator'}).status_code == 201
    response = client.post('/login/', json={'username': 'admin', 'password': "' OR '1'='1"})
    assert response.status_code == 401 and 'logged_in' not in response.cookies


def test_sql_injection_in_operator_name(client):
    name = "Robert'); DROP TABLE operators;--"
    assert client.post('/operators/', params={'name': name}).status_code == 200
    assert client.get('/operators/').json()[0]['name'] == name
    assert client.post('/operators/', params={'name': 'Kolejny'}).status_code == 200


def test_xss_in_order_creation(client, order_payload):
    # API storage/JSON contract only; this is not a browser security test.
    order_payload['client_name'] = "<script>alert('hack')</script>"
    response = client.post('/orders/', json=order_payload)
    assert response.status_code == 201
    result = client.get(f"/orders/{response.json()['order_id']}")
    assert result.headers['content-type'] == 'application/json'
    assert result.json()['client_name'] == order_payload['client_name']


def test_nonexistent_order_access(client):
    assert client.get('/orders/99999999').status_code == 404


def test_invalid_http_method(client):
    assert client.delete('/orders/').status_code == 405


def test_invalid_order_id_type(client):
    assert client.get('/orders/abc').status_code == 422
