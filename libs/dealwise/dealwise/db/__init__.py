from sqlalchemy import inspect, text
from sqlmodel import SQLModel

from .models import DW_MODELS, AuditLog, Contract, Vendor, VendorAlias

__all__ = [
    "AuditLog",
    "Contract",
    "Vendor",
    "VendorAlias",
    "init_db",
    "ensure_columns",
    "get_engine",
]


def get_engine():
    """The upstream Kotaemon engine (same SQLite file as the rest of the app)."""
    from ktem.db.engine import engine

    return engine


def init_db(engine) -> None:
    """Create DealWise tables (idempotent) and add any columns introduced by
    later versions. Upstream tables are never altered."""
    SQLModel.metadata.create_all(engine, tables=[m.__table__ for m in DW_MODELS])
    ensure_columns(engine)


def ensure_columns(engine) -> None:
    """Minimal forward migration: add nullable columns missing from existing
    ``dw_`` tables (Alembic is disabled upstream)."""
    insp = inspect(engine)
    for model in DW_MODELS:
        table = model.__table__
        if not insp.has_table(table.name):
            continue
        existing = {c["name"] for c in insp.get_columns(table.name)}
        for col in table.columns:
            if col.name in existing:
                continue
            if not col.nullable and col.default is None and col.server_default is None:
                raise RuntimeError(
                    f"Cannot auto-add non-null column {table.name}.{col.name}"
                )
            ddl = col.type.compile(dialect=engine.dialect)
            with engine.begin() as conn:
                conn.execute(text(f"ALTER TABLE {table.name} ADD COLUMN {col.name} {ddl}"))
