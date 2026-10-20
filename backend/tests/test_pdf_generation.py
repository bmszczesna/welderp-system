import pytest
from fastapi import HTTPException

from backend.integrations import HTML


def test_native_order_card_pdf(client, order_payload):
    try:
        HTML(string='<p>Sprawdzenie generatora</p>')
    except HTTPException as exc:
        if exc.status_code == 503:
            pytest.skip('Brak natywnego WeasyPrint/Pango w podstawowej instalacji')
        raise
    response = client.post('/orders/', json=order_payload)
    result = client.get(f"/orders/{response.json()['order_id']}/pdf")
    assert result.status_code == 200
    assert result.headers['content-type'] == 'application/pdf'
    assert result.content.startswith(b'%PDF-') and len(result.content) > 500
