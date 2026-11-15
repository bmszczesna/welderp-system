"""Small warehouse ledger: explicit receipts/issues, exact amounts, no negative stock."""
from datetime import datetime
from decimal import Decimal
from typing import Annotated, Literal
import unicodedata
from uuid import uuid4

from fastapi import HTTPException
from pydantic import Field, field_validator
from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError

from backend import models
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


class MaterialResponse(ORMResponse):
    id: int
    name: str
    unit: str
    quantity: Decimal
    minimum_quantity: Decimal
    is_low_stock: bool


class MovementResponse(ORMResponse):
    id: int
    material_id: int
    kind: str
    quantity: Decimal
    balance_after: Decimal
    note: str
    created_at: datetime


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
    if (movement.material_id, movement.kind, movement.quantity, movement.note) != (
            material_id, payload.kind, delta, payload.note):
        raise HTTPException(status_code=409, detail="To zgłoszenie zostało już zapisane z innymi danymi. Odśwież magazyn.")
    return movement


def record_movement(db, material_id, payload):
    get_material(db, material_id)
    previous = existing_movement(db, material_id, payload)
    if previous is not None:
        return previous
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
                                            kind=payload.kind, quantity=delta, balance_after=balance, note=payload.note)
        db.add(movement)
        db.commit()
        db.refresh(movement)
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
