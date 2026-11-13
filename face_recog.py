import base64
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
        confidence_threshold: float = 85.0,
    ) -> None:
        self.base_dir = Path(base_dir)
        self.base_dir.mkdir(parents=True, exist_ok=True)
        self.model_path = self.base_dir / "lbph_model.yml"
        self.confidence_threshold = confidence_threshold
        self._lock = threading.Lock()

        cascade_data = getattr(cv2, "data", None)
        cascade_file = (
            Path(cascade_data.haarcascades) / "haarcascade_frontalface_default.xml"
            if cascade_data is not None and hasattr(cascade_data, "haarcascades")
            else Path("haarcascade_frontalface_default.xml")
        )
        if cascade_file.exists():
            self.face_cascade = cv2.CascadeClassifier(str(cascade_file))
        else:
            self.face_cascade = cv2.CascadeClassifier()

        self._recognizer = self._create_recognizer()
        self._is_trained = False
        if self.model_path.exists() and self._recognizer is not None:
            try:
                self._recognizer.read(str(self._ensure_safe_path(self.model_path)))
                self._is_trained = True
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
                stripped = stripped.split(",", 1)[1]
            raw_bytes = base64.b64decode(stripped)
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

        for angle, raw_input in angle_inputs.items():
            img_bytes = self._decode_image_bytes(raw_input)
            if img_bytes is None:
                continue
            resident_dir.mkdir(parents=True, exist_ok=True)
            file_path = self._ensure_safe_path(resident_dir / f"{angle}.jpg")
            file_path.write_bytes(img_bytes)
            saved_paths[angle] = f"/known_faces/{resident_dir.name}/{angle}.jpg"

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
                                minNeighbors=4,
                                minSize=(30, 30),
                            )
                            if not self.face_cascade.empty()
                            else ()
                        )
                        if len(detected) > 0:
                            x, y, w, h = max(detected, key=lambda b: b[2] * b[3])
                            face_roi = gray[y : y + h, x : x + w]
                        else:
                            face_roi = gray

                        resized = cv2.resize(face_roi, (160, 160))
                        faces.append(resized)
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
                return True
            except Exception as exc:
                logger.warning("LBPH training failed: %s", exc)
                self._is_trained = False
                return False

    def train_async(self) -> threading.Thread:
        thread = threading.Thread(target=self.train_model, daemon=True)
        thread.start()
        return thread

    def recognize_faces(self, bgr_frame: np.ndarray) -> list[dict[str, Any]]:
        if bgr_frame is None or bgr_frame.size == 0:
            return []

        if len(bgr_frame.shape) == 3:
            gray = cv2.cvtColor(bgr_frame, cv2.COLOR_BGR2GRAY)
        else:
            gray = bgr_frame

        if not self.face_cascade.empty():
            detected = self.face_cascade.detectMultiScale(
                gray,
                scaleFactor=1.1,
                minNeighbors=5,
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
                resident_id: int | None = None
                confidence: float = 999.0
                if self._is_trained and self._recognizer is not None:
                    roi = cv2.resize(gray[y : y + h, x : x + w], (160, 160))
                    label, conf = self._recognizer.predict(roi)
                    confidence = float(conf)
                    if confidence <= self.confidence_threshold:
                        resident_id = int(label)

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
