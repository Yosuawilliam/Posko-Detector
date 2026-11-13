# Posko Detector & Sistem Manajemen Distribusi Logistik Bencana (Modern MySQL Edition) — Design Spec

## 1. Tujuan & Konteks (Intent & Why)
Membangun sistem terpadu manajemen pengungsi, distribusi makanan harian (maksimal 3 porsi/hari), logistik galon air, dan pemantauan tenda berbasis kamera AI (YOLO + Face Recognition) untuk posko bencana. Sistem ini dirancang dalam **versi arsitektur terbaru** menggunakan **MySQL (InnoDB)** sebagai basis data relasional menggantikan SQLite, sehingga mendukung konkurensi tinggi antar banyak posko secara simultan tanpa *database-level lock bottleneck*.

## 2. Goals, Non-Goals, & Anti-Goals
* **Goals:**
  * Menyediakan 8 kartu metrik utama secara *real-time*, pemilih posko aktif di navbar, dan *Global Warning Banner* berdenyut (pulsing) saat stok makanan atau galon menipis/habis.
  * Mengimplementasikan 5 Tab fungsional: (1) Scan QR Makanan, (2) Registrasi Warga & Akuisisi Wajah 3 Sudut, (3) Cetak Digital ID QR, (4) Manajemen Stok & Posko, (5) Audit Log Riwayat Distribusi.
  * Menjamin keamanan transaksi klaim makanan dari *race condition* menggunakan transaksi **MySQL InnoDB `SELECT ... FOR UPDATE`**.
  * Menerapkan proteksi lintas posko (*Cross-Posko Claim Denied* HTTP 403 saat klaim makan & *Cross-Posko Lockout* `CROSS_POSKO_BLOCKED` saat registrasi NIK duplikat).
  * Mengintegrasikan skrip kamera AI (`posko_detector.py` dan `face_recog.py`) dengan backend (`app.py`) melalui `POST /api/detector/sync` yang dilindungi header `X-Admin-Token`.
* **Non-Goals:**
  * Integrasi pembayaran *payment gateway* eksternal (perhitungan biaya Rp 35.000/porsi dan Rp 22.000/galon adalah pencatatan beban logistik internal).
  * Aplikasi mobile native (Android/iOS); seluruh akses operator menggunakan Web Dashboard responsif dan jendela OpenCV untuk operator kamera.
* **Anti-Goals:**
  * Klaim makanan ganda yang lolos akibat klik/scan berbarengan (*race condition*).
  * Kerentanan *path traversal* saat menyimpan/menghapus foto wajah warga di `known_faces/`.
  * Kerentanan XSS pada tampilan tabel log dan kartu warga.

## 3. Tech Stack (Versi Terbaru dengan MySQL)

| Layer | Teknologi / Framework (Versi Terbaru) | Peran Utama |
| :--- | :--- | :--- |
| **Backend Framework** | **FastAPI (Python 3.11+)** + **Uvicorn** *(atau Flask 3.1+)* di `app.py` | REST API asinkron/cepat, dependency injection untuk `verify_admin_auth()`, & *BackgroundTasks* untuk re-train wajah LBPH. |
| **Database & ORM** | **MySQL 8.0+ / MariaDB 10.6+ (InnoDB)** + **SQLAlchemy 2.0** + **PyMySQL** | Penyimpanan relasional dengan *Connection Pooling* (`QueuePool`), *Row-Level Locking* (`SELECT ... FOR UPDATE`), dan `ON DELETE CASCADE`. |
| **Frontend UI** | **Tailwind CSS v3/v4 + Alpine.js / Vanilla ES6+** *(atau React 19 + Vite)* | Dashboard 5 Tab responsif, 8 Kartu Metrik, Global Pulsing Warning Banner, Modal Restock, Print ID Card (`@media print`), & `escapeHTML()`. |
| **Scanner & Media Web** | **Html5QrcodeScanner (`html5-qrcode`)**, **Web Audio API**, **MediaDevices API** | Scan QR live via webcam, sintesis nada *beep* (sukses/kuota habis/ditolak), dan tangkapan foto wajah 3 sudut (Depan, Kiri, Kanan). |
| **AI & Computer Vision** | **Ultralytics YOLOv11 / YOLOv8 (`ultralytics>=8.3`)** + **ByteTrack** + **OpenCV Contrib 4.10+ (`cv2.face.LBPHFaceRecognizer`)** | Deteksi & tracking orang di tenda (`final.pt` / fallback `yolo11n.pt`), pengenalan wajah warga 3 sudut, dan kontrol cepat keyboard (`s`, `a`, `g`, `p`, `r`). |
| **QR & Image Utils** | **`qrcode[pil]`** + **Pillow 11+** | Pembuatan QR Code warga (`QR-<POSKO>-<NIK>`) secara *on-the-fly*. |

