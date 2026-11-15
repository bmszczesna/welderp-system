from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend import database, models, warehouse as service

router = APIRouter(prefix="/api/warehouse", tags=["Magazyn"])


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
