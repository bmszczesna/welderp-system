"""Idempotent, additive upgrades of the existing SQLite/PostgreSQL schema."""
from collections import defaultdict
from math import isfinite

from sqlalchemy import inspect, text


def migrate_part_material_usage(connection):
    from backend.schemas import normalize_unit

    grouped = defaultdict(list)
    rows = connection.execute(text('''
        SELECT p.id, p.material, s.material_used, s.material_quantity, s.material_unit
        FROM order_parts p JOIN process_steps s ON s.part_id = p.id
        WHERE p.material_quantity IS NULL AND p.material_unit IS NULL
          AND s.material_quantity IS NOT NULL
    ''')).mappings()
    for row in rows:
        material = (row['material'] or '').strip().casefold()
        used = (row['material_used'] or '').strip().casefold()
        if not material or (used and used != material):
            continue  # Do not combine welding wire with the part's base material.
        try:
            unit = normalize_unit(row['material_unit'])
            quantity = float(row['material_quantity'])
            value = (quantity, unit) if unit and isfinite(quantity) and quantity >= 0 else None
        except (ValueError, TypeError):
            value = None
        grouped[row['id']].append(value)
    for part_id, values in grouped.items():
        if any(value is None for value in values) or len({value[1] for value in values}) != 1:
            continue  # Different dimensions or unknown units need explicit user input.
        quantity = sum(value[0] for value in values)
        if not isfinite(quantity):
            continue
        connection.execute(text('''UPDATE order_parts SET material_quantity = :quantity,
                                  material_unit = :unit WHERE id = :id'''),
                           {'quantity': quantity, 'unit': values[0][1], 'id': part_id})


def initialize_database(engine):
    from backend.models import Base

    Base.metadata.create_all(bind=engine)
    additions = {
        "orders": {"needs_correction": "BOOLEAN DEFAULT false", "status_changed_at": "TIMESTAMP",
                   "completed_at": "TIMESTAMP"},
        "order_parts": {"material": "VARCHAR", "material_missing": "BOOLEAN DEFAULT false",
                        "material_quantity": "DOUBLE PRECISION", "material_unit": "VARCHAR",
                        "position": "INTEGER NOT NULL DEFAULT 0"},
        "process_steps": {"completed_at": "TIMESTAMP", "completed_by": "VARCHAR",
                          "production_notes": "TEXT", "material_used": "VARCHAR",
                          "material_quantity": "DOUBLE PRECISION", "material_unit": "VARCHAR",
                          "position": "INTEGER NOT NULL DEFAULT 0"},
    }
    with engine.begin() as connection:
        inspector = inspect(connection)
        part_usage_added = False
        for table, columns in additions.items():
            existing = {column["name"] for column in inspector.get_columns(table)}
            for column, sql_type in columns.items():
                if column not in existing:
                    connection.execute(text(f'ALTER TABLE "{table}" ADD COLUMN "{column}" {sql_type}'))
                    if table == "order_parts" and column == "material_quantity":
                        part_usage_added = True
        # Run only when upgrading an old schema. A later cleared value stays cleared.
        if part_usage_added:
            migrate_part_material_usage(connection)
