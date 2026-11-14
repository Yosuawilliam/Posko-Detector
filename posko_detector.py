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

    def fetch_residents_from_api(self) -> None:
        url = f"{self.api_base_url}/api/residents"
        req = urllib.request.Request(url, method="GET")
        try:
            with urllib.request.urlopen(req, timeout=2.5) as resp:
                residents = json.loads(resp.read().decode("utf-8"))
                if isinstance(residents, list):
                    for r_obj in residents:
                        if isinstance(r_obj, dict) and "id" in r_obj:
                            self.resident_cache[int(r_obj["id"])] = r_obj
        except Exception as exc:
            logger.debug("Could not pre-fetch residents from %s: %s", url, exc)

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
                for r_obj in body.get("all_residents", []):
                    if isinstance(r_obj, dict) and "id" in r_obj:
                        self.resident_cache[int(r_obj["id"])] = r_obj
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

    def _format_resident_border_label(
        self,
        rid: int | None,
    ) -> tuple[str, tuple[int, int, int], str]:
        if rid is not None and rid not in self.resident_cache:
            self.fetch_residents_from_api()

        resident_info = self.resident_cache.get(rid) if rid is not None else None
        box_status, color = classify_resident_box(resident_info, self.active_posko)

        if resident_info is not None:
            name = resident_info.get("full_name", f"Warga #{rid}")
            posko = resident_info.get("posko_code", self.active_posko)
            used_meals = int(resident_info.get("today_meals", 0))
            max_m = int(resident_info.get("max_meals", MAX_DAILY_MEALS))
            remaining_meals = max(0, max_m - used_meals)
            label = f"{name} | Sisa Makanan: {remaining_meals}/{max_m} Porsi [{posko}]"
        else:
            label = "Belum Terdaftar | Sisa Makanan: -"

        return label, color, box_status

    @staticmethod
    def _draw_border_with_top_label(
        canvas: np.ndarray,
        x1: int,
        y1: int,
        x2: int,
        y2: int,
        label: str,
        color: tuple[int, int, int],
        bottom_tag: str | None = None,
    ) -> None:
        cv2.rectangle(canvas, (x1, y1), (x2, y2), color, 2)

        font = cv2.FONT_HERSHEY_SIMPLEX
        font_scale = 0.58
        thickness = 2
        (text_w, text_h), baseline = cv2.getTextSize(label, font, font_scale, thickness)
        pad_x, pad_y = 8, 6
        box_h = text_h + baseline + pad_y * 2
        box_w = text_w + pad_x * 2

        # Place label badge right above the top border (x1, y1); clamp inside frame if y1 is near top edge
        if y1 - box_h >= 4:
            bg_y1 = y1 - box_h
            bg_y2 = y1
        else:
            bg_y1 = max(0, y1)
            bg_y2 = min(canvas.shape[0], y1 + box_h)

        bg_x1 = max(0, x1)
        bg_x2 = min(canvas.shape[1], bg_x1 + box_w)

        # White background badge with colored border right above the top border
        cv2.rectangle(canvas, (bg_x1, bg_y1), (bg_x2, bg_y2), (255, 255, 255), -1)
        cv2.rectangle(canvas, (bg_x1, bg_y1), (bg_x2, bg_y2), color, 2)
        text_color = (15, 23, 42)
        text_y = bg_y2 - pad_y - baseline // 2
        cv2.putText(
            canvas,
            label,
            (bg_x1 + pad_x, text_y),
            font,
            font_scale,
            text_color,
            thickness,
            cv2.LINE_AA,
        )

        if bottom_tag:
            cv2.putText(
                canvas,
                bottom_tag,
                (x1 + 6, max(20, y2 - 8)),
                font,
                0.45,
                color,
                1,
                cv2.LINE_AA,
            )

    def process_frame(
        self,
        frame: np.ndarray,
    ) -> tuple[np.ndarray, int, list[int]]:
        annotated = frame.copy()
        h_frame, w_frame = frame.shape[:2]
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
        matched_face_indices: set[int] = set()

        for x1, y1, x2, y2, tid in person_boxes:
            matched_rid: int | None = None
            best_conf: float = 999.0

            for idx, f_det in enumerate(face_detections):
                fx, fy, fw, fh = f_det["bbox"]
                # Skip synthetic full-frame fallback box when real person_boxes exist
                if fx == 0 and fy == 0 and fw == w_frame and fh == h_frame:
                    matched_face_indices.add(idx)
                    continue
                cx = fx + fw / 2.0
                cy = fy + fh / 2.0
                if x1 <= cx <= x2 and y1 <= cy <= y2:
                    matched_face_indices.add(idx)
                    if f_det["resident_id"] is not None and f_det["confidence"] < best_conf:
                        matched_rid = int(f_det["resident_id"])
                        best_conf = float(f_det["confidence"])

            if matched_rid is None:
                crop_y1, crop_y2 = max(0, y1), min(h_frame, y2)
                crop_x1, crop_x2 = max(0, x1), min(w_frame, x2)
                if crop_y2 > crop_y1 and crop_x2 > crop_x1:
                    crop_rid, _crop_conf = self.face_service.predict_person_crop(
                        frame[crop_y1:crop_y2, crop_x1:crop_x2]
                    )
                    if crop_rid is not None:
                        matched_rid = int(crop_rid)

            if matched_rid is not None and matched_rid not in detected_resident_ids:
                detected_resident_ids.append(matched_rid)

            label, color, _status = self._format_resident_border_label(matched_rid)
            tag = f"Person #{tid}" if tid is not None else "Person"
            self._draw_border_with_top_label(
                annotated, x1, y1, x2, y2, label, color, bottom_tag=tag
            )

        for idx, f_det in enumerate(face_detections):
            if idx in matched_face_indices:
                continue
            fx, fy, fw, fh = f_det["bbox"]
            rid = f_det["resident_id"]
            if rid is not None and rid not in detected_resident_ids:
                detected_resident_ids.append(rid)

            label, color, _status = self._format_resident_border_label(rid)
            self._draw_border_with_top_label(
                annotated, fx, fy, fx + fw, fy + fh, label, color
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

        h_frame, w_frame = frame.shape[:2]
        hud_h = 54
        y_top = max(0, h_frame - hud_h)
        cv2.rectangle(frame, (0, y_top), (w_frame, h_frame), (255, 255, 255), -1)
        cv2.line(frame, (0, y_top), (w_frame, y_top), (203, 213, 225), 2)
        cv2.putText(
            frame,
            header,
            (12, y_top + 22),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.55,
            (15, 23, 42),
            2,
        )
        cv2.putText(
            frame,
            controls,
            (12, y_top + 44),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.44,
            (71, 85, 105),
            1,
        )

    def run(self) -> None:
        self.face_service.train_model()
        self.fetch_residents_from_api()
        self.sync_with_server(0, [], action=None)
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
