# Posko Detector (Modern MySQL Edition) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the complete Posko Detector & Disaster Logistics Distribution system using FastAPI, MySQL (InnoDB with row-level locking), Tailwind CSS + Vanilla ES6 JS, OpenCV LBPH 3-angle face recognition, and Ultralytics YOLO + ByteTrack tent monitoring.

**Architecture:** A FastAPI backend (`app.py`) connected to MySQL via SQLAlchemy 2.0 (`database.py`, `models.py`) serves both the REST API (`/api/...`) and the single-page 5-tab dashboard (`templates/index.html`, `static/js/app.js`, `static/css/style.css`). A background-capable face recognizer (`face_recog.py`) trains an OpenCV LBPH model on 3-angle resident photos in `known_faces/`, while the standalone AI camera worker (`posko_detector.py`) runs YOLO + ByteTrack + LBPH and syncs telemetry and hotkey actions (`s`, `a`, `g`, `p`, `r`) to `POST /api/detector/sync`.

**Tech Stack:** Python 3.11+, FastAPI, Uvicorn, SQLAlchemy 2.0, PyMySQL, MySQL 8.0+/MariaDB (InnoDB), OpenCV Contrib (`opencv-contrib-python`), Ultralytics YOLO (`ultralytics`), `qrcode[pil]`, Pillow, HTML5/Tailwind CSS, `html5-qrcode`, Web Audio API, Pytest.

**Spec:** `docs/superpowers/specs/2026-11-13-posko-detector-mysql-design.md`

## Global Constraints

- Meal cost constant: `MEAL_PRICE = 35000` (Rp 35.000 / porsi).
- Gallon cost constant: `GALLON_PRICE = 22000` (Rp 22.000 / galon).
- Daily meal quota per resident: `MAX_DAILY_MEALS = 3` (resets automatically by local date `claim_date`).
- QR Token format: `QR-<POSKO>-<NIK>`; auto-generated NIK format when empty: `NO-NIK-<8-char-UUID>`.
- Admin endpoint protection: Header `X-Admin-Token` verified by `verify_admin_auth()` against env `ADMIN_TOKEN` (default `"posko-secret-admin-2026"`).
- Anti-race-condition locking: Use SQLAlchemy `.with_for_update()` (`SELECT ... FOR UPDATE` on MySQL InnoDB) inside database transactions when scanning meals.
- Anti-path-traversal: All resident face photo paths must resolve inside `known_faces/` using `Path.resolve().is_relative_to(KNOWN_FACES_DIR)`.
- Anti-XSS: All dynamic user strings rendered in HTML templates/JS must pass through `escapeHTML()`.

## Review Focus

- Path traversal via malicious `nik` or `full_name` during 3-angle face upload: `POST /api/residents` must strip unsafe characters and verify `Path.resolve().is_relative_to(KNOWN_FACES_DIR)` before writing files (pinned in Task 3).
- Cross-Posko duplicate NIK registration: registering an already-registered `nik` under another `posko_code` at `POST /api/residents` must return HTTP 409 with `detail.error_code == "CROSS_POSKO_BLOCKED"` (pinned in Task 3).
- Denied meal claims (`DENIED_WRONG_POSKO`, `DENIED_EXCEEDED`, `DENIED_NO_STOCK`) must still persist a `DistributionLog` row with `cost_rupiah = 0` and must not decrement `posko.stock_meals` (pinned in Task 4).
- Gallon depletion guard: calling `POST /api/poskos/{code}/use-gallon` when `stock_gallons == 0` must return HTTP 400 and never record negative stock or bogus `GALLON_USE` expense (pinned in Task 2).
- XSS payload in resident name or log notes: `escapeHTML()` in `static/js/app.js` must escape `<`, `>`, `&`, `"`, and `'` into safe HTML entities (pinned in Task 6).

---

### Task 1: Database Models, MySQL Auto-Initializer, & Admin Auth Security

**Files:**
- Create: `requirements.txt`
- Create: `database.py`
- Create: `models.py`
- Create: `auth.py`
- Test: `tests/test_models_and_auth.py`

