"""Small warehouse ledger: explicit receipts/issues, exact amounts, no negative stock."""
from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Literal
import unicodedata
from uuid import uuid4

from fastapi import HTTPException
from pydantic import Field, field_validator
from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError

from backend import auth, models, reservations
from backend.schemas import InputModel, ORMResponse, normalize_unit

MAX_QUANTITY = Decimal("999999999.999999")
Amount = Annotated[Decimal, Field(ge=0, le=MAX_QUANTITY, max_digits=15, decimal_places=6)]
PositiveAmount = Annotated[Decimal, Field(gt=0, le=MAX_QUANTITY, max_digits=15, decimal_places=6)]


class WarehouseInput(InputModel):
    @field_validator("quantity", "opening_quantity", "minimum_quantity", mode="before", check_fields=False)
    @classmethod
    def decimal_amount(cls, value):
        if isinstance(value, bool):
            raise ValueError("Ilość musi być liczbą")
        return value.strip().replace(",", ".") if isinstance(value, str) else value


class MaterialCreate(WarehouseInput):
    name: Annotated[str, Field(min_length=1, max_length=150)]
    unit: str
    opening_quantity: Amount = Decimal("0")
    minimum_quantity: Amount = Decimal("0")

    @field_validator("name", mode="before")
    @classmethod
    def clean_name(cls, value):
        return " ".join(unicodedata.normalize("NFKC", value).split()) if isinstance(value, str) else value

    @field_validator("unit", mode="before")
    @classmethod
    def clean_unit(cls, value):
        unit = normalize_unit(value)
        if unit is None:
            raise ValueError("Wybierz jednostkę materiału")
        return unit


class MinimumUpdate(WarehouseInput):
    minimum_quantity: Amount


class MovementCreate(WarehouseInput):
    kind: Literal["receipt", "issue"]
    quantity: PositiveAmount
    note: Annotated[str, Field(max_length=500)] = ""
    request_id: Annotated[str, Field(min_length=1, max_length=100)] = Field(default_factory=lambda: str(uuid4()))
    order_part_id: Annotated[int, Field(gt=0)] | None = None


class MaterialResponse(ORMResponse):
    id: int
    name: str
    unit: str
    quantity: Decimal
    minimum_quantity: Decimal
    is_low_stock: bool
    reserved_quantity: Decimal
    available_quantity: Decimal


class MovementResponse(ORMResponse):
    id: int
    material_id: int
    kind: str
    quantity: Decimal
    balance_after: Decimal
    note: str
    created_at: datetime
    order_part_id: int | None = None
    order_number: str = ""
    part_name: str = ""


def get_material(db, material_id):
    material = db.get(models.WarehouseMaterial, material_id)
    if material is None:
        raise HTTPException(status_code=404, detail="Nie znaleziono materiału w magazynie")
    return material


def create_material(db, payload):
    key = payload.name.casefold()
    if len(key) > 512:
        raise HTTPException(status_code=422, detail="Nazwa materiału jest zbyt długa")
    material = models.WarehouseMaterial(name=payload.name, name_key=key, unit=payload.unit,
                                        quantity=payload.opening_quantity, minimum_quantity=payload.minimum_quantity)
    db.add(material)
    try:
        db.flush()
        if payload.opening_quantity:
            db.add(models.WarehouseMovement(material_id=material.id, kind="opening",
                                            quantity=payload.opening_quantity, balance_after=payload.opening_quantity,
                                            note="Stan początkowy"))
        db.commit()
        db.refresh(material)
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="Ten materiał z tą jednostką już istnieje. Użyj przyjęcia.") from exc
    except Exception:
        db.rollback()
        raise
    return material


def existing_movement(db, material_id, payload):
    movement = db.scalar(select(models.WarehouseMovement).where(
        models.WarehouseMovement.request_id == payload.request_id))
    if movement is None:
        return None
    delta = payload.quantity if payload.kind == "receipt" else -payload.quantity
    if (movement.material_id, movement.kind, movement.quantity, movement.note, movement.order_part_id) != (
            material_id, payload.kind, delta, payload.note, payload.order_part_id):
        raise HTTPException(status_code=409, detail="To zgłoszenie zostało już zapisane z innymi danymi. Odśwież magazyn.")
    return movement


