# tests/test_posko_and_dashboard.py
from fastapi.testclient import TestClient
from auth import ADMIN_TOKEN


def test_posko_lifecycle_and_gallon_guard(client: TestClient):
    headers = {"X-Admin-Token": ADMIN_TOKEN}
    res = client.post(
        "/api/poskos",
        json={
            "code": "POSKO-UJI",
            "name": "Posko Uji",
            "location": "Kecamatan A",
            "stock_meals": 15,
            "min_stock_alert": 20,
            "stock_gallons": 1,
            "min_gallon_alert": 3,
        },
        headers=headers,
    )
    assert res.status_code == 201
    data = res.json()
    assert data["stock_status"] == "STOK MENIPIS"
    assert data["meals_value_rupiah"] == 15 * 35000
    assert data["gallon_status"] == "STOK MENIPIS"
    assert data["gallons_value_rupiah"] == 1 * 22000
    assert data["resident_count"] == 0

    # Use 1 gallon -> succeeds and records Rp 22.000
    g1 = client.post("/api/poskos/POSKO-UJI/use-gallon", headers=headers)
    assert g1.status_code == 200
    assert g1.json()["stock_gallons"] == 0
    assert g1.json()["gallon_status"] == "STOK HABIS"

    # Review Focus: Gallon depletion guard when stock_gallons == 0
    g2 = client.post("/api/poskos/POSKO-UJI/use-gallon", headers=headers)
    assert g2.status_code == 400

    # Check 8 metrics & global warning banner
    summary = client.get("/api/dashboard/summary?posko=POSKO-UJI").json()
    assert summary["active_posko"]["stock_meals"] == 15
    assert summary["active_posko"]["stock_gallons"] == 0
    assert summary["total_residents"] == 0
    assert summary["today_meals_distributed"] == 0
    assert summary["active_posko_today_meals"] == 0
    assert summary["today_meal_cost"] == 0
    assert summary["today_gallons_used"] == 1
    assert summary["today_gallon_cost"] == 22000
    assert summary["today_total_cost"] == 22000
    assert summary["protection_status"]["cross_lock_active"] is True
    assert "yolo_camera_online" in summary["protection_status"]
    assert "person_count" in summary["protection_status"]
    assert len(summary["global_warnings"]) >= 1

    # Verify audit logs recorded exactly 1 GALLON_USE log (no bogus second log)
    logs_res = client.get("/api/logs?posko=POSKO-UJI&status=GALLON_USE")
    assert logs_res.status_code == 200
    logs = logs_res.json()
    assert len(logs) == 1
    assert logs[0]["resident_name"] == "Logistik Galon Air"
    assert logs[0]["cost_rupiah"] == 22000
    assert logs[0]["scanner_posko_code"] == "POSKO-UJI"


def test_posko_crud_restock_and_auth(client: TestClient):
    headers = {"X-Admin-Token": ADMIN_TOKEN}

    # List seeded poskos
    list_res = client.get("/api/poskos")
    assert list_res.status_code == 200
    poskos = list_res.json()
    assert len(poskos) == 2
    codes = {p["code"] for p in poskos}
    assert codes == {"POSKO-A", "POSKO-B"}
    posko_a = next(p for p in poskos if p["code"] == "POSKO-A")
    assert posko_a["stock_status"] == "Stok Aman"
    assert posko_a["gallon_status"] == "Stok Aman"
    assert posko_a["meals_value_rupiah"] == 150 * 35000
    assert posko_a["gallons_value_rupiah"] == 20 * 22000

    # Admin auth required for mutations
    unauth = client.post(
        "/api/poskos",
        json={"code": "POSKO-X", "name": "X", "location": "Loc"},
    )
    assert unauth.status_code == 401

    # Duplicate code returns 409
    dup = client.post(
        "/api/poskos",
        json={"code": "POSKO-A", "name": "Duplicate", "location": "Loc"},
        headers=headers,
    )
    assert dup.status_code == 409

    # Restock meals & update min_stock_alert
    rm = client.post(
        "/api/poskos/POSKO-A/restock-meals",
        json={"amount": 50, "min_stock_alert": 30},
        headers=headers,
    )
    assert rm.status_code == 200
    assert rm.json()["stock_meals"] == 200
    assert rm.json()["min_stock_alert"] == 30
    assert rm.json()["meals_value_rupiah"] == 200 * 35000

    # Restock gallons & update min_gallon_alert
    rg = client.post(
        "/api/poskos/POSKO-A/restock-gallons",
        json={"amount": 10, "min_gallon_alert": 8},
        headers=headers,
    )
    assert rg.status_code == 200
    assert rg.json()["stock_gallons"] == 30
    assert rg.json()["min_gallon_alert"] == 8
    assert rg.json()["gallons_value_rupiah"] == 30 * 22000

    # Create and delete posko
    c_res = client.post(
        "/api/poskos",
        json={
            "code": "POSKO-TEMP",
            "name": "Posko Sementara",
            "location": "Desa B",
            "stock_meals": 0,
            "min_stock_alert": 10,
            "stock_gallons": 2,
            "min_gallon_alert": 2,
        },
        headers=headers,
    )
    assert c_res.status_code == 201
    assert c_res.json()["stock_status"] == "STOK HABIS"

    # Record a gallon use on POSKO-TEMP then delete POSKO-TEMP -> cascades logs
    client.post("/api/poskos/POSKO-TEMP/use-gallon", headers=headers)
    assert len(client.get("/api/logs?posko=POSKO-TEMP").json()) == 1

    del_res = client.delete("/api/poskos/POSKO-TEMP", headers=headers)
    assert del_res.status_code == 200
    assert len(client.get("/api/logs?posko=POSKO-TEMP").json()) == 0
    assert "POSKO-TEMP" not in {p["code"] for p in client.get("/api/poskos").json()}
