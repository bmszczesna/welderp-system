from datetime import date
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend import auth, database, models, warehouse as service
from backend import warehouse_forecast as forecast

router = APIRouter(prefix="/api/warehouse", tags=["Magazyn"], dependencies=[Depends(auth.require_manager)])


@router.get("/materials", response_model=list[service.MaterialResponse])
def list_materials(db: Session = Depends(database.get_db)):
    return db.scalars(select(models.WarehouseMaterial).order_by(
        models.WarehouseMaterial.name_key, models.WarehouseMaterial.unit)).all()


@router.post("/materials", response_model=service.MaterialResponse, status_code=201)
def create_material(payload: service.MaterialCreate, db: Session = Depends(database.get_db)):
    return service.create_material(db, payload)


@router.patch("/materials/{material_id}/minimum", response_model=service.MaterialResponse)
def update_minimum(material_id: int, payload: service.MinimumUpdate, db: Session = Depends(database.get_db)):
    material = service.get_material(db, material_id)
    material.minimum_quantity = payload.minimum_quantity
    db.commit()
    return material


@router.post("/materials/{material_id}/movements", response_model=service.MovementResponse, status_code=201)
def create_movement(material_id: int, payload: service.MovementCreate, db: Session = Depends(database.get_db)):
    return service.record_movement(db, material_id, payload)


@router.get("/materials/{material_id}/movements", response_model=list[service.MovementResponse])
def list_movements(material_id: int, limit: int = Query(default=100, ge=1, le=200),
                   db: Session = Depends(database.get_db)):
    service.get_material(db, material_id)
    return db.scalars(select(models.WarehouseMovement).where(
        models.WarehouseMovement.material_id == material_id).order_by(
            models.WarehouseMovement.id.desc()).limit(limit)).all()


@router.get("/forecast", response_model=forecast.ForecastResponse)
def get_forecast(as_of: date | None = None, days: int = Query(default=7, ge=1, le=90),
                 db: Session = Depends(database.get_db)):
    return forecast.calculate_forecast(db, as_of or date.today(), days)


@router.get("/demands", response_model=forecast.DemandList)
def list_demands(db: Session = Depends(database.get_db)):
    return {"items": forecast.active_demands(db)[0]}


@router.get("/deliveries", response_model=list[forecast.DeliveryResponse])
def list_deliveries(db: Session = Depends(database.get_db)):
    return db.scalars(select(models.WarehouseDelivery).order_by(models.WarehouseDelivery.id.desc())).all()


@router.post("/deliveries", response_model=forecast.DeliveryResponse, status_code=201)
def create_delivery(payload: forecast.DeliveryCreate, db: Session = Depends(database.get_db)):
    service.get_material(db, payload.material_id)
    delivery = models.WarehouseDelivery(**payload.model_dump(), status="ordered")
    db.add(delivery)
    db.commit()
    db.refresh(delivery)
    return delivery


@router.post("/deliveries/{delivery_id}/receive", response_model=forecast.DeliveryResponse)
def receive_delivery(delivery_id: int, db: Session = Depends(database.get_db)):
    delivery = db.scalar(select(models.WarehouseDelivery).where(
        models.WarehouseDelivery.id == delivery_id).with_for_update())
    if delivery is None:
        raise HTTPException(status_code=404, detail="Nie znaleziono dostawy")
    if delivery.status == "cancelled":
        raise HTTPException(status_code=409, detail="Ta dostawa została anulowana")
    if delivery.status == "received":
        return delivery
    service.record_movement(db, delivery.material_id, service.MovementCreate(kind="receipt",
        quantity=delivery.quantity, request_id=f"delivery:{delivery.id}",
        note=f"Dostawa {delivery.id}: {delivery.note}"[:500]), commit=False)
    delivery.status = "received"
    db.commit()
    db.refresh(delivery)
    return delivery


@router.post("/deliveries/{delivery_id}/cancel", response_model=forecast.DeliveryResponse)
def cancel_delivery(delivery_id: int, db: Session = Depends(database.get_db)):
    delivery = db.scalar(select(models.WarehouseDelivery).where(
        models.WarehouseDelivery.id == delivery_id).with_for_update())
    if delivery is None:
        raise HTTPException(status_code=404, detail="Nie znaleziono dostawy")
    if delivery.status == "received":
        raise HTTPException(status_code=409, detail="Nie można anulować już przyjętej dostawy")
    delivery.status = "cancelled"
    db.commit()
    return delivery
