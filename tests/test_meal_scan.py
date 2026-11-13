# tests/test_meal_scan.py
from fastapi.testclient import TestClient

from auth import ADMIN_TOKEN
import database


def test_meal_scan_4_layer_validation_and_audit_logs(client: TestClient):
    headers = {"X-Admin-Token": ADMIN_TOKEN}

    # Register resident in POSKO-A (default stock_meals = 150)
    r_res = client.post(
        "/api/residents",
        json={
            "nik": "32018888",
            "full_name": "Dedi Mulyadi",
            "age": 38,
            "gender": "L",
            "kk_name": "Dedi Mulyadi",
            "posko_code": "POSKO-A",
        },
    )
    assert r_res.status_code == 201
    resident = r_res.json()
    qr_token = resident["qr_token"]

    # Layer 1a: Unknown QR / NIK -> 404 NOT_FOUND with audio_cue="denied"
    nf_res = client.post(
        "/api/meals/scan",
        json={"qr_or_nik": "QR-TIDAK-ADA", "scanner_posko_code": "POSKO-A"},
    )
    assert nf_res.status_code == 404
    assert nf_res.json()["status"] == "NOT_FOUND"
    assert nf_res.json()["audio_cue"] == "denied"

    # Layer 1b: Unknown Scanner Posko -> 404 NOT_FOUND with audio_cue="denied"
    nf_posko = client.post(
        "/api/meals/scan",
        json={"qr_or_nik": qr_token, "scanner_posko_code": "POSKO-Z"},
    )
    assert nf_posko.status_code == 404
    assert nf_posko.json()["status"] == "NOT_FOUND"
    assert nf_posko.json()["audio_cue"] == "denied"

    # Layer 2: Cross-Posko protection (resident of POSKO-A scanning at POSKO-B) -> 403 DENIED_WRONG_POSKO
    wrong_posko = client.post(
        "/api/meals/scan",
        json={"qr_or_nik": qr_token, "scanner_posko_code": "POSKO-B"},
    )
    assert wrong_posko.status_code == 403
    wp_data = wrong_posko.json()
    assert wp_data["status"] == "DENIED_WRONG_POSKO"
    assert wp_data["audio_cue"] == "denied"
    assert "POSKO-A" in wp_data["message"]
    assert wp_data["resident"]["nik"] == "32018888"

    # Verify POSKO-B stock_meals is untouched (still 100) and log recorded with cost_rupiah = 0
    poskos_after_wp = {p["code"]: p for p in client.get("/api/poskos").json()}
    assert poskos_after_wp["POSKO-B"]["stock_meals"] == 100
    wp_logs = client.get("/api/logs?posko=POSKO-B&status=DENIED_WRONG_POSKO").json()
    assert len(wp_logs) == 1
    assert wp_logs[0]["cost_rupiah"] == 0
    assert wp_logs[0]["meal_sequence"] is None
    assert wp_logs[0]["resident_posko_code"] == "POSKO-A"
    assert wp_logs[0]["scanner_posko_code"] == "POSKO-B"

    # Layer 3 & 5: Valid claims 1, 2, 3 at POSKO-A (via qr_token and nik)
    for expected_seq, identifier in [(1, qr_token), (2, "32018888"), (3, qr_token)]:
        ok_res = client.post(
            "/api/meals/scan",
            json={"qr_or_nik": identifier, "scanner_posko_code": "POSKO-A"},
        )
        assert ok_res.status_code == 200
        ok_data = ok_res.json()
        assert ok_data["status"] == "SUCCESS"
        assert ok_data["audio_cue"] == "success"
        assert ok_data["meal_sequence"] == expected_seq
        assert ok_data["max_meals"] == 3
        assert ok_data["cost_rupiah"] == 35000
        assert ok_data["posko_stock_remaining"] == 150 - expected_seq
        assert ok_data["resident"]["today_meals"] == expected_seq

    # 4th claim on the same day -> 429 DENIED_EXCEEDED with audio_cue="quota_exceeded"
    exceeded = client.post(
        "/api/meals/scan",
        json={"qr_or_nik": qr_token, "scanner_posko_code": "POSKO-A"},
    )
    assert exceeded.status_code == 429
    ex_data = exceeded.json()
    assert ex_data["status"] == "DENIED_EXCEEDED"
    assert ex_data["audio_cue"] == "quota_exceeded"
    assert ex_data["meal_sequence"] == 3
    assert ex_data["max_meals"] == 3
    assert "3/3" in ex_data["message"]

    # Verify POSKO-A stock_meals is still 147 (not decremented on 4th scan) and log has cost_rupiah = 0
    poskos_after_ex = {p["code"]: p for p in client.get("/api/poskos").json()}
    assert poskos_after_ex["POSKO-A"]["stock_meals"] == 147
    ex_logs = client.get("/api/logs?posko=POSKO-A&status=DENIED_EXCEEDED").json()
    assert len(ex_logs) == 1
    assert ex_logs[0]["cost_rupiah"] == 0
    assert ex_logs[0]["meal_sequence"] == 3

    # Layer 4: Stock availability check (stock_meals <= 0 -> 400 DENIED_NO_STOCK)
    c_posko = client.post(
        "/api/poskos",
        json={
            "code": "POSKO-KOSONG",
            "name": "Posko Stok Habis",
            "location": "Desa C",
            "stock_meals": 0,
            "min_stock_alert": 10,
            "stock_gallons": 5,
            "min_gallon_alert": 2,
        },
        headers=headers,
    )
    assert c_posko.status_code == 201

    r2_res = client.post(
        "/api/residents",
        json={
            "nik": "32017777",
            "full_name": "Warga Posko Kosong",
            "age": 25,
            "gender": "P",
            "kk_name": "Keluarga C",
            "posko_code": "POSKO-KOSONG",
        },
    )
    assert r2_res.status_code == 201
    r2_qr = r2_res.json()["qr_token"]

    no_stock = client.post(
        "/api/meals/scan",
        json={"qr_or_nik": r2_qr, "scanner_posko_code": "POSKO-KOSONG"},
    )
    assert no_stock.status_code == 400
    ns_data = no_stock.json()
    assert ns_data["status"] == "DENIED_NO_STOCK"
    assert ns_data["audio_cue"] == "denied"
    assert "habis" in ns_data["message"].lower()

    # Verify POSKO-KOSONG stock_meals stays 0 and DENIED_NO_STOCK log has cost_rupiah = 0
    poskos_after_ns = {p["code"]: p for p in client.get("/api/poskos").json()}
    assert poskos_after_ns["POSKO-KOSONG"]["stock_meals"] == 0
    ns_logs = client.get("/api/logs?posko=POSKO-KOSONG&status=DENIED_NO_STOCK").json()
    assert len(ns_logs) == 1
    assert ns_logs[0]["cost_rupiah"] == 0
    assert ns_logs[0]["meal_sequence"] is None

    # Verify dashboard summary counts only the 3 SUCCESS claims
    summary = client.get("/api/dashboard/summary?posko=POSKO-A").json()
    assert summary["today_meals_distributed"] == 3
    assert summary["active_posko_today_meals"] == 3
    assert summary["today_meal_cost"] == 3 * 35000


def test_scan_meal_claim_direct_function_signature(client: TestClient):
    from app import scan_meal_claim

    r_res = client.post(
        "/api/residents",
        json={
            "nik": "32016666",
            "full_name": "Direct Caller",
            "age": 31,
            "gender": "L",
            "kk_name": "Direct Caller",
            "posko_code": "POSKO-B",
        },
    )
    assert r_res.status_code == 201
    qr_token = r_res.json()["qr_token"]

    with database.SessionLocal() as db:
        status_code, payload = scan_meal_claim(db, qr_token, "POSKO-B")
        assert status_code == 200
        assert payload["status"] == "SUCCESS"
        assert payload["meal_sequence"] == 1
        assert payload["cost_rupiah"] == 35000
        assert payload["posko_stock_remaining"] == 99