## 4. Perbandingan Pendekatan (Approaches)

1. **Pendekatan 1 (Direkomendasikan — Modern Python Hybrid): FastAPI (`app.py`) + SQLAlchemy 2.0 (MySQL) + Tailwind CSS & Vanilla ES6/Alpine.js**
   * **Alur:** `app.py` melayani seluruh endpoint REST API (`/api/...`) sekaligus menyajikan dashboard frontend (`templates/index.html` & `static/`).
   * **Kelebihan:** Tetap mempertahankan arsitektur satu repo (`app.py`, `face_recog.py`, `posko_detector.py`) sesuai spesifikasi awal, tidak membutuhkan proses *build* Node.js terpisah di komputer posko, namun sudah menggunakan engine modern (FastAPI + SQLAlchemy 2.0 + MySQL InnoDB Pool + Tailwind CSS).
   * **Trade-off:** Logika UI berada di satu file JS modular (`static/js/app.js`).

2. **Pendekatan 2 (Full Decoupled SPA): FastAPI (`app.py`) + MySQL + React 19 (Vite + TypeScript + Tailwind)**
   * **Alur:** Backend Python dan Frontend React berjalan sebagai dua layanan terpisah (port 8000 dan port 5173/build statis).
   * **Kelebihan:** Manajemen state komponen sangat terstruktur untuk skala besar.
   * **Trade-off:** Membutuhkan instalasi Node.js/npm dan proses *build* tambahan saat dideploy di laptop posko.

3. **Pendekatan 3 (Classic Upgrade): Flask 3.1 (`app.py`) + PyMySQL + Tailwind CSS + Vanilla JS**
   * **Alur:** Menggunakan Flask klasik dengan query SQL langsung ke MySQL.
   * **Kelebihan:** Sangat sederhana dan familiar.
   * **Trade-off:** Tidak memiliki validasi skema otomatis sekuat Pydantic v2 di FastAPI.

**Keputusan Desain:** Menggunakan **Pendekatan 1** (atau Pendekatan 3 jika pengguna menginginkan Flask murni) dengan struktur endpoint dan nama file (`app.py`, `posko_detector.py`, `face_recog.py`) yang 100% kompatibel dengan spesifikasi.

## 5. Skema Database MySQL (InnoDB)

Database: `posko_detector_db` (otomatis dibuat melalui `CREATE DATABASE IF NOT EXISTS posko_detector_db CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;`).

### 5.1. Tabel `poskos`
```sql
CREATE TABLE poskos (
    id INT AUTO_INCREMENT PRIMARY KEY,
    code VARCHAR(32) NOT NULL UNIQUE,
    name VARCHAR(120) NOT NULL,
    location VARCHAR(255) NOT NULL,
    stock_meals INT NOT NULL DEFAULT 0,
    min_stock_alert INT NOT NULL DEFAULT 20,
    stock_gallons INT NOT NULL DEFAULT 0,
    min_gallon_alert INT NOT NULL DEFAULT 5,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
) ENGINE=InnoDB;
```

