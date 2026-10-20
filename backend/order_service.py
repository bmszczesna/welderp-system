"""Transactional writes shared by the order and production endpoints."""
from datetime import datetime

from fastapi import HTTPException
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import selectinload

from backend import models
from backend.models import OrderStatus


def order_query(db):
    return db.query(models.Order).options(
        selectinload(models.Order.parts).selectinload(models.OrderPart.steps),
        selectinload(models.Order.parts).selectinload(models.OrderPart.pdfs),
    )


def get_order(db, order_id):
    order = order_query(db).filter(models.Order.id == order_id).first()
    if order is None:
        raise HTTPException(status_code=404, detail="Nie znaleziono zlecenia")
    return order


def commit(db):
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=400, detail="Numer zlecenia lub nazwa użytkownika/operatora już istnieje") from exc
    except Exception:
        db.rollback()
        raise


def editable(order):
    if order.status in (OrderStatus.COMPLETED, OrderStatus.ARCHIVED):
        raise HTTPException(status_code=400, detail="Zakończone i zarchiwizowane zlecenia są tylko do odczytu")


def change_status(db, order, target, reason):
    if order.status == target:
        return
    now = datetime.utcnow()
    db.add(models.OrderStatusEvent(order=order, from_status=order.status,
                                  to_status=target, occurred_at=now, reason=reason))
    order.status = target
    order.status_changed_at = now
    if target == OrderStatus.COMPLETED:
        order.completed_at = now
    elif target != OrderStatus.ARCHIVED:
        order.completed_at = None
    if target not in (OrderStatus.QUALITY_CHECK, OrderStatus.COMPLETED, OrderStatus.ARCHIVED):
        order.is_approved = False


def has_missing_material(order):
    return any(part.material_missing for part in order.parts)


def ensure_material_available(order):
    if has_missing_material(order):
        raise HTTPException(status_code=400, detail="Najpierw uzupełnij brakujący materiał przy wszystkich częściach")


def duplicate_number(db, number, exclude=None):
    query = db.query(models.Order).filter(models.Order.order_number == number)
    if exclude is not None:
        query = query.filter(models.Order.id != exclude)
    if query.first() is not None:
        raise HTTPException(status_code=400, detail="Numer zlecenia już istnieje")


def write_step(step, payload, position):
    values = payload.model_dump(exclude_unset=True, exclude={"id"})
    for field, value in values.items():
        setattr(step, field, value)
    step.position = position
    if payload.is_completed:
        step.completed_at = step.completed_at or datetime.utcnow()
    elif "is_completed" in payload.model_fields_set:
        step.completed_at = None
        step.completed_by = None


def created_parts(order, payload):
    return [{"id": part.id, "client_key": data.client_key,
             "steps": [{"id": step.id} for step in part.steps]}
            for part, data in zip(order.parts, payload.parts)]


def validate_step_material(part, step):
    if step.material_quantity is not None and not (step.material_used or part.material):
        raise HTTPException(status_code=422, detail="Podaj materiał przy części lub operacji z podaną ilością")


def create_order(db, payload):
    duplicate_number(db, payload.order_number)
    order = models.Order(order_number=payload.order_number, client_name=payload.client_name,
                         date_received=payload.date_received, date_deadline=payload.date_deadline,
                         operator=payload.operator, status=OrderStatus.IN_QUEUE)
    db.add(order)
    db.add(models.OrderStatusEvent(order=order, from_status=None, to_status=OrderStatus.IN_QUEUE,
                                  reason="Utworzenie zlecenia"))
    for index, data in enumerate(payload.parts):
        part = models.OrderPart(part_name=data.part_name, material=data.material,
                                operator=data.operator, position=index,
                                material_quantity=data.material_quantity, material_unit=data.material_unit)
        order.parts.append(part)
        for step_index, step_data in enumerate(data.steps):
            step = models.ProcessStep()
            write_step(step, step_data, step_index)
            validate_step_material(part, step)
            part.steps.append(step)
    commit(db)
    return order


def update_order(db, order, payload):
    editable(order)
    duplicate_number(db, payload.order_number, exclude=order.id)
    if order.date_received and payload.date_deadline.date() < order.date_received.date():
        raise HTTPException(status_code=422, detail="Termin realizacji nie może być wcześniejszy niż data otrzymania")

    existing_parts = {part.id: part for part in order.parts}
    # Check ownership of every ID before changing any object.
    for data in payload.parts:
        if data.id is not None and data.id not in existing_parts:
            raise HTTPException(status_code=400, detail="Część nie należy do tego zlecenia")
        step_ids = {step.id for step in existing_parts[data.id].steps} if data.id is not None else set()
        if any(step.id is not None and step.id not in step_ids for step in data.steps):
            raise HTTPException(status_code=400, detail="Operacja nie należy do wskazanej części")

    order.order_number = payload.order_number
    order.client_name = payload.client_name
    order.date_deadline = payload.date_deadline
    order.operator = payload.operator
    order.is_approved = False
    removed_paths = []
    updated_parts = []
    for position, data in enumerate(payload.parts):
        part = existing_parts.get(data.id)
        if part is None:
            part = models.OrderPart()
        part.part_name, part.material, part.operator, part.position = (
            data.part_name, data.material, data.operator, position)
        for field in ("material_quantity", "material_unit"):
            if field in data.model_fields_set:
                setattr(part, field, getattr(data, field))
        if part.material_quantity is not None and not part.material:
            raise HTTPException(status_code=422, detail="Podaj materiał części z zapisaną ilością")
        existing_steps = {step.id: step for step in part.steps}
        updated_steps = []
        for step_position, step_data in enumerate(data.steps):
            step = existing_steps.get(step_data.id) or models.ProcessStep()
            write_step(step, step_data, step_position)
            validate_step_material(part, step)
            updated_steps.append(step)
        part.steps = updated_steps
        updated_parts.append(part)
    kept_ids = {part.id for part in updated_parts if part.id is not None}
    for old_part in order.parts:
        if old_part.id not in kept_ids:
            removed_paths.extend(pdf.file_path for pdf in old_part.pdfs)
    order.parts = updated_parts
    if has_missing_material(order):
        change_status(db, order, OrderStatus.IN_QUEUE, "Edycja zlecenia z brakującym materiałem")
    commit(db)
    return removed_paths
