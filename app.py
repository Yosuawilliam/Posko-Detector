import io
import uuid
from contextlib import asynccontextmanager
from datetime import date, datetime, timezone
from typing import Any

import qrcode
from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response, status
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from auth import verify_admin_auth
from database import get_db, init_db
from face_recog import FaceRecognitionService
from models import (
    GALLON_PRICE,
    MAX_DAILY_MEALS,
    MEAL_PRICE,
    DistributionLog,
    Posko,
    Resident,
)
from posko_detector import classify_resident_box

face_service = FaceRecognitionService()

DEFAULT_DETECTOR_STATE: dict[str, Any] = {
    "online": False,
    "yolo_camera_online": False,
    "active_posko": "POSKO-A",
    "person_count": 0,
    "detected_residents": [],
    "recognized_residents": [],
    "last_sync": None,
}

detector_state: dict[str, Any] = dict(DEFAULT_DETECTOR_STATE)


def reset_detector_state() -> None:
    detector_state.clear()
    detector_state.update(DEFAULT_DETECTOR_STATE)
    detector_state["detected_residents"] = []
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


class ResidentCreateRequest(BaseModel):
    nik: str | None = Field(default="")
    full_name: str = Field(..., min_length=1, max_length=150)
    age: int = Field(..., ge=0, le=150)
    gender: str = Field(..., min_length=1, max_length=4)
    kk_name: str | None = Field(default=None, max_length=150)
    posko_code: str = Field(..., min_length=1, max_length=32)
    photo_front: str | None = Field(default=None)
    photo_left: str | None = Field(default=None)
    photo_right: str | None = Field(default=None)


class MealScanRequest(BaseModel):
    qr_or_nik: str = Field(..., min_length=1, max_length=128)
    scanner_posko_code: str = Field(..., min_length=1, max_length=32)


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


def serialize_resident(resident: Resident, today_meals: int = 0) -> dict[str, Any]:
    return {
        "id": resident.id,
        "nik": resident.nik,
        "qr_token": resident.qr_token,
        "full_name": resident.full_name,
        "age": resident.age,
        "gender": resident.gender,
        "kk_name": resident.kk_name,
        "posko_code": resident.posko_code,
        "face_front_path": resident.face_front_path,
        "face_left_path": resident.face_left_path,
        "face_right_path": resident.face_right_path,
        "today_meals": today_meals,
        "max_meals": MAX_DAILY_MEALS,
        "qr_url": f"/api/residents/qr/{resident.qr_token}",
        "created_at": resident.created_at.isoformat() if resident.created_at else None,
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
    clean_code = code.strip().upper()
    posko = db.scalar(select(Posko).where(Posko.code == clean_code))
    if posko is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Posko '{clean_code}' tidak ditemukan",
        )
    residents_to_delete = list(posko.residents or [])
    for res in residents_to_delete:
        face_service.delete_resident_photos(res.id, res.nik)
    db.delete(posko)
    db.commit()
    if residents_to_delete:
        face_service.train_async()
    return {"status": "deleted", "code": clean_code}


@app.post("/api/poskos/{code}/restock-meals")
def restock_meals(
    code: str,
    payload: RestockMealsRequest,
    db: Session = Depends(get_db),
    _admin: str = Depends(verify_admin_auth),
) -> dict[str, Any]:
    clean_code = code.strip().upper()
    posko = db.scalar(select(Posko).where(Posko.code == clean_code).with_for_update())
    if posko is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Posko '{clean_code}' tidak ditemukan",
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
    clean_code = code.strip().upper()
    posko = db.scalar(select(Posko).where(Posko.code == clean_code).with_for_update())
    if posko is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Posko '{clean_code}' tidak ditemukan",
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
    clean_code = code.strip().upper()
    posko = db.scalar(select(Posko).where(Posko.code == clean_code).with_for_update())
    if posko is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Posko '{clean_code}' tidak ditemukan",
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

    clean_posko = posko.strip().upper() if posko and posko.strip() else None
    target_code = clean_posko or detector_state.get("active_posko") or poskos[0].code
    active_posko_obj = next((p for p in poskos if p.code == target_code), None)
    if active_posko_obj is None:
        if clean_posko is not None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Posko '{clean_posko}' tidak ditemukan",
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
        stmt = stmt.where(DistributionLog.scanner_posko_code == posko.strip().upper())
    if status:
        stmt = stmt.where(DistributionLog.status == status.strip().upper())
    stmt = stmt.order_by(DistributionLog.id.desc()).limit(limit)
    logs = db.scalars(stmt).all()
    return [serialize_log(log) for log in logs]


