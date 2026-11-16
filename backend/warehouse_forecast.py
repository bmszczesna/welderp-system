"""Deterministic material projection from unissued order plans and scheduled deliveries."""
from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal

from pydantic import BaseModel, Field
from sqlalchemy import func, select

from backend import models
from backend.warehouse import PositiveAmount, WarehouseInput


class DeliveryCreate(WarehouseInput):
    material_id: int = Field(gt=0)
    quantity: PositiveAmount
    expected_on: date
    note: str = Field(default="", max_length=500)


class DeliveryResponse(BaseModel):
    id: int
    material_id: int
    quantity: Decimal
    expected_on: date
    status: str
    note: str
    model_config = {"from_attributes": True}


class ForecastDay(BaseModel):
    day: date
    demand_quantity: Decimal
    receipt_quantity: Decimal
    projected_quantity: Decimal


class MaterialForecast(BaseModel):
    material_id: int
    name: str
    unit: str
    current_quantity: Decimal
    minimum_quantity: Decimal
    tomorrow_quantity: Decimal
    target_quantity: Decimal
    depletion_on: date | None
    shortage_on: date | None
    suggested_order_quantity: Decimal
    days: list[ForecastDay]


class ForecastResponse(BaseModel):
    as_of: date
    horizon_days: int
    target_date: date
    unplanned_parts: int
    overdue_deliveries: int
    items: list[MaterialForecast]


class DemandResponse(BaseModel):
    part_id: int
    material_id: int
    order_number: str
    part_name: str
    required_quantity: Decimal
    remaining_quantity: Decimal
    reserved_quantity: Decimal
    needed_on: date


class DemandList(BaseModel):
    items: list[DemandResponse]


def active_demands(db):
    rows = db.execute(select(models.OrderPart, models.Order).join(models.Order,
        models.Order.id == models.OrderPart.order_id).where(models.Order.status.notin_(
            [models.OrderStatus.COMPLETED, models.OrderStatus.ARCHIVED]))).all()
    issued = dict(db.execute(select(models.WarehouseMovement.order_part_id,
        (-func.sum(models.WarehouseMovement.quantity)).label('issued_quantity')).where(
            models.WarehouseMovement.order_part_id.is_not(None),
            models.WarehouseMovement.kind == "issue").group_by(models.WarehouseMovement.order_part_id)).all())
    demands, unplanned = [], 0
    for part, order in rows:
        due = part.planned_for or (order.date_deadline.date() if order.date_deadline else None)
        if not part.warehouse_material_id or part.planned_quantity is None or due is None:
            unplanned += 1
            continue
        remaining = max(Decimal("0"), part.planned_quantity - issued.get(part.id, Decimal("0")))
        if remaining:
            demands.append({"part_id": part.id, "material_id": part.warehouse_material_id,
                "order_number": order.order_number, "part_name": part.part_name,
                "required_quantity": part.planned_quantity, "remaining_quantity": remaining,
                "reserved_quantity": part.reserved_quantity, "needed_on": due})
    return demands, unplanned


def calculate_forecast(db, as_of, horizon_days):
    # PostgreSQL share locks keep stock aligned with issue/delivery ledger reads.
    materials = db.scalars(select(models.WarehouseMaterial).order_by(models.WarehouseMaterial.id)
                          .with_for_update(read=True)).all()
    materials.sort(key=lambda item: (item.name_key, item.unit))
    demands, unplanned = active_demands(db)
    need = defaultdict(lambda: Decimal("0"))
    incoming = defaultdict(lambda: Decimal("0"))
    # Keep the legacy tomorrow value even when the selected date is today.
    calculated_days = max(1, horizon_days)
    until = as_of + timedelta(days=calculated_days)
    for demand in demands:
        if demand['needed_on'] <= until:
            need[(demand['material_id'], max(as_of, demand['needed_on']))] += demand['remaining_quantity']
    overdue = 0
    for delivery in db.scalars(select(models.WarehouseDelivery).where(models.WarehouseDelivery.status == "ordered")):
        if delivery.expected_on < as_of:
            overdue += 1  # A missed delivery is not assumed to have arrived.
        elif delivery.expected_on <= until:
            incoming[(delivery.material_id, delivery.expected_on)] += delivery.quantity
    items = []
    for material in materials:
        balance = material.quantity
        lowest = None
        depletion = shortage = None
        days = []
        for offset in range(calculated_days + 1):
            day = as_of + timedelta(days=offset)
            requested, supplied = need[(material.id, day)], incoming[(material.id, day)]
            balance += supplied - requested
            if offset <= horizon_days:
                lowest = balance if lowest is None else min(lowest, balance)
                if balance <= 0 and depletion is None:
                    depletion = day
                if balance < 0 and shortage is None:
                    shortage = day
            days.append(ForecastDay(day=day, demand_quantity=requested, receipt_quantity=supplied,
                                    projected_quantity=balance))
        items.append(MaterialForecast(material_id=material.id, name=material.name, unit=material.unit,
            current_quantity=material.quantity, minimum_quantity=material.minimum_quantity,
            tomorrow_quantity=days[1].projected_quantity, depletion_on=depletion, shortage_on=shortage,
            target_quantity=days[horizon_days].projected_quantity,
            suggested_order_quantity=max(Decimal("0"), material.minimum_quantity - lowest),
            days=days[:horizon_days + 1]))
    return ForecastResponse(as_of=as_of, horizon_days=horizon_days, unplanned_parts=unplanned,
                            target_date=as_of + timedelta(days=horizon_days),
                            overdue_deliveries=overdue, items=items)