### 5.2. Tabel `residents`
```sql
CREATE TABLE residents (
    id INT AUTO_INCREMENT PRIMARY KEY,
    nik VARCHAR(64) NOT NULL UNIQUE,          -- NIK asli atau NO-NIK-<UUID> (Unik lintas posko!)
    qr_token VARCHAR(128) NOT NULL UNIQUE,    -- Format: QR-<POSKO>-<NIK>
    full_name VARCHAR(150) NOT NULL,
    age INT NOT NULL,
    gender ENUM('L', 'P') NOT NULL,
    kk_name VARCHAR(150) DEFAULT NULL,
    posko_code VARCHAR(32) NOT NULL,
    face_front_path VARCHAR(255) DEFAULT NULL,
    face_left_path VARCHAR(255) DEFAULT NULL,
    face_right_path VARCHAR(255) DEFAULT NULL,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT fk_resident_posko FOREIGN KEY (posko_code)
        REFERENCES poskos(code) ON DELETE CASCADE ON UPDATE CASCADE
) ENGINE=InnoDB;
```

### 5.3. Tabel `distribution_logs`
```sql
CREATE TABLE distribution_logs (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    created_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
    claim_date DATE NOT NULL,                 -- Tanggal lokal (YYYY-MM-DD) untuk hitung kuota 3x/hari
    resident_id INT DEFAULT NULL,
    resident_name VARCHAR(150) NOT NULL,      -- Nama warga atau 'Logistik Galon Air'
    resident_nik VARCHAR(64) DEFAULT NULL,
    scanner_posko_code VARCHAR(32) NOT NULL,  -- Posko tempat scan/kejadian
    resident_posko_code VARCHAR(32) DEFAULT NULL,
    status ENUM(
        'SUCCESS',
        'GALLON_USE',
        'DENIED_WRONG_POSKO',
        'DENIED_EXCEEDED',
        'DENIED_NO_STOCK'
    ) NOT NULL,
    meal_sequence TINYINT DEFAULT NULL,       -- 1, 2, atau 3 (NULL jika gagal / galon)
    cost_rupiah INT NOT NULL DEFAULT 0,       -- 35000 untuk makan sukses, 22000 untuk galon, 0 untuk ditolak
    notes VARCHAR(255) DEFAULT NULL,
    INDEX idx_claim_daily (resident_id, claim_date, status),
    INDEX idx_posko_date (scanner_posko_code, claim_date, status),
    CONSTRAINT fk_log_posko FOREIGN KEY (scanner_posko_code)
        REFERENCES poskos(code) ON DELETE CASCADE ON UPDATE CASCADE,
    CONSTRAINT fk_log_resident FOREIGN KEY (resident_id)
        REFERENCES residents(id) ON DELETE SET NULL
) ENGINE=InnoDB;
```

## 6. Spesifikasi Detail Fitur & Alur Transaksi

### 6.1. Konstanta Bisnis & Keamanan
* Tarif Makanan: `MEAL_PRICE = 35000` (Rp 35.000 / porsi).
* Tarif Galon Air: `GALLON_PRICE = 22000` (Rp 22.000 / galon).
* Kuota Harian: `MAX_DAILY_MEALS = 3` (reset otomatis mengikuti `CURDATE()` / tanggal lokal server).
* Proteksi Admin: Header `X-Admin-Token` divalidasi oleh `verify_admin_auth()` terhadap environment variable `ADMIN_TOKEN` (default dev: `posko-secret-admin-2026`). Operasi mutasi sensitif (tambah/hapus posko, restock, hapus warga, sinkronisasi kamera detektor) wajib menyertakan token ini.

