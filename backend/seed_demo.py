"""Add a fixed demo dataset without replacing existing orders or operators.

Docker: run scripts/load_demo_docker.py on the host (it creates a pg_dump first).
Local SQLite: python -m backend.seed_demo (automatic SQLite backup).
"""
import argparse
from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
import sqlite3

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend import database, models, settings
from backend.migrations import initialize_database
from backend.models import OrderStatus as Status
from backend.schemas import OrderCreate


PREFIX = "DEMO/"
OPERATORS = (
    "Jan Majewski (DEMO)", "Piotr Kowalik (DEMO)",
    "Tomasz Baran (DEMO)", "Marek Kurek (DEMO)",
    "Anna Wrona (DEMO)", "Katarzyna Lis (DEMO)",
    "Adam Sobczak (DEMO)", "Ewa Wilk (DEMO)",
)
CLIENTS = (
    "MetalForma (DEMO)", "StalProjekt (DEMO)", "TechRam (DEMO)",
    "HalaBud (DEMO)", "AgroMechanika (DEMO)", "InoxDesign (DEMO)",
)
# Two parts per order, three operations per part.
PARTS = (
    ("Rama nośna", "S235JR", "18,5", "kg", ("Cięcie profili", "Spawanie MAG", "Szlifowanie")),
    ("Wspornik montażowy", "S235JR", "4", "szt.", ("Wycinanie", "Wiercenie", "Montaż")),
    ("Balustrada", "AISI 304", "3,2", "m", ("Cięcie rur", "Spawanie TIG", "Polerowanie")),
    ("Płyta mocująca", "AISI 304", "2", "szt.", ("Cięcie laserowe", "Wiercenie", "Gratowanie")),
    ("Segment ogrodzenia", "S355", "2,4", "m²", ("Przygotowanie elementów", "Spawanie MAG", "Kontrola wymiarów")),
    ("Słupek nośny", "S235JR", "2,1", "mb", ("Cięcie", "Wiercenie", "Spawanie podstawy")),
    ("Podstawa maszyny", "S355", "32", " KG ", ("Cięcie blach", "Spawanie MAG", "Kontrola wymiarów")),
    ("Uchwyt mocujący", "S235JR", "6", "szt", ("Cięcie", "Gięcie", "Szlifowanie")),
)


@dataclass(frozen=True)
class Scenario:
    status: Status
    received: int
    deadline: int
    finished_steps: int = 0
    completed: int | None = None
    archived: int | None = None
    missing: bool = False
    correction: bool = False
    approved: bool = False


SCENARIOS = (
    Scenario(Status.IN_QUEUE, -1, 8),
    Scenario(Status.IN_QUEUE, -6, 4, 1, missing=True),
    Scenario(Status.IN_QUEUE, -14, 2, 5, correction=True),
    Scenario(Status.IN_QUEUE, -16, -2),
    Scenario(Status.IN_PROGRESS, -3, 5, 1),
    Scenario(Status.IN_PROGRESS, -7, 2, 3),
    Scenario(Status.IN_PROGRESS, -8, -1, 4),
    Scenario(Status.IN_PROGRESS, -1, 10),
    Scenario(Status.IN_PROGRESS, -5, 1, 5),
    Scenario(Status.QUALITY_CHECK, -10, 1, 6),
    Scenario(Status.QUALITY_CHECK, -12, 2, 6, approved=True),
    Scenario(Status.QUALITY_CHECK, -9, -1, 6),
    Scenario(Status.COMPLETED, -15, -1, 6, completed=-3, approved=True),
    Scenario(Status.COMPLETED, -18, -6, 6, completed=-5, approved=True),
    Scenario(Status.ARCHIVED, -35, -20, 6, completed=-22, archived=-21, approved=True),
    Scenario(Status.ARCHIVED, -28, -15, 6, completed=-14, archived=-13, approved=True),
    Scenario(Status.ARCHIVED, -24, -12, 6, completed=-13, archived=-12, approved=True),
    Scenario(Status.ARCHIVED, -21, -8, 6, completed=-7, archived=-6, approved=True),
    Scenario(Status.ARCHIVED, -18, -5, 6, completed=-6, archived=-5, approved=True),
    Scenario(Status.ARCHIVED, -16, -3, 6, completed=-4, archived=-3, approved=True),
)