def record_movement(db, material_id, payload, commit=True):
    reservations.synchronize(db)
    material = get_material(db, material_id)
    previous = existing_movement(db, material_id, payload)
    if previous is not None:
        return previous
    part = None
    if payload.order_part_id is not None:
        if payload.kind != "issue":
            raise HTTPException(status_code=422, detail="Do części przypisuje się rozchód materiału")
        part = db.scalar(select(models.OrderPart).where(
            models.OrderPart.id == payload.order_part_id).with_for_update().execution_options(populate_existing=True))
        if part is None or part.warehouse_material_id != material_id:
            raise HTTPException(status_code=422, detail="Część nie ma zaplanowanego tego materiału")
        previous = existing_movement(db, material_id, payload)
        if previous is not None:
            return previous
        if part.owner.status in (models.OrderStatus.COMPLETED, models.OrderStatus.ARCHIVED):
            raise HTTPException(status_code=409, detail="Zlecenie jest już zakończone")
        remaining = part.planned_quantity - issued_quantity(db, part.id)
        if payload.quantity > remaining:
            raise HTTPException(status_code=409, detail="Rozchód przekracza pozostałe zapotrzebowanie części")
        if payload.quantity > part.reserved_quantity:
            raise HTTPException(status_code=409, detail='Rozchód przekracza rezerwację tej części. Uzupełnij magazyn.')
        part.reserved_quantity -= payload.quantity
    elif payload.kind == 'issue' and payload.quantity > material.available_quantity:
        raise HTTPException(status_code=409, detail='Materiał jest zarezerwowany dla zleceń. Wybierz właściwe zlecenie lub wydaj tylko wolną ilość.')
    delta = payload.quantity if payload.kind == "receipt" else -payload.quantity
    # The stock check and update form one SQL operation, including concurrent issues.
    statement = update(models.WarehouseMaterial).where(models.WarehouseMaterial.id == material_id)
    if delta < 0:
        statement = statement.where(models.WarehouseMaterial.quantity >= -delta)
    else:
        statement = statement.where(models.WarehouseMaterial.quantity <= MAX_QUANTITY - delta)
    statement = statement.values(quantity=func.round(models.WarehouseMaterial.quantity + delta, 6))
    try:
        balance = db.execute(statement.returning(models.WarehouseMaterial.quantity),
                             execution_options={"synchronize_session": False}).scalar_one_or_none()
        if balance is None:
            db.rollback()
            previous = existing_movement(db, material_id, payload)
            if previous is not None:
                return previous
            detail = "Za mało materiału w magazynie" if delta < 0 else "Przyjęcie przekroczyłoby maksymalny stan magazynu"
            raise HTTPException(status_code=409, detail=detail)
        movement = models.WarehouseMovement(material_id=material_id, request_id=payload.request_id,
                                            kind=payload.kind, quantity=delta, balance_after=balance, note=payload.note,
                                            order_part_id=part.id if part else None,
                                            order_number=part.owner.order_number if part else "",
                                            part_name=part.part_name if part else "")
        db.add(movement)
        if part is not None:
            # A manual issue is the same material usage, not an additional report to enter.
            from backend.material_usage import set_report
            db.flush()
            set_report(part, issued_quantity(db, part.id), material.unit, force=True)
        reservations.synchronize(db)
        if commit:
            db.commit()
            db.refresh(movement)
        else:
            db.flush()
    except IntegrityError:
        # A retry racing the original request must not create a second receipt/issue.
        db.rollback()
        previous = existing_movement(db, material_id, payload)
        if previous is None:
            raise
        return previous
    except Exception:
        db.rollback()
        raise
    return movement


PLAN_FIELDS = ("warehouse_material_id", "planned_quantity", "planned_for")


def issued_quantity(db, part_id):
    if part_id is None:
        return Decimal("0")
    return -db.scalar(select(func.coalesce(func.sum(models.WarehouseMovement.quantity), 0)).where(
        models.WarehouseMovement.order_part_id == part_id,
        models.WarehouseMovement.kind.in_(["issue", "receipt"])))


def apply_part_plan(db, part, data, user):
    fields = set(PLAN_FIELDS) & data.model_fields_set
    if not fields:
        return
    if part.id:
        with db.no_autoflush:
            db.scalar(select(models.OrderPart.id).where(models.OrderPart.id == part.id).with_for_update())
            db.refresh(part, attribute_names=list(PLAN_FIELDS))
    changed = any(getattr(part, field) != getattr(data, field) for field in fields)
    if not changed:
        return
    auth.require_user(user)
    material_id = data.warehouse_material_id
    planned = data.planned_quantity
    if material_id is not None:
        get_material(db, material_id)
    issued = issued_quantity(db, part.id)
    if issued and (material_id != part.warehouse_material_id or planned is None or planned < issued):
        raise HTTPException(status_code=409, detail="Plan nie może usuwać ani zmieniać materiału już wydanego na tę część")
    if material_id != part.warehouse_material_id:
        part.reserved_quantity = Decimal('0')
    for field in PLAN_FIELDS:
        setattr(part, field, getattr(data, field))
    if part.id:
        db.expire(part, ["planned_material"])


def detach_part_history(db, part_id):
    db.execute(update(models.WarehouseMovement).where(
        models.WarehouseMovement.order_part_id == part_id).values(order_part_id=None))
