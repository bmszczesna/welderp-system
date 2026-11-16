"""Reconcile a part's total consumption with its immutable warehouse ledger.

The caller owns the transaction. All inventory writers take the same lock, so a
retry, correction or competing order cannot debit the same reservation twice.
"""
from decimal import Decimal
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import func, select, update

from backend import models, reservations, warehouse


def set_report(part, quantity, unit, force=False):
    previous = Decimal(str(part.material_quantity)) if part.material_quantity is not None else None
    if force or (previous, part.material_unit) != (quantity, unit):
        part.material_quantity = float(quantity) if quantity is not None else None
        part.material_unit = unit
        part.material_usage_version = (part.material_usage_version or 0) + 1
        part.owner.is_approved = False


def reconcile(db, part_id, usage):
    reservations.synchronize(db)
    part = db.scalar(select(models.OrderPart).where(models.OrderPart.id == part_id)
                     .with_for_update().execution_options(populate_existing=True))
    if part is None:
        raise HTTPException(status_code=404, detail="Nie znaleziono części")
    if part.owner.status != models.OrderStatus.IN_PROGRESS:
        raise HTTPException(status_code=400, detail="Zużycie materiału zapisuje się podczas realizacji zlecenia")
    if usage.material_quantity is not None and not part.material:
        raise HTTPException(status_code=422, detail="Podaj materiał części przed zapisaniem zużycia")
    if not part.warehouse_material_id or part.planned_quantity is None:
        raise HTTPException(status_code=422, detail="Najpierw wybierz materiał z magazynu i potrzebną ilość w edycji zlecenia")
    material = warehouse.get_material(db, part.warehouse_material_id)
    if usage.material_unit is not None and usage.material_unit != material.unit:
        raise HTTPException(status_code=422, detail=f"Zużycie tego materiału podaj w jednostce: {material.unit}")

    target = usage.material_quantity or Decimal('0')
    issued = warehouse.issued_quantity(db, part.id)
    previous = Decimal(str(part.material_quantity)) if part.material_quantity is not None else None
    already_saved = (previous, part.material_unit, issued) == (usage.material_quantity, usage.material_unit, target)
    if usage.expected_version is not None and usage.expected_version != part.material_usage_version:
        if already_saved:
            return part  # Safe retry after the first response was lost.
        raise HTTPException(status_code=409, detail="Zużycie zmieniło się w międzyczasie. Odśwież zlecenie i sprawdź aktualną ilość.")
    if already_saved:
        return part

    delta = issued - target  # Positive values return a corrected excess to stock.
    if delta < 0:
        reservations.ensure_order_ready(db, part.owner)
        available = part.reserved_quantity + material.available_quantity
        if -delta > available:
            raise HTTPException(status_code=409, detail="Za mało wolnego materiału na takie zużycie. Pozostały zapas jest zarezerwowany dla innych zleceń.")
        part.reserved_quantity -= min(part.reserved_quantity, -delta)
    elif delta > 0:
        # The returned amount still needed by this part retains its reservation.
        own_needed = max(Decimal('0'), part.planned_quantity - target - part.reserved_quantity)
        part.reserved_quantity += min(delta, own_needed)

    if delta:
        statement = update(models.WarehouseMaterial).where(models.WarehouseMaterial.id == material.id)
        statement = statement.where(models.WarehouseMaterial.quantity >= -delta) if delta < 0 else statement.where(
            models.WarehouseMaterial.quantity <= warehouse.MAX_QUANTITY - delta)
        balance = db.execute(statement.values(quantity=func.round(models.WarehouseMaterial.quantity + delta, 6))
                             .returning(models.WarehouseMaterial.quantity),
                             execution_options={"synchronize_session": False}).scalar_one_or_none()
        if balance is None:
            raise HTTPException(status_code=409, detail="Nie można rozliczyć zużycia: sprawdź stan magazynu i wielkość korekty")
        db.add(models.WarehouseMovement(material_id=material.id, order_part_id=part.id,
            order_number=part.owner.order_number, part_name=part.part_name, request_id=f"usage:{uuid4()}",
            kind='receipt' if delta > 0 else 'issue', quantity=delta, balance_after=balance,
            note='Korekta zużycia — zwrot do magazynu' if delta > 0 else 'Zużycie materiału — zapis z produkcji'))
    set_report(part, usage.material_quantity, usage.material_unit, force=bool(delta))
    reservations.synchronize(db)
    return part