def moment(reference, days, hour=9):
    return datetime.combine(reference + timedelta(days=days), time(hour))


def history_for(spec, reference):
    events = [(Status.IN_QUEUE, moment(reference, spec.received), "Utworzenie zlecenia")]
    if spec.status != Status.IN_QUEUE or spec.missing or spec.correction:
        events.append((Status.IN_PROGRESS, moment(reference, spec.received + 1, 8), "Rozpoczęcie realizacji"))
    if spec.correction:
        events.append((Status.QUALITY_CHECK, moment(reference, -2, 11), "Przekazanie do kontroli jakości"))
        events.append((Status.IN_QUEUE, moment(reference, -1, 14), "Kontrola jakości: poprawa spoiny"))
    elif spec.missing:
        events.append((Status.IN_QUEUE, moment(reference, -1, 14), "Brak profili stalowych; oczekiwanie na dostawę"))
    elif spec.status in (Status.QUALITY_CHECK, Status.COMPLETED, Status.ARCHIVED):
        qc_day = spec.completed if spec.completed is not None else -1
        events.append((Status.QUALITY_CHECK, moment(reference, qc_day, 9), "Przekazanie do kontroli jakości"))
    if spec.completed is not None:
        events.append((Status.COMPLETED, moment(reference, spec.completed, 14), "Zakończenie po zatwierdzeniu jakości"))
    if spec.archived is not None:
        events.append((Status.ARCHIVED, moment(reference, spec.archived, 10), "Archiwizacja wykonanego zlecenia"))
    return events


def build_order(index, spec, reference):
    events = history_for(spec, reference)
    start = events[1][1] if len(events) > 1 else events[0][1]
    if spec.correction:
        stop = events[-2][1] - timedelta(hours=1)
    elif spec.missing:
        stop = events[-1][1] - timedelta(hours=1)
    elif spec.status in (Status.COMPLETED, Status.ARCHIVED, Status.QUALITY_CHECK):
        stop = next(at for status, at, _ in events if status == Status.QUALITY_CHECK) - timedelta(hours=1)
    else:
        stop = max(start, moment(reference, -1, 17))
    completed_indices = set(range(spec.finished_steps))
    if spec.correction:
        completed_indices = {0, 2, 3, 4, 5}  # Welding must be repeated after inspection.
    parts = []
    done = 0
    for part_index in range(2):
        name, material, quantity, unit, steps = PARTS[((index % 4) * 2 + part_index)]
        part_operator = OPERATORS[(index + part_index) % len(OPERATORS)]
        records = []
        for step_index, step_name in enumerate(steps):
            ordinal = part_index * 3 + step_index
            is_completed = ordinal in completed_indices
            record = {"step_name": step_name, "operator": part_operator, "is_completed": is_completed}
            if is_completed:
                done += 1
                record.update(
                    completed_at=start + (stop - start) * done / (spec.finished_steps + 1),
                    completed_by=part_operator,
                    production_notes="Dane demonstracyjne. Operacja wykonana zgodnie z kartą.",
                )
                if step_index == 0:
                    record.update(material_used=material, material_quantity=quantity, material_unit=unit)
                elif "Spawanie" in step_name:
                    record.update(material_used="Drut TIG 308LSi" if material == "AISI 304" else "Drut spawalniczy SG2",
                                  material_quantity="0,35", material_unit="kg")
            if spec.correction and ordinal == 1:
                record["production_notes"] = "Dane demonstracyjne. Należy poprawić spoinę po kontroli jakości."
            if spec.missing and not is_completed:
                record["production_notes"] = "Dane demonstracyjne. Oczekiwanie na dostawę materiału."
            records.append(record)
        parts.append({"part_name": name, "material": material, "operator": part_operator, "steps": records})

    # Use the same field and unit validation as the real API.
    payload = OrderCreate(
        order_number=f"{PREFIX}{index + 1:03d}", client_name=CLIENTS[index % len(CLIENTS)],
        date_received=moment(reference, spec.received), date_deadline=moment(reference, spec.deadline, 17),
        operator=OPERATORS[index % len(OPERATORS)], parts=parts,
    )
    order = models.Order(**payload.model_dump(exclude={"parts"}), status=spec.status,
                         is_approved=spec.approved, needs_correction=spec.correction,
                         status_changed_at=events[-1][1],
                         completed_at=moment(reference, spec.completed, 14) if spec.completed is not None else None)
    for position, part_data in enumerate(payload.parts):
        part = models.OrderPart(**part_data.model_dump(exclude={"steps", "client_key"}),
                                position=position, material_missing=spec.missing and position == 0)
        for step_position, step_data in enumerate(part_data.steps):
            part.steps.append(models.ProcessStep(**step_data.model_dump(), position=step_position))
        order.parts.append(part)
    previous = None
    for status, occurred_at, reason in events:
        order.status_events.append(models.OrderStatusEvent(
            from_status=previous, to_status=status, occurred_at=occurred_at,
            reason=f"{reason} (dane demonstracyjne)",
        ))
        previous = status
    return order


