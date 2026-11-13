import re
import shutil
import subprocess
from pathlib import Path

from fastapi.testclient import TestClient


def test_index_html_renders_5_tabs_and_8_metric_cards(client: TestClient):
    res = client.get("/")
    assert res.status_code == 200
    assert "text/html" in res.headers.get("content-type", "")
    html = res.text

    # Global Warning Banner & Navbar elements
    assert 'id="globalWarningBanner"' in html
    assert 'id="activePoskoSelect"' in html

    # All 5 Tab Titles
    expected_tabs = [
        "Scan QR Makanan",
        "Registrasi Warga Baru",
        "Cetak Digital ID QR",
        "Manajemen Stok & Posko",
        "Log Riwayat Distribusi",
    ]
    for tab in expected_tabs:
        assert tab in html, f"Missing tab title: {tab}"

    # 8 Main Metric Cards
    expected_metrics = [
        "Stok Posko Aktif",
        "Total Warga Terdaftar",
        "Porsi Terdistribusi Hari Ini",
        "Biaya Makanan (Hari Ini)",
        "Pengeluaran Galon Air (Hari Ini)",
        "Total Pengeluaran (Hari Ini)",
        "Stok Galon Air Posko",
        "Status Proteksi & Stock",
    ]
    for metric in expected_metrics:
        assert metric in html, f"Missing metric card: {metric}"

    # Live Camera Tent Monitor & 3-angle capture & filter buttons
    assert "Live Camera Tent Monitor" in html
    for status_filter in [
        "ALL",
        "SUCCESS",
        "GALLON_USE",
        "DENIED_WRONG_POSKO",
        "DENIED_EXCEEDED",
        "DENIED_NO_STOCK",
    ]:
        assert status_filter in html, f"Missing log filter button: {status_filter}"


def test_static_css_and_js_served(client: TestClient):
    res_css = client.get("/static/css/style.css")
    assert res_css.status_code == 200
    assert "text/css" in res_css.headers.get("content-type", "")
    assert "@media print" in res_css.text

    res_js = client.get("/static/js/app.js")
    assert res_js.status_code == 200
    js = res_js.text
    assert "function escapeHTML" in js
    assert "function playBeep" in js
    assert "AudioContext" in js or "webkitAudioContext" in js
    assert "sawtooth" in js
    assert "square" in js
    assert "Html5QrcodeScanner" in js or "Html5Qrcode" in js
    assert "getUserMedia" in js
    assert "X-Admin-Token" in js
    assert "CROSS_POSKO_BLOCKED" in js


def test_escape_html_prevents_xss(client: TestClient):
    res_js = client.get("/static/js/app.js")
    assert res_js.status_code == 200
    js_code = res_js.text

    # Verify escapeHTML replaces &, <, >, ", '
    assert "&amp;" in js_code
    assert "&lt;" in js_code
    assert "&gt;" in js_code
    assert "&quot;" in js_code
    assert "&#39;" in js_code or "&#039;" in js_code

    node_bin = shutil.which("node")
    if node_bin:
        js_path = Path(__file__).resolve().parent.parent / "static" / "js" / "app.js"
        script = f"""
        const fs = require('fs');
        const code = fs.readFileSync({ repr(str(js_path)) }, 'utf8');
        const match = code.match(/function\\s+escapeHTML\\s*\\([^)]*\\)\\s*\\{{[\\s\\S]*?\\n\\}}/);
        if (!match) {{
            console.error('escapeHTML function not found');
            process.exit(1);
        }}
        eval(match[0]);
        const payload = '<script>alert("xss")</script>\\'&"';
        const out = escapeHTML(payload);
        console.log(out);
        """
        proc = subprocess.run(
            [node_bin, "-e", script],
            capture_output=True,
            text=True,
            check=True,
        )
        escaped = proc.stdout.strip()
        assert "<script>" not in escaped
        assert "</script>" not in escaped
        assert "&lt;script&gt;alert(&quot;xss&quot;)&lt;/script&gt;" in escaped
        assert "&amp;" in escaped
        assert "&#39;" in escaped or "&#039;" in escaped
    else:
        # Fallback regex verification of escape chain
        assert re.search(r'\.replace\(/&/g,\s*["\']&amp;["\']\)', js_code)
        assert re.search(r'\.replace\(/</g,\s*["\']&lt;["\']\)', js_code)
        assert re.search(r'\.replace\(/>/g,\s*["\']&gt;["\']\)', js_code)
        assert re.search(r'\.replace\(/\\?"/g,\s*["\']&quot;["\']\)', js_code)
        assert re.search(r"\.replace\(/\\?'/g,\s*[\"']&#0?39;[\"']\)", js_code)


def test_frontend_photo_url_normalization_and_tent_quick_actions(client: TestClient):
    res_js = client.get("/static/js/app.js")
    assert res_js.status_code == 200
    js = res_js.text

    # 1. No double leading slash (`/${escapeHTML(r.face_front_path)}`) in resident cards
    assert "`/${escapeHTML(r.face_front_path)}`" not in js
    assert 'rawPhotoPath.startsWith("/")' in js

    # 2. Tent Monitor stores detectedResidentIds & detectedPersonCount and passes them in triggerDetectorAction
    assert "appState.detectedResidentIds = recognized.map((r) => r.id).filter(Boolean)" in js
    assert "appState.detectedPersonCount = det.person_count || 0" in js
    assert "detected_resident_ids: appState.detectedResidentIds || []" in js
    assert "person_count: appState.detectedPersonCount || 0" in js

    # 3. Post-registration canvas clear & status reset + 3-second polling interval
    assert "clearRect" in js
    assert '"Belum diambil"' in js
    assert "setInterval(" in js
    assert "3000" in js
