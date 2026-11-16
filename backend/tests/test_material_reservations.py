from copy import deepcopy
from decimal import Decimal
from uuid import uuid4

import pytest

BASE='/api/warehouse'


@pytest.fixture
def stock_manager(client):
    assert client.post('/register/',json={'username':'stock-owner','password':'test','full_name':'Kierownik'}).status_code==201
    assert client.post('/login/',json={'username':'stock-owner','password':'test'}).status_code==200
    return client


def item(client, quantity='100', name='Stal rezerwacja'):
    response=client.post(BASE+'/materials',json={'name':name,'unit':'kg','opening_quantity':quantity})
    assert response.status_code==201,response.text
    return response.json()['id']


def create(client, template, material_id, quantity='60', deadline='2030-01-01T00:00:00'):
    payload=deepcopy(template)
    payload.update(order_number='RES/'+uuid4().hex[:8],date_deadline=deadline)
    payload['parts'][0].update(warehouse_material_id=material_id,planned_quantity=quantity,planned_for=None)
    result=client.post('/orders/',json=payload)
    assert result.status_code==201,result.text
    return client.get(f"/orders/{result.json()['order_id']}").json(),result.json()


def amount(value):
    return Decimal(str(value))


def stock(client, material_id):
    return next(i for i in client.get(BASE+'/materials').json() if i['id']==material_id)


def edit(saved):
    return {'order_number':saved['order_number'],'client_name':saved['client_name'],
        'date_deadline':saved['date_deadline'],'parts':[
        {'id':p['id'],'part_name':p['part_name'],'material':p['material'],
         'warehouse_material_id':p['warehouse_material_id'],'planned_quantity':p['planned_quantity'],
         'steps':[{'id':s['id'],'step_name':s['step_name']} for s in p['steps']]}
        for p in saved['parts']]}


def test_creation_reserves_immediately_without_consumption_date(stock_manager,order_payload):
    mid=item(stock_manager)
    saved,response=create(stock_manager,order_payload,mid)
    assert saved['parts'][0]['planned_for'] is None
    assert amount(saved['parts'][0]['reserved_quantity'])==60
    assert saved['materials_ready'] and response['materials_ready']
    result=stock(stock_manager,mid)
    assert amount(result['quantity'])==100
    assert amount(result['reserved_quantity'])==60
    assert amount(result['available_quantity'])==40


def test_orders_cannot_reserve_the_same_stock_and_show_exact_shortage(stock_manager,order_payload):
    mid=item(stock_manager)
    first,_=create(stock_manager,order_payload,mid,deadline='2040-01-01T00:00:00')
    second,response=create(stock_manager,order_payload,mid,deadline='2030-01-01T00:00:00')
    part=second['parts'][0]
    assert amount(part['reserved_quantity'])==40
    assert amount(part['shortage_quantity'])==20
    assert not response['materials_ready'] and '20 kg' in response['material_warnings'][0]
    assert second['status']=='IN_QUEUE'
    for target in ('IN_PROGRESS','QUALITY_CHECK','COMPLETED'):
        result=stock_manager.put(f"/orders/{second['id']}/status?status={target}")
        assert result.status_code==400,result.text
        assert 'Uzupełnij magazyn' in result.json()['detail']
    assert stock_manager.put(f"/orders/{first['id']}/status?status=IN_PROGRESS").status_code==200


def test_receipt_fills_waiting_reservation_and_unblocks_kanban(stock_manager,order_payload):
    mid=item(stock_manager,'0')
    saved,_=create(stock_manager,order_payload,mid,'10')
    assert not saved['materials_ready']
    # A future delivery plan alone cannot unlock production.
    planned=stock_manager.post(BASE+'/deliveries',json={'material_id':mid,'quantity':'10','expected_on':'2030-01-01'}).json()
    assert stock_manager.put(f"/orders/{saved['id']}/status?status=IN_PROGRESS").status_code==400
    assert stock_manager.post(f"{BASE}/deliveries/{planned['id']}/receive").status_code==200
    after=stock_manager.get(f"/orders/{saved['id']}").json()
    assert after['materials_ready'] and not after['material_warnings']
    assert amount(after['parts'][0]['reserved_quantity'])==10
    assert stock_manager.put(f"/orders/{saved['id']}/status?status=IN_PROGRESS").status_code==200


def test_generic_issue_cannot_take_an_order_reservation(stock_manager,order_payload):
    mid=item(stock_manager)
    saved,_=create(stock_manager,order_payload,mid)
    response=stock_manager.post(f'{BASE}/materials/{mid}/movements',json={'kind':'issue','quantity':'41'})
    assert response.status_code==409
    assert 'zarezerwowany' in response.json()['detail']
    assert stock_manager.post(f'{BASE}/materials/{mid}/movements',json={'kind':'issue','quantity':'40'}).status_code==201
    assert amount(stock(stock_manager,mid)['quantity'])==60
    assert stock_manager.get(f"/orders/{saved['id']}").json()['materials_ready']


