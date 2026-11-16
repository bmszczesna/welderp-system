from copy import deepcopy
from datetime import date, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, text

from backend import models
from backend.migrations import initialize_database

BASE = '/api/warehouse'
DAY = date(2027, 1, 10)


def number(value):
    return Decimal(str(value))


@pytest.fixture
def manager(client):
    assert client.post('/register/', json={'username': 'chief', 'password': 'chief-test', 'full_name': 'Kierownik'}).status_code == 201
    assert client.post('/login/', json={'username': 'chief', 'password': 'chief-test'}).status_code == 200
    assert client.get('/auth/me').json()['role'] == 'manager'
    return client


def material(client, name='S235', unit='kg', quantity='100', minimum='20'):
    response = client.post(BASE+'/materials', json={'name': name, 'unit': unit,
        'opening_quantity': quantity, 'minimum_quantity': minimum})
    assert response.status_code == 201, response.text
    return response.json()['id']


def order(client, payload, item_id=None, quantity=None, due=1, planned_for=None):
    data = deepcopy(payload)
    data.update(order_number='PLAN/'+uuid4().hex[:10], date_received=(DAY-timedelta(days=10)).isoformat()+'T00:00:00',
                date_deadline=(DAY+timedelta(days=due)).isoformat()+'T00:00:00')
    for key in ('warehouse_material_id','planned_quantity','planned_for'):
        data['parts'][0].pop(key, None)
    if item_id is not None:
        data['parts'][0].update(warehouse_material_id=item_id, planned_quantity=quantity,
                                planned_for=planned_for.isoformat() if planned_for else None)
    response = client.post('/orders/', json=data)
    assert response.status_code == 201, response.text
    result = client.get(f"/orders/{response.json()['order_id']}").json()
    return result


def forecast(client, item_id, days=7):
    response = client.get(BASE+'/forecast', params={'as_of': DAY.isoformat(), 'days': days})
    assert response.status_code == 200, response.text
    return next(item for item in response.json()['items'] if item['material_id'] == item_id)


def delivery(client, item_id, quantity, day):
    response = client.post(BASE+'/deliveries', json={'material_id': item_id, 'quantity': quantity,
        'expected_on': (DAY+timedelta(days=day)).isoformat(), 'note': 'Dostawa testowa'})
    assert response.status_code == 201, response.text
    return response.json()['id']


def edit_payload(saved):
    return {'order_number': saved['order_number'], 'client_name': saved['client_name'],
        'date_deadline': saved['date_deadline'], 'parts': [
            {key: value for key, value in part.items() if key in
             ('id','part_name','material','warehouse_material_id','planned_quantity','planned_for')} |
            {'steps': [{'id': step['id'], 'step_name': step['step_name']} for step in part['steps']]}
            for part in saved['parts']]}


def test_forecast_tomorrow_shortage_and_safety_stock(manager, order_payload):
    item_id = material(manager)
    for quantity, due in [('40',1), ('50',2), ('30',4)]:
        order(manager, order_payload, item_id, quantity, due)
    result = forecast(manager, item_id)
    assert number(result['tomorrow_quantity']) == 60
    assert result['depletion_on'] == (DAY+timedelta(days=4)).isoformat()
    assert result['shortage_on'] == result['depletion_on']
    assert number(result['suggested_order_quantity']) == 40
    delivery(manager, item_id, '10', 3)
    result = forecast(manager, item_id)
    assert number(result['suggested_order_quantity']) == 30
    assert number(result['days'][-1]['projected_quantity']) == -10


def test_issue_to_part_is_not_counted_twice(manager, order_payload):
    item_id = material(manager)
    saved = order(manager, order_payload, item_id, '40', 1)
    part_id = saved['parts'][0]['id']
    before = forecast(manager, item_id)
    response = manager.post(f'{BASE}/materials/{item_id}/movements', json={
        'kind':'issue','quantity':'20','order_part_id':part_id})
    assert response.status_code == 201, response.text
    after = forecast(manager, item_id)
    assert number(after['current_quantity']) == 80
    assert number(before['tomorrow_quantity']) == number(after['tomorrow_quantity']) == 60
    assert number(after['days'][1]['demand_quantity']) == 20
    assert response.json()['order_number'] == saved['order_number']
    assert manager.post(f'{BASE}/materials/{item_id}/movements', json={
        'kind':'issue','quantity':'21','order_part_id':part_id}).status_code == 409
    assert manager.post(f'{BASE}/materials/{item_id}/movements', json={
        'kind':'receipt','quantity':'1','order_part_id':part_id}).status_code == 422


