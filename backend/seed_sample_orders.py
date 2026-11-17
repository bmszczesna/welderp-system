"""Add five materials and ten sample orders, without replacing existing data.

Run: docker compose exec backend python -m backend.seed_sample_orders
"""
from datetime import datetime, time, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from sqlalchemy import select

from backend import database, models, reservations


# Name, unit, opening stock, minimum stock.
MATERIALS = (
    ("Blacha stalowa S235JR 5 mm", "kg", "1000", "100"),
    ("Profil stalowy S355 60x40x3 mm", "m", "300", "30"),
    ("Rura nierdzewna AISI 304 42,4 mm", "m", "150", "15"),
    ("Blacha aluminiowa EN AW-5754 3 mm", "kg", "500", "50"),
    ("Płaskownik stalowy S235JR 40x5 mm", "m", "200", "20"),
)

# Client, material index, two parts with required quantities, deadline in days.
ORDERS = (
    ("MetalForma", 0, (("Podstawa maszyny", "80"), ("Płyty mocujące", "20")), 3),
    ("StalProjekt", 1, (("Rama regału", "24"), ("Poprzeczki regału", "12")), 5),
    ("InoxDesign", 2, (("Poręcz schodowa", "12"), ("Słupki balustrady", "8")), 7),
    ("TechRam", 3, (("Osłona maszyny", "35"), ("Panel serwisowy", "10")), 10),
    ("HalaBud", 4, (("Wsporniki montażowe", "18"), ("Łączniki konstrukcyjne", "6")), 12),
    ("AgroMechanika", 0, (("Płyta robocza", "120"), ("Żebra wzmacniające", "30")), 14),
    ("LogiStal", 1, (("Stelaż stołu", "30"), ("Rama półki", "15")), 16),
    ("GastroInox", 2, (("Stelaż wózka", "18"), ("Uchwyty wózka", "4")), 20),
    ("AluTech", 3, (("Obudowa urządzenia", "45"), ("Pokrywa obudowy", "15")), 25),
    ("BudMetal", 4, (("Krata zabezpieczająca", "32"), ("Uchwyty montażowe", "8")), 30),
)


def seed(db):
    """Write one transaction; repeated calls skip matching material/order keys."""
    reservations.lock_inventory(db)
    now = datetime.utcnow()
    today = datetime.now(ZoneInfo("Europe/Warsaw")).date()
    materials = []
    added_materials = added_orders = 0
    for name, unit, opening, minimum in MATERIALS:
        material = db.scalar(select(models.WarehouseMaterial).where(
            models.WarehouseMaterial.name_key == name.casefold(),
            models.WarehouseMaterial.unit == unit,
        ))
        if material is None:
            material = models.WarehouseMaterial(
                name=name, name_key=name.casefold(), unit=unit,
                quantity=Decimal(opening), minimum_quantity=Decimal(minimum),
            )
            db.add(material)
            db.flush()
            db.add(models.WarehouseMovement(
                material_id=material.id, kind="opening", quantity=Decimal(opening),
                balance_after=Decimal(opening), note="Stan początkowy — przykładowe materiały",
            ))
            added_materials += 1
        materials.append(material)

    for index, (client, material_index, parts, days) in enumerate(ORDERS, start=1):
        number = f"PRZYKLAD/{index:03d}"
        if db.scalar(select(models.Order.id).where(models.Order.order_number == number)) is not None:
            continue
        material = materials[material_index]
        order = models.Order(
            order_number=number, client_name=client, date_received=now,
            date_deadline=datetime.combine(today + timedelta(days=days), time()),
            status=models.OrderStatus.IN_QUEUE, status_changed_at=now,
            is_approved=False, needs_correction=False,
        )
        order.status_events.append(models.OrderStatusEvent(
            from_status=None, to_status=models.OrderStatus.IN_QUEUE,
            occurred_at=now, reason="Utworzenie przykładowego zlecenia",
        ))
        for position, (name, quantity) in enumerate(parts):
            part = models.OrderPart(
                part_name=name, position=position, material=material.name,
                warehouse_material_id=material.id, planned_quantity=Decimal(quantity),
                reserved_quantity=Decimal("0"), material_missing=False,
            )
            welding = "Spawanie TIG" if material_index in (2, 3) else "Spawanie MAG"
            for step_position, step_name in enumerate(("Cięcie i przygotowanie", welding, "Obróbka i kontrola wymiarów")):
                part.steps.append(models.ProcessStep(
                    step_name=step_name, position=step_position,
                    status=models.OrderStatus.IN_QUEUE, is_completed=False,
                ))
            order.parts.append(part)
        db.add(order)
        added_orders += 1
    reservations.synchronize(db)
    return added_materials, added_orders


def main():
    with database.SessionLocal.begin() as db:
        materials, orders = seed(db)
    print(f"Dodano materiałów: {materials}; zleceń: {orders}.")
    print("Zlecenia PRZYKLAD/001–010 są w oczekujących. Odśwież Kanban i magazyn.")


if __name__ == "__main__":
    main()
