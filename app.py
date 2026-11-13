from contextlib import asynccontextmanager
from datetime import date
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from auth import verify_admin_auth
from database import get_db, init_db
from models import (
    GALLON_PRICE,
    MAX_DAILY_MEALS,
    MEAL_PRICE,
    DistributionLog,
    Posko,
    Resident,
)

DEFAULT_DETECTOR_STATE: dict[str, Any] = {
    "yolo_camera_online": False,
    "active_posko": "POSKO-A",
    "person_count": 0,
    "recognized_residents": [],
    "last_sync": None,
}

detector_state: dict[str, Any] = dict(DEFAULT_DETECTOR_STATE)


def reset_detector_state() -> None:
    detector_state.clear()
    detector_state.update(DEFAULT_DETECTOR_STATE)
    detector_state["recognized_residents"] = []


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    yield


app = FastAPI(
    title="Posko Detector & Sistem Manajemen Distribusi Logistik Bencana",
    version="2.0.0",
    lifespan=lifespan,
)


class PoskoCreateRequest(BaseModel):
    code: str = Field(..., min_length=1, max_length=32)
    name: str = Field(..., min_length=1, max_length=120)
    location: str = Field(..., min_length=1, max_length=255)
    stock_meals: int = Field(default=0, ge=0)
    min_stock_alert: int = Field(default=20, ge=0)
    stock_gallons: int = Field(default=0, ge=0)
    min_gallon_alert: int = Field(default=5, ge=0)


class RestockMealsRequest(BaseModel):
    amount: int = Field(default=0, ge=0)
    min_stock_alert: int | None = Field(default=None, ge=0)


class RestockGallonsRequest(BaseModel):
    amount: int = Field(default=0, ge=0)
    min_gallon_alert: int | None = Field(default=None, ge=0)


def compute_stock_status(stock: int, min_alert: int) -> str:
    if stock <= 0:
        return "STOK HABIS"
    if stock <= min_alert:
        return "STOK MENIPIS"
    return "Stok Aman"


def serialize_posko(
    posko: Posko,
    db: Session | None = None,
    resident_count: int | None = None,
) -> dict[str, Any]:
    if resident_count is None:
        if db is not None:
            resident_count = (
                db.scalar(
                    select(func.count(Resident.id)).where(Resident.posko_code == posko.code)
                )
                or 0
            )
        else:
            resident_count = len(posko.residents) if posko.residents else 0

    return {
        "id": posko.id,
        "code": posko.code,
        "name": posko.name,
        "location": posko.location,
        "stock_meals": posko.stock_meals,
        "min_stock_alert": posko.min_stock_alert,
        "stock_status": compute_stock_status(posko.stock_meals, posko.min_stock_alert),
        "meals_value_rupiah": posko.stock_meals * MEAL_PRICE,
        "stock_gallons": posko.stock_gallons,
        "min_gallon_alert": posko.min_gallon_alert,
        "gallon_status": compute_stock_status(posko.stock_gallons, posko.min_gallon_alert),
        "gallons_value_rupiah": posko.stock_gallons * GALLON_PRICE,
        "resident_count": resident_count,
        "created_at": posko.created_at.isoformat() if posko.created_at else None,
    }


def serialize_log(log: DistributionLog) -> dict[str, Any]:
    return {
        "id": log.id,
        "created_at": log.created_at.isoformat() if log.created_at else None,
        "claim_date": log.claim_date.isoformat() if log.claim_date else None,
        "resident_id": log.resident_id,
        "resident_name": log.resident_name,
        "resident_nik": log.resident_nik,
        "scanner_posko_code": log.scanner_posko_code,
        "resident_posko_code": log.resident_posko_code,
        "status": log.status,
        "meal_sequence": log.meal_sequence,
        "cost_rupiah": log.cost_rupiah,
        "notes": log.notes,
    }


@app.get("/api/poskos")
def list_poskos(db: Session = Depends(get_db)) -> list[dict[str, Any]]:
    poskos = db.scalars(select(Posko).order_by(Posko.code.asc())).all()
    counts_rows = db.execute(
        select(Resident.posko_code, func.count(Resident.id)).group_by(Resident.posko_code)
    ).all()
    counts_map = {code: count for code, count in counts_rows}
    return [
        serialize_posko(p, resident_count=counts_map.get(p.code, 0))
        for p in poskos
    ]


