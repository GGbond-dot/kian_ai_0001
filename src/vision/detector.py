"""YOLO + QR detection module using OpenVINO and OpenCV."""
from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

import cv2
import numpy as np

from src.utils.logging_config import get_logger

logger = get_logger(__name__)


@dataclass
class DetectResult:
    detected: bool = False
    qr_detected: bool = False
    qr_data: str = ""
    class_id: int = -1
    confidence: float = 0.0
    bbox_center_x: float = 0.0
    bbox_center_y: float = 0.0
    bbox_width: float = 0.0
    bbox_height: float = 0.0
    elapsed_ms: float = 0.0
    detections: list = None  # all YOLO boxes: [(x1,y1,x2,y2,conf,cls), ...]
    qr_corners: list = None  # QR corner points: [(x,y), ...]
    fps: float = 0.0

    def __post_init__(self):
        if self.detections is None:
            self.detections = []
        if self.qr_corners is None:
            self.qr_corners = []


def _letterbox(
    img: np.ndarray, new_shape: tuple[int, int], color: tuple[int, int, int] = (114, 114, 114)
) -> tuple[np.ndarray, tuple[float, float, float]]:
    shape = img.shape[:2]
    r = min(new_shape[0] / shape[0], new_shape[1] / shape[1])
    new_unpad = (int(round(shape[1] * r)), int(round(shape[0] * r)))
    dw, dh = new_shape[1] - new_unpad[0], new_shape[0] - new_unpad[1]
    dw, dh = dw // 2, dh // 2
    img = cv2.resize(img, new_unpad, interpolation=cv2.INTER_LINEAR)
    top, bottom = dh, new_shape[0] - new_unpad[1] - dh
    left, right = dw, new_shape[1] - new_unpad[0] - dw
    img = cv2.copyMakeBorder(img, top, bottom, left, right, cv2.BORDER_CONSTANT, value=color)
    return img, (r, dw, dh)


def _preprocess(
    img: np.ndarray, input_size: tuple[int, int]
) -> tuple[np.ndarray, float, float, float]:
    img, (ratio, dw, dh) = _letterbox(img, input_size)
    img = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
    img = img.astype(np.float32) / 255.0
    img = img.transpose(2, 0, 1)
    img = np.expand_dims(img, axis=0)
    return img, ratio, dw, dh


def _postprocess(
    output: np.ndarray, ratio: float, dw: float, dh: float, conf_threshold: float
) -> list[tuple[float, float, float, float, float, int]]:
    detections = []
    for pred in output[0]:
        x1, y1, x2, y2, conf, cls = pred
        if conf < conf_threshold:
            continue
        x1 = (x1 - dw) / ratio
        y1 = (y1 - dh) / ratio
        x2 = (x2 - dw) / ratio
        y2 = (y2 - dh) / ratio
        detections.append((x1, y1, x2, y2, float(conf), int(cls)))
    return detections


class VisionDetector:
    def __init__(
        self,
        model_path: str,
        device: str = "CPU",
        input_size: tuple[int, int] = (640, 640),
        conf_threshold: float = 0.3,
        enable_qr: bool = True,
    ) -> None:
        self.input_size = input_size
        self.conf_threshold = conf_threshold
        self.enable_qr = enable_qr

        if not Path(model_path).exists():
            raise FileNotFoundError(f"YOLO model not found: {model_path}")

        import openvino as ov

        core = ov.Core()
        compiled_model = core.compile_model(model_path, device)
        self._infer_request = compiled_model.create_infer_request()
        logger.info("VisionDetector: YOLO model loaded on %s from %s", device, model_path)

        self._qr_detector = cv2.QRCodeDetector() if enable_qr else None
        self._fps_times: list[float] = []

    def detect(self, frame: np.ndarray) -> DetectResult:
        t0 = time.time()
        result = DetectResult()

        # QR detection
        if self._qr_detector is not None:
            qr_data, qr_corners = self._detect_qr(frame)
            if qr_data:
                result.qr_detected = True
                result.qr_data = qr_data
                result.qr_corners = qr_corners
                if len(qr_corners) == 4:
                    result.bbox_center_x = sum(p[0] for p in qr_corners) / 4.0
                    result.bbox_center_y = sum(p[1] for p in qr_corners) / 4.0
                    logger.info("QR detected: %s", qr_data)

        # YOLO inference
        blob, ratio, dw, dh = _preprocess(frame, self.input_size)
        self._infer_request.set_input_tensor(_make_ov_tensor(blob))
        self._infer_request.infer()
        output = self._infer_request.get_output_tensor().data
        detections = _postprocess(output, ratio, dw, dh, self.conf_threshold)

        if detections:
            best = detections[0]
            x1, y1, x2, y2, conf, cls = best
            result.detected = True
            result.confidence = float(conf)
            result.class_id = int(cls)
            result.bbox_width = float(abs(x2 - x1))
            result.bbox_height = float(abs(y2 - y1))
            result.detections = detections
            if not result.qr_detected:
                result.bbox_center_x = float((x1 + x2) / 2.0)
                result.bbox_center_y = float((y1 + y2) / 2.0)

        result.elapsed_ms = (time.time() - t0) * 1000.0
        self._fps_times.append(time.time())
        if len(self._fps_times) > 30:
            self._fps_times = self._fps_times[-30:]
        result.fps = self.fps

        return result

    def _detect_qr(self, frame: np.ndarray) -> tuple[str, list[tuple[float, float]]]:
        if self._qr_detector is None:
            return "", []
        found, points = self._qr_detector.detect(frame)
        if found and points is not None and len(points) > 0:
            try:
                data, _ = self._qr_detector.decode(frame, points)
            except cv2.error:
                return "", []
            if data:
                return data, points.reshape(-1, 2).tolist()
        return "", []

    @property
    def fps(self) -> float:
        if len(self._fps_times) < 2:
            return 0.0
        return (len(self._fps_times) - 1) / (self._fps_times[-1] - self._fps_times[0])


