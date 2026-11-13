import argparse
import json
import logging
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

import cv2
import numpy as np

from auth import ADMIN_TOKEN
from face_recog import FaceRecognitionService
from models import MAX_DAILY_MEALS

logger = logging.getLogger(__name__)

# BGR Color Constants
COLOR_GREEN = (0, 200, 0)      # Valid resident of active posko with quota < 3
COLOR_RED = (0, 0, 220)        # Wrong posko OR daily meal quota (3/3) full
COLOR_YELLOW = (0, 215, 255)   # Unrecognized / unregistered face

HANDLE_HOTKEY_MAP: dict[int, str] = {
    ord("s"): "serve_all",
    ord("a"): "restock_20",
    ord("g"): "use_gallon",
    ord("p"): "switch_posko",
    ord("r"): "reload_faces",
}


def classify_resident_box(
    resident_data: dict[str, Any] | None,
    active_posko: str,
) -> tuple[str, tuple[int, int, int]]:
    """Classify a detected resident for bounding-box status and BGR color."""
    if resident_data is None:
        return ("UNREGISTERED", COLOR_YELLOW)

    res_posko = str(resident_data.get("posko_code", "")).strip().upper()
    target_posko = str(active_posko or "").strip().upper()
    if res_posko != target_posko:
        return ("WRONG_POSKO", COLOR_RED)

    today_meals = int(resident_data.get("today_meals", 0))
    max_meals = int(resident_data.get("max_meals", MAX_DAILY_MEALS))
    if today_meals >= max_meals:
        return ("QUOTA_FULL", COLOR_RED)

    return ("VALID", COLOR_GREEN)


