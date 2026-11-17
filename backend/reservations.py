"""Immediate stock reservations, independent of production dates."""
from collections import defaultdict
from decimal import Decimal

from fastapi import HTTPException
from sqlalchemy import select, text, update

from backend import models

ZERO = Decimal('0')
TERMINAL = (models.OrderStatus.COMPLETED, models.OrderStatus.ARCHIVED)


def lock_inventory(db):
    # Every stock/requirement write takes this lock before locking individual rows.
    # A single inventory lock also serializes competing order reservations.
    with db.no_autoflush:
        if db.bind.dialect.name == 'postgresql':
            db.execute(text('SELECT pg_advisory_xact_lock(19372502)'))
        elif db.bind.dialect.name == 'sqlite':
            db.execute(update(models.WarehouseMaterial).where(models.WarehouseMaterial.id ==
                select(models.WarehouseMaterial.id).order_by(models.WarehouseMaterial.id).limit(1).scalar_subquery()
            ).values(quantity=models.WarehouseMaterial.quantity), execution_options={'synchronize_session': False})


def synchronize(db):
    """Keep existing reservations, then fill shortages with genuinely free stock."""
    lock_inventory(db)
    db.flush()
    db.expire_all()
    materials = {m.id: m for m in db.scalars(select(models.WarehouseMaterial).order_by(models.WarehouseMaterial.id)
                                           .with_for_update())}
    parts = db.scalars(select(models.OrderPart).order_by(models.OrderPart.id)).all()
    allocated = defaultdict(lambda: ZERO)
    active = []
    for part in parts:
        if part.owner.status in TERMINAL or part.warehouse_material_id not in materials or part.planned_quantity is None:
            part.reserved_quantity = ZERO
            continue
        material = materials[part.warehouse_material_id]
        remaining = part.remaining_quantity
        # Preserve allocations already granted to other orders, including on edits.
        part.reserved_quantity = max(ZERO, min(part.reserved_quantity or ZERO, remaining,
                                              material.quantity - allocated[material.id]))
        allocated[material.id] += part.reserved_quantity
        active.append(part)
    # Parts waiting for stock are served in creation order; dates do not reserve stock.
    for part in active:
        material = materials[part.warehouse_material_id]
        extra = min(part.remaining_quantity - part.reserved_quantity,
                    material.quantity - allocated[material.id])
        part.reserved_quantity += extra
        allocated[material.id] += extra
    db.flush()
    # Refresh correlated issued/reserved totals after ledger or allocation changes.
    db.expire_all()


def ensure_order_ready(db, order):
    synchronize(db)
    warnings = order.material_warnings
    if warnings:
        raise HTTPException(status_code=400, detail='Nie można przesunąć zlecenia. ' + ' '.join(warnings))