**Interfaces:**
- Consumes: Environment variables `MYSQL_HOST`, `MYSQL_PORT`, `MYSQL_USER`, `MYSQL_PASSWORD`, `MYSQL_DB`, `DATABASE_URL`, `ADMIN_TOKEN`.
- Produces:
  - `database.py`: `engine`, `SessionLocal`, `Base`, `init_db() -> None`, `get_db() -> Generator[Session, None, None]`
  - `models.py`: `Posko`, `Resident`, `DistributionLog`, `MEAL_PRICE = 35000`, `GALLON_PRICE = 22000`, `MAX_DAILY_MEALS = 3`
  - `auth.py`: `ADMIN_TOKEN: str`, `verify_admin_auth(x_admin_token: str | None = Header(default=None)) -> str`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_models_and_auth.py
import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from models import Base, Posko, Resident, DistributionLog, MEAL_PRICE, GALLON_PRICE, MAX_DAILY_MEALS
from auth import verify_admin_auth, ADMIN_TOKEN

def test_constants_and_cascade_delete():
    assert MEAL_PRICE == 35000
    assert GALLON_PRICE == 22000
    assert MAX_DAILY_MEALS == 3

    engine = create_engine("sqlite:///:memory:")
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

def test_verify_admin_auth():
    assert verify_admin_auth(ADMIN_TOKEN) == ADMIN_TOKEN
    with pytest.raises(HTTPException) as exc:
        verify_admin_auth("wrong-token")
    assert exc.value.status_code == 401
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_models_and_auth.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'models'`

- [ ] **Step 3: Implement `requirements.txt`, `database.py`, `models.py`, and `auth.py`**

Create `requirements.txt` with `fastapi`, `uvicorn[standard]`, `sqlalchemy>=2.0`, `pymysql`, `cryptography`, `pydantic>=2.0`, `python-multipart`, `qrcode[pil]`, `pillow`, `opencv-contrib-python`, `ultralytics`, `requests`, `pytest`, `httpx`.
In `database.py`, connect using `DATABASE_URL` if set, otherwise build `mysql+pymysql://{MYSQL_USER}:{MYSQL_PASSWORD}@{MYSQL_HOST}:{MYSQL_PORT}/{MYSQL_DB}?charset=utf8mb4` with `QueuePool(pool_size=10, max_overflow=20, pool_pre_ping=True)`. In `init_db()`, if the dialect is MySQL, execute `CREATE DATABASE IF NOT EXISTS {MYSQL_DB} CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci` first, then run `Base.metadata.create_all(bind=engine)` and seed default poskos (`POSKO-A`, `POSKO-B`) if the `poskos` table is empty.
In `models.py`, define `Posko`, `Resident`, and `DistributionLog` with `__table_args__ = {"mysql_engine": "InnoDB", "mysql_charset": "utf8mb4"}` and foreign key `ON DELETE CASCADE` on `posko_code`.
In `auth.py`, implement `verify_admin_auth(x_admin_token: str | None = Header(default=None)) -> str` raising HTTP 401 if `x_admin_token != ADMIN_TOKEN`.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_models_and_auth.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git init
git add requirements.txt database.py models.py auth.py tests/test_models_and_auth.py
git commit -m "feat: add MySQL database layer, SQLAlchemy models, and admin token auth"
```

---

### Task 2: Posko Management, Stock/Gallon Operations, Logs & 8-Card Dashboard Summary API

**Files:**
- Create: `app.py`
- Test: `tests/test_posko_and_dashboard.py`

**Interfaces:**
- Consumes: `database.get_db`, `models.(Posko, Resident, DistributionLog, MEAL_PRICE, GALLON_PRICE)`, `auth.verify_admin_auth`
- Produces:
  - `GET /api/poskos` -> lists all poskos with meal/gallon stock, alerts, and status badge (`Stok Aman`, `STOK MENIPIS`, `STOK HABIS`)
  - `POST /api/poskos` (Admin Auth) -> creates new posko
  - `DELETE /api/poskos/{code}` (Admin Auth) -> deletes posko and cascades residents/logs
  - `POST /api/poskos/{code}/restock-meals` (Admin Auth) -> adds `+amount` meals and updates `min_stock_alert`
  - `POST /api/poskos/{code}/use-gallon` (Admin Auth) -> decrements `stock_gallons` by 1 and logs `GALLON_USE` with `cost_rupiah = 22000`
  - `POST /api/poskos/{code}/restock-gallons` (Admin Auth) -> adds `+amount` gallons and updates `min_gallon_alert`
  - `GET /api/dashboard/summary?posko={code}` -> returns all 8 metric cards + `global_warnings` list for pulsing banner
  - `GET /api/logs?posko={code}&status={status}` -> returns chronological audit logs

- [ ] **Step 1: Write the failing test**

```python
# tests/test_posko_and_dashboard.py
from fastapi.testclient import TestClient
from auth import ADMIN_TOKEN