def seed_demo(engine, reference_date=None):
    reference = reference_date or datetime.now(timezone.utc).date()
    added = skipped = added_operators = 0
    with Session(engine) as db, db.begin():
        names = set(db.scalars(select(models.Operator.name)))
        for name in OPERATORS:
            if name not in names:
                db.add(models.Operator(name=name))
                added_operators += 1
        numbers = set(db.scalars(select(models.Order.order_number)))
        for index, spec in enumerate(SCENARIOS):
            number = f"{PREFIX}{index + 1:03d}"
            if number in numbers:
                skipped += 1
                continue
            db.add(build_order(index, spec, reference))
            added += 1
    return {"added_orders": added, "skipped_orders": skipped, "added_operators": added_operators}


def backup_sqlite(engine):
    if not engine.url.database or engine.url.database == ":memory:":
        return None
    source = Path(engine.url.database).resolve()
    if not source.exists():
        return None
    directory = settings.ROOT_DIR / "backups"
    directory.mkdir(exist_ok=True)
    destination = directory / f"before_demo_{datetime.now():%Y%m%d_%H%M%S_%f}.sqlite"
    with sqlite3.connect(str(source)) as connection, sqlite3.connect(str(destination)) as backup:
        connection.backup(backup)
    return destination


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--backup-file", type=Path, help="Existing PostgreSQL pg_dump created before this command")
    args = parser.parse_args()
    if database.engine.dialect.name == "sqlite":
        backup = backup_sqlite(database.engine)
    else:
        backup = args.backup_file
        if backup is None or not backup.is_file():
            parser.error("PostgreSQL wymaga kopii pg_dump. Uruchom scripts/load_demo_docker.py na hoście.")
        contents = backup.read_bytes()
        if b"PostgreSQL database dump" not in contents or b"PostgreSQL database dump complete" not in contents:
            parser.error("Plik kopii nie jest kompletnym zrzutem pg_dump SQL.")
    if backup:
        print(f"Backup: {backup}")
    initialize_database(database.engine)
    print(seed_demo(database.engine))
    with Session(database.engine) as db:
        orders = list(db.scalars(select(models.Order).where(models.Order.order_number.like(PREFIX + "%"))))
        print("Demo orders:", len(orders))
        print("Demo clients:", len({order.client_name for order in orders}))
        print("Demo statuses:", dict(Counter(order.status.value for order in orders)))


if __name__ == "__main__":
    main()