class PoskoTentDetector:
    """AI Tent Camera Detector combining YOLO + ByteTrack + LBPH Face Recognition."""

    def __init__(
        self,
        api_base_url: str = "http://127.0.0.1:8000",
        active_posko: str = "POSKO-A",
        admin_token: str | None = None,
        camera_index: int | str = 0,
        model_path: str | Path = "final.pt",
        sync_interval: float = 1.5,
        face_service: FaceRecognitionService | None = None,
    ) -> None:
        self.api_base_url = api_base_url.rstrip("/")
        self.active_posko = active_posko.strip().upper()
        self.admin_token = admin_token if admin_token is not None else ADMIN_TOKEN
        self.camera_index = camera_index
        self.model_path = Path(model_path)
        self.sync_interval = sync_interval
        self.face_service = face_service or FaceRecognitionService()

        self._yolo_model: Any = None
        self.resident_cache: dict[int, dict[str, Any]] = {}
        self.posko_summary: dict[str, Any] = {}
        self.last_action_banner: str = ""
        self.last_sync_time: float = 0.0

    def resolve_model_weights(self) -> str:
        if self.model_path.exists():
            return str(self.model_path)
        fallback_v11 = Path("yolo11n.pt")
        if fallback_v11.exists():
            return str(fallback_v11)
        return "yolov8n.pt"

    def load_yolo_model(self) -> Any:
        if self._yolo_model is None:
            from ultralytics import YOLO

            weights = self.resolve_model_weights()
            logger.info("Loading YOLO model from '%s'...", weights)
            self._yolo_model = YOLO(weights)
        return self._yolo_model

    def sync_with_server(
        self,
        person_count: int,
        detected_resident_ids: list[int],
        action: str | None = None,
    ) -> dict[str, Any] | None:
        url = f"{self.api_base_url}/api/detector/sync"
        payload = {
            "active_posko": self.active_posko,
            "person_count": int(person_count),
            "detected_resident_ids": [int(rid) for rid in detected_resident_ids],
            "action": action,
        }
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=data,
            headers={
                "Content-Type": "application/json",
                "X-Admin-Token": self.admin_token,
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=3.0) as resp:
                body = json.loads(resp.read().decode("utf-8"))
                self.active_posko = str(body.get("active_posko", self.active_posko))
                self.posko_summary = body.get("posko_summary") or {}
                for r_obj in body.get("detected_residents", []):
                    if isinstance(r_obj, dict) and "id" in r_obj:
                        self.resident_cache[int(r_obj["id"])] = r_obj
                if body.get("action_result"):
                    self.last_action_banner = json.dumps(body["action_result"])
                self.last_sync_time = time.time()
                return body
        except (urllib.error.URLError, TimeoutError, ValueError) as exc:
            logger.warning("Failed to sync with backend %s: %s", url, exc)
            return None

    def handle_hotkey(
        self,
        key_code: int,
        person_count: int = 0,
        detected_resident_ids: list[int] | None = None,
    ) -> dict[str, Any] | None:
        action = HANDLE_HOTKEY_MAP.get(key_code & 0xFF)
        if action is None:
            return None
        if action == "reload_faces":
            self.face_service.train_model()
        return self.sync_with_server(
            person_count=person_count,
            detected_resident_ids=detected_resident_ids or [],
            action=action,
        )

    def process_frame(
        self,
        frame: np.ndarray,
    ) -> tuple[np.ndarray, int, list[int]]:
        annotated = frame.copy()
        person_boxes: list[tuple[int, int, int, int, int | None]] = []

        if self._yolo_model is not None:
            results = self._yolo_model.track(
                frame,
                persist=True,
                classes=[0],
                tracker="bytetrack.yaml",
                verbose=False,
            )
            if results and len(results) > 0:
                boxes = getattr(results[0], "boxes", None)
                if boxes is not None and boxes.xyxy is not None:
                    xyxy_list = boxes.xyxy.cpu().numpy().astype(int).tolist()
                    track_ids = (
                        boxes.id.cpu().numpy().astype(int).tolist()
                        if getattr(boxes, "id", None) is not None
                        else [None] * len(xyxy_list)
                    )
                    for coords, tid in zip(xyxy_list, track_ids):
                        x1, y1, x2, y2 = coords
                        person_boxes.append((x1, y1, x2, y2, tid))

        face_detections = self.face_service.recognize_faces(frame)
        detected_resident_ids: list[int] = []

        for f_det in face_detections:
            fx, fy, fw, fh = f_det["bbox"]
            rid = f_det["resident_id"]
            if rid is not None and rid not in detected_resident_ids:
                detected_resident_ids.append(rid)

            resident_info = self.resident_cache.get(rid) if rid is not None else None
            box_status, color = classify_resident_box(resident_info, self.active_posko)

            if resident_info is not None:
                name = resident_info.get("full_name", f"ID:{rid}")
                posko = resident_info.get("posko_code", "?")
                meals = resident_info.get("today_meals", 0)
                max_m = resident_info.get("max_meals", MAX_DAILY_MEALS)
                label = f"{name} [{posko}] ({meals}/{max_m}) - {box_status}"
            else:
                label = f"UNREGISTERED ({box_status})"

            cv2.rectangle(annotated, (fx, fy), (fx + fw, fy + fh), color, 2)
            cv2.putText(
                annotated,
                label,
                (fx, max(20, fy - 8)),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.55,
                color,
                2,
            )

        for x1, y1, x2, y2, tid in person_boxes:
            cv2.rectangle(annotated, (x1, y1), (x2, y2), (255, 180, 0), 1)
            tag = f"Person #{tid}" if tid is not None else "Person"
            cv2.putText(
                annotated,
                tag,
                (x1, min(annotated.shape[0] - 10, y2 + 16)),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.45,
                (255, 180, 0),
                1,
            )

        person_count = max(len(person_boxes), len(face_detections))
        self._draw_hud(annotated, person_count, len(detected_resident_ids))
        return annotated, person_count, detected_resident_ids

    def _draw_hud(
        self,
        frame: np.ndarray,
        person_count: int,
        recognized_count: int,
    ) -> None:
        stock_meals = self.posko_summary.get("stock_meals", "?")
        stock_gallons = self.posko_summary.get("stock_gallons", "?")
        header = (
            f"POSKO: {self.active_posko} | Orang: {person_count} "
            f"(Dikenali: {recognized_count}) | Porsi: {stock_meals} | Galon: {stock_gallons}"
        )
        controls = "[S] Serve Valid  [A] +20 Porsi  [G] -1 Galon  [P] Ganti Posko  [R] Reload Wajah  [Q] Keluar"

        cv2.rectangle(frame, (0, 0), (frame.shape[1], 58), (20, 20, 20), -1)
        cv2.putText(
            frame,
            header,
            (12, 24),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.6,
            (255, 255, 255),
            2,
        )
        cv2.putText(
            frame,
            controls,
            (12, 48),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.45,
            (180, 220, 255),
            1,
        )

    def run(self) -> None:
        self.load_yolo_model()
        cap = cv2.VideoCapture(self.camera_index)
        if not cap.isOpened():
            logger.error("Cannot open camera source: %s", self.camera_index)
            return

        try:
            while True:
                ok, frame = cap.read()
                if not ok or frame is None:
                    break

                annotated, person_count, resident_ids = self.process_frame(frame)
                now = time.time()
                if now - self.last_sync_time >= self.sync_interval:
                    self.sync_with_server(person_count, resident_ids, action=None)

                cv2.imshow("Posko AI Tent Detector", annotated)
                key = cv2.waitKey(1) & 0xFF
                if key == ord("q"):
                    break
                if key in HANDLE_HOTKEY_MAP:
                    self.handle_hotkey(key, person_count, resident_ids)
        finally:
            cap.release()
            cv2.destroyAllWindows()


def main() -> None:
    parser = argparse.ArgumentParser(description="Posko AI Tent Camera Detector")
    parser.add_argument("--api", default="http://127.0.0.1:8000", help="Backend API base URL")
    parser.add_argument("--posko", default="POSKO-A", help="Initial active posko code")
    parser.add_argument("--camera", default=0, help="Camera index or video stream path")
    parser.add_argument("--model", default="final.pt", help="YOLO weights path")
    parser.add_argument("--token", default=ADMIN_TOKEN, help="X-Admin-Token value")
    args = parser.parse_args()

    cam_src: int | str = int(args.camera) if str(args.camera).isdigit() else args.camera
    detector = PoskoTentDetector(
        api_base_url=args.api,
        active_posko=args.posko,
        admin_token=args.token,
        camera_index=cam_src,
        model_path=args.model,
    )
    detector.run()


if __name__ == "__main__":
    main()