@app.get("/api/residents")
def list_residents(
    search: str | None = Query(default=None),
    posko: str | None = Query(default=None),
    db: Session = Depends(get_db),
) -> list[dict[str, Any]]:
    stmt = select(Resident)
    if posko and posko.strip():
        stmt = stmt.where(Resident.posko_code == posko.strip().upper())
    if search and search.strip():
        q = f"%{search.strip()}%"
        stmt = stmt.where(
            or_(
                Resident.full_name.ilike(q),
                Resident.nik.ilike(q),
                Resident.qr_token.ilike(q),
                Resident.kk_name.ilike(q),
            )
        )
    stmt = stmt.order_by(Resident.id.desc())
    residents = db.scalars(stmt).all()

    today = date.today()
    meals_rows = db.execute(
        select(DistributionLog.resident_id, func.count(DistributionLog.id))
        .where(
            DistributionLog.claim_date == today,
            DistributionLog.status == "SUCCESS",
            DistributionLog.resident_id.is_not(None),
        )
        .group_by(DistributionLog.resident_id)
    ).all()
    meals_map = {res_id: count for res_id, count in meals_rows}

    return [
        serialize_resident(r, today_meals=meals_map.get(r.id, 0))
        for r in residents
    ]


async def _extract_photo_field(val: Any) -> bytes | str | None:
    if val is None:
        return None
    if hasattr(val, "read"):
        data = await val.read()
        return data if data else None
    if isinstance(val, (bytes, str)):
        return val
    return None


@app.post("/api/residents", status_code=status.HTTP_201_CREATED)
async def create_resident(
    request: Request,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    content_type = request.headers.get("content-type", "").lower()
    photo_front: bytes | str | None = None
    photo_left: bytes | str | None = None
    photo_right: bytes | str | None = None

    if "application/json" in content_type:
        body = await request.json()
        payload = ResidentCreateRequest(**body)
        raw_nik = payload.nik or ""
        full_name = payload.full_name.strip()
        age = payload.age
        gender = payload.gender.strip().upper()
        kk_name = payload.kk_name.strip() if payload.kk_name else None
        posko_code = payload.posko_code.strip().upper()
        photo_front = payload.photo_front
        photo_left = payload.photo_left
        photo_right = payload.photo_right
    else:
        form = await request.form()
        raw_nik = str(form.get("nik") or "")
        full_name = str(form.get("full_name") or "").strip()
        if not full_name:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Nama lengkap warga wajib diisi",
            )
        try:
            age = int(form.get("age", 0))
        except (TypeError, ValueError) as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Umur warga harus berupa angka",
            ) from exc
        gender = str(form.get("gender") or "").strip().upper()
        kk_raw = form.get("kk_name")
        kk_name = str(kk_raw).strip() if kk_raw else None
        posko_code = str(form.get("posko_code") or "").strip().upper()
        photo_front = await _extract_photo_field(form.get("photo_front"))
        photo_left = await _extract_photo_field(form.get("photo_left"))
        photo_right = await _extract_photo_field(form.get("photo_right"))

    if gender not in {"L", "P"}:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Jenis kelamin harus 'L' atau 'P'",
        )

    posko = db.scalar(select(Posko).where(Posko.code == posko_code))
    if posko is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Posko '{posko_code}' tidak ditemukan",
        )

    clean_nik = raw_nik.strip()
    if not clean_nik:
        while True:
            candidate = f"NO-NIK-{uuid.uuid4().hex[:8].upper()}"
            if db.scalar(select(Resident).where(Resident.nik == candidate)) is None:
                clean_nik = candidate
                break
    else:
        existing = db.scalar(select(Resident).where(Resident.nik == clean_nik))
        if existing is not None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "error_code": "CROSS_POSKO_BLOCKED",
                    "message": (
                        f"Cross-Posko Lockout: NIK '{clean_nik}' sudah terdaftar atas nama "
                        f"'{existing.full_name}' di {existing.posko_code}"
                    ),
                    "existing_posko": existing.posko_code,
                },
            )

    qr_token = f"QR-{posko.code}-{clean_nik}"
    resident = Resident(
        nik=clean_nik,
        qr_token=qr_token,
        full_name=full_name,
        age=age,
        gender=gender,
        kk_name=kk_name,
        posko_code=posko.code,
    )
    db.add(resident)
    db.flush()

    saved_paths = face_service.save_three_angle_photos(
        resident_id=resident.id,
        nik=clean_nik,
        front_data=photo_front,
        left_data=photo_left,
        right_data=photo_right,
    )
    resident.face_front_path = saved_paths["front"]
    resident.face_left_path = saved_paths["left"]
    resident.face_right_path = saved_paths["right"]

    db.commit()
    db.refresh(resident)

    if any(saved_paths.values()):
        face_service.train_async()

    return serialize_resident(resident, today_meals=0)


