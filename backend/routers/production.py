from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from backend import database, models, order_service as service
from backend.models import OrderStatus
from backend.schemas import PartMaterialUsage, StepCompletion, StepProductionRecord

router = APIRouter()


def get_step(db, step_id):
    step = db.get(models.ProcessStep, step_id)
    if step is None:
        raise HTTPException(status_code=404, detail="Nie znaleziono operacji")
    return step


def get_active_step(db, step_id):
    step = get_step(db, step_id)
    order = step.owner_part.owner
    service.ensure_material_available(order)
    if order.status != OrderStatus.IN_PROGRESS:
        raise HTTPException(status_code=400, detail="Wykonanie operacji zapisuje się podczas realizacji zlecenia")
    return step


def set_completion(db, step, completed):
    order = step.owner_part.owner
    service.editable(order)
    if completed:
        service.ensure_material_available(order)
        if order.status != OrderStatus.IN_PROGRESS:
            raise HTTPException(status_code=400, detail="Wykonanie operacji zapisuje się podczas realizacji zlecenia")
    if step.is_completed == completed:
        return
    step.is_completed = completed
    order.is_approved = False
    if completed:
        step.completed_at = datetime.utcnow()
        step.completed_by = step.operator or step.owner_part.operator or order.operator
    else:
        # Correcting completion does not erase material consumption or production notes.
        step.completed_at = None
        step.completed_by = None
        if order.status == OrderStatus.QUALITY_CHECK:
            target = OrderStatus.IN_QUEUE if service.has_missing_material(order) else OrderStatus.IN_PROGRESS
            service.change_status(db, order, target, f"Cofnięcie wykonania operacji {step.id}: {step.step_name}")
            order.needs_correction = True


def completion_response(step):
    order = step.owner_part.owner
    return {"is_completed": step.is_completed, "completed_at": step.completed_at,
            "completed_by": step.completed_by, "order_status": order.status.value,
            "is_approved": order.is_approved}


@router.put("/parts/{part_id}/material-usage", tags=["Dane produkcyjne"])
def part_material_usage(part_id: int, usage: PartMaterialUsage, db: Session = Depends(database.get_db)):
    part = db.get(models.OrderPart, part_id)
    if part is None:
        raise HTTPException(status_code=404, detail="Nie znaleziono części")
    order = part.owner
    service.editable(order)
    service.ensure_material_available(order)
    if order.status != OrderStatus.IN_PROGRESS:
        raise HTTPException(status_code=400, detail="Zużycie materiału zapisuje się podczas realizacji zlecenia")
    if usage.material_quantity is not None and not part.material:
        raise HTTPException(status_code=422, detail="Podaj materiał części przed zapisaniem zużycia")
    part.material_quantity, part.material_unit = usage.material_quantity, usage.material_unit
    service.commit(db)
    return {"message": "Zużycie materiału zapisane", "part_id": part.id,
            "material_quantity": part.material_quantity, "material_unit": part.material_unit}


@router.put("/steps/{step_id}/completion", tags=["Dane produkcyjne"])
def update_completion(step_id: int, payload: StepCompletion, db: Session = Depends(database.get_db)):
    step = get_step(db, step_id)
    set_completion(db, step, payload.is_completed)
    service.commit(db)
    return completion_response(step)


@router.put("/steps/{step_id}/production-record", tags=["Dane produkcyjne"])
def production_record(step_id: int, record: StepProductionRecord, db: Session = Depends(database.get_db)):
    step = get_active_step(db, step_id)
    values = record.model_dump()
    if record.material_quantity is not None and not (record.material_used or step.owner_part.material):
        raise HTTPException(status_code=422, detail="Podaj materiał przy części lub we wpisie produkcyjnym")
    for field, value in values.items():
        setattr(step, field, value)
    if step.is_completed:
        step.completed_at = step.completed_at or datetime.utcnow()
        step.completed_by = step.completed_by or step.operator or step.owner_part.operator or step.owner_part.owner.operator
    else:
        step.completed_at = None
        step.completed_by = None
    service.commit(db)
    return {"id": step.id, **values, "completed_at": step.completed_at, "completed_by": step.completed_by}


@router.put("/steps/{step_id}/toggle", tags=["Dane produkcyjne"])
def toggle_step(step_id: int, db: Session = Depends(database.get_db)):
    step = get_step(db, step_id)
    set_completion(db, step, not step.is_completed)
    service.commit(db)
    return completion_response(step)


@router.get("/operators/", tags=["Operatorzy"])
def get_operators(db: Session = Depends(database.get_db)):
    return db.query(models.Operator).order_by(models.Operator.id).all()


@router.post("/operators/", tags=["Operatorzy"])
def add_operator(name: str = Query(min_length=1, max_length=255), db: Session = Depends(database.get_db)):
    name = name.strip()
    if not name:
        raise HTTPException(status_code=422, detail="Podaj nazwę operatora")
    operator = models.Operator(name=name)
    db.add(operator)
    service.commit(db)
    return {"id": operator.id, "name": operator.name}


@router.delete("/operators/{op_id}", tags=["Operatorzy"])
def delete_operator(op_id: int, db: Session = Depends(database.get_db)):
    operator = db.get(models.Operator, op_id)
    if operator is None:
        raise HTTPException(status_code=404, detail="Nie znaleziono operatora")
    for order in service.order_query(db).filter(
            models.Order.status.notin_([OrderStatus.COMPLETED, OrderStatus.ARCHIVED])).all():
        names = [order.operator] + [part.operator for part in order.parts]
        names += [step.operator for part in order.parts for step in part.steps]
        if operator.name in names:
            raise HTTPException(status_code=400, detail="Operator jest przypisany do trwającego zlecenia")
    db.delete(operator)
    service.commit(db)
    return {"message": "Operator usunięty"}
