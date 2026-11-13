# tests/test_residents_and_faces.py
import base64
import io
from pathlib import Path
import numpy as np
import pytest
from PIL import Image
from fastapi.testclient import TestClient

from auth import ADMIN_TOKEN


def _make_dummy_jpeg_b64() -> str:
    buf = io.BytesIO()
    Image.new("RGB", (100, 100), color=(128, 128, 128)).save(buf, format="JPEG")
    return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()


@pytest.fixture(autouse=True)
def isolated_known_faces(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    import app as app_module
    from face_recog import FaceRecognitionService

    temp_service = FaceRecognitionService(base_dir=tmp_path / "known_faces")
    monkeypatch.setattr(app_module, "face_service", temp_service)
    return temp_service


def test_register_resident_cross_posko_lockout_and_qr(client: TestClient):
    img_b64 = _make_dummy_jpeg_b64()
    res1 = client.post(
        "/api/residents",
        json={
            "nik": "32019999",
            "full_name": "Siti Aminah",
            "age": 29,
            "gender": "P",
            "kk_name": "Ahmad",
            "posko_code": "POSKO-A",
            "photo_front": img_b64,
            "photo_left": img_b64,
            "photo_right": img_b64,
        },
    )
    assert res1.status_code == 201
    r1_data = res1.json()
    assert r1_data["qr_token"] == "QR-POSKO-A-32019999"
    assert r1_data["today_meals"] == 0
    assert r1_data["max_meals"] == 3
    assert r1_data["face_front_path"] is not None
    assert r1_data["face_left_path"] is not None
    assert r1_data["face_right_path"] is not None

    # Review Focus: Cross-Posko duplicate NIK must be blocked with CROSS_POSKO_BLOCKED
    res_dup = client.post(
        "/api/residents",
        json={
            "nik": "32019999",
            "full_name": "Siti Aminah",
            "age": 29,
            "gender": "P",
            "kk_name": "Ahmad",
            "posko_code": "POSKO-B",
            "photo_front": img_b64,
            "photo_left": img_b64,
            "photo_right": img_b64,
        },
    )
    assert res_dup.status_code == 409
    assert res_dup.json()["detail"]["error_code"] == "CROSS_POSKO_BLOCKED"

    # Auto NO-NIK-<UUID> generation and Path Traversal sanitization check
    res_nonik = client.post(
        "/api/residents",
        json={
            "nik": "",
            "full_name": "../../EvilName",
            "age": 40,
            "gender": "L",
            "kk_name": "Kepala",
            "posko_code": "POSKO-A",
            "photo_front": img_b64,
            "photo_left": img_b64,
            "photo_right": img_b64,
        },
    )
    assert res_nonik.status_code == 201
    assert res_nonik.json()["nik"].startswith("NO-NIK-")
    assert len(res_nonik.json()["nik"]) == len("NO-NIK-") + 8

    # On-the-fly QR endpoint returns PNG
    qr_res = client.get(f"/api/residents/qr/{r1_data['qr_token']}")
    assert qr_res.status_code == 200
    assert qr_res.headers["content-type"] == "image/png"
    assert qr_res.content.startswith(b"\x89PNG")

    # Serving saved face photo safely via /known_faces/...
    photo_res = client.get(r1_data["face_front_path"])
    assert photo_res.status_code == 200
    assert photo_res.headers["content-type"] == "image/jpeg"

    # Multipart form-data registration also works
    buf = io.BytesIO()
    Image.new("RGB", (80, 80), color=(200, 100, 50)).save(buf, format="JPEG")
    mp_res = client.post(
        "/api/residents",
        data={
            "nik": "32015555",
            "full_name": "Agus Salim",
            "age": "52",
            "gender": "L",
            "kk_name": "Agus Salim",
            "posko_code": "POSKO-B",
        },
        files={
            "photo_front": ("front.jpg", buf.getvalue(), "image/jpeg"),
        },
    )
    assert mp_res.status_code == 201
    assert mp_res.json()["qr_token"] == "QR-POSKO-B-32015555"
    assert mp_res.json()["face_front_path"] is not None


def test_residents_list_search_delete_and_posko_cascade_photos(
    client: TestClient, isolated_known_faces
):
    img_b64 = _make_dummy_jpeg_b64()
    headers = {"X-Admin-Token": ADMIN_TOKEN}

    r1 = client.post(
        "/api/residents",
        json={
            "nik": "32010101",
            "full_name": "Budi Hartono",
            "age": 45,
            "gender": "L",
            "kk_name": "Budi Hartono",
            "posko_code": "POSKO-A",
            "photo_front": img_b64,
            "photo_left": img_b64,
            "photo_right": img_b64,
        },
    ).json()

    r2 = client.post(
        "/api/residents",
        json={
            "nik": "32010202",
            "full_name": "Rina Nose",
            "age": 30,
            "gender": "P",
            "kk_name": "Rina",
            "posko_code": "POSKO-B",
            "photo_front": img_b64,
        },
    ).json()

    # List all residents
    all_res = client.get("/api/residents").json()
    assert len(all_res) == 2

    # Filter by posko
    posko_a_res = client.get("/api/residents?posko=POSKO-A").json()
    assert len(posko_a_res) == 1
    assert posko_a_res[0]["nik"] == "32010101"

    # Search by name or NIK
    search_res = client.get("/api/residents?search=Rina").json()
    assert len(search_res) == 1
    assert search_res[0]["nik"] == "32010202"

    # Delete resident requires admin auth
    unauth_del = client.delete(f"/api/residents/{r1['id']}")
    assert unauth_del.status_code == 401

    # Authorized delete removes DB record and photo folder
    r1_dir = isolated_known_faces.get_resident_dir(r1["id"], r1["nik"])
    assert r1_dir.exists()
    del_res = client.delete(f"/api/residents/{r1['id']}", headers=headers)
    assert del_res.status_code == 200
    assert not r1_dir.exists()

    # Deleting posko cleans up resident photo directories for that posko
    r2_dir = isolated_known_faces.get_resident_dir(r2["id"], r2["nik"])
    assert r2_dir.exists()
    del_posko = client.delete("/api/poskos/posko-b", headers=headers)
    assert del_posko.status_code == 200
    assert not r2_dir.exists()


def test_face_recognition_service_anti_traversal_and_training(tmp_path: Path):
    from face_recog import FaceRecognitionService

    svc = FaceRecognitionService(base_dir=tmp_path / "known_faces")
    raw_b64 = _make_dummy_jpeg_b64()

    # Path traversal in NIK is sanitized and stays inside base_dir
    paths = svc.save_three_angle_photos(
        resident_id=99,
        nik="../../../etc/passwd",
        front_data=raw_b64,
        left_data=raw_b64,
        right_data=raw_b64,
    )
    for key in ("front", "left", "right"):
        assert paths[key] is not None
        saved_file = svc.base_dir / Path(paths[key]).parent.name / f"{key}.jpg"
        assert saved_file.resolve().is_relative_to(svc.base_dir.resolve())
        assert saved_file.exists()

    # Explicit traversal check raises ValueError if outside base_dir
    with pytest.raises(ValueError):
        svc._ensure_safe_path(tmp_path / "outside_dir" / "evil.jpg")

    # Train and run recognition on a synthetic frame without errors
    trained = svc.train_model()
    assert isinstance(trained, bool)
    assert (svc.base_dir / "lbph_model.yml").exists()

    dummy_frame = np.zeros((200, 200, 3), dtype=np.uint8)
    detections = svc.recognize_faces(dummy_frame)
    assert isinstance(detections, list)
    assert svc.recognize_face(dummy_frame) == detections

    # Delete resident photos cleans directory
    svc.delete_resident_photos(99, "../../../etc/passwd")
    assert not (svc.base_dir / Path(paths["front"]).parent.name).exists()


def test_create_resident_validation_and_invalid_base64_handling(
    client: TestClient, isolated_known_faces
):
    from face_recog import FaceRecognitionService

    # Direct unit check on _decode_image_bytes invalid base64
    with pytest.raises(ValueError, match="Invalid base64 image data"):
        FaceRecognitionService._decode_image_bytes("not-valid-base64!!!")

    with pytest.raises(ValueError, match="Invalid base64 image data"):
        FaceRecognitionService._decode_image_bytes("data:image/jpeg;base64,abc")

    # 1. JSON branch Pydantic ValidationError -> 422
    bad_json_age = client.post(
        "/api/residents",
        json={
            "nik": "32014444",
            "full_name": "Warga Invalid Umur",
            "age": 200,
            "gender": "L",
            "posko_code": "POSKO-A",
        },
    )
    assert bad_json_age.status_code == 422

    bad_json_name = client.post(
        "/api/residents",
        json={
            "nik": "32014445",
            "full_name": "",
            "age": 25,
            "gender": "L",
            "posko_code": "POSKO-A",
        },
    )
    assert bad_json_name.status_code == 422

    # 2. Multipart form branch age validation (0 <= age <= 150) -> 422
    for invalid_age in ("-1", "151", "bukan-angka"):
        mp_bad_age = client.post(
            "/api/residents",
            data={
                "nik": "32014446",
                "full_name": "Warga Form Invalid",
                "age": invalid_age,
                "gender": "P",
                "posko_code": "POSKO-A",
            },
        )
        assert mp_bad_age.status_code == 422
        assert mp_bad_age.json()["detail"] == "Umur harus antara 0 dan 150"

    # 3. Invalid base64 photo in POST /api/residents -> 400 + rollback + cleanup partial files
    valid_b64 = _make_dummy_jpeg_b64()
    bad_photo_res = client.post(
        "/api/residents",
        json={
            "nik": "32019900",
            "full_name": "Warga Base64 Rusak",
            "age": 33,
            "gender": "L",
            "posko_code": "POSKO-A",
            "photo_front": valid_b64,
            "photo_left": "data:image/jpeg;base64,!!!corrupted_base64!!!",
        },
    )
    assert bad_photo_res.status_code == 400
    assert "Invalid base64 image data" in bad_photo_res.json()["detail"]

    # Ensure DB rolled back and no partial resident or directory remains
    residents = client.get("/api/residents?search=32019900").json()
    assert residents == []
    leftover_dirs = [
        d for d in isolated_known_faces.base_dir.iterdir() if d.is_dir() and "32019900" in d.name
    ]
    assert leftover_dirs == []
