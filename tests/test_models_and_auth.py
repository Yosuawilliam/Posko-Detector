# tests/test_models_and_auth.py
from datetime import date
import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import sessionmaker
from models import Base, Posko, Resident, DistributionLog, MEAL_PRICE, GALLON_PRICE, MAX_DAILY_MEALS
from auth import verify_admin_auth, ADMIN_TOKEN
import database


def test_constants_and_cascade_delete():
    assert MEAL_PRICE == 35000
    assert GALLON_PRICE == 22000
    assert MAX_DAILY_MEALS == 3

    engine = create_engine("sqlite:///:memory:")

    @event.listens_for(engine, "connect")
    def _enable_sqlite_fk(dbapi_conn, connection_record):
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    with Session() as db:
        posko = Posko(code="POSKO-A", name="Posko Utama", location="Aula", stock_meals=100, stock_gallons=10)
        db.add(posko)
        db.commit()

        res = Resident(
            nik="32010001",
            qr_token="QR-POSKO-A-32010001",
            full_name="Budi Santoso",
            age=35,
            gender="L",
            kk_name="Budi Santoso",
            posko_code="POSKO-A",
        )
        db.add(res)
        db.commit()
        assert db.scalar(select(Resident).where(Resident.nik == "32010001")) is not None

        log = DistributionLog(
            claim_date=date.today(),
            resident_id=res.id,
            resident_name=res.full_name,
            resident_nik=res.nik,
            scanner_posko_code="POSKO-A",
            resident_posko_code="POSKO-A",
            status="SUCCESS",
            meal_sequence=1,
            cost_rupiah=MEAL_PRICE,
        )
        db.add(log)
        db.commit()
        assert db.scalar(select(DistributionLog).where(DistributionLog.scanner_posko_code == "POSKO-A")) is not None

        # Verify ON DELETE CASCADE when deleting posko
        db.delete(posko)
        db.commit()
        assert db.scalar(select(Resident).where(Resident.nik == "32010001")) is None
        assert db.scalar(select(DistributionLog).where(DistributionLog.scanner_posko_code == "POSKO-A")) is None


def test_verify_admin_auth():
    assert verify_admin_auth(ADMIN_TOKEN) == ADMIN_TOKEN
    with pytest.raises(HTTPException) as exc:
        verify_admin_auth("wrong-token")
    assert exc.value.status_code == 401
    with pytest.raises(HTTPException) as exc_none:
        verify_admin_auth(None)
    assert exc_none.value.status_code == 401


def test_init_db_seeds_default_poskos(monkeypatch):
    test_engine = create_engine("sqlite:///:memory:")
    TestSession = sessionmaker(bind=test_engine, autocommit=False, autoflush=False)
    monkeypatch.setattr(database, "engine", test_engine)
    monkeypatch.setattr(database, "SessionLocal", TestSession)

    database.init_db()

    with TestSession() as db:
        poskos = db.scalars(select(Posko).order_by(Posko.code)).all()
        assert len(poskos) == 2
        assert poskos[0].code == "POSKO-A"
        assert poskos[0].stock_meals == 150
        assert poskos[0].min_stock_alert == 25
        assert poskos[0].stock_gallons == 20
        assert poskos[0].min_gallon_alert == 5
        assert poskos[1].code == "POSKO-B"
        assert poskos[1].stock_meals == 100
        assert poskos[1].min_stock_alert == 20
        assert poskos[1].stock_gallons == 15
        assert poskos[1].min_gallon_alert == 5
