"""YOLO person detection — composition features (rec 5).

Counts people and measures how much of the poster they occupy, separating
"one giant floating torso" from "small ensemble lineup" — a composition
axis the face detector alone cannot see (faces miss bodies-from-behind,
silhouettes, and distant figures).

Part of the EXTRA_QUALITY_ENABLED feature group. The model file is
optional: if it is missing the features are skipped with a logged warning
(this is an enhancement, not core infrastructure — per design 04 §13 only
failures that break every candidate abort a run).

Export the ONNX model once (~5MB, needs the `ultralytics` package):

    pip install ultralytics
    yolo export model=yolo11n.pt format=onnx dynamic=True
    mv yolo11n.onnx marquee/ml/models/

The decoder below handles the standard YOLOv8/11 detection output
(1, 84, N): 4 xywh rows + 80 COCO class scores, no baked-in NMS.
"""

from __future__ import annotations

import logging
from pathlib import Path

import cv2
import numpy as np
import onnxruntime as ort

from marquee.core.pipeline_config import pipeline_settings
from marquee.ml.hardware import create_onnx_session

logger = logging.getLogger(__name__)

_PERSON_CLASS = 0  # COCO class id
_INPUT_SIZE = 640
_NMS_IOU = 0.45


class PersonDetector:
    """Lazily loaded YOLO11n/YOLOv8n person detector (ONNX, no ultralytics dep)."""

    def __init__(
        self,
        model_path: str | Path | None = None,
        *,
        execution_provider: str | None = None,
        session: ort.InferenceSession | None = None,
    ):
        self.model_path = Path(model_path or pipeline_settings.PERSON_MODEL_PATH)
        self._execution_provider = execution_provider
        self._session = session

    @property
    def available(self) -> bool:
        return self._session is not None or self.model_path.exists()

    @property
    def session(self) -> ort.InferenceSession:
        if self._session is None:
            if not self.model_path.exists():
                raise FileNotFoundError(
                    f"Person detector model not found: {self.model_path}. "
                    "Export it with: yolo export model=yolo11n.pt format=onnx dynamic=True"
                )
            self._session = create_onnx_session(
                self.model_path,
                execution_provider=self._execution_provider,
            )
        return self._session

    def detect(self, image_bgr: np.ndarray) -> list[tuple[float, float, float, float]]:
        """Person boxes (x1, y1, x2, y2) in original image coordinates."""
        height, width = image_bgr.shape[:2]
        scale = min(_INPUT_SIZE / width, _INPUT_SIZE / height)
        new_w, new_h = round(width * scale), round(height * scale)
        resized = cv2.resize(image_bgr, (new_w, new_h))
        canvas = np.full((_INPUT_SIZE, _INPUT_SIZE, 3), 114, dtype=np.uint8)
        canvas[:new_h, :new_w] = resized

        blob = canvas[:, :, ::-1].astype(np.float32) / 255.0  # BGR -> RGB
        blob = blob.transpose(2, 0, 1)[np.newaxis]

        input_name = self.session.get_inputs()[0].name
        output = self.session.run(None, {input_name: blob})[0]

        # (1, 84, N) -> (N, 84): rows of [cx, cy, w, h, 80 class scores]
        predictions = np.squeeze(output, axis=0).T
        person_scores = predictions[:, 4 + _PERSON_CLASS]
        keep = person_scores >= pipeline_settings.PERSON_CONFIDENCE_THRESHOLD
        if not keep.any():
            return []
        boxes_xywh = predictions[keep, :4]
        scores = person_scores[keep]

        boxes = np.column_stack(
            (
                boxes_xywh[:, 0] - boxes_xywh[:, 2] / 2,
                boxes_xywh[:, 1] - boxes_xywh[:, 3] / 2,
                boxes_xywh[:, 0] + boxes_xywh[:, 2] / 2,
                boxes_xywh[:, 1] + boxes_xywh[:, 3] / 2,
            )
        )
        keep_idx = self._nms(boxes, scores)
        boxes = boxes[keep_idx] / scale
        boxes[:, [0, 2]] = np.clip(boxes[:, [0, 2]], 0, width)
        boxes[:, [1, 3]] = np.clip(boxes[:, [1, 3]], 0, height)
        return [tuple(map(float, box)) for box in boxes]

    @staticmethod
    def _nms(boxes: np.ndarray, scores: np.ndarray) -> list[int]:
        x1, y1, x2, y2 = boxes.T
        areas = np.maximum(0, x2 - x1) * np.maximum(0, y2 - y1)
        order = scores.argsort()[::-1]
        keep: list[int] = []
        while order.size > 0:
            current = int(order[0])
            keep.append(current)
            xx1 = np.maximum(x1[current], x1[order[1:]])
            yy1 = np.maximum(y1[current], y1[order[1:]])
            xx2 = np.minimum(x2[current], x2[order[1:]])
            yy2 = np.minimum(y2[current], y2[order[1:]])
            inter = np.maximum(0.0, xx2 - xx1) * np.maximum(0.0, yy2 - yy1)
            union = areas[current] + areas[order[1:]] - inter
            overlap = np.where(union > 0, inter / union, 0.0)
            order = order[np.where(overlap <= _NMS_IOU)[0] + 1]
        return keep

    def person_features(self, image_bgr: np.ndarray) -> dict[str, float]:
        height, width = image_bgr.shape[:2]
        image_area = float(width * height)
        boxes = self.detect(image_bgr)
        areas = [max(0.0, x2 - x1) * max(0.0, y2 - y1) for x1, y1, x2, y2 in boxes]
        total = min(sum(areas) / image_area, 1.0) if image_area > 0 else 0.0
        return {
            "person_count": float(len(boxes)),
            "person_area_frac": float(total),
        }