def _make_ov_tensor(blob: np.ndarray) -> Any:
    import openvino as ov

    return ov.Tensor(blob)


COCO_CLASSES = [
    "box", "bicycle", "car", "motorcycle", "airplane", "bus", "train", "truck", "boat",
    "traffic light", "fire hydrant", "stop sign", "parking meter", "bench",
    "bird", "cat", "dog", "horse", "sheep", "cow", "elephant", "bear", "zebra", "giraffe",
    "backpack", "umbrella", "handbag", "tie", "suitcase",
    "frisbee", "skis", "snowboard", "sports ball", "kite", "baseball bat", "baseball glove",
    "skateboard", "surfboard", "tennis racket",
    "bottle", "wine glass", "cup", "fork", "knife", "spoon", "bowl",
    "banana", "apple", "sandwich", "orange", "broccoli", "carrot", "hot dog", "pizza",
    "donut", "cake",
    "chair", "couch", "potted plant", "bed", "dining table", "toilet",
    "tv", "laptop", "mouse", "remote", "keyboard", "cell phone",
    "microwave", "oven", "toaster", "sink", "refrigerator",
    "book", "clock", "vase", "scissors", "teddy bear", "hair drier", "toothbrush",
]


def draw_overlay(
    frame: np.ndarray,
    detections: list,
    qr_data: str,
    qr_corners: list,
    matched: bool,
    fps: float,
) -> None:
    """Draw YOLO bboxes + QR overlay on frame in-place (参照 vision_server.py line 72-103)."""
    for x1, y1, x2, y2, conf, cls in detections:
        x1, y1, x2, y2 = map(int, [x1, y1, x2, y2])
        cv2.rectangle(frame, (x1, y1), (x2, y2), (0, 255, 0), 2)
        label = f"{COCO_CLASSES[cls]} {conf:.2f}"
        (tw, th), _ = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, 0.5, 1)
        cv2.rectangle(frame, (x1, y1 - th - 4), (x1 + tw, y1), (0, 255, 0), -1)
        cv2.putText(frame, label, (x1, y1 - 4), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 0, 0), 1)

    if qr_corners and len(qr_corners) == 4:
        pts = np.array(qr_corners, dtype=np.int32)
        cv2.polylines(frame, [pts], True, (0, 255, 255), 2)
        if qr_data:
            cx = int(sum(p[0] for p in qr_corners) / 4)
            cy = int(min(p[1] for p in qr_corners)) - 10
            cv2.putText(frame, qr_data, (cx - 40, cy),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 255), 1)

    cv2.putText(frame, f"FPS: {fps:.1f}", (10, 25),
                cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 255), 2)

    status_parts = []
    if detections:
        status_parts.append("YOLO")
    if qr_data:
        status_parts.append("QR")
    if matched:
        status_parts.append("MATCHED")
    status = " | ".join(status_parts) if status_parts else "SEARCHING..."
    color = (0, 255, 0) if matched else (0, 165, 255)
    cv2.putText(frame, status, (10, frame.shape[0] - 12),
                cv2.FONT_HERSHEY_SIMPLEX, 0.6, color, 2)