def test_posko_lifecycle_and_gallon_guard(client: TestClient):
    headers = {"X-Admin-Token": ADMIN_TOKEN}
    res = client.post("/api/poskos", json={
        "code": "POSKO-UJI",
        "name": "Posko Uji",
        "location": "Kecamatan A",
        "stock_meals": 15,
        "min_stock_alert": 20,
        "stock_gallons": 1,
        "min_gallon_alert": 3,
    }, headers=headers)
    assert res.status_code == 201
    data = res.json()
    assert data["stock_status"] == "STOK MENIPIS"

    # Use 1 gallon -> succeeds and records Rp 22.000
    g1 = client.post("/api/poskos/POSKO-UJI/use-gallon", headers=headers)
    assert g1.status_code == 200
    assert g1.json()["stock_gallons"] == 0

    # Review Focus: Gallon depletion guard when stock_gallons == 0
    g2 = client.post("/api/poskos/POSKO-UJI/use-gallon", headers=headers)
    assert g2.status_code == 400

    # Check 8 metrics & global warning banner
    summary = client.get("/api/dashboard/summary?posko=POSKO-UJI").json()
    assert summary["active_posko"]["stock_meals"] == 15
    assert summary["today_gallons_used"] == 1
    assert summary["today_gallon_cost"] == 22000
    assert summary["today_total_cost"] == 22000
    assert len(summary["global_warnings"]) >= 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_posko_and_dashboard.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app'`

- [ ] **Step 3: Implement Posko, Gallon, Dashboard Summary, and Audit Log endpoints in `app.py` and test fixtures in `tests/conftest.py`**

Create `tests/conftest.py` providing a `client` fixture backed by an isolated test database session.
In `app.py`, implement helper `compute_stock_status(stock: int, min_alert: int) -> str` returning `"STOK HABIS"` when `stock <= 0`, `"STOK MENIPIS"` when `stock <= min_alert`, and `"Stok Aman"` otherwise. Implement `/api/poskos` CRUD, `/restock-meals`, `/use-gallon` (locking the posko row with `.with_for_update()`, rejecting with HTTP 400 if `stock_gallons <= 0`, decrementing by 1, and inserting a `DistributionLog` with `status="GALLON_USE"`, `resident_name="Logistik Galon Air"`, `cost_rupiah=22000`), `/restock-gallons`, `/api/dashboard/summary`, and `/api/logs`.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_posko_and_dashboard.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add app.py tests/conftest.py tests/test_posko_and_dashboard.py
git commit -m "feat: implement posko management, gallon tracking, audit logs, and 8-metric summary API"
```

---

### Task 3: Face Recognition Engine (`face_recog.py`) & Resident 3-Angle Registration + QR Code API

**Files:**
- Create: `face_recog.py`
- Modify: `app.py`
- Test: `tests/test_residents_and_faces.py`

**Interfaces:**
- Consumes: `known_faces/` directory, `models.Resident`, `auth.verify_admin_auth`
- Produces:
  - `face_recog.py`: `FaceRecognitionService` with `save_three_angle_photos(resident_id: int, nik: str, front_bytes: bytes, left_bytes: bytes, right_bytes: bytes) -> dict[str, str]`, `delete_resident_photos(resident_id: int, nik: str) -> None`, `train_model() -> bool`, `train_async() -> None`, `recognize_face(bgr_frame) -> list[dict]`
  - `GET /api/residents?search={q}&posko={code}` -> lists residents with today's meal claim count (`today_meals: 0..3`)
  - `POST /api/residents` -> registers resident (with/without NIK), blocks duplicate NIK across poskos (`CROSS_POSKO_BLOCKED`), saves 3-angle photos, triggers async LBPH training
  - `GET /api/residents/qr/{token}` -> returns PNG image (`image/png`) of the QR code
  - `DELETE /api/residents/{id}` (Admin Auth) -> deletes resident, cleans up `known_faces/` folder, triggers async LBPH retrain

- [ ] **Step 1: Write the failing test**

```python
# tests/test_residents_and_faces.py
import base64
import io
from PIL import Image
from fastapi.testclient import TestClient
from auth import ADMIN_TOKEN

