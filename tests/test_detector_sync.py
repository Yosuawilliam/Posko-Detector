# tests/test_detector_sync.py
from fastapi.testclient import TestClient

from auth import ADMIN_TOKEN
from posko_detector import HANDLE_HOTKEY_MAP, PoskoTentDetector, classify_resident_box


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


def test_hotkey_map_and_detector_class():
    assert HANDLE_HOTKEY_MAP == {
        ord("s"): "serve_all",
        ord("a"): "restock_20",
        ord("g"): "use_gallon",
        ord("p"): "switch_posko",
        ord("r"): "reload_faces",
    }
    detector = PoskoTentDetector(
        api_base_url="http://127.0.0.1:8000",
        active_posko="POSKO-A",
        admin_token=ADMIN_TOKEN,
    )
    assert detector.active_posko == "POSKO-A"


def test_detector_sync_auth_and_initial_status(client: TestClient):
    status_res = client.get("/api/detector/status")
    assert status_res.status_code == 200
    status_body = status_res.json()
    assert status_body["online"] is False
    assert status_body["active_posko"] == "POSKO-A"
    assert status_body["person_count"] == 0
    assert status_body["detected_residents"] == []
    assert status_body["last_sync"] is None

    unauth_res = client.post(
        "/api/detector/sync",
        json={
            "active_posko": "POSKO-A",
            "person_count": 1,
            "detected_resident_ids": [],
            "action": None,
        },
    )
    assert unauth_res.status_code == 401