@app.get("/api/residents/qr/{token}")
def get_resident_qr(token: str) -> Response:
    clean_token = token.strip()
    if not clean_token:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Token QR tidak valid",
        )
    qr = qrcode.QRCode(
        version=1,
        error_correction=qrcode.constants.ERROR_CORRECT_M,
        box_size=8,
        border=2,
    )
    qr.add_data(clean_token)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return Response(content=buf.getvalue(), media_type="image/png")


@app.delete("/api/residents/{resident_id}")
def delete_resident(
    resident_id: int,
    db: Session = Depends(get_db),
    _admin: str = Depends(verify_admin_auth),
) -> dict[str, Any]:
    resident = db.scalar(select(Resident).where(Resident.id == resident_id))
    if resident is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Warga dengan ID '{resident_id}' tidak ditemukan",
        )
    res_id = resident.id
    res_nik = resident.nik
    db.delete(resident)
    db.commit()

    face_service.delete_resident_photos(res_id, res_nik)
    face_service.train_async()
    return {"status": "deleted", "id": res_id, "nik": res_nik}


@app.get("/known_faces/{folder}/{filename}")
def get_known_face_photo(folder: str, filename: str) -> FileResponse:
    try:
        safe_path = face_service._ensure_safe_path(
            face_service.base_dir / folder / filename
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Path foto tidak valid",
        ) from exc

    if (
        not safe_path.exists()
        or not safe_path.is_file()
        or safe_path.suffix.lower() not in {".jpg", ".jpeg", ".png"}
    ):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Foto wajah tidak ditemukan",
        )
    return FileResponse(safe_path, media_type="image/jpeg")


def scan_meal_claim(
    db: Session,
    qr_or_nik: str,
    scanner_posko_code: str,
) -> tuple[int, dict[str, Any]]:
    clean_qr_or_nik = qr_or_nik.strip()
    clean_posko_code = scanner_posko_code.strip().upper()

    resident = db.scalar(
        select(Resident)
        .where(
            or_(
                Resident.qr_token == clean_qr_or_nik,
                Resident.nik == clean_qr_or_nik,
            )
        )
        .with_for_update()
    )
    if resident is None:
        return (
            status.HTTP_404_NOT_FOUND,
            {
                "status": "NOT_FOUND",
                "audio_cue": "denied",
                "message": f"Warga dengan QR/NIK '{clean_qr_or_nik}' tidak ditemukan",
            },
        )

    posko = db.scalar(
        select(Posko).where(Posko.code == clean_posko_code).with_for_update()
    )
    if posko is None:
        return (
            status.HTTP_404_NOT_FOUND,
            {
                "status": "NOT_FOUND",
                "audio_cue": "denied",
                "message": f"Posko '{clean_posko_code}' tidak ditemukan",
            },
        )

    today = date.today()
    count = (
        db.scalar(
            select(func.count(DistributionLog.id)).where(
                DistributionLog.resident_id == resident.id,
                DistributionLog.claim_date == today,
                DistributionLog.status == "SUCCESS",
            )
        )
        or 0
    )

    if resident.posko_code != posko.code:
        msg = f"Cross-Posko Claim Denied: Warga terdaftar di {resident.posko_code}"
        log_entry = DistributionLog(
            claim_date=today,
            resident_id=resident.id,
            resident_name=resident.full_name,
            resident_nik=resident.nik,
            scanner_posko_code=posko.code,
            resident_posko_code=resident.posko_code,
            status="DENIED_WRONG_POSKO",
            meal_sequence=None,
            cost_rupiah=0,
            notes=msg,
        )
        db.add(log_entry)
        db.commit()
        return (
            status.HTTP_403_FORBIDDEN,
            {
                "status": "DENIED_WRONG_POSKO",
                "audio_cue": "denied",
                "message": msg,
                "resident": serialize_resident(resident, today_meals=count),
            },
        )

    if count >= MAX_DAILY_MEALS:
        msg = "Kuota harian 3/3 porsi sudah habis"
        log_entry = DistributionLog(
            claim_date=today,
            resident_id=resident.id,
            resident_name=resident.full_name,
            resident_nik=resident.nik,
            scanner_posko_code=posko.code,
            resident_posko_code=resident.posko_code,
            status="DENIED_EXCEEDED",
            meal_sequence=count,
            cost_rupiah=0,
            notes=msg,
        )
        db.add(log_entry)
        db.commit()
        return (
            status.HTTP_429_TOO_MANY_REQUESTS,
            {
                "status": "DENIED_EXCEEDED",
                "audio_cue": "quota_exceeded",
                "meal_sequence": count,
                "max_meals": MAX_DAILY_MEALS,
                "message": msg,
                "resident": serialize_resident(resident, today_meals=count),
            },
        )

    if posko.stock_meals <= 0:
        msg = "Stok porsi makanan di posko ini habis (0)"
        log_entry = DistributionLog(
            claim_date=today,
            resident_id=resident.id,
            resident_name=resident.full_name,
            resident_nik=resident.nik,
            scanner_posko_code=posko.code,
            resident_posko_code=resident.posko_code,
            status="DENIED_NO_STOCK",
            meal_sequence=None,
            cost_rupiah=0,
            notes=msg,
        )
        db.add(log_entry)
        db.commit()
        return (
            status.HTTP_400_BAD_REQUEST,
            {
                "status": "DENIED_NO_STOCK",
                "audio_cue": "denied",
                "message": msg,
                "resident": serialize_resident(resident, today_meals=count),
            },
        )

    posko.stock_meals -= 1
    seq = count + 1
    msg = f"Klaim makan ke-{seq}/{MAX_DAILY_MEALS} berhasil"
    log_entry = DistributionLog(
        claim_date=today,
        resident_id=resident.id,
        resident_name=resident.full_name,
        resident_nik=resident.nik,
        scanner_posko_code=posko.code,
        resident_posko_code=resident.posko_code,
        status="SUCCESS",
        meal_sequence=seq,
        cost_rupiah=MEAL_PRICE,
        notes=msg,
    )
    db.add(log_entry)
    db.commit()
    db.refresh(posko)
    return (
        status.HTTP_200_OK,
        {
            "status": "SUCCESS",
            "audio_cue": "success",
            "meal_sequence": seq,
            "max_meals": MAX_DAILY_MEALS,
            "cost_rupiah": MEAL_PRICE,
            "posko_stock_remaining": posko.stock_meals,
            "message": msg,
            "resident": serialize_resident(resident, today_meals=seq),
        },
    )