def _make_dummy_jpeg_b64() -> str:
    buf = io.BytesIO()
    Image.new("RGB", (100, 100), color=(128, 128, 128)).save(buf, format="JPEG")
    return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()

def test_register_resident_cross_posko_lockout_and_qr(client: TestClient):
    img_b64 = _make_dummy_jpeg_b64()
    res1 = client.post("/api/residents", json={
        "nik": "32019999",
        "full_name": "Siti Aminah",
        "age": 29,
        "gender": "P",
        "kk_name": "Ahmad",
        "posko_code": "POSKO-A",
        "photo_front": img_b64,
        "photo_left": img_b64,
        "photo_right": img_b64,
    })
    assert res1.status_code == 201
    r1_data = res1.json()
    assert r1_data["qr_token"] == "QR-POSKO-A-32019999"

    # Review Focus: Cross-Posko duplicate NIK must be blocked with CROSS_POSKO_BLOCKED
    res_dup = client.post("/api/residents", json={
        "nik": "32019999",
        "full_name": "Siti Aminah",
        "age": 29,
        "gender": "P",
        "kk_name": "Ahmad",
        "posko_code": "POSKO-B",
        "photo_front": img_b64,
        "photo_left": img_b64,
        "photo_right": img_b64,
    })
    assert res_dup.status_code == 409
    assert res_dup.json()["detail"]["error_code"] == "CROSS_POSKO_BLOCKED"

    # Auto NO-NIK-<UUID> generation and Path Traversal sanitization check
    res_nonik = client.post("/api/residents", json={
        "nik": "",
        "full_name": "../../EvilName",
        "age": 40,
        "gender": "L",
        "kk_name": "Kepala",
        "posko_code": "POSKO-A",
        "photo_front": img_b64,
        "photo_left": img_b64,
        "photo_right": img_b64,
    })
    assert res_nonik.status_code == 201
    assert res_nonik.json()["nik"].startswith("NO-NIK-")

    # On-the-fly QR endpoint returns PNG
    qr_res = client.get(f"/api/residents/qr/{r1_data['qr_token']}")
    assert qr_res.status_code == 200
    assert qr_res.headers["content-type"] == "image/png"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_residents_and_faces.py -v`
Expected: FAIL with `404 Not Found` on `/api/residents`

- [ ] **Step 3: Implement `face_recog.py` and Resident + QR endpoints in `app.py`**

In `face_recog.py`, implement `FaceRecognitionService`:
- Sanitize directory names with `re.sub(r"[^A-Za-z0-9_-]", "_", f"{resident_id}_{nik}")` and verify `target_dir.resolve().is_relative_to(self.base_dir.resolve())`.
- Use `cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_frontalface_default.xml")` and `cv2.face.LBPHFaceRecognizer_create()`.
- Train on all images in `known_faces/<id>_<nik>/*.jpg` using `resident_id` as the integer label, saving the trained model to `known_faces/lbph_model.yml`.
In `app.py`, add `GET /api/residents`, `POST /api/residents` (accepting either JSON with base64 images or multipart form data), `GET /api/residents/qr/{token}`, and `DELETE /api/residents/{id}`.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_residents_and_faces.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add face_recog.py app.py tests/test_residents_and_faces.py
git commit -m "feat: add 3-angle LBPH face recognition service, resident registration, and QR generator"
```

---

### Task 4: Meal QR Scan & Anti-Fraud Multi-Layer Validation (`POST /api/meals/scan`)

**Files:**
- Modify: `app.py`
- Test: `tests/test_meal_scan.py`

**Interfaces:**
- Consumes: `models.(Posko, Resident, DistributionLog, MEAL_PRICE, MAX_DAILY_MEALS)`
- Produces:
  - `POST /api/meals/scan` accepting `{"qr_or_nik": str, "scanner_posko_code": str}` and returning claim result with `meal_sequence` (`1`, `2`, `3`), `max_meals` (`3`), `resident`, `posko_stock_remaining`, and `audio_cue` (`"success"`, `"quota_exceeded"`, `"denied"`).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_meal_scan.py
from fastapi.testclient import TestClient

def test_meal_scan_layers_cross_posko_quota_and_zero_stock(client: TestClient):
    # Register resident in POSKO-A
    res = client.post("/api/residents", json={
        "nik": "32018888",
        "full_name": "Andi Wijaya",
        "age": 31,
        "gender": "L",
        "kk_name": "Andi Wijaya",
        "posko_code": "POSKO-A",
    }).json()
    token = res["qr_token"]

    # 1. Cross-Posko Claim Denied (HTTP 403) when scanning at POSKO-B
    wrong_posko = client.post("/api/meals/scan", json={
        "qr_or_nik": token,
        "scanner_posko_code": "POSKO-B",
    })
    assert wrong_posko.status_code == 403
    assert wrong_posko.json()["status"] == "DENIED_WRONG_POSKO"

    # 2. Valid claims 1/3, 2/3, 3/3 at POSKO-A
    for seq in (1, 2, 3):
        ok = client.post("/api/meals/scan", json={
            "qr_or_nik": token,
            "scanner_posko_code": "POSKO-A",
        })
        assert ok.status_code == 200
        body = ok.json()
        assert body["status"] == "SUCCESS"
        assert body["meal_sequence"] == seq
        assert body["cost_rupiah"] == 35000

    # 3. 4th claim on same day -> DENIED_EXCEEDED (HTTP 429)
    exceeded = client.post("/api/meals/scan", json={
        "qr_or_nik": token,
        "scanner_posko_code": "POSKO-A",
    })
    assert exceeded.status_code == 429
    assert exceeded.json()["status"] == "DENIED_EXCEEDED"

    # Review Focus: Verify denied logs have cost_rupiah == 0
    logs = client.get("/api/logs?posko=POSKO-B&status=DENIED_WRONG_POSKO").json()
    assert len(logs) == 1
    assert logs[0]["cost_rupiah"] == 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_meal_scan.py -v`