def test_linked_issue_consumes_only_own_reservation_and_retry_is_safe(stock_manager,order_payload):
    mid=item(stock_manager)
    saved,_=create(stock_manager,order_payload,mid)
    other,_=create(stock_manager,order_payload,mid)
    payload={'kind':'issue','quantity':'20','order_part_id':saved['parts'][0]['id'],'request_id':uuid4().hex}
    for _ in range(2):
        assert stock_manager.post(f'{BASE}/materials/{mid}/movements',json=payload).status_code==201
    state=stock(stock_manager,mid)
    assert amount(state['quantity'])==80 and amount(state['reserved_quantity'])==80
    first=stock_manager.get(f"/orders/{saved['id']}").json()['parts'][0]
    second=stock_manager.get(f"/orders/{other['id']}").json()['parts'][0]
    assert amount(first['reserved_quantity'])==40 and amount(first['remaining_quantity'])==40
    assert amount(second['reserved_quantity'])==40 and amount(second['shortage_quantity'])==20


def test_edit_and_delete_release_reservations_to_waiting_orders(stock_manager,order_payload):
    mid=item(stock_manager)
    first,_=create(stock_manager,order_payload,mid)
    second,_=create(stock_manager,order_payload,mid)
    payload=edit(first)
    payload['parts'][0]['planned_quantity']='30'
    assert stock_manager.put(f"/orders/{first['id']}",json=payload).status_code==200
    assert stock_manager.get(f"/orders/{second['id']}").json()['materials_ready']
    assert amount(stock(stock_manager,mid)['available_quantity'])==10
    assert stock_manager.delete(f"/orders/{first['id']}").status_code==200
    assert amount(stock(stock_manager,mid)['quantity'])==100
    assert amount(stock(stock_manager,mid)['available_quantity'])==40


def test_earlier_order_edit_does_not_steal_later_order_reservation(stock_manager,order_payload):
    mid=item(stock_manager)
    first,_=create(stock_manager,order_payload,mid,'20')
    second,_=create(stock_manager,order_payload,mid,'80')
    payload=edit(first)
    payload['parts'][0]['planned_quantity']='40'
    assert stock_manager.put(f"/orders/{first['id']}",json=payload).status_code==200
    after=stock_manager.get(f"/orders/{second['id']}").json()
    assert after['materials_ready'] and amount(after['parts'][0]['reserved_quantity'])==80
    assert amount(stock_manager.get(f"/orders/{first['id']}").json()['parts'][0]['shortage_quantity'])==20


def test_order_without_catalog_material_cannot_skip_stock_check(stock_manager,order_payload):
    payload=deepcopy(order_payload)
    payload['parts'][0].pop('warehouse_material_id')
    payload['parts'][0].pop('planned_quantity')
    response=stock_manager.post('/orders/',json=payload)
    assert response.status_code==201
    oid=response.json()['order_id']
    assert not response.json()['materials_ready']
    assert stock_manager.put(f'/orders/{oid}/status?status=IN_PROGRESS').status_code==400


def test_edit_to_unavailable_material_returns_order_to_queue(stock_manager,order_payload):
    full=item(stock_manager)
    empty=item(stock_manager,'0',name='Brakująca stal')
    saved,_=create(stock_manager,order_payload,full)
    assert stock_manager.put(f"/orders/{saved['id']}/status?status=IN_PROGRESS").status_code==200
    payload=edit(saved)
    payload['parts'][0]['warehouse_material_id']=empty
    response=stock_manager.put(f"/orders/{saved['id']}",json=payload)
    assert response.status_code==200 and not response.json()['materials_ready']
    assert stock_manager.get(f"/orders/{saved['id']}").json()['status']=='IN_QUEUE'
    assert amount(stock(stock_manager,full)['available_quantity'])==100


def test_multiple_parts_share_one_material_and_cannot_double_reserve(stock_manager,order_payload):
    mid=item(stock_manager,'10')
    payload=deepcopy(order_payload)
    part=payload['parts'][0]
    part.update(warehouse_material_id=mid,planned_quantity='7')
    payload['parts'].append(deepcopy(part))
    payload['parts'][1].update(client_key='part-2',part_name='Druga część')
    response=stock_manager.post('/orders/',json=payload)
    saved=stock_manager.get(f"/orders/{response.json()['order_id']}").json()
    assert [amount(p['reserved_quantity']) for p in saved['parts']]==[7,3]
    assert amount(saved['parts'][1]['shortage_quantity'])==4
    assert not saved['materials_ready']


def test_manual_flag_cannot_bypass_warehouse_shortage(stock_manager,order_payload):
    mid=item(stock_manager,'0')
    saved,_=create(stock_manager,order_payload,mid,'10')
    assert stock_manager.put(f"/parts/{saved['parts'][0]['id']}/material-missing?missing=false").status_code==200
    assert stock_manager.put(f"/orders/{saved['id']}/status?status=IN_PROGRESS").status_code==400
