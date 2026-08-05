from sqlalchemy import create_engine, text
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
from backend.config import settings

connect_args = {"check_same_thread": False} if settings.DATABASE_URL.startswith("sqlite") else {}

engine = create_engine(settings.DATABASE_URL, connect_args=connect_args,
                       echo=(settings.APP_ENV == "development"))
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

def _sqlite_columns(conn, table: str) -> set[str]:
    return {row[1] for row in conn.execute(text(f"PRAGMA table_info({table})")).fetchall()}

def _add_column_if_missing(conn, table: str, column: str, ddl: str):
    cols = _sqlite_columns(conn, table)
    if column not in cols:
        conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}"))

def migrate_sqlite_schema():
    """Small additive migration so existing rfid_lab.db files keep working.

    SQLAlchemy create_all does not add new columns to old SQLite tables, so we add
    the Phase 2 columns safely if they are missing.
    """
    if not settings.DATABASE_URL.startswith("sqlite"):
        return
    with engine.begin() as conn:
        # DB1 master additions: unique tag master terminology + SKU/GTIN.
        for col, ddl in {
            "sku": "VARCHAR(80)",
            "gtin": "VARCHAR(40)",
            "tag_source": "VARCHAR(50)",
            "expected_antenna_id": "INTEGER",
            "item_type": "VARCHAR(60)",
        }.items():
            _add_column_if_missing(conn, "db1_master", col, ddl)

        # DB2 scan additions: antenna evidence + zone confidence.
        for col, ddl in {
            "antenna_id": "VARCHAR(20)",
            "read_count": "INTEGER DEFAULT 1",
            "assigned_zone": "VARCHAR(60)",
            "zone_confidence": "FLOAT",
            "zone_reason": "TEXT",
            "sku": "VARCHAR(80)",
            "gtin": "VARCHAR(40)",
            "tag_classification": "VARCHAR(40)",
        }.items():
            _add_column_if_missing(conn, "db2_incoming", col, ddl)

        # Visitor live tracking tables are created by create_all. These additive
        # columns keep early local DBs compatible if the model evolves.
        for table, columns in {
            "visitors": {
                "badge_label": "VARCHAR(80)",
                "host": "VARCHAR(120)",
                "purpose": "VARCHAR(160)",
                "previous_zone": "VARCHAR(4)",
                "notes": "TEXT",
            },
            "visitor_location_events": {
                "scan_id": "VARCHAR(30)",
                "source": "VARCHAR(30)",
            },
        }.items():
            if not _sqlite_columns(conn, table):
                continue
            for col, ddl in columns.items():
                _add_column_if_missing(conn, table, col, ddl)
        conn.execute(text("DROP INDEX IF EXISTS ix_visitors_epc"))
        conn.execute(text("CREATE INDEX IF NOT EXISTS ix_visitors_epc ON visitors (epc)"))

def create_tables():
    from backend import models  # noqa
    Base.metadata.create_all(bind=engine)
    migrate_sqlite_schema()