Expected: FAIL with `404 Not Found` on `/api/meals/scan`

- [ ] **Step 3: Implement `POST /api/meals/scan` with `.with_for_update()` locking in `app.py`**

Implement `scan_meal_claim(db: Session, qr_or_nik: str, scanner_posko_code: str)` inside `app.py` (reusable by both `POST /api/meals/scan` and the camera `'s'` serve-all action):
- Query `Resident` matching `qr_token == qr_or_nik` or `nik == qr_or_nik` with `.with_for_update()`. Raise 404 if not found.
- Query `Posko` matching `code == scanner_posko_code` with `.with_for_update()`. Raise 404 if not found.
- If `resident.posko_code != scanner_posko_code`: insert `DistributionLog(status="DENIED_WRONG_POSKO", cost_rupiah=0, ...)`, commit, and return HTTP 403 JSON response.
- Count today's `SUCCESS` logs for `resident.id` where `claim_date == date.today()`. If `count >= MAX_DAILY_MEALS`: insert `DistributionLog(status="DENIED_EXCEEDED", cost_rupiah=0, ...)`, commit, and return HTTP 429 JSON response.
- If `posko.stock_meals <= 0`: insert `DistributionLog(status="DENIED_NO_STOCK", cost_rupiah=0, ...)`, commit, and return HTTP 400 JSON response.
- Otherwise decrement `posko.stock_meals -= 1`, insert `DistributionLog(status="SUCCESS", meal_sequence=count + 1, cost_rupiah=MEAL_PRICE, ...)`, commit, and return HTTP 200 JSON response.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_meal_scan.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add app.py tests/test_meal_scan.py
git commit -m "feat: implement meal QR scan with MySQL FOR UPDATE locking and 3x daily quota"
```

---

### Task 5: AI Tent Camera Detector Sync API & YOLO + ByteTrack + LBPH Worker (`posko_detector.py`)

**Files:**
- Create: `posko_detector.py`
- Modify: `app.py`
- Test: `tests/test_detector_sync.py`

**Interfaces:**
- Consumes: `face_recog.FaceRecognitionService`, `auth.verify_admin_auth`, `app.scan_meal_claim`
- Produces:
  - `POST /api/detector/sync` (Admin Auth) -> updates live tent telemetry (`online`, `active_posko`, `person_count`, `detected_residents`) and executes optional `action` (`"serve_all"`, `"restock_20"`, `"use_gallon"`, `"switch_posko"`, `"reload_faces"`), returning updated posko/resident state for bounding-box coloring.
  - `GET /api/detector/status` -> returns live tent camera state for the web dashboard.
  - `posko_detector.py`: `classify_resident_box(resident_data: dict | None, active_posko: str) -> tuple[str, tuple[int, int, int]]` returning status label and BGR color (`Green (0, 200, 0)`, `Red (0, 0, 220)`, `Yellow (0, 215, 255)`), plus main camera loop with YOLO (`final.pt` or fallback `yolo11n.pt`/`yolov8n.pt`) + ByteTrack + hotkeys `s`, `a`, `g`, `p`, `r`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_detector_sync.py
from fastapi.testclient import TestClient
from auth import ADMIN_TOKEN
from posko_detector import classify_resident_box

def test_classify_resident_box_colors():
    # Unregistered -> Yellow
    status, color = classify_resident_box(None, "POSKO-A")
    assert status == "UNREGISTERED"
    assert color == (0, 215, 255)

    # Active posko & quota < 3 -> Green
    status, color = classify_resident_box(
        {"posko_code": "POSKO-A", "today_meals": 1, "full_name": "Budi"}, "POSKO-A"
    )
    assert status == "VALID"
    assert color == (0, 200, 0)

    # Wrong posko -> Red
    status, color = classify_resident_box(
        {"posko_code": "POSKO-B", "today_meals": 0, "full_name": "Budi"}, "POSKO-A"
    )
    assert status == "WRONG_POSKO"
    assert color == (0, 0, 220)

    # Quota 3/3 full -> Red
    status, color = classify_resident_box(
        {"posko_code": "POSKO-A", "today_meals": 3, "full_name": "Budi"}, "POSKO-A"
    )
    assert status == "QUOTA_FULL"
    assert color == (0, 0, 220)

def test_detector_sync_and_quick_actions(client: TestClient):
    headers = {"X-Admin-Token": ADMIN_TOKEN}
    r = client.post("/api/residents", json={
        "nik": "32017777",
        "full_name": "Dewi Sartika",
        "age": 27,
        "gender": "P",
        "kk_name": "Dewi",
        "posko_code": "POSKO-A",
    }).json()

    # Sync camera telemetry and trigger 'serve_all' (hotkey 's')
    sync_res = client.post("/api/detector/sync", json={
        "active_posko": "POSKO-A",
        "person_count": 2,
        "detected_resident_ids": [r["id"]],
        "action": "serve_all",
    }, headers=headers)
    assert sync_res.status_code == 200
    body = sync_res.json()
    assert body["detector_online"] is True
    assert body["action_result"]["served_count"] == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_detector_sync.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'posko_detector'`