def test_delivery_today_can_cover_the_minimum_stock(manager):
    item_id = material(manager, quantity='0', minimum='20')
    delivery(manager, item_id, '50', 0)
    result = forecast(manager, item_id)
    assert number(result['tomorrow_quantity']) == 50
    assert number(result['suggested_order_quantity']) == 0
    assert result['depletion_on'] is None


def test_receiving_delivery_moves_real_stock_once_and_removes_future_supply(manager, order_payload):
    item_id = material(manager)
    order(manager, order_payload, item_id, '120', 1)
    did = delivery(manager, item_id, '30', 1)
    before = forecast(manager, item_id)
    assert number(before['current_quantity']) == 100 and number(before['tomorrow_quantity']) == 10
    for _ in range(2):
        assert manager.post(f'{BASE}/deliveries/{did}/receive').status_code == 200
    after = forecast(manager, item_id)
    assert number(after['current_quantity']) == 130 and number(after['tomorrow_quantity']) == 10
    assert all(number(day['receipt_quantity']) == 0 for day in after['days'])
    assert len(manager.get(f'{BASE}/materials/{item_id}/movements').json()) == 2
    assert manager.post(f'{BASE}/deliveries/{did}/cancel').status_code == 409


def test_cancelled_and_overdue_deliveries_do_not_cover_demand(manager, order_payload):
    item_id = material(manager)
    order(manager, order_payload, item_id, '120', 1)
    cancelled = delivery(manager, item_id, '50', 1)
    assert manager.post(f'{BASE}/deliveries/{cancelled}/cancel').status_code == 200
    assert manager.post(f'{BASE}/deliveries/{cancelled}/receive').status_code == 409
    delivery(manager, item_id, '100', -1)
    result = forecast(manager, item_id)
    assert number(result['tomorrow_quantity']) == -20
    data = manager.get(BASE+'/forecast', params={'as_of': DAY.isoformat()}).json()
    assert data['overdue_deliveries'] == 1


def test_terminal_orders_excluded_and_unplanned_reported(manager, order_payload):
    item_id = material(manager)
    saved = order(manager, order_payload, item_id, '80', 1)
    for status in ('QUALITY_CHECK','COMPLETED','ARCHIVED'):
        if status == 'COMPLETED':
            assert manager.put(f"/orders/{saved['id']}/approve").status_code == 200
        assert manager.put(f"/orders/{saved['id']}/status?status={status}").status_code == 200
    unplanned = order(manager, order_payload)
    assert number(forecast(manager, item_id)['tomorrow_quantity']) == 100
    data = manager.get(BASE+'/forecast', params={'as_of':DAY.isoformat()}).json()
    assert data['unplanned_parts'] == 1


def test_deadline_change_and_explicit_plan_date(manager, order_payload):
    item_id = material(manager)
    saved = order(manager, order_payload, item_id, '30', 1)
    payload = edit_payload(saved)
    payload['date_deadline'] = (DAY+timedelta(days=5)).isoformat()+'T00:00:00'
    assert manager.put(f"/orders/{saved['id']}",json=payload).status_code == 200
    assert number(forecast(manager,item_id)['tomorrow_quantity']) == 100
    payload['parts'][0]['planned_for'] = (DAY+timedelta(days=1)).isoformat()
    assert manager.put(f"/orders/{saved['id']}",json=payload).status_code == 200
    assert number(forecast(manager,item_id)['tomorrow_quantity']) == 70


def test_plan_changes_cannot_discard_already_issued_quantity(manager, order_payload):
    item_id = material(manager)
    saved = order(manager, order_payload, item_id, '30', 1)
    assert manager.post(f'{BASE}/materials/{item_id}/movements',json={
        'kind':'issue','quantity':'20','order_part_id':saved['parts'][0]['id']}).status_code == 201
    payload = edit_payload(saved)
    payload['parts'][0]['planned_quantity'] = '19'
    assert manager.put(f"/orders/{saved['id']}",json=payload).status_code == 409
    payload['parts'][0].update(planned_quantity=None,warehouse_material_id=None)
    assert manager.put(f"/orders/{saved['id']}",json=payload).status_code == 409
    assert number(manager.get(f"/orders/{saved['id']}").json()['parts'][0]['planned_quantity']) == 30


