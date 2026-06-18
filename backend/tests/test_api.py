import sys
from unittest.mock import MagicMock
import uuid

sys.modules["weasyprint"] = MagicMock()

from fastapi.testclient import TestClient
from backend.main import app

client = TestClient(app)


def print_response(test_name, method, endpoint, response):
    print("\n" + "=" * 70)
    print(f"TEST: {test_name}")
    print(f"REQUEST: {method} {endpoint}")
    print(f"STATUS: {response.status_code}")

    try:
        print(f"RESPONSE: {response.json()}")
    except Exception:
        print(f"RESPONSE: {response.text}")

    print("=" * 70)


# ============================================================
# COMPONENT TESTING
# ============================================================

def test_get_orders():

    response = client.get("/orders/")

    print_response(
        "COMP-01 Pobranie listy zlecen",
        "GET",
        "/orders/",
        response
    )

    assert response.status_code == 200


def test_create_order():
    order_number = f"TEST_{uuid.uuid4().hex[:8]}"

    response = client.post(
        "/orders/",
        json={
            "order_number": order_number,
            "client_name": "Klient Testowy",
            "parts": []
        }
    )

    print_response(
        "COMP-02 Utworzenie zlecenia",
        "POST",
        "/orders/",
        response
    )

    assert response.status_code in [201, 400]


def test_get_nonexistent_order():

    response = client.get("/orders/999999")

    print_response(
        "COMP-03 Pobranie nieistniejacego zlecenia",
        "GET",
        "/orders/999999",
        response
    )

    assert response.status_code == 404


def test_invalid_status():

    response = client.put(
        "/orders/1/status?status=XYZ"
    )

    print_response(
        "COMP-04 Niepoprawna zmiana statusu",
        "PUT",
        "/orders/1/status?status=XYZ",
        response
    )

    assert response.status_code in [400, 404]


def test_add_operator():

    unique_name = f"TestOperator_{uuid.uuid4().hex[:8]}"

    response = client.post(
        f"/operators/?name={unique_name}"
    )

    print_response(
        "COMP-05 Dodanie operatora",
        "POST",
        f"/operators/?name={unique_name}",
        response
    )

    assert response.status_code == 200


# ============================================================
# SECURITY TESTING
# ============================================================

def test_sql_injection_login():

    response = client.post(
        "/login/",
        params={
            "username": "admin",
            "password": "' OR '1'='1"
        }
    )

    print_response(
        "SEC-01 SQL Injection podczas logowania",
        "POST",
        "/login/",
        response
    )

    assert response.status_code == 401


def test_sql_injection_in_operator_name():

    payload = f"Robert_{uuid.uuid4().hex[:8]}'); DROP TABLE operators;--"

    response = client.post(
        f"/operators/?name={payload}"
    )

    print_response(
        "SEC-02 SQL Injection w nazwie operatora",
        "POST",
        f"/operators/?name={payload}",
        response
    )

    assert response.status_code == 200


def test_xss_in_order_creation():
    order_number = f"XSS_{uuid.uuid4().hex[:8]}"

    response = client.post(
        "/orders/",
        json={
            "order_number": order_number,
            "client_name": "<script>alert('hack')</script>",
            "parts": []
        }
    )

    print_response(
        "SEC-03 XSS w danych klienta",
        "POST",
        "/orders/",
        response
    )

    assert response.status_code in [201, 400]


def test_nonexistent_order_access():

    response = client.get("/orders/99999999")

    print_response(
        "SEC-04 Dostep do nieistniejacego zasobu",
        "GET",
        "/orders/99999999",
        response
    )

    assert response.status_code == 404


def test_invalid_http_method():

    response = client.delete("/orders/")

    print_response(
        "SEC-05 Niepoprawna metoda HTTP",
        "DELETE",
        "/orders/",
        response
    )

    assert response.status_code in [404, 405]


def test_invalid_order_id_type():

    response = client.get("/orders/abc")

    print_response(
        "SEC-06 Niepoprawny typ identyfikatora",
        "GET",
        "/orders/abc",
        response
    )

    assert response.status_code in [404, 422]