### 6.2. Pencegahan Race Condition di `POST /api/meals/scan` (MySQL `FOR UPDATE`)
1. Mulai transaksi database (`session.begin()`).
2. Cari warga berdasarkan `qr_token` atau `nik` dengan **`SELECT ... FROM residents WHERE qr_token = :q OR nik = :q FOR UPDATE`**. Jika tidak ditemukan, kembalikan `404`.
3. Kunci baris posko aktif dengan **`SELECT ... FROM poskos WHERE code = :active_posko FOR UPDATE`**.
4. **Cek Lintas Posko:** Jika `resident.posko_code != active_posko`, catat ke `distribution_logs` dengan status `DENIED_WRONG_POSKO` (`cost_rupiah = 0`), `COMMIT`, dan kembalikan **HTTP 403** (`Cross-Posko Claim Denied`).
5. **Cek Kuota Harian:** Hitung jumlah klaim `SUCCESS` warga tersebut pada `claim_date = CURDATE()`. Jika sudah `>= 3`, catat ke `distribution_logs` dengan status `DENIED_EXCEEDED`, `COMMIT`, dan kembalikan **HTTP 429 / 400** beserta indikator `meal_count = 3`.
6. **Cek Stok Makanan Posko:** Jika `posko.stock_meals <= 0`, catat ke `distribution_logs` dengan status `DENIED_NO_STOCK`, `COMMIT`, dan kembalikan **HTTP 400**.
7. **Klaim Berhasil:** Kurangi `posko.stock_meals -= 1`, hitung `meal_sequence = current_count + 1`, simpan log `SUCCESS` dengan `cost_rupiah = 35000`, `COMMIT`, dan kembalikan **HTTP 200** beserta detail warga & `meal_sequence` (`1/3`, `2/3`, atau `3/3`).

### 6.3. Registrasi Warga & Akuisisi Wajah 3 Sudut (`POST /api/residents`)
* Menerima data warga + 3 foto (`front`, `left`, `right`) baik berupa Base64 data URL (dari Webcam) maupun file `multipart/form-data`.
* Jika `nik` kosong, generate `NO-NIK-<8-char-UUID>`.
* Jika `nik` sudah terdaftar di posko mana pun, tolak dengan kode error `CROSS_POSKO_BLOCKED` (HTTP 409).
* Simpan 3 foto ke `known_faces/<resident_id>_<sanitized_nik>/front.jpg`, `left.jpg`, `right.jpg` setelah memvalidasi `Path.resolve()` berada di dalam direktori `known_faces/`.
* Picu pelatihan ulang model LBPH (`face_recog.train_async()`) di background thread.

### 6.4. Integrasi Kamera Tenda AI (`posko_detector.py`, `face_recog.py`, `POST /api/detector/sync`)
* `face_recog.py`: Menggunakan Haar Cascade (`haarcascade_frontalface_default.xml`) untuk mendeteksi wajah dan `cv2.face.LBPHFaceRecognizer_create()` untuk mengenali ID warga dari dataset `known_faces/`. Menyimpan model terlatih ke `known_faces/lbph_model.yml`.
* `posko_detector.py`:
  * Memuat model YOLO `final.pt` (jika file tidak ditemukan, otomatis fallback ke `yolo11n.pt` / `yolov8n.pt`) dengan tracker `bytetrack.yaml`.
  * Menggambar bounding box:
    * **Hijau**: Warga terdaftar di posko aktif & kuota makan `< 3`.
    * **Merah**: Warga terdaftar di posko lain (Cross-Posko) ATAU kuota makan sudah `3/3`.
    * **Kuning**: Wajah belum dikenali / belum terdaftar.
  * Hotkeys di jendela kamera:
    * `s`: Klaim makan massal untuk seluruh warga valid (Hijau) yang terlihat di frame saat ini.
    * `a`: Restock cepat `+20` porsi makanan di posko aktif.
    * `g`: Catat pemakaian `-1` galon air di posko aktif.
    * `p`: Beralih ke posko aktif berikutnya.
    * `r`: Muat ulang & latih ulang dataset wajah LBPH.
* State kamera dikirim berkala ke `POST /api/detector/sync` dan ditampilkan pada panel **Live Camera Tent Monitor** di dashboard web.

## 7. Yang Diserahkan ke Builder (Left to Builder)
* Pemilihan warna palet spesifik Tailwind CSS selama kontras badge (`Stok Aman` hijau, `STOK MENIPIS` kuning/oranye, `STOK HABIS` merah berdenyut) jelas.
* Frekuensi tepat *oscillator* Web Audio API (misal 880Hz->1320Hz untuk sukses, 220Hz sawtooth untuk kuota habis, 150Hz square untuk ditolak).
* Interval polling dashboard (misal setiap 2–3 detik untuk sinkronisasi status kamera tenda dan kartu metrik).