def test_deleted_order_keeps_stock_and_movement_snapshot(manager, order_payload):
    item_id = material(manager)
    saved = order(manager,order_payload,item_id,'30',1)
    assert manager.post(f'{BASE}/materials/{item_id}/movements',json={
        'kind':'issue','quantity':'10','order_part_id':saved['parts'][0]['id']}).status_code == 201
    assert manager.delete(f"/orders/{saved['id']}").status_code == 200
    history = manager.get(f'{BASE}/materials/{item_id}/movements').json()
    assert history[0]['order_part_id'] is None and history[0]['order_number'] == saved['order_number']
    assert number(forecast(manager,item_id)['tomorrow_quantity']) == 90


def test_distinct_units_are_never_combined(manager, order_payload):
    kg = material(manager)
    metres = material(manager,unit='m',quantity='50')
    order(manager,order_payload,kg,'30')
    order(manager,order_payload,metres,'20')
    assert number(forecast(manager,kg)['tomorrow_quantity']) == 70
    assert number(forecast(manager,metres)['tomorrow_quantity']) == 30


def test_warehouse_requires_real_manager_session(client):
    assert client.get(BASE+'/materials').status_code == 401
    client.cookies.set('logged_in','true')
    assert client.get(BASE+'/materials').status_code == 401
    assert client.post('/register/',json={'username':'first','password':'pw','full_name':'Manager','role':'manager'}).status_code == 422
    assert client.post('/register/',json={'username':'first','password':'pw','full_name':'Manager'}).status_code == 201
    assert client.post('/register/',json={'username':'second','password':'pw','full_name':'Operator'}).status_code == 201
    assert client.post('/login/',json={'username':'second','password':'pw'}).status_code == 200
    assert client.get('/auth/me').json()['role'] == 'operator'
    client.cookies.set('role','manager')
    for route in ('materials','forecast','deliveries','demands'):
        assert client.get(BASE+'/'+route).status_code == 403
    assert client.get('/metadata/material-catalog').status_code == 200


def test_logout_and_expiration_revoke_manager_session(manager, db_engine):
    assert manager.get(BASE+'/materials').status_code == 200
    token = manager.cookies.get('welderp_session')
    assert manager.post('/logout/').status_code == 200
    assert manager.get(BASE+'/materials',headers={'Cookie':f'welderp_session={token}; logged_in=true'}).status_code == 401
    manager.post('/login/',json={'username':'chief','password':'chief-test'})
    with db_engine.begin() as connection:
        connection.execute(text("UPDATE user_sessions SET expires_at='2000-01-01 00:00:00'"))
    assert manager.get(BASE+'/materials').status_code == 401


def test_legacy_role_migration_promotes_only_first_owner():
    engine=create_engine('sqlite://')
    models.Base.metadata.create_all(engine)
    with engine.begin() as connection:
        connection.execute(text("INSERT INTO users(id,username,full_name,hashed_password) VALUES (1,'owner','Owner','oldhash'),(2,'worker','Worker','otherhash')"))
        connection.execute(text('ALTER TABLE users DROP COLUMN role'))
    initialize_database(engine)
    initialize_database(engine)
    with engine.connect() as connection:
        assert connection.execute(text('SELECT id,role,hashed_password FROM users ORDER BY id')).all() == [
            (1,'manager','oldhash'),(2,'operator','otherhash')]
    engine.dispose()


def test_operator_can_request_material_but_cannot_manage_stock(manager, order_payload):
    item_id = material(manager)
    manager.post('/register/',json={'username':'worker','password':'pw','full_name':'Operator'})
    manager.post('/login/',json={'username':'worker','password':'pw'})
    payload=deepcopy(order_payload)
    payload['parts'][0].update(warehouse_material_id=item_id,planned_quantity='10')
    assert manager.post('/orders/',json=payload).status_code == 201
    saved=manager.get('/orders/').json()[0]
    assert saved['parts'][0]['reserved_quantity']=='10.000000'
    assert manager.get(BASE+'/materials').status_code == 403
    catalog=manager.get('/metadata/material-catalog').json()
    assert set(catalog[0]) == {'id','name','unit','available_quantity'}


@pytest.mark.parametrize('fields',[
    {'warehouse_material_id':1}, {'planned_quantity':'2'},
    {'planned_for':'2027-01-11'}, {'warehouse_material_id':1,'planned_quantity':'-1'},
    {'warehouse_material_id':1,'planned_quantity':True},
])
def test_incomplete_plans_never_create_partial_orders(manager,order_payload,fields):
    material(manager)
    payload=deepcopy(order_payload)
    for key in ('warehouse_material_id','planned_quantity','planned_for'):
        payload['parts'][0].pop(key,None)
    payload['parts'][0].update(fields)
    assert manager.post('/orders/',json=payload).status_code == 422
    assert manager.get('/orders/').json() == []


