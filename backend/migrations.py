"""Idempotent, additive upgrades of the existing SQLite/PostgreSQL schema."""
from sqlalchemy import inspect, text


def initialize_database(engine):
    from backend.models import Base

    Base.metadata.create_all(bind=engine)
    additions = {
        "orders": {"needs_correction": "BOOLEAN DEFAULT false", "status_changed_at": "TIMESTAMP",
                   "completed_at": "TIMESTAMP"},
        "order_parts": {"material": "VARCHAR", "material_missing": "BOOLEAN DEFAULT false",
                        "position": "INTEGER NOT NULL DEFAULT 0"},
        "process_steps": {"completed_at": "TIMESTAMP", "completed_by": "VARCHAR",
                          "production_notes": "TEXT", "material_used": "VARCHAR",
                          "material_quantity": "DOUBLE PRECISION", "material_unit": "VARCHAR",
                          "position": "INTEGER NOT NULL DEFAULT 0"},
    }
    with engine.begin() as connection:
        inspector = inspect(connection)
        for table, columns in additions.items():
            existing = {column["name"] for column in inspector.get_columns(table)}
            for column, sql_type in columns.items():
                if column not in existing:
                    connection.execute(text(f'ALTER TABLE "{table}" ADD COLUMN "{column}" {sql_type}'))