@app.post("/api/poskos", status_code=status.HTTP_201_CREATED)
def create_posko(
    payload: PoskoCreateRequest,
    db: Session = Depends(get_db),
    _admin: str = Depends(verify_admin_auth),
) -> dict[str, Any]:
    clean_code = payload.code.strip().upper()
    existing = db.scalar(select(Posko).where(Posko.code == clean_code))
    if existing is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Posko dengan kode '{clean_code}' sudah terdaftar",
        )

    posko = Posko(
        code=clean_code,
        name=payload.name.strip(),
        location=payload.location.strip(),
        stock_meals=payload.stock_meals,
        min_stock_alert=payload.min_stock_alert,
        stock_gallons=payload.stock_gallons,
        min_gallon_alert=payload.min_gallon_alert,
    )
    db.add(posko)
    db.commit()
    db.refresh(posko)
    return serialize_posko(posko, db=db, resident_count=0)


@app.delete("/api/poskos/{code}")
def delete_posko(
    code: str,
    db: Session = Depends(get_db),
    _admin: str = Depends(verify_admin_auth),
) -> dict[str, Any]:
    posko = db.scalar(select(Posko).where(Posko.code == code))
    if posko is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Posko '{code}' tidak ditemukan",
        )
    db.delete(posko)
    db.commit()
    return {"status": "deleted", "code": code}


@app.post("/api/poskos/{code}/restock-meals")
def restock_meals(
    code: str,
    payload: RestockMealsRequest,
    db: Session = Depends(get_db),
    _admin: str = Depends(verify_admin_auth),
) -> dict[str, Any]:
    posko = db.scalar(select(Posko).where(Posko.code == code).with_for_update())
    if posko is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Posko '{code}' tidak ditemukan",
        )
    posko.stock_meals += payload.amount
    if payload.min_stock_alert is not None:
        posko.min_stock_alert = payload.min_stock_alert
    db.commit()
    db.refresh(posko)
    return serialize_posko(posko, db=db)


@app.post("/api/poskos/{code}/use-gallon")
def use_gallon(
    code: str,
    db: Session = Depends(get_db),
    _admin: str = Depends(verify_admin_auth),
) -> dict[str, Any]:
    posko = db.scalar(select(Posko).where(Posko.code == code).with_for_update())
    if posko is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Posko '{code}' tidak ditemukan",
        )
    if posko.stock_gallons <= 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Stok galon di {posko.code} habis (0 galon)",
        )

    posko.stock_gallons -= 1
    log_entry = DistributionLog(
        claim_date=date.today(),
        resident_id=None,
        resident_name="Logistik Galon Air",
        resident_nik=None,
        scanner_posko_code=posko.code,
        resident_posko_code=posko.code,
        status="GALLON_USE",
        meal_sequence=None,
        cost_rupiah=GALLON_PRICE,
        notes="Pemakaian 1 galon air",
    )
    db.add(log_entry)
    db.commit()
    db.refresh(posko)
    return serialize_posko(posko, db=db)


@app.post("/api/poskos/{code}/restock-gallons")
def restock_gallons(
    code: str,
    payload: RestockGallonsRequest,
    db: Session = Depends(get_db),
    _admin: str = Depends(verify_admin_auth),
) -> dict[str, Any]:
    posko = db.scalar(select(Posko).where(Posko.code == code).with_for_update())
    if posko is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Posko '{code}' tidak ditemukan",
        )
    posko.stock_gallons += payload.amount
    if payload.min_gallon_alert is not None:
        posko.min_gallon_alert = payload.min_gallon_alert
    db.commit()
    db.refresh(posko)
    return serialize_posko(posko, db=db)