@app.post("/api/meals/scan")
def scan_meals_endpoint(
    payload: MealScanRequest,
    db: Session = Depends(get_db),
) -> JSONResponse:
    http_status, body = scan_meal_claim(
        db=db,
        qr_or_nik=payload.qr_or_nik,
        scanner_posko_code=payload.scanner_posko_code,
    )
    return JSONResponse(status_code=http_status, content=body)


class DetectorSyncRequest(BaseModel):
    active_posko: str = Field(default="POSKO-A", min_length=1, max_length=32)
    person_count: int = Field(default=0, ge=0)
    detected_resident_ids: list[int] = Field(default_factory=list)
    action: str | None = Field(default=None)


@app.get("/api/detector/status")
def get_detector_status() -> dict[str, Any]:
    return {
        "online": bool(detector_state.get("online", False)),
        "yolo_camera_online": bool(detector_state.get("yolo_camera_online", False)),
        "active_posko": detector_state.get("active_posko", "POSKO-A"),
        "person_count": int(detector_state.get("person_count", 0)),
        "detected_residents": list(detector_state.get("detected_residents", [])),
        "recognized_residents": list(detector_state.get("recognized_residents", [])),
        "last_sync": detector_state.get("last_sync"),
    }


@app.post("/api/detector/sync")
def sync_detector(
    payload: DetectorSyncRequest,
    db: Session = Depends(get_db),
    _admin: str = Depends(verify_admin_auth),
) -> dict[str, Any]:
    active_posko = payload.active_posko.strip().upper()
    poskos = db.scalars(select(Posko).order_by(Posko.code.asc())).all()
    if poskos and not any(p.code == active_posko for p in poskos):
        active_posko = poskos[0].code

    today = date.today()
    unique_ids: list[int] = []
    for raw_id in payload.detected_resident_ids:
        rid = int(raw_id)
        if rid not in unique_ids:
            unique_ids.append(rid)

    action_result: dict[str, Any] | None = None
    action_name = payload.action.strip().lower() if payload.action else None

    if action_name == "switch_posko":
        codes = [p.code for p in poskos]
        if codes:
            curr_idx = codes.index(active_posko) if active_posko in codes else 0
            active_posko = codes[(curr_idx + 1) % len(codes)]
        action_result = {
            "action": "switch_posko",
            "active_posko": active_posko,
        }

    elif action_name == "serve_all":
        served_count = 0
        results: list[dict[str, Any]] = []
        for rid in unique_ids:
            resident = db.scalar(select(Resident).where(Resident.id == rid))
            if resident is None or resident.posko_code != active_posko:
                continue
            current_meals = (
                db.scalar(
                    select(func.count(DistributionLog.id)).where(
                        DistributionLog.resident_id == resident.id,
                        DistributionLog.claim_date == today,
                        DistributionLog.status == "SUCCESS",
                    )
                )
                or 0
            )
            if current_meals < MAX_DAILY_MEALS:
                http_code, claim_body = scan_meal_claim(
                    db=db,
                    qr_or_nik=resident.qr_token,
                    scanner_posko_code=active_posko,
                )
                results.append(claim_body)
                if http_code == status.HTTP_200_OK and claim_body.get("status") == "SUCCESS":
                    served_count += 1
        action_result = {
            "action": "serve_all",
            "served_count": served_count,
            "results": results,
        }

    elif action_name == "restock_20":
        posko_locked = db.scalar(
            select(Posko).where(Posko.code == active_posko).with_for_update()
        )
        if posko_locked is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Posko '{active_posko}' tidak ditemukan",
            )
        posko_locked.stock_meals += 20
        db.commit()
        db.refresh(posko_locked)
        action_result = {
            "action": "restock_20",
            "stock_meals": posko_locked.stock_meals,
        }

    elif action_name == "use_gallon":
        posko_locked = db.scalar(
            select(Posko).where(Posko.code == active_posko).with_for_update()
        )
        if posko_locked is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Posko '{active_posko}' tidak ditemukan",
            )
        if posko_locked.stock_gallons > 0:
            posko_locked.stock_gallons -= 1
            log_entry = DistributionLog(
                claim_date=today,
                resident_id=None,
                resident_name="Logistik Galon Air",
                resident_nik=None,
                scanner_posko_code=posko_locked.code,
                resident_posko_code=posko_locked.code,
                status="GALLON_USE",
                meal_sequence=None,
                cost_rupiah=GALLON_PRICE,
                notes="Pemakaian 1 galon air",
            )
            db.add(log_entry)
            db.commit()
            db.refresh(posko_locked)
        action_result = {
            "action": "use_gallon",
            "stock_gallons": posko_locked.stock_gallons,
        }

    elif action_name == "reload_faces":
        trained = bool(face_service.train_model())
        action_result = {
            "action": "reload_faces",
            "trained": trained,
        }

    detected_residents: list[dict[str, Any]] = []
    if unique_ids:
        res_rows = db.scalars(
            select(Resident).where(Resident.id.in_(unique_ids))
        ).all()
        res_by_id = {r.id: r for r in res_rows}
        meals_rows = db.execute(
            select(DistributionLog.resident_id, func.count(DistributionLog.id))
            .where(
                DistributionLog.resident_id.in_(unique_ids),
                DistributionLog.claim_date == today,
                DistributionLog.status == "SUCCESS",
            )
            .group_by(DistributionLog.resident_id)
        ).all()
        meals_map = {res_id: cnt for res_id, cnt in meals_rows}

        for rid in unique_ids:
            resident = res_by_id.get(rid)
            if resident is None:
                continue
            today_meals = meals_map.get(resident.id, 0)
            enriched = serialize_resident(resident, today_meals=today_meals)
            box_status, _color = classify_resident_box(enriched, active_posko)
            enriched["box_status"] = box_status
            detected_residents.append(enriched)

    active_posko_obj = db.scalar(select(Posko).where(Posko.code == active_posko))
    posko_summary = (
        serialize_posko(active_posko_obj, db=db) if active_posko_obj is not None else {}
    )

    now_iso = datetime.now(timezone.utc).isoformat()
    detector_state["online"] = True
    detector_state["yolo_camera_online"] = True
    detector_state["active_posko"] = active_posko
    detector_state["person_count"] = int(payload.person_count)
    detector_state["detected_residents"] = detected_residents
    detector_state["recognized_residents"] = detected_residents
    detector_state["last_sync"] = now_iso

    return {
        "detector_online": True,
        "online": True,
        "active_posko": active_posko,
        "person_count": int(payload.person_count),
        "detected_residents": detected_residents,
        "action_result": action_result,
        "posko_summary": posko_summary,
        "last_sync": now_iso,
    }