def test_detector_sync_and_quick_actions(client: TestClient):
    headers = {"X-Admin-Token": ADMIN_TOKEN}
    r_a = client.post(
        "/api/residents",
        json={
            "nik": "32017777",
            "full_name": "Dewi Sartika",
            "age": 27,
            "gender": "P",
            "kk_name": "Dewi",
            "posko_code": "POSKO-A",
        },
    ).json()

    r_b = client.post(
        "/api/residents",
        json={
            "nik": "32018889",
            "full_name": "Agus Salim",
            "age": 40,
            "gender": "L",
            "kk_name": "Agus",
            "posko_code": "POSKO-B",
        },
    ).json()

    # 1. Sync camera telemetry and trigger 'serve_all' (hotkey 's')
    sync_res = client.post(
        "/api/detector/sync",
        json={
            "active_posko": "POSKO-A",
            "person_count": 2,
            "detected_resident_ids": [r_a["id"], r_b["id"]],
            "action": "serve_all",
        },
        headers=headers,
    )
    assert sync_res.status_code == 200
    body = sync_res.json()
    assert body["detector_online"] is True
    assert body["active_posko"] == "POSKO-A"
    assert body["person_count"] == 2
    assert body["action_result"]["action"] == "serve_all"
    assert body["action_result"]["served_count"] == 1
    assert isinstance(body["action_result"]["results"], list)
    assert body["posko_summary"]["code"] == "POSKO-A"
    assert body["posko_summary"]["stock_meals"] == 149

    detected_by_id = {d["id"]: d for d in body["detected_residents"]}
    assert detected_by_id[r_a["id"]]["today_meals"] == 1
    assert detected_by_id[r_a["id"]]["max_meals"] == 3
    assert detected_by_id[r_a["id"]]["box_status"] == "VALID"
    assert detected_by_id[r_a["id"]]["qr_token"] == r_a["qr_token"]
    assert detected_by_id[r_b["id"]]["box_status"] == "WRONG_POSKO"
    assert detected_by_id[r_b["id"]]["today_meals"] == 0

    # Verify GET /api/detector/status reflects updated state
    st = client.get("/api/detector/status").json()
    assert st["online"] is True
    assert st["active_posko"] == "POSKO-A"
    assert st["person_count"] == 2
    assert len(st["detected_residents"]) == 2
    assert st["last_sync"] is not None

    # 2. Quick action: restock_20 (hotkey 'a')
    restock_res = client.post(
        "/api/detector/sync",
        json={
            "active_posko": "POSKO-A",
            "person_count": 1,
            "detected_resident_ids": [r_a["id"]],
            "action": "restock_20",
        },
        headers=headers,
    )
    assert restock_res.status_code == 200
    rb = restock_res.json()
    assert rb["action_result"] == {"action": "restock_20", "stock_meals": 169}
    assert rb["posko_summary"]["stock_meals"] == 169

    # 3. Quick action: use_gallon (hotkey 'g')
    gallon_res = client.post(
        "/api/detector/sync",
        json={
            "active_posko": "POSKO-A",
            "person_count": 1,
            "detected_resident_ids": [r_a["id"]],
            "action": "use_gallon",
        },
        headers=headers,
    )
    assert gallon_res.status_code == 200
    gb = gallon_res.json()
    assert gb["action_result"] == {"action": "use_gallon", "stock_gallons": 19}
    assert gb["posko_summary"]["stock_gallons"] == 19
    g_logs = client.get("/api/logs?posko=POSKO-A&status=GALLON_USE").json()
    assert len(g_logs) == 1
    assert g_logs[0]["cost_rupiah"] == 22000

    # 4. Quick action: switch_posko (hotkey 'p')
    switch_res = client.post(
        "/api/detector/sync",
        json={
            "active_posko": "POSKO-A",
            "person_count": 1,
            "detected_resident_ids": [r_b["id"]],
            "action": "switch_posko",
        },
        headers=headers,
    )
    assert switch_res.status_code == 200
    sb = switch_res.json()
    assert sb["action_result"] == {"action": "switch_posko", "active_posko": "POSKO-B"}
    assert sb["active_posko"] == "POSKO-B"
    assert sb["posko_summary"]["code"] == "POSKO-B"
    assert sb["detected_residents"][0]["box_status"] == "VALID"

    # 5. Quick action: reload_faces (hotkey 'r')
    reload_res = client.post(
        "/api/detector/sync",
        json={
            "active_posko": "POSKO-B",
            "person_count": 0,
            "detected_resident_ids": [],
            "action": "reload_faces",
        },
        headers=headers,
    )
    assert reload_res.status_code == 200
    rel_b = reload_res.json()
    assert rel_b["action_result"]["action"] == "reload_faces"
    assert isinstance(rel_b["action_result"]["trained"], bool)
    # Preserves existing detected_residents for POSKO-B when UI quick action omits IDs
    assert len(rel_b["detected_residents"]) == 1
    assert rel_b["detected_residents"][0]["id"] == r_b["id"]

    # 6. UI Quick Action serve_all with empty detected_resident_ids uses existing detector_state
    ui_serve_res = client.post(
        "/api/detector/sync",
        json={
            "active_posko": "POSKO-B",
            "person_count": 0,
            "detected_resident_ids": [],
            "action": "serve_all",
        },
        headers=headers,
    )
    assert ui_serve_res.status_code == 200
    ui_sb = ui_serve_res.json()
    assert ui_sb["action_result"]["action"] == "serve_all"
    assert ui_sb["action_result"]["served_count"] == 1
    assert ui_sb["detected_residents"][0]["id"] == r_b["id"]
    assert ui_sb["detected_residents"][0]["today_meals"] == 1


def test_tent_camera_endpoints_and_top_border_label(client: TestClient):
    detector = PoskoTentDetector(active_posko="POSKO-A")
    detector.resident_cache[1] = {
        "id": 1,
        "full_name": "Yosua William",
        "posko_code": "POSKO-A",
        "today_meals": 1,
        "max_meals": 3,
    }
    label, color, status_str = detector._format_resident_border_label(1)
    assert "Yosua William" in label
    assert "Sisa Makanan: 2/3 Porsi" in label
    assert "[POSKO-A]" in label
    assert status_str == "VALID"
    assert color == (0, 200, 0)

    stop_res = client.post("/api/detector/camera/stop")
    assert stop_res.status_code == 200
    assert stop_res.json()["camera_running"] is False

    stream_res = client.get("/api/detector/stream")
    assert stream_res.status_code == 200
    assert "multipart/x-mixed-replace" in stream_res.headers.get("content-type", "")