- [ ] **Step 3: Implement `/api/detector/sync` & `/api/detector/status` in `app.py` and `posko_detector.py`**

In `app.py`, store live detector state (`last_seen_ts`, `active_posko`, `person_count`, `detected_residents`) and handle quick actions (`serve_all`, `restock_20`, `use_gallon`, `switch_posko`, `reload_faces`) protected by `verify_admin_auth`.
In `posko_detector.py`, implement `classify_resident_box()` and `PoskoTentDetector` class using Ultralytics YOLO (`final.pt` if present, fallback `yolov8n.pt`) with `model.track(frame, persist=True, classes=[0], tracker="bytetrack.yaml")`, OpenCV LBPH recognition via `face_recog.py`, overlay rendering, and keyboard controls (`s`, `a`, `g`, `p`, `r`, `q`).

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_detector_sync.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add app.py posko_detector.py tests/test_detector_sync.py
git commit -m "feat: add AI tent camera sync endpoint and YOLO+ByteTrack+LBPH detector script"
```

---

### Task 6: 5-Tab Web Dashboard Frontend, Live QR Scanner, 3-Angle Webcam, Web Audio Beep, & Print ID Card

**Files:**
- Create: `templates/index.html`
- Create: `static/css/style.css`
- Create: `static/js/app.js`
- Modify: `app.py`
- Test: `tests/test_frontend_and_xss.py`

**Interfaces:**
- Consumes: All `/api/...` endpoints in `app.py`
- Produces:
  - `GET /` in `app.py` serving `templates/index.html` and static assets at `/static`
  - `static/js/app.js` with `escapeHTML(str)`, `playBeep(type)`, `switchPosko(code)`, `switchTab(tabId)`, Live QR scanner (`Html5QrcodeScanner`), 3-angle webcam capture (`front`, `left`, `right`), real-time resident search & printable ID Card modal/window, Posko & Gallon stock controls, and Audit Log status filter.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_frontend_and_xss.py
from pathlib import Path
from fastapi.testclient import TestClient

def test_index_html_and_static_assets_served(client: TestClient):
    res = client.get("/")
    assert res.status_code == 200
    html = res.text
    assert "Scan QR Makanan" in html
    assert "Registrasi Warga Baru" in html
    assert "Cetak Digital ID QR" in html
    assert "Manajemen Stok & Posko" in html
    assert "Log Riwayat Distribusi" in html

    js_res = client.get("/static/js/app.js")
    assert js_res.status_code == 200
    assert "function escapeHTML" in js_res.text
    assert "AudioContext" in js_res.text
    assert "Html5QrcodeScanner" in js_res.text
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_frontend_and_xss.py -v`
Expected: FAIL with `404 Not Found` on `/`

