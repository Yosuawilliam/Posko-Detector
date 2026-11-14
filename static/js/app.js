"use strict";

function escapeHTML(str) {
    if (str === null || str === undefined) return "";
    return String(str)
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;")
        .replace(/"/g, "&quot;")
        .replace(/'/g, "&#39;");
}

function formatRupiah(amount) {
    const num = Number(amount) || 0;
    return "Rp " + num.toLocaleString("id-ID");
}

function getAdminToken() {
    return localStorage.getItem("posko_admin_token") || "posko-secret-admin-2026";
}

function setAdminToken(token) {
    localStorage.setItem("posko_admin_token", (token || "").trim() || "posko-secret-admin-2026");
}

function playBeep(type) {
    try {
        const AudioCtx = window.AudioContext || window.webkitAudioContext;
        if (!AudioCtx) return;
        const ctx = new AudioCtx();
        const osc = ctx.createOscillator();
        const gain = ctx.createGain();
        osc.connect(gain);
        gain.connect(ctx.destination);

        const now = ctx.currentTime;
        if (type === "success") {
            osc.type = "triangle";
            osc.frequency.setValueAtTime(660, now);
            osc.frequency.exponentialRampToValueAtTime(990, now + 0.18);
            gain.gain.setValueAtTime(0.22, now);
            gain.gain.exponentialRampToValueAtTime(0.001, now + 0.25);
            osc.start(now);
            osc.stop(now + 0.25);
        } else if (type === "quota_exceeded") {
            osc.type = "sawtooth";
            osc.frequency.setValueAtTime(340, now);
            osc.frequency.setValueAtTime(240, now + 0.15);
            gain.gain.setValueAtTime(0.25, now);
            gain.gain.exponentialRampToValueAtTime(0.001, now + 0.35);
            osc.start(now);
            osc.stop(now + 0.35);
        } else {
            // "denied" or default error
            osc.type = "square";
            osc.frequency.setValueAtTime(200, now);
            osc.frequency.setValueAtTime(140, now + 0.18);
            gain.gain.setValueAtTime(0.28, now);
            gain.gain.exponentialRampToValueAtTime(0.001, now + 0.4);
            osc.start(now);
            osc.stop(now + 0.4);
        }
    } catch (err) {
        console.warn("Web Audio API tidak tersedia:", err);
    }
}

const appState = {
    activePosko: "POSKO-A",
    poskos: [],
    residents: [],
    currentLogStatusFilter: "ALL",
    detectedResidentIds: [],
    detectedPersonCount: 0,
    capturedAngles: {
        front: null,
        left: null,
        right: null,
    },
    webcamStream: null,
    qrScanner: null,
};

function statusBadgeClass(statusText) {
    const s = String(statusText || "").toUpperCase();
    if (s.includes("HABIS") || s.includes("DENIED") || s.includes("WRONG")) {
        return "badge badge-danger";
    }
    if (s.includes("MENIPIS") || s.includes("EXCEEDED") || s.includes("GALLON")) {
        return "badge badge-warning";
    }
    if (s.includes("AMAN") || s.includes("SUCCESS") || s.includes("ONLINE") || s.includes("VALID")) {
        return "badge badge-safe";
    }
    return "badge badge-neutral";
}

async function fetchJSON(url, options = {}) {
    const headers = Object.assign({}, options.headers || {});
    if (options.adminAuth) {
        headers["X-Admin-Token"] = getAdminToken();
    }
    if (options.body && typeof options.body === "object" && !(options.body instanceof FormData)) {
        headers["Content-Type"] = "application/json";
        options.body = JSON.stringify(options.body);
    }
    const response = await fetch(url, Object.assign({}, options, { headers }));
    let data = null;
    try {
        data = await response.json();
    } catch (_e) {
        data = null;
    }
    return { ok: response.ok, status: response.status, data };
}

async function loadPoskosAndSummary() {
    const poskoRes = await fetchJSON("/api/poskos");
    if (poskoRes.ok && Array.isArray(poskoRes.data)) {
        appState.poskos = poskoRes.data;
        renderPoskoSelectors();
        renderPoskoManagementCards();
    }

    const summaryRes = await fetchJSON(
        `/api/dashboard/summary?posko=${encodeURIComponent(appState.activePosko)}`
    );
    if (summaryRes.ok && summaryRes.data) {
        renderDashboardSummary(summaryRes.data);
    }

    const detRes = await fetchJSON("/api/detector/status");
    if (detRes.ok && detRes.data) {
        renderTentMonitor(detRes.data);
    }
}

function renderPoskoSelectors() {
    const activeSelect = document.getElementById("activePoskoSelect");
    const regSelect = document.getElementById("regPoskoCode");
    const filterSelect = document.getElementById("residentPoskoFilter");

    if (!appState.poskos.some((p) => p.code === appState.activePosko) && appState.poskos.length > 0) {
        appState.activePosko = appState.poskos[0].code;
    }

    if (activeSelect) {
        activeSelect.innerHTML = appState.poskos
            .map(
                (p) =>
                    `<option value="${escapeHTML(p.code)}" ${
                        p.code === appState.activePosko ? "selected" : ""
                    }>${escapeHTML(p.code)} — ${escapeHTML(p.name)}</option>`
            )
            .join("");
    }

    if (regSelect) {
        const currentVal = regSelect.value || appState.activePosko;
        regSelect.innerHTML = appState.poskos
            .map(
                (p) =>
                    `<option value="${escapeHTML(p.code)}" ${
                        p.code === currentVal ? "selected" : ""
                    }>${escapeHTML(p.code)} — ${escapeHTML(p.name)}</option>`
            )
            .join("");
    }

    if (filterSelect) {
        const currentFilter = filterSelect.value || "";
        filterSelect.innerHTML =
            `<option value="">Semua Posko</option>` +
            appState.poskos
                .map(
                    (p) =>
                        `<option value="${escapeHTML(p.code)}" ${
                            p.code === currentFilter ? "selected" : ""
                        }>${escapeHTML(p.code)} (${escapeHTML(p.name)})</option>`
                )
                .join("");
    }
}

function renderDashboardSummary(summary) {
    const banner = document.getElementById("globalWarningBanner");
    const bannerText = document.getElementById("globalWarningText");
    const warnings = Array.isArray(summary.global_warnings) ? summary.global_warnings : [];

    if (banner && bannerText) {
        if (warnings.length > 0) {
            banner.classList.remove("hidden");
            bannerText.innerHTML = warnings
                .map((w) => `<span>${escapeHTML(w.message)}</span>`)
                .join(" &bull; ");
        } else {
            banner.classList.add("hidden");
            bannerText.textContent = "Seluruh stok makanan & galon air posko dalam kondisi aman.";
        }
    }

    const active = summary.active_posko || {};
    const stockMealsEl = document.getElementById("metricStockMeals");
    const stockBadgeEl = document.getElementById("metricStockStatusBadge");
    const activeNameEl = document.getElementById("metricActivePoskoName");

    if (stockMealsEl) stockMealsEl.textContent = String(active.stock_meals ?? 0);
    if (stockBadgeEl) {
        stockBadgeEl.textContent = active.stock_status || "Stok Aman";
        stockBadgeEl.className = statusBadgeClass(active.stock_status);
    }
    if (activeNameEl) {
        activeNameEl.textContent = `${active.code || "-"}: ${active.name || "-"} (Valuasi: ${formatRupiah(
            active.meals_value_rupiah || 0
        )})`;
    }

    const totalResEl = document.getElementById("metricTotalResidents");
    const poskoResEl = document.getElementById("metricPoskoResidents");
    if (totalResEl) totalResEl.textContent = String(summary.total_residents ?? 0);
    if (poskoResEl) poskoResEl.textContent = String(active.resident_count ?? 0);

    const todayMealsEl = document.getElementById("metricTodayMeals");
    const activeTodayMealsEl = document.getElementById("metricActivePoskoTodayMeals");
    if (todayMealsEl) todayMealsEl.textContent = String(summary.today_meals_distributed ?? 0);
    if (activeTodayMealsEl) activeTodayMealsEl.textContent = String(summary.active_posko_today_meals ?? 0);

    const mealCostEl = document.getElementById("metricMealCost");
    const gallonCostEl = document.getElementById("metricGallonCost");
    const todayGallonsEl = document.getElementById("metricTodayGallonsUsed");
    const totalCostEl = document.getElementById("metricTotalCost");

    if (mealCostEl) mealCostEl.textContent = formatRupiah(summary.today_meal_cost || 0);
    if (gallonCostEl) gallonCostEl.textContent = formatRupiah(summary.today_gallon_cost || 0);
    if (todayGallonsEl) todayGallonsEl.textContent = String(summary.today_gallons_used ?? 0);
    if (totalCostEl) totalCostEl.textContent = formatRupiah(summary.today_total_cost || 0);

    const stockGallonsEl = document.getElementById("metricStockGallons");
    const gallonBadgeEl = document.getElementById("metricGallonStatusBadge");
    const gallonValEl = document.getElementById("metricGallonStockValue");

    if (stockGallonsEl) stockGallonsEl.textContent = String(active.stock_gallons ?? 0);
    if (gallonBadgeEl) {
        gallonBadgeEl.textContent = active.gallon_status || "Stok Aman";
        gallonBadgeEl.className = statusBadgeClass(active.gallon_status);
    }
    if (gallonValEl) gallonValEl.textContent = formatRupiah(active.gallons_value_rupiah || 0);

    const prot = summary.protection_status || {};
    const yoloBadgeEl = document.getElementById("metricYoloBadge");
    const protStatusEl = document.getElementById("metricProtectionStatus");
    const tentPersonMetricEl = document.getElementById("metricTentPersonCount");

    if (yoloBadgeEl) {
        const isOnline = Boolean(prot.yolo_camera_online);
        yoloBadgeEl.textContent = isOnline ? "YOLO ONLINE" : "YOLO Standby";
        yoloBadgeEl.className = isOnline ? "badge badge-safe" : "badge badge-neutral";
    }
    if (protStatusEl) {
        protStatusEl.textContent = prot.cross_lock_active ? "Cross-Lock Aktif" : "Standby";
    }
    if (tentPersonMetricEl) {
        tentPersonMetricEl.textContent = String(prot.person_count ?? 0);
    }
}

function renderTentMonitor(det) {
    const badgeEl = document.getElementById("tentCameraStatusBadge");
    const syncTimeEl = document.getElementById("tentLastSyncText");
    const countEl = document.getElementById("tentPersonCount");
    const listEl = document.getElementById("tentRecognizedList");
    const btnCamera = document.getElementById("btnToggleTentCamera");
    const videoBox = document.getElementById("tentVideoPreviewBox");
    const monitorBody = document.getElementById("tentMonitorBody");
    const streamImg = document.getElementById("tentLiveStreamImg");

    const isCameraRunning = Boolean(det.camera_running);
    const isOnline = Boolean(det.online || det.yolo_camera_online || isCameraRunning);
    appState.tentCameraRunning = isCameraRunning;

    if (badgeEl) {
        badgeEl.textContent = isOnline ? "ONLINE" : "Standby";
        badgeEl.className = isOnline ? "badge badge-safe" : "badge badge-neutral";
    }
    if (btnCamera) {
        if (isCameraRunning) {
            btnCamera.textContent = "⏹ Matikan Kamera Tenda";
            btnCamera.className = "btn btn-sm btn-danger";
        } else {
            btnCamera.textContent = "🎥 Nyalakan Kamera Tenda";
            btnCamera.className = "btn btn-sm btn-primary";
        }
    }
    if (videoBox && monitorBody && streamImg) {
        if (isCameraRunning) {
            videoBox.classList.remove("hidden");
            monitorBody.classList.add("has-video");
            const currentSrc = streamImg.getAttribute("src") || "";
            if (!currentSrc.startsWith("/api/detector/stream")) {
                streamImg.setAttribute("src", `/api/detector/stream?t=${Date.now()}`);
            }
        } else {
            videoBox.classList.add("hidden");
            monitorBody.classList.remove("has-video");
            if (streamImg.getAttribute("src")) {
                streamImg.removeAttribute("src");
            }
        }
    }
    if (syncTimeEl) {
        if (!isOnline && det.camera_error) {
            syncTimeEl.textContent = `Info Kamera: ${det.camera_error}`;
        } else {
            syncTimeEl.textContent = det.last_sync
                ? `Sinkronisasi terakhir: ${det.last_sync} (${det.active_posko || appState.activePosko})`
                : "Belum ada sinkronisasi kamera (Klik 'Nyalakan Kamera Tenda')";
        }
    }
    if (countEl) {
        countEl.textContent = String(det.person_count ?? 0);
    }

    const recognized = Array.isArray(det.recognized_residents)
        ? det.recognized_residents
        : Array.isArray(det.detected_residents)
        ? det.detected_residents
        : [];
    appState.detectedResidentIds = recognized.map((r) => r.id).filter(Boolean);
    appState.detectedPersonCount = det.person_count || 0;

    if (listEl) {
        if (recognized.length === 0) {
            listEl.innerHTML = `<div class="empty-inline">Belum ada warga yang terdeteksi di kamera tenda.</div>`;
        } else {
            listEl.innerHTML = recognized
                .map((r) => {
                    const remaining = Math.max(0, (r.max_meals || 3) - (r.today_meals || 0));
                    const boxStatus = r.box_status || "VALID";
                    return `
                        <div class="tent-resident-chip">
                            <span class="${statusBadgeClass(boxStatus)}">${escapeHTML(boxStatus)}</span>
                            <strong>${escapeHTML(r.full_name)}</strong>
                            <span>(${escapeHTML(r.posko_code)})</span>
                            <span>Kuota: <strong>${escapeHTML(r.today_meals || 0)}/3</strong> (Sisa ${escapeHTML(
                        remaining
                    )})</span>
                        </div>
                    `;
                })
                .join("");
        }
    }
}

async function toggleTentCamera(forceAction) {
    const shouldStart =
        typeof forceAction === "boolean" ? forceAction : !Boolean(appState.tentCameraRunning);
    const btnCamera = document.getElementById("btnToggleTentCamera");
    const showWindow = Boolean(document.getElementById("chkShowOpenCvWindow")?.checked);

    if (btnCamera) {
        btnCamera.disabled = true;
        btnCamera.textContent = shouldStart ? "Menghubungkan Kamera..." : "Mematikan Kamera...";
    }

    try {
        if (shouldStart) {
            const res = await fetchJSON("/api/detector/camera/start", {
                method: "POST",
                body: {
                    camera_index: 0,
                    active_posko: appState.activePosko,
                    show_window: showWindow,
                },
            });
            if (res.data) {
                renderTentMonitor(res.data);
            }
            if (!res.ok) {
                alert(
                    res.data?.camera_error ||
                        "Gagal menyalakan kamera tenda. Pastikan webcam tidak sedang dipakai tab/aplikasi lain."
                );
            }
        } else {
            const res = await fetchJSON("/api/detector/camera/stop", {
                method: "POST",
            });
            if (res.data) {
                renderTentMonitor(res.data);
            }
        }
        await loadPoskosAndSummary();
    } finally {
        if (btnCamera) {
            btnCamera.disabled = false;
        }
    }
}


async function triggerDetectorAction(actionName) {
    const res = await fetchJSON("/api/detector/sync", {
        method: "POST",
        adminAuth: true,
        body: {
            active_posko: appState.activePosko,
            person_count: appState.detectedPersonCount || 0,
            detected_resident_ids: appState.detectedResidentIds || [],
            action: actionName,
        },
    });
    if (res.ok && res.data) {
        if (res.data.active_posko) {
            appState.activePosko = res.data.active_posko;
        }
        await loadPoskosAndSummary();
        await loadLogs();
        await loadResidents();
    } else {
        alert(res.data?.detail || "Gagal menjalankan aksi cepat kamera (periksa X-Admin-Token).");
    }
}

/* TAB 1: Scan QR Makanan */
function updateMealDots(count, status) {
    const dots = document.querySelectorAll("#mealDotsContainer .meal-dot");
    dots.forEach((dot) => {
        const seq = Number(dot.getAttribute("data-seq"));
        dot.classList.remove("claimed", "exceeded");
        if (seq <= count) {
            dot.classList.add(status === "DENIED_EXCEEDED" ? "exceeded" : "claimed");
        }
    });
}

async function performMealScan(qrOrNik) {
    const cleanInput = (qrOrNik || "").trim();
    if (!cleanInput) return;

    const res = await fetchJSON("/api/meals/scan", {
        method: "POST",
        body: {
            qr_or_nik: cleanInput,
            scanner_posko_code: appState.activePosko,
        },
    });

    const data = res.data || {};
    const audioCue = data.audio_cue || (res.ok ? "success" : "denied");
    playBeep(audioCue);

    const badgeEl = document.getElementById("scanResultBadge");
    const msgEl = document.getElementById("scanResultMessage");
    const detailEl = document.getElementById("scanResidentDetail");

    if (badgeEl) {
        badgeEl.textContent = data.status || (res.ok ? "SUCCESS" : "DENIED");
        badgeEl.className = statusBadgeClass(data.status);
    }
    if (msgEl) {
        msgEl.textContent = data.message || "Hasil pemindaian diterima.";
    }

    const resident = data.resident;
    const mealCount = Number(data.meal_sequence ?? resident?.today_meals ?? 0);
    updateMealDots(mealCount, data.status);

    if (detailEl) {
        if (resident) {
            detailEl.classList.remove("hidden");
            detailEl.innerHTML = `
                <div><strong>Nama Warga:</strong> ${escapeHTML(resident.full_name)}</div>
                <div><strong>NIK:</strong> ${escapeHTML(resident.nik)} | <strong>Token QR:</strong> <code>${escapeHTML(
                resident.qr_token
            )}</code></div>
                <div><strong>Posko Domisili:</strong> <span class="badge badge-info">${escapeHTML(
                    resident.posko_code
                )}</span> | <strong>Posko Scanner:</strong> <span class="badge badge-primary">${escapeHTML(
                appState.activePosko
            )}</span></div>
                <div><strong>Klaim Hari Ini:</strong> ${escapeHTML(resident.today_meals)} / ${escapeHTML(
                resident.max_meals || 3
            )} Porsi</div>
            `;
        } else {
            detailEl.classList.add("hidden");
            detailEl.innerHTML = "";
        }
    }

    await loadPoskosAndSummary();
    await loadLogs();
    await loadResidents();
}

function initQrScannerControls() {
    const startBtn = document.getElementById("startQrScannerBtn");
    const stopBtn = document.getElementById("stopQrScannerBtn");
    const readerEl = document.getElementById("qrReader");

    if (startBtn) {
        startBtn.addEventListener("click", () => {
            if (typeof window.Html5QrcodeScanner === "undefined") {
                if (readerEl) {
                    readerEl.innerHTML = `<div class="qr-placeholder">Library Html5QrcodeScanner belum dimuat atau koneksi offline. Gunakan input manual di bawah.</div>`;
                }
                return;
            }
            readerEl.innerHTML = "";
            appState.qrScanner = new window.Html5QrcodeScanner(
                "qrReader",
                { fps: 10, qrbox: { width: 220, height: 220 } },
                false
            );
            appState.qrScanner.render(
                (decodedText) => {
                    const inputEl = document.getElementById("qrOrNikInput");
                    if (inputEl) inputEl.value = decodedText;
                    performMealScan(decodedText);
                },
                (_errorMessage) => {}
            );
            startBtn.classList.add("hidden");
            if (stopBtn) stopBtn.classList.remove("hidden");
        });
    }

    if (stopBtn) {
        stopBtn.addEventListener("click", async () => {
            if (appState.qrScanner) {
                try {
                    await appState.qrScanner.clear();
                } catch (_e) {}
                appState.qrScanner = null;
            }
            if (readerEl) {
                readerEl.innerHTML = `<div class="qr-placeholder">Kamera QR dimatikan.</div>`;
            }
            stopBtn.classList.add("hidden");
            if (startBtn) startBtn.classList.remove("hidden");
        });
    }
}

/* TAB 2: Registrasi Warga Baru & 3-Angle Webcam Capture */
function initWebcamAndAngles() {
    const toggleBtn = document.getElementById("toggleWebcamBtn");
    const stage = document.getElementById("webcamStage");
    const video = document.getElementById("webcamVideo");

    if (toggleBtn) {
        toggleBtn.addEventListener("click", async () => {
            if (appState.webcamStream) {
                appState.webcamStream.getTracks().forEach((t) => t.stop());
                appState.webcamStream = null;
                if (video) video.srcObject = null;
                if (stage) stage.classList.add("hidden");
                toggleBtn.textContent = "Nyalakan Webcam";
                return;
            }
            if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
                alert("Browser tidak mendukung MediaDevices.getUserMedia.");
                return;
            }
            try {
                const stream = await navigator.mediaDevices.getUserMedia({
                    video: { width: 640, height: 480 },
                    audio: false,
                });
                appState.webcamStream = stream;
                if (video) video.srcObject = stream;
                if (stage) stage.classList.remove("hidden");
                toggleBtn.textContent = "Matikan Webcam";
            } catch (err) {
                alert("Gagal mengakses kamera webcam: " + err.message);
            }
        });
    }

    document.querySelectorAll(".capture-angle-btn").forEach((btn) => {
        btn.addEventListener("click", () => {
            const angle = btn.getAttribute("data-angle");
            if (!angle) return;
            const capName = angle.charAt(0).toUpperCase() + angle.slice(1);
            const canvas = document.getElementById(`canvas${capName}`);
            const statusEl = document.getElementById(`status${capName}`);
            if (!canvas) return;

            const ctx = canvas.getContext("2d");
            if (appState.webcamStream && video && video.readyState >= 2) {
                ctx.drawImage(video, 0, 0, canvas.width, canvas.height);
            } else {
                ctx.fillStyle = "#1e293b";
                ctx.fillRect(0, 0, canvas.width, canvas.height);
                ctx.fillStyle = "#38bdf8";
                ctx.font = "bold 16px sans-serif";
                ctx.fillText(`Angle: ${angle.toUpperCase()}`, 20, 120);
            }
            appState.capturedAngles[angle] = canvas.toDataURL("image/jpeg", 0.85);
            if (statusEl) statusEl.textContent = `Foto ${angle} tersimpan dari kamera`;
        });
    });

    document.querySelectorAll(".angle-file-input").forEach((input) => {
        input.addEventListener("change", (e) => {
            const angle = input.getAttribute("data-angle");
            const file = e.target.files && e.target.files[0];
            if (!angle || !file) return;
            const capName = angle.charAt(0).toUpperCase() + angle.slice(1);
            const canvas = document.getElementById(`canvas${capName}`);
            const statusEl = document.getElementById(`status${capName}`);

            const reader = new FileReader();
            reader.onload = (ev) => {
                const dataUrl = ev.target.result;
                appState.capturedAngles[angle] = dataUrl;
                if (canvas) {
                    const img = new Image();
                    img.onload = () => {
                        const ctx = canvas.getContext("2d");
                        ctx.clearRect(0, 0, canvas.width, canvas.height);
                        ctx.drawImage(img, 0, 0, canvas.width, canvas.height);
                    };
                    img.src = dataUrl;
                }
                if (statusEl) statusEl.textContent = `File: ${file.name}`;
            };
            reader.readAsDataURL(file);
        });
    });
}

async function handleRegisterResident(e) {
    e.preventDefault();
    const alertBox = document.getElementById("registerAlertBox");
    if (alertBox) {
        alertBox.className = "alert-box hidden";
        alertBox.textContent = "";
    }

    const payload = {
        full_name: document.getElementById("regFullName")?.value || "",
        posko_code: document.getElementById("regPoskoCode")?.value || appState.activePosko,
        nik: document.getElementById("regNik")?.value || "",
        kk_name: document.getElementById("regKkName")?.value || "",
        age: Number(document.getElementById("regAge")?.value || 0),
        gender: document.getElementById("regGender")?.value || "L",
        photo_front: appState.capturedAngles.front,
        photo_left: appState.capturedAngles.left,
        photo_right: appState.capturedAngles.right,
    };

    const res = await fetchJSON("/api/residents", {
        method: "POST",
        body: payload,
    });

    if (res.ok && res.data) {
        playBeep("success");
        if (alertBox) {
            alertBox.className = "alert-box success";
            alertBox.innerHTML = `Registrasi berhasil! Warga <strong>${escapeHTML(
                res.data.full_name
            )}</strong> terdaftar di <strong>${escapeHTML(
                res.data.posko_code
            )}</strong> dengan Token QR: <code>${escapeHTML(res.data.qr_token)}</code>.`;
        }
        document.getElementById("registerResidentForm")?.reset();
        appState.capturedAngles = { front: null, left: null, right: null };
        ["Front", "Left", "Right"].forEach((capName) => {
            const canvas = document.getElementById(`canvas${capName}`);
            if (canvas) {
                canvas.getContext("2d").clearRect(0, 0, canvas.width, canvas.height);
            }
            const statusEl = document.getElementById(`status${capName}`);
            if (statusEl) {
                statusEl.textContent = "Belum diambil";
            }
        });
        await loadPoskosAndSummary();
        await loadResidents();
    } else {
        playBeep("denied");
        const detail = res.data?.detail;
        let errMsg = "Gagal mendaftarkan warga.";
        if (detail && typeof detail === "object" && detail.error_code === "CROSS_POSKO_BLOCKED") {
            errMsg = `[${detail.error_code}] ${detail.message} (Terdaftar di: ${detail.existing_posko})`;
        } else if (typeof detail === "string") {
            errMsg = detail;
        }
        if (alertBox) {
            alertBox.className = "alert-box error";
            alertBox.textContent = errMsg;
        }
    }
}

/* TAB 3: Cetak Digital ID QR */
async function loadResidents() {
    const searchVal = document.getElementById("residentSearchInput")?.value || "";
    const poskoVal = document.getElementById("residentPoskoFilter")?.value || "";
    const params = new URLSearchParams();
    if (searchVal.trim()) params.set("search", searchVal.trim());
    if (poskoVal.trim()) params.set("posko", poskoVal.trim());

    const res = await fetchJSON(`/api/residents?${params.toString()}`);
    if (res.ok && Array.isArray(res.data)) {
        appState.residents = res.data;
        renderResidentsGrid();
    }
}

function renderResidentsGrid() {
    const grid = document.getElementById("residentsCardGrid");
    if (!grid) return;

    if (appState.residents.length === 0) {
        grid.innerHTML = `<div class="empty-state">Tidak ada warga ditemukan untuk filter pencarian ini.</div>`;
        return;
    }

    grid.innerHTML = appState.residents
        .map((r) => {
            const rawPhotoPath = r.face_front_path ? String(r.face_front_path).trim() : "";
            const normalizedPhotoPath = rawPhotoPath
                ? rawPhotoPath.startsWith("/")
                    ? rawPhotoPath
                    : "/" + rawPhotoPath
                : "";
            const photoUrl = normalizedPhotoPath
                ? escapeHTML(normalizedPhotoPath)
                : `https://ui-avatars.com/api/?name=${encodeURIComponent(r.full_name || "W")}&background=f1f5f9&color=0f172a`;
            const qrUrl = `/api/residents/qr/${encodeURIComponent(r.qr_token)}`;
            const meals = Number(r.today_meals || 0);
            const maxMeals = Number(r.max_meals || 3);

            return `
                <article class="id-card" data-resident-id="${escapeHTML(r.id)}">
                    <div class="id-card-top">
                        <img src="${photoUrl}" alt="Foto ${escapeHTML(r.full_name)}" class="id-card-photo">
                        <div class="id-card-info">
                            <div class="id-card-name">${escapeHTML(r.full_name)}</div>
                            <div class="id-card-meta">NIK: <strong>${escapeHTML(r.nik)}</strong></div>
                            <div class="id-card-meta">Umur: ${escapeHTML(r.age)} thn (${escapeHTML(
                r.gender
            )}) | KK: ${escapeHTML(r.kk_name || "-")}</div>
                            <span class="badge badge-info">${escapeHTML(r.posko_code)}</span>
                        </div>
                    </div>

                    <div class="id-card-middle">
                        <div>
                            <div class="id-card-meta">Token Digital ID:</div>
                            <code>${escapeHTML(r.qr_token)}</code>
                            <div style="margin-top: 6px;">
                                <span class="${
                                    meals >= maxMeals ? "badge badge-warning" : "badge badge-safe"
                                }">${escapeHTML(meals)}/${escapeHTML(maxMeals)} Porsi Hari Ini</span>
                            </div>
                        </div>
                        <img src="${qrUrl}" alt="QR ${escapeHTML(r.qr_token)}" class="id-card-qr">
                    </div>

                    <div class="id-card-actions">
                        <button type="button" class="btn btn-sm btn-primary btn-print-card" data-id="${escapeHTML(
                            r.id
                        )}">
                            Print ID Card
                        </button>
                        <button type="button" class="btn btn-sm btn-danger btn-delete-resident" data-id="${escapeHTML(
                            r.id
                        )}">
                            Hapus Warga
                        </button>
                    </div>
                </article>
            `;
        })
        .join("");

    grid.querySelectorAll(".btn-print-card").forEach((btn) => {
        btn.addEventListener("click", () => {
            const id = Number(btn.getAttribute("data-id"));
            printResidentIdCard(id);
        });
    });

    grid.querySelectorAll(".btn-delete-resident").forEach((btn) => {
        btn.addEventListener("click", () => {
            const id = Number(btn.getAttribute("data-id"));
            deleteResidentById(id);
        });
    });
}

function printResidentIdCard(residentId) {
    const r = appState.residents.find((item) => item.id === residentId);
    if (!r) return;
    const container = document.getElementById("printableCardContainer");
    if (!container) return;

    const rawPhotoPath = r.face_front_path ? String(r.face_front_path).trim() : "";
    const normalizedPhotoPath = rawPhotoPath
        ? rawPhotoPath.startsWith("/")
            ? rawPhotoPath
            : "/" + rawPhotoPath
        : "";
    const photoUrl = normalizedPhotoPath
        ? escapeHTML(normalizedPhotoPath)
        : `https://ui-avatars.com/api/?name=${encodeURIComponent(r.full_name || "W")}&background=f1f5f9&color=0f172a`;
    const qrUrl = `/api/residents/qr/${encodeURIComponent(r.qr_token)}`;
    container.innerHTML = `
        <div class="print-id-sheet">
            <h2 style="margin-bottom: 4px;">KARTU DIGITAL LOGISTIK PENGUNGSI</h2>
            <p style="font-size: 12px; margin-bottom: 12px;">Posko Domisili: <strong>${escapeHTML(
                r.posko_code
            )}</strong></p>
            <hr style="margin-bottom: 12px;">
            <div style="text-align: center; margin-bottom: 10px;">
                <img src="${photoUrl}" alt="Foto ${escapeHTML(r.full_name)}" style="width: 72px; height: 72px; object-fit: cover; border-radius: 8px;">
            </div>
            <p><strong>Nama Lengkap:</strong> ${escapeHTML(r.full_name)}</p>
            <p><strong>NIK:</strong> ${escapeHTML(r.nik)}</p>
            <p><strong>Umur / JK:</strong> ${escapeHTML(r.age)} Tahun / ${escapeHTML(r.gender)}</p>
            <p><strong>Kepala Keluarga:</strong> ${escapeHTML(r.kk_name || "-")}</p>
            <div style="text-align: center; margin-top: 16px;">
                <img src="${qrUrl}" alt="QR Code" style="width: 160px; height: 160px;">
                <div style="font-family: monospace; font-weight: bold; margin-top: 6px;">${escapeHTML(
                    r.qr_token
                )}</div>
            </div>
        </div>
    `;
    window.print();
}

async function deleteResidentById(residentId) {
    const res = await fetchJSON(`/api/residents/${encodeURIComponent(residentId)}`, {
        method: "DELETE",
        adminAuth: true,
    });
    if (res.ok) {
        await loadPoskosAndSummary();
        await loadResidents();
    } else {
        alert(res.data?.detail || "Gagal menghapus warga (pastikan X-Admin-Token valid).");
    }
}

/* TAB 4: Manajemen Stok & Posko */
function renderPoskoManagementCards() {
    const container = document.getElementById("poskoManagementGrid");
    if (!container) return;

    if (appState.poskos.length === 0) {
        container.innerHTML = `<div class="empty-state">Belum ada posko terdaftar.</div>`;
        return;
    }

    container.innerHTML = appState.poskos
        .map((p) => {
            const mealPercent = Math.min(100, Math.round((p.stock_meals / Math.max(1, p.min_stock_alert * 3)) * 100));
            const gallonPercent = Math.min(
                100,
                Math.round((p.stock_gallons / Math.max(1, p.min_gallon_alert * 3)) * 100)
            );
            const mealFillClass =
                p.stock_meals <= 0 ? "danger" : p.stock_meals <= p.min_stock_alert ? "warning" : "";
            const gallonFillClass =
                p.stock_gallons <= 0 ? "danger" : p.stock_gallons <= p.min_gallon_alert ? "warning" : "";

            return `
                <div class="posko-item-card" data-posko-code="${escapeHTML(p.code)}">
                    <div class="posko-item-header">
                        <div>
                            <strong>${escapeHTML(p.code)} — ${escapeHTML(p.name)}</strong>
                            <div class="id-card-meta">${escapeHTML(p.location)} | Warga Terdaftar: <strong>${escapeHTML(
                p.resident_count || 0
            )}</strong> jiwa</div>
                        </div>
                        <button type="button" class="btn btn-xs btn-danger btn-delete-posko" data-code="${escapeHTML(
                            p.code
                        )}">
                            Hapus Posko
                        </button>
                    </div>

                    <div class="stock-bars-grid">
                        <!-- Meal Stock Box -->
                        <div class="stock-box">
                            <div style="display:flex; justify-content:space-between; align-items:center;">
                                <span><strong>Stok Makanan:</strong> ${escapeHTML(p.stock_meals)} Porsi</span>
                                <span class="${statusBadgeClass(p.stock_status)}">${escapeHTML(
                p.stock_status
            )}</span>
                            </div>
                            <div class="progress-track">
                                <div class="progress-fill ${mealFillClass}" style="width: ${mealPercent}%;"></div>
                            </div>
                            <div class="id-card-meta" style="margin-bottom:8px;">
                                Valuasi (Stok &times; Rp 35.000): <strong>${formatRupiah(
                                    p.meals_value_rupiah
                                )}</strong> (Min Alert: ${escapeHTML(p.min_stock_alert)})
                            </div>
                            <button type="button" class="btn btn-xs btn-success btn-open-restock-meals" data-code="${escapeHTML(
                                p.code
                            )}" data-min="${escapeHTML(p.min_stock_alert)}">
                                + Restock Makanan
                            </button>
                        </div>

                        <!-- Gallon Stock Box -->
                        <div class="stock-box">
                            <div style="display:flex; justify-content:space-between; align-items:center;">
                                <span><strong>Stok Galon Air:</strong> ${escapeHTML(p.stock_gallons)} Galon</span>
                                <span class="${statusBadgeClass(p.gallon_status)}">${escapeHTML(
                p.gallon_status
            )}</span>
                            </div>
                            <div class="progress-track">
                                <div class="progress-fill ${gallonFillClass}" style="width: ${gallonPercent}%;"></div>
                            </div>
                            <div class="id-card-meta" style="margin-bottom:8px;">
                                Valuasi (Stok &times; Rp 22.000): <strong>${formatRupiah(
                                    p.gallons_value_rupiah
                                )}</strong> (Min Alert: ${escapeHTML(p.min_gallon_alert)})
                            </div>
                            <div style="display:flex; gap:6px;">
                                <button type="button" class="btn btn-xs btn-warning btn-use-gallon" data-code="${escapeHTML(
                                    p.code
                                )}">
                                    -1 Pakai Galon
                                </button>
                                <button type="button" class="btn btn-xs btn-primary btn-open-restock-gallons" data-code="${escapeHTML(
                                    p.code
                                )}" data-min="${escapeHTML(p.min_gallon_alert)}">
                                    + Restock Galon
                                </button>
                            </div>
                        </div>
                    </div>
                </div>
            `;
        })
        .join("");

    container.querySelectorAll(".btn-delete-posko").forEach((btn) => {
        btn.addEventListener("click", async () => {
            const code = btn.getAttribute("data-code");
            if (!code) return;
            const res = await fetchJSON(`/api/poskos/${encodeURIComponent(code)}`, {
                method: "DELETE",
                adminAuth: true,
            });
            if (res.ok) {
                await loadPoskosAndSummary();
                await loadResidents();
            } else {
                alert(res.data?.detail || "Gagal menghapus posko.");
            }
        });
    });

    container.querySelectorAll(".btn-use-gallon").forEach((btn) => {
        btn.addEventListener("click", async () => {
            const code = btn.getAttribute("data-code");
            if (!code) return;
            const res = await fetchJSON(`/api/poskos/${encodeURIComponent(code)}/use-gallon`, {
                method: "POST",
                adminAuth: true,
            });
            if (res.ok) {
                playBeep("success");
                await loadPoskosAndSummary();
                await loadLogs();
            } else {
                playBeep("denied");
                alert(res.data?.detail || "Gagal mencatat pemakaian galon.");
            }
        });
    });

    container.querySelectorAll(".btn-open-restock-meals").forEach((btn) => {
        btn.addEventListener("click", () => {
            const code = btn.getAttribute("data-code") || "";
            const minAlert = btn.getAttribute("data-min") || "20";
            document.getElementById("restockMealsPoskoCode").value = code;
            document.getElementById("restockMealsPoskoLabel").textContent = code;
            document.getElementById("restockMealsMinAlert").value = minAlert;
            document.getElementById("restockMealsModal")?.classList.remove("hidden");
        });
    });

    container.querySelectorAll(".btn-open-restock-gallons").forEach((btn) => {
        btn.addEventListener("click", () => {
            const code = btn.getAttribute("data-code") || "";
            const minAlert = btn.getAttribute("data-min") || "5";
            document.getElementById("restockGallonsPoskoCode").value = code;
            document.getElementById("restockGallonsPoskoLabel").textContent = code;
            document.getElementById("restockGallonsMinAlert").value = minAlert;
            document.getElementById("restockGallonsModal")?.classList.remove("hidden");
        });
    });
}

/* TAB 5: Log Riwayat Distribusi */
async function loadLogs() {
    const params = new URLSearchParams();
    if (appState.currentLogStatusFilter && appState.currentLogStatusFilter !== "ALL") {
        params.set("status", appState.currentLogStatusFilter);
    }
    const res = await fetchJSON(`/api/logs?${params.toString()}`);
    if (res.ok && Array.isArray(res.data)) {
        renderLogsTable(res.data);
    }
}

function renderLogsTable(logs) {
    const tbody = document.getElementById("distributionLogsTableBody");
    if (!tbody) return;

    if (logs.length === 0) {
        tbody.innerHTML = `<tr><td colspan="9" class="empty-cell">Belum ada riwayat distribusi untuk filter ini.</td></tr>`;
        return;
    }

    tbody.innerHTML = logs
        .map((log) => {
            const seqText = log.meal_sequence ? `${escapeHTML(log.meal_sequence)}/3` : "-";
            return `
                <tr>
                    <td>${escapeHTML(log.created_at || log.claim_date || "-")}</td>
                    <td><strong>${escapeHTML(log.resident_name || "-")}</strong></td>
                    <td><code>${escapeHTML(log.resident_nik || "-")}</code></td>
                    <td>${escapeHTML(log.scanner_posko_code || "-")}</td>
                    <td>${escapeHTML(log.resident_posko_code || "-")}</td>
                    <td><span class="${statusBadgeClass(log.status)}">${escapeHTML(log.status)}</span></td>
                    <td>${seqText}</td>
                    <td>${formatRupiah(log.cost_rupiah || 0)}</td>
                    <td>${escapeHTML(log.notes || "-")}</td>
                </tr>
            `;
        })
        .join("");
}

function initTabsAndModals() {
    document.querySelectorAll(".tab-btn").forEach((btn) => {
        btn.addEventListener("click", () => {
            const targetId = btn.getAttribute("data-tab");
            document.querySelectorAll(".tab-btn").forEach((b) => {
                b.classList.toggle("active", b === btn);
                b.setAttribute("aria-selected", b === btn ? "true" : "false");
            });
            document.querySelectorAll(".tab-panel").forEach((panel) => {
                panel.classList.toggle("active", panel.id === targetId);
            });
        });
    });

    document.querySelectorAll(".close-modal-btn").forEach((btn) => {
        btn.addEventListener("click", () => {
            const modalId = btn.getAttribute("data-modal");
            if (modalId) document.getElementById(modalId)?.classList.add("hidden");
        });
    });

    const openTokenBtn = document.getElementById("openAdminTokenModalBtn");
    if (openTokenBtn) {
        openTokenBtn.addEventListener("click", () => {
            const input = document.getElementById("adminTokenInput");
            if (input) input.value = getAdminToken();
            document.getElementById("adminTokenModal")?.classList.remove("hidden");
        });
    }

    const tokenForm = document.getElementById("adminTokenForm");
    if (tokenForm) {
        tokenForm.addEventListener("submit", (e) => {
            e.preventDefault();
            const val = document.getElementById("adminTokenInput")?.value || "";
            setAdminToken(val);
            document.getElementById("adminTokenModal")?.classList.add("hidden");
        });
    }
}

document.addEventListener("DOMContentLoaded", () => {
    initTabsAndModals();
    initQrScannerControls();
    initWebcamAndAngles();

    const activeSelect = document.getElementById("activePoskoSelect");
    if (activeSelect) {
        activeSelect.addEventListener("change", async () => {
            appState.activePosko = activeSelect.value;
            if (appState.tentCameraRunning) {
                await fetchJSON("/api/detector/camera/start", {
                    method: "POST",
                    body: {
                        camera_index: 0,
                        active_posko: appState.activePosko,
                        show_window: Boolean(document.getElementById("chkShowOpenCvWindow")?.checked),
                    },
                });
            }
            loadPoskosAndSummary();
        });
    }


    const manualScanForm = document.getElementById("manualScanForm");
    if (manualScanForm) {
        manualScanForm.addEventListener("submit", (e) => {
            e.preventDefault();
            const val = document.getElementById("qrOrNikInput")?.value || "";
            performMealScan(val);
        });
    }

    document.querySelectorAll("[data-beep]").forEach((btn) => {
        btn.addEventListener("click", () => {
            playBeep(btn.getAttribute("data-beep"));
        });
    });

    const regForm = document.getElementById("registerResidentForm");
    if (regForm) {
        regForm.addEventListener("submit", handleRegisterResident);
    }

    const searchInput = document.getElementById("residentSearchInput");
    if (searchInput) {
        searchInput.addEventListener("input", () => loadResidents());
    }
    const poskoFilter = document.getElementById("residentPoskoFilter");
    if (poskoFilter) {
        poskoFilter.addEventListener("change", () => loadResidents());
    }

    const addPoskoForm = document.getElementById("addPoskoForm");
    if (addPoskoForm) {
        addPoskoForm.addEventListener("submit", async (e) => {
            e.preventDefault();
            const res = await fetchJSON("/api/poskos", {
                method: "POST",
                adminAuth: true,
                body: {
                    code: document.getElementById("poskoCodeInput")?.value || "",
                    name: document.getElementById("poskoNameInput")?.value || "",
                    location: document.getElementById("poskoLocationInput")?.value || "",
                    stock_meals: Number(document.getElementById("poskoInitialMeals")?.value || 0),
                    min_stock_alert: Number(document.getElementById("poskoMinMealAlert")?.value || 20),
                    stock_gallons: Number(document.getElementById("poskoInitialGallons")?.value || 0),
                    min_gallon_alert: Number(document.getElementById("poskoMinGallonAlert")?.value || 5),
                },
            });
            if (res.ok) {
                addPoskoForm.reset();
                await loadPoskosAndSummary();
            } else {
                alert(res.data?.detail || "Gagal menambah posko.");
            }
        });
    }

    const restockMealsForm = document.getElementById("restockMealsForm");
    if (restockMealsForm) {
        restockMealsForm.addEventListener("submit", async (e) => {
            e.preventDefault();
            const code = document.getElementById("restockMealsPoskoCode")?.value || "";
            const amount = Number(document.getElementById("restockMealsAmount")?.value || 0);
            const minAlert = Number(document.getElementById("restockMealsMinAlert")?.value || 20);
            const res = await fetchJSON(`/api/poskos/${encodeURIComponent(code)}/restock-meals`, {
                method: "POST",
                adminAuth: true,
                body: { amount, min_stock_alert: minAlert },
            });
            if (res.ok) {
                document.getElementById("restockMealsModal")?.classList.add("hidden");
                await loadPoskosAndSummary();
            } else {
                alert(res.data?.detail || "Gagal restock makanan.");
            }
        });
    }

    const restockGallonsForm = document.getElementById("restockGallonsForm");
    if (restockGallonsForm) {
        restockGallonsForm.addEventListener("submit", async (e) => {
            e.preventDefault();
            const code = document.getElementById("restockGallonsPoskoCode")?.value || "";
            const amount = Number(document.getElementById("restockGallonsAmount")?.value || 0);
            const minAlert = Number(document.getElementById("restockGallonsMinAlert")?.value || 5);
            const res = await fetchJSON(`/api/poskos/${encodeURIComponent(code)}/restock-gallons`, {
                method: "POST",
                adminAuth: true,
                body: { amount, min_gallon_alert: minAlert },
            });
            if (res.ok) {
                document.getElementById("restockGallonsModal")?.classList.add("hidden");
                await loadPoskosAndSummary();
            } else {
                alert(res.data?.detail || "Gagal restock galon.");
            }
        });
    }

    document.querySelectorAll(".log-filters .btn-filter").forEach((btn) => {
        btn.addEventListener("click", () => {
            document.querySelectorAll(".log-filters .btn-filter").forEach((b) => b.classList.remove("active"));
            btn.classList.add("active");
            appState.currentLogStatusFilter = btn.getAttribute("data-status") || "ALL";
            loadLogs();
        });
    });

    document.getElementById("btnToggleTentCamera")?.addEventListener("click", () => toggleTentCamera());

    const chkAutoStart = document.getElementById("chkAutoStartTentCamera");
    if (chkAutoStart) {
        chkAutoStart.checked = localStorage.getItem("posko_auto_start_tent_camera") === "1";
        chkAutoStart.addEventListener("change", () => {
            localStorage.setItem("posko_auto_start_tent_camera", chkAutoStart.checked ? "1" : "0");
            if (chkAutoStart.checked && !appState.tentCameraRunning) {
                toggleTentCamera(true);
            }
        });
    }

    const chkShowWindow = document.getElementById("chkShowOpenCvWindow");
    if (chkShowWindow) {
        chkShowWindow.addEventListener("change", () => {
            if (appState.tentCameraRunning) {
                toggleTentCamera(true);
            }
        });
    }

    document.getElementById("btnQuickServeAll")?.addEventListener("click", () => triggerDetectorAction("serve_all"));
    document.getElementById("btnQuickRestock20")?.addEventListener("click", () => triggerDetectorAction("restock_20"));
    document.getElementById("btnQuickUseGallon")?.addEventListener("click", () => triggerDetectorAction("use_gallon"));
    document.getElementById("btnQuickReloadFaces")?.addEventListener("click", () => triggerDetectorAction("reload_faces"));

    loadPoskosAndSummary().then(() => {
        if (localStorage.getItem("posko_auto_start_tent_camera") === "1" && !appState.tentCameraRunning) {
            toggleTentCamera(true);
        }
    });
    loadResidents();
    loadLogs();
    setInterval(() => {
        loadPoskosAndSummary();
    }, 3000);
});