def test_full_demo_has_roles_and_complete_material_plans(db_engine):
    from sqlalchemy.orm import Session
    from sqlalchemy import select
    from backend.seed_demo import seed_demo,seed_warehouse_demo
    initialize_database(db_engine)
    seed_demo(db_engine,reference_date=DAY)
    first=seed_warehouse_demo(db_engine,reference_date=DAY)
    second=seed_warehouse_demo(db_engine,reference_date=DAY)
    assert first['new_part_plans']>0 and second['new_part_plans']==0
    with Session(db_engine) as db:
        users={u.username:u.role for u in db.scalars(select(models.User))}
        assert users['kierownik_demo']=='manager' and users['operator_demo']=='operator'
        assert len(db.scalars(select(models.WarehouseMaterial)).all())==7
        assert len(db.scalars(select(models.WarehouseDelivery)).all())==1
        from backend.warehouse_forecast import calculate_forecast
        result=calculate_forecast(db,DAY,7)
        assert result.unplanned_parts==0


def selected_forecast(client, item_id, offset):
    response=client.get(BASE+'/forecast',params={'as_of':DAY.isoformat(),
        'target_date':(DAY+timedelta(days=offset)).isoformat()})
    assert response.status_code==200,response.text
    data=response.json()
    return data,next(item for item in data['items'] if item['material_id']==item_id)


def test_selected_date_includes_all_events_until_that_day(manager,order_payload):
    item_id=material(manager)
    for quantity,due in [('40',1),('50',2),('30',4),('300',5)]:
        order(manager,order_payload,item_id,quantity,due)
    delivery(manager,item_id,'10',3)
    delivery(manager,item_id,'1000',5)
    data,item=selected_forecast(manager,item_id,4)
    assert data['as_of']==DAY.isoformat()
    assert data['target_date']==(DAY+timedelta(days=4)).isoformat()
    assert data['horizon_days']==4 and len(item['days'])==5
    assert number(item['target_quantity'])==-10
    assert number(item['suggested_order_quantity'])==30
    assert item['depletion_on']==data['target_date']
    assert number(item['days'][-1]['projected_quantity'])==-10


def test_delivery_before_selected_date_is_not_treated_as_overdue(manager,order_payload):
    item_id=material(manager)
    order(manager,order_payload,item_id,'60',1)
    delivery(manager,item_id,'40',2)
    data,item=selected_forecast(manager,item_id,3)
    assert data['overdue_deliveries']==0
    assert number(item['target_quantity'])==80
    assert number(item['days'][2]['receipt_quantity'])==40


def test_today_selection_does_not_include_tomorrows_shortage(manager,order_payload):
    item_id=material(manager)
    order(manager,order_payload,item_id,'50',0)
    order(manager,order_payload,item_id,'80',1)
    delivery(manager,item_id,'20',0)
    data,item=selected_forecast(manager,item_id,0)
    assert data['horizon_days']==0 and data['target_date']==DAY.isoformat()
    assert len(item['days'])==1
    assert number(item['target_quantity'])==70
    assert number(item['tomorrow_quantity'])==-10
    assert item['depletion_on'] is None and item['shortage_on'] is None
    assert number(item['suggested_order_quantity'])==0


def test_purchase_suggestion_covers_shortage_before_selected_date(manager,order_payload):
    item_id=material(manager,quantity='30')
    order(manager,order_payload,item_id,'50',1)
    delivery(manager,item_id,'100',3)
    _,item=selected_forecast(manager,item_id,3)
    assert number(item['target_quantity'])==80
    assert number(item['suggested_order_quantity'])==40
    assert item['shortage_on']==(DAY+timedelta(days=1)).isoformat()


@pytest.mark.parametrize('offset',[-1,366])
def test_selected_date_outside_supported_range_rejected(manager,offset):
    response=manager.get(BASE+'/forecast',params={'as_of':DAY.isoformat(),
        'target_date':(DAY+timedelta(days=offset)).isoformat()})
    assert response.status_code==422
    assert '365 dni' in response.json()['detail']


def test_forecast_supports_a_date_one_year_ahead(manager):
    item_id=material(manager)
    before=manager.get(BASE+'/materials').json()
    data,item=selected_forecast(manager,item_id,365)
    assert data['horizon_days']==365 and len(item['days'])==366
    assert number(item['target_quantity'])==100
    assert manager.get(BASE+'/materials').json()==before