- [ ] **Step 3: Implement `templates/index.html`, `static/css/style.css`, `static/js/app.js`, and mount static/template routes in `app.py`**

In `templates/index.html`, build the complete responsive UI:
- Top **Global Warning Banner** (pulsing red/amber alert when any posko has low/empty meals or gallons).
- **Navbar** with Active Posko Selector dropdown, Admin Token configuration button, and Camera Cross-Lock status pill.
- **8 Main Metric Cards** (Stok Posko Aktif + badge, Total Warga Terdaftar, Porsi Terdistribusi Hari Ini, Biaya Makanan Rp 35.000/porsi, Pengeluaran Galon Rp 22.000/galon, Total Pengeluaran Hari Ini, Stok Galon Air Posko + alert badge, Status Proteksi & Stock YOLO + Cross-Lock) + **Live Camera Tent Monitor** panel.
- **5 Navigation Tabs**:
  - Tab 1: Live Camera QR Scanner (`Html5QrcodeScanner`) + Manual QR/NIK fallback + 3 Meal Dots (`1/3`, `2/3`, `3/3`) + Web Audio API beep indicator.
  - Tab 2: Resident Registration Form (with/without NIK) + 3-Angle Face Capture (Webcam live capture & file upload for Front, Left, Right).
  - Tab 3: Digital ID QR Cards + Real-Time Search + `Print ID Card` window (`@media print`) + Delete Resident button.
  - Tab 4: Posko & Warehouse Logistics Management (Add/Delete Posko, Meal Stock progress bar & Rupiah valuation, Restock Meals modal, Gallon Stock progress bar & Rupiah valuation, quick `-1 Pakai Galon` button, Restock Gallons modal).
  - Tab 5: Chronological Distribution Audit Logs with 5 status filter pills (`SUCCESS`, `GALLON_USE`, `DENIED_WRONG_POSKO`, `DENIED_EXCEEDED`, `DENIED_NO_STOCK`).

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest -v`
Expected: PASS (all test files pass)

- [ ] **Step 5: Commit**

```bash
git add templates/index.html static/css/style.css static/js/app.js app.py tests/test_frontend_and_xss.py
git commit -m "feat: add 5-tab dashboard frontend, QR scanner, 3-angle webcam, audio cues, and print ID card"
```