@app.get("/api/dashboard/summary")
def get_dashboard_summary(
    posko: str | None = Query(default=None),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    poskos = db.scalars(select(Posko).order_by(Posko.code.asc())).all()
    if not poskos:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Belum ada data posko",
        )

    target_code = posko or detector_state.get("active_posko") or poskos[0].code
    active_posko_obj = next((p for p in poskos if p.code == target_code), None)
    if active_posko_obj is None:
        if posko is not None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Posko '{posko}' tidak ditemukan",
            )
        active_posko_obj = poskos[0]

    today = date.today()

    total_residents = db.scalar(select(func.count(Resident.id))) or 0

    today_meals_distributed = (
        db.scalar(
            select(func.count(DistributionLog.id)).where(
                DistributionLog.claim_date == today,
                DistributionLog.status == "SUCCESS",
            )
        )
        or 0
    )

    active_posko_today_meals = (
        db.scalar(
            select(func.count(DistributionLog.id)).where(
                DistributionLog.claim_date == today,
                DistributionLog.status == "SUCCESS",
                DistributionLog.scanner_posko_code == active_posko_obj.code,
            )
        )
        or 0
    )

    today_gallons_used = (
        db.scalar(
            select(func.count(DistributionLog.id)).where(
                DistributionLog.claim_date == today,
                DistributionLog.status == "GALLON_USE",
            )
        )
        or 0
    )

    active_posko_today_gallons = (
        db.scalar(
            select(func.count(DistributionLog.id)).where(
                DistributionLog.claim_date == today,
                DistributionLog.status == "GALLON_USE",
                DistributionLog.scanner_posko_code == active_posko_obj.code,
            )
        )
        or 0
    )

    today_meal_cost = today_meals_distributed * MEAL_PRICE
    today_gallon_cost = today_gallons_used * GALLON_PRICE
    today_total_cost = today_meal_cost + today_gallon_cost

    global_warnings: list[dict[str, Any]] = []
    for p in poskos:
        if p.stock_meals <= p.min_stock_alert:
            meal_status = compute_stock_status(p.stock_meals, p.min_stock_alert)
            global_warnings.append(
                {
                    "posko_code": p.code,
                    "posko_name": p.name,
                    "type": "MEAL",
                    "status": meal_status,
                    "current_stock": p.stock_meals,
                    "min_alert": p.min_stock_alert,
                    "message": (
                        f"{p.code} ({p.name}): Stok Makanan {meal_status} "
                        f"({p.stock_meals} porsi, min {p.min_stock_alert})"
                    ),
                }
            )
        if p.stock_gallons <= p.min_gallon_alert:
            gallon_status = compute_stock_status(p.stock_gallons, p.min_gallon_alert)
            global_warnings.append(
                {
                    "posko_code": p.code,
                    "posko_name": p.name,
                    "type": "GALLON",
                    "status": gallon_status,
                    "current_stock": p.stock_gallons,
                    "min_alert": p.min_gallon_alert,
                    "message": (
                        f"{p.code} ({p.name}): Stok Galon Air {gallon_status} "
                        f"({p.stock_gallons} galon, min {p.min_gallon_alert})"
                    ),
                }
            )

    serialized_active = serialize_posko(active_posko_obj, db=db)

    return {
        "active_posko": serialized_active,
        "total_residents": total_residents,
        "today_meals_distributed": today_meals_distributed,
        "active_posko_today_meals": active_posko_today_meals,
        "today_meal_cost": today_meal_cost,
        "today_gallons_used": today_gallons_used,
        "active_posko_today_gallons": active_posko_today_gallons,
        "today_gallon_cost": today_gallon_cost,
        "today_total_cost": today_total_cost,
        "meal_price": MEAL_PRICE,
        "gallon_price": GALLON_PRICE,
        "max_daily_meals": MAX_DAILY_MEALS,
        "protection_status": {
            "cross_lock_active": True,
            "yolo_camera_online": bool(detector_state.get("yolo_camera_online", False)),
            "active_posko": active_posko_obj.code,
            "person_count": int(detector_state.get("person_count", 0)),
        },
        "global_warnings": global_warnings,
    }


@app.get("/api/logs")
def list_logs(
    posko: str | None = Query(default=None),
    status: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=1000),
    db: Session = Depends(get_db),
) -> list[dict[str, Any]]:
    stmt = select(DistributionLog)
    if posko:
        stmt = stmt.where(DistributionLog.scanner_posko_code == posko)
    if status:
        stmt = stmt.where(DistributionLog.status == status)
    stmt = stmt.order_by(DistributionLog.id.desc()).limit(limit)
    logs = db.scalars(stmt).all()
    return [serialize_log(log) for log in logs]
