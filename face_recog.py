import base64
import binascii
import io
import logging
import re
import shutil
import threading
from pathlib import Path
from typing import Any

import cv2
import numpy as np
from PIL import Image

logger = logging.getLogger(__name__)

KNOWN_FACES_DIR = Path("known_faces")


class FaceRecognitionService:
    def __init__(
        self,
        base_dir: Path | str = KNOWN_FACES_DIR,
        confidence_threshold: float = 135.0,
    ) -> None:
        self.base_dir = Path(base_dir)
        self.base_dir.mkdir(parents=True, exist_ok=True)
        self.model_path = self.base_dir / "lbph_model.yml"
        self.confidence_threshold = confidence_threshold
        self._lock = threading.Lock()

        cascade_data = getattr(cv2, "data", None)
        local_cascade = Path(__file__).resolve().parent / "haarcascade_frontalface_default.xml"
        cascade_file = (
            Path(cascade_data.haarcascades) / "haarcascade_frontalface_default.xml"
            if cascade_data is not None and hasattr(cascade_data, "haarcascades")
            else local_cascade
        )
        if not cascade_file.exists() and local_cascade.exists():
            cascade_file = local_cascade

        if cascade_file.exists():
            self.face_cascade = cv2.CascadeClassifier(str(cascade_file))
        else:
            self.face_cascade = cv2.CascadeClassifier()

        self._recognizer = self._create_recognizer()
        self._is_trained = False
        self._model_mtime: float = 0.0
        self._reload_model_if_updated()

    def _reload_model_if_updated(self) -> None:
        if self._recognizer is None or not self.model_path.exists():
            return
        try:
            safe_model = self._ensure_safe_path(self.model_path)
            mtime = safe_model.stat().st_mtime
            if not self._is_trained or mtime > self._model_mtime:
                self._recognizer.read(str(safe_model))
                self._is_trained = True
                self._model_mtime = mtime
        except Exception as exc:
            logger.warning("Could not load existing LBPH model: %s", exc)

    @staticmethod
    def _create_recognizer():
        if hasattr(cv2, "face") and hasattr(cv2.face, "LBPHFaceRecognizer_create"):
            return cv2.face.LBPHFaceRecognizer_create()
        return None

    @staticmethod
    def sanitize_segment(value: str) -> str:
        cleaned = re.sub(r"[^A-Za-z0-9_-]", "_", str(value or "").strip())
        cleaned = cleaned.strip("._")
        return cleaned or "unknown"

    def _ensure_safe_path(self, target_path: Path) -> Path:
        resolved_base = self.base_dir.resolve()
        resolved_target = target_path.resolve()
        if not resolved_target.is_relative_to(resolved_base):
            raise ValueError(
                f"Path traversal blocked: '{target_path}' resolves outside '{resolved_base}'"
            )
        return resolved_target

    def get_resident_dir_name(self, resident_id: int, nik: str) -> str:
        safe_id = int(resident_id)
        safe_nik = self.sanitize_segment(nik)
        return f"{safe_id}_{safe_nik}"

    def get_resident_dir(self, resident_id: int, nik: str) -> Path:
        dir_name = self.get_resident_dir_name(resident_id, nik)
        target_dir = self.base_dir / dir_name
        resolved = self._ensure_safe_path(target_dir)
        if resolved == self.base_dir.resolve():
            raise ValueError("Invalid resident directory resolving to base_dir")
        return resolved

    @staticmethod
    def _decode_image_bytes(data: bytes | str | None) -> bytes | None:
        if data is None:
            return None
        raw_bytes: bytes
        if isinstance(data, bytes):
            if not data:
                return None
            raw_bytes = data
        elif isinstance(data, str):
            stripped = data.strip()
            if not stripped:
                return None
            if stripped.startswith("data:") and "," in stripped:
                stripped = stripped.split(",", 1)[1].strip()
            try:
                raw_bytes = base64.b64decode(re.sub(r"\s+", "", stripped), validate=True)
                if not raw_bytes:
                    raise ValueError("Empty base64 image data")
            except (binascii.Error, ValueError) as exc:
                raise ValueError("Invalid base64 image data") from exc
        else:
            return None

        try:
            with Image.open(io.BytesIO(raw_bytes)) as img:
                rgb = img.convert("RGB")
                out = io.BytesIO()
                rgb.save(out, format="JPEG", quality=90)
                return out.getvalue()
        except Exception:
            return raw_bytes

    def save_three_angle_photos(
        self,
        resident_id: int,
        nik: str,
        front_data: bytes | str | None = None,
        left_data: bytes | str | None = None,
        right_data: bytes | str | None = None,
        *,
        front_bytes: bytes | str | None = None,
        left_bytes: bytes | str | None = None,
        right_bytes: bytes | str | None = None,
    ) -> dict[str, str | None]:
        front_input = front_data if front_data is not None else front_bytes
        left_input = left_data if left_data is not None else left_bytes
        right_input = right_data if right_data is not None else right_bytes

        resident_dir = self.get_resident_dir(resident_id, nik)
        angle_inputs = {
            "front": front_input,
            "left": left_input,
            "right": right_input,
        }
        saved_paths: dict[str, str | None] = {
            "front": None,
            "left": None,
            "right": None,
        }

        try:
            for angle, raw_input in angle_inputs.items():
                img_bytes = self._decode_image_bytes(raw_input)
                if img_bytes is None:
                    continue
                resident_dir.mkdir(parents=True, exist_ok=True)
                file_path = self._ensure_safe_path(resident_dir / f"{angle}.jpg")
                file_path.write_bytes(img_bytes)
                saved_paths[angle] = f"/known_faces/{resident_dir.name}/{angle}.jpg"
        except Exception:
            if resident_dir.exists() and resident_dir.is_dir():
                shutil.rmtree(resident_dir, ignore_errors=True)
            raise

        return saved_paths

    def delete_resident_photos(self, resident_id: int, nik: str = "") -> None:
        safe_id = int(resident_id)
        if nik:
            resident_dir = self.get_resident_dir(safe_id, nik)
            if resident_dir.exists() and resident_dir.is_dir():
                shutil.rmtree(resident_dir, ignore_errors=True)

        prefix = f"{safe_id}_"
        if self.base_dir.exists():
            for child in self.base_dir.iterdir():
                if child.is_dir() and child.name.startswith(prefix):
                    safe_child = self._ensure_safe_path(child)
                    if safe_child != self.base_dir.resolve():
                        shutil.rmtree(safe_child, ignore_errors=True)

    def train_model(self) -> bool:
        with self._lock:
            try:
                if not self.base_dir.exists():
                    self._is_trained = False
                    return False

                faces: list[np.ndarray] = []
                labels: list[int] = []

                for child in sorted(self.base_dir.iterdir()):
                    if not child.is_dir():
                        continue
                    safe_dir = self._ensure_safe_path(child)
                    parts = safe_dir.name.split("_", 1)
                    if not parts or not parts[0].isdigit():
                        continue
                    resident_id = int(parts[0])

                    for img_file in sorted(safe_dir.glob("*.jpg")):
                        safe_img = self._ensure_safe_path(img_file)
                        gray = cv2.imread(str(safe_img), cv2.IMREAD_GRAYSCALE)
                        if gray is None or gray.size == 0:
                            continue

                        detected = (
                            self.face_cascade.detectMultiScale(
                                gray,
                                scaleFactor=1.1,
                                minNeighbors=3,
                                minSize=(30, 30),
                            )
                            if not self.face_cascade.empty()
                            else ()
                        )
                        if len(detected) > 0:
                            x, y, w, h = max(detected, key=lambda b: b[2] * b[3])
                            face_roi = gray[y : y + h, x : x + w]
                            resized_face = cv2.resize(face_roi, (160, 160))
                            faces.append(resized_face)
                            labels.append(resident_id)
                            faces.append(cv2.equalizeHist(resized_face))
                            labels.append(resident_id)

                        # Also train on full/center region for fallback when eyeglasses/backlight prevent tight Haar box
                        resized_full = cv2.resize(gray, (160, 160))
                        faces.append(resized_full)
                        labels.append(resident_id)
                        faces.append(cv2.equalizeHist(resized_full))
                        labels.append(resident_id)

                if not faces or self._recognizer is None:
                    self._is_trained = False
                    if self.model_path.exists():
                        self._ensure_safe_path(self.model_path).unlink(missing_ok=True)
                    return False

                self._recognizer.train(faces, np.array(labels, dtype=np.int32))
                safe_model_path = self._ensure_safe_path(self.model_path)
                self._recognizer.write(str(safe_model_path))
                self._is_trained = True
                self._model_mtime = safe_model_path.stat().st_mtime
                return True
            except Exception as exc:
                logger.warning("LBPH training failed: %s", exc)
                self._is_trained = False
                return False

    def train_async(self) -> threading.Thread:
        thread = threading.Thread(target=self.train_model, daemon=True)
        thread.start()
        return thread

    def _predict_gray_roi(self, gray_roi: np.ndarray) -> tuple[int | None, float]:
        if not self._is_trained or self._recognizer is None or gray_roi.size == 0:
            return (None, 999.0)
        resized = cv2.resize(gray_roi, (160, 160))
        lbl1, conf1 = self._recognizer.predict(resized)
        lbl2, conf2 = self._recognizer.predict(cv2.equalizeHist(resized))
        if float(conf2) < float(conf1):
            best_lbl, best_conf = int(lbl2), float(conf2)
        else:
            best_lbl, best_conf = int(lbl1), float(conf1)
        if best_conf <= self.confidence_threshold:
            return (best_lbl, best_conf)
        return (None, best_conf)

    def predict_person_crop(self, bgr_crop: np.ndarray) -> tuple[int | None, float]:
        if bgr_crop is None or bgr_crop.size == 0:
            return (None, 999.0)
        if len(bgr_crop.shape) == 3:
            gray = cv2.cvtColor(bgr_crop, cv2.COLOR_BGR2GRAY)
        else:
            gray = bgr_crop

        with self._lock:
            self._reload_model_if_updated()
            if not self._is_trained or self._recognizer is None:
                return (None, 999.0)

            candidates: list[np.ndarray] = [gray]
            h_c, w_c = gray.shape[:2]
            if h_c >= 60 and w_c >= 60:
                # Upper-center head region inside person bounding box
                y_end = max(40, int(h_c * 0.7))
                x_pad = int(w_c * 0.15)
                head_crop = gray[0:y_end, x_pad : max(x_pad + 40, w_c - x_pad)]
                if head_crop.size > 0:
                    candidates.insert(0, head_crop)

            best_rid: int | None = None
            best_conf: float = 999.0
            for cand in candidates:
                rid, conf = self._predict_gray_roi(cand)
                if conf < best_conf:
                    best_conf = conf
                    best_rid = rid
            return (best_rid, best_conf)

    def recognize_faces(self, bgr_frame: np.ndarray) -> list[dict[str, Any]]:
        if bgr_frame is None or bgr_frame.size == 0:
            return []

        if len(bgr_frame.shape) == 3:
            gray = cv2.cvtColor(bgr_frame, cv2.COLOR_BGR2GRAY)
        else:
            gray = bgr_frame

        with self._lock:
            self._reload_model_if_updated()

        if not self.face_cascade.empty():
            detected = self.face_cascade.detectMultiScale(
                gray,
                scaleFactor=1.1,
                minNeighbors=4,
                minSize=(40, 40),
            )
            if len(detected) == 0:
                detected = self.face_cascade.detectMultiScale(
                    cv2.equalizeHist(gray),
                    scaleFactor=1.08,
                    minNeighbors=3,
                    minSize=(40, 40),
                )
        elif self._is_trained and float(np.std(gray)) > 5.0:
            h_img, w_img = gray.shape[:2]
            detected = [(0, 0, w_img, h_img)] if h_img >= 40 and w_img >= 40 else []
        else:
            detected = []

        results: list[dict[str, Any]] = []
        with self._lock:
            for x, y, w, h in detected:
                roi = gray[y : y + h, x : x + w]
                resident_id, confidence = self._predict_gray_roi(roi)
                results.append(
                    {
                        "bbox": (int(x), int(y), int(w), int(h)),
                        "resident_id": resident_id,
                        "confidence": confidence,
                    }
                )
        return results

    def recognize_face(self, bgr_frame: np.ndarray) -> list[dict[str, Any]]:
        return self.recognize_faces(bgr_frame)
