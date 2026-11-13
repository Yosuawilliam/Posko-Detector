import logging
import os
import re
from collections.abc import Generator
from urllib.parse import quote_plus
from sqlalchemy import create_engine, event, select, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import QueuePool

from models import Base, DistributionLog, Posko, Resident

logger = logging.getLogger(__name__)

MYSQL_HOST = os.getenv("MYSQL_HOST", "localhost")
MYSQL_PORT = os.getenv("MYSQL_PORT", "3306")
MYSQL_USER = os.getenv("MYSQL_USER", "root")
MYSQL_PASSWORD = os.getenv("MYSQL_PASSWORD", "")
MYSQL_DB = os.getenv("MYSQL_DB", "posko_detector_db")


def build_database_url() -> str:
    env_url = os.getenv("DATABASE_URL", "").strip()
    if env_url:
        return env_url
    encoded_user = quote_plus(MYSQL_USER)
    encoded_password = quote_plus(MYSQL_PASSWORD)
    auth_part = f"{encoded_user}:{encoded_password}" if MYSQL_PASSWORD else encoded_user
    return f"mysql+pymysql://{auth_part}@{MYSQL_HOST}:{MYSQL_PORT}/{MYSQL_DB}?charset=utf8mb4"


DATABASE_URL = build_database_url()


def _attach_sqlite_fk_pragma(target_engine) -> None:
    @event.listens_for(target_engine, "connect")
    def _set_sqlite_pragma(dbapi_connection, connection_record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()


def create_app_engine(db_url: str):
    if db_url.startswith("sqlite"):
        connect_args = {"check_same_thread": False}
        eng = create_engine(db_url, connect_args=connect_args)
        _attach_sqlite_fk_pragma(eng)
        return eng
    return create_engine(
        db_url,
        poolclass=QueuePool,
        pool_size=10,
        max_overflow=20,
        pool_pre_ping=True,
    )


engine = create_app_engine(DATABASE_URL)
SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False)


def _ensure_mysql_database_exists() -> None:
    target_db = engine.url.database or MYSQL_DB
    safe_db_name = re.sub(r"[^A-Za-z0-9_]", "_", target_db)
    server_url = engine.url.set(database=None)
    if server_url.database:
        server_url = server_url.set(database="")
    server_engine = create_engine(server_url, pool_pre_ping=True)
    try:
        with server_engine.connect() as conn:
            conn.execute(
                text(
                    f"CREATE DATABASE IF NOT EXISTS `{safe_db_name}` "
                    "CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci"
                )
            )
            conn.commit()
    finally:
        server_engine.dispose()


def seed_default_poskos(db: Session) -> None:
    existing = db.scalar(select(Posko).limit(1))
    if existing is not None:
        return
    default_poskos = [
        Posko(
            code="POSKO-A",
            name="Posko Utama Balai Desa",
            location="Balai Desa Sukamaju",
            stock_meals=150,
            min_stock_alert=25,
            stock_gallons=20,
            min_gallon_alert=5,
        ),
        Posko(
            code="POSKO-B",
            name="Posko Lapangan Timur",
            location="Lapangan Kecamatan Timur",
            stock_meals=100,
            min_stock_alert=20,
            stock_gallons=15,
            min_gallon_alert=5,
        ),
    ]
    db.add_all(default_poskos)
    db.commit()


def init_db() -> None:
    global engine
    backend = engine.url.get_backend_name()
    if backend == "mysql":
        try:
            _ensure_mysql_database_exists()
            Base.metadata.create_all(bind=engine)
        except OperationalError as exc:
            logger.warning(
                "MySQL server unreachable (%s); falling back to local SQLite database.",
                exc,
            )
            fallback_url = os.getenv("SQLITE_FALLBACK_URL", "sqlite:///./posko_detector.db")
            engine = create_app_engine(fallback_url)
            SessionLocal.configure(bind=engine)
            Base.metadata.create_all(bind=engine)
    else:
        if backend == "sqlite":
            _attach_sqlite_fk_pragma(engine)
        Base.metadata.create_all(bind=engine)

    with SessionLocal() as db:
        seed_default_poskos(db)


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
