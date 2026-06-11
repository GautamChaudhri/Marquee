"""SCRFD ONNX face detection and poster face-area measurement."""

from __future__ import annotations

import logging
from pathlib import Path

import cv2
import numpy as np
import onnxruntime as ort

from marquee.core.pipeline_config import pipeline_settings
from marquee.ml.hardware import create_onnx_session

logger = logging.getLogger(__name__)


class FaceDetector:
    """Lightweight SCRFD detector using the InsightFace ONNX output layout."""

    def __init__(
        self,
        model_path: str | Path | None = None,
        *,
        input_size: tuple[int, int] = (640, 640),
        execution_provider: str | None = None,
    ):
        self.model_path = Path(model_path or pipeline_settings.FACE_MODEL_PATH)
        self.input_size = input_size
        self.execution_provider = execution_provider
        self._session: ort.InferenceSession | None = None
        self._center_cache: dict[tuple[int, int, int], np.ndarray] = {}

    @property
    def session(self) -> ort.InferenceSession:
        if self._session is None:
            if not self.model_path.exists():
                raise FileNotFoundError(f"SCRFD model not found: {self.model_path}")
            self._session = create_onnx_session(
                self.model_path,
                execution_provider=self.execution_provider,
            )
        return self._session

    def detect(self, image_bgr: np.ndarray) -> list[tuple[float, float, float, float]]:
        image_height, image_width = image_bgr.shape[:2]
        input_width, input_height = self.input_size
        image_ratio = image_height / image_width
        model_ratio = input_height / input_width
        if image_ratio > model_ratio:
            resized_height = input_height
            resized_width = int(resized_height / image_ratio)
        else:
            resized_width = input_width
            resized_height = int(resized_width * image_ratio)
        det_scale = resized_height / image_height

        resized = cv2.resize(image_bgr, (resized_width, resized_height))
        detector_input = np.zeros((input_height, input_width, 3), dtype=np.uint8)
        detector_input[:resized_height, :resized_width] = resized
        blob = cv2.dnn.blobFromImage(
            detector_input,
            1.0 / 128.0,
            self.input_size,
            (127.5, 127.5, 127.5),
            swapRB=True,
        )

        input_name = self.session.get_inputs()[0].name
        outputs = self.session.run(None, {input_name: blob})
        feature_map_count = len(outputs) // 3
        if feature_map_count not in {3, 5}:
            feature_map_count = len(outputs) // 2
        strides = [8, 16, 32] if feature_map_count == 3 else [8, 16, 32, 64, 128]

        scores_list: list[np.ndarray] = []
        boxes_list: list[np.ndarray] = []
        for index, stride in enumerate(strides):
            scores = outputs[index].reshape(-1)
            bbox_predictions = outputs[index + feature_map_count].reshape(-1, 4) * stride
            feature_height = input_height // stride
            feature_width = input_width // stride
            cache_key = (feature_height, feature_width, stride)
            anchor_centers = self._center_cache.get(cache_key)
            if anchor_centers is None:
                grid_x, grid_y = np.meshgrid(
                    np.arange(feature_width),
                    np.arange(feature_height),
                )
                anchor_centers = np.stack((grid_x, grid_y), axis=-1).astype(np.float32)
                anchor_centers = (anchor_centers * stride).reshape(-1, 2)
                if bbox_predictions.shape[0] == anchor_centers.shape[0] * 2:
                    anchor_centers = np.repeat(anchor_centers, 2, axis=0)
                self._center_cache[cache_key] = anchor_centers

            positive = np.where(scores >= pipeline_settings.FACE_CONFIDENCE_THRESHOLD)[0]
            if positive.size == 0:
                continue
            distances = bbox_predictions[positive]
            centers = anchor_centers[positive]
            boxes = np.column_stack(
                (
                    centers[:, 0] - distances[:, 0],
                    centers[:, 1] - distances[:, 1],
                    centers[:, 0] + distances[:, 2],
                    centers[:, 1] + distances[:, 3],
                )
            )
            scores_list.append(scores[positive])
            boxes_list.append(boxes / det_scale)

        if not boxes_list:
            return []

        scores = np.concatenate(scores_list)
        boxes = np.concatenate(boxes_list)
        order = scores.argsort()[::-1]
        keep = self._nms(boxes[order], scores[order])
        result = boxes[order][keep]
        result[:, [0, 2]] = np.clip(result[:, [0, 2]], 0, image_width)
        result[:, [1, 3]] = np.clip(result[:, [1, 3]], 0, image_height)
        return [tuple(map(float, box)) for box in result]

    @staticmethod
    def _nms(boxes: np.ndarray, scores: np.ndarray) -> list[int]:
        x1, y1, x2, y2 = boxes.T
        areas = np.maximum(0, x2 - x1 + 1) * np.maximum(0, y2 - y1 + 1)
        order = scores.argsort()[::-1]
        keep: list[int] = []
        while order.size > 0:
            current = int(order[0])
            keep.append(current)
            xx1 = np.maximum(x1[current], x1[order[1:]])
            yy1 = np.maximum(y1[current], y1[order[1:]])
            xx2 = np.minimum(x2[current], x2[order[1:]])
            yy2 = np.minimum(y2[current], y2[order[1:]])
            width = np.maximum(0.0, xx2 - xx1 + 1)
            height = np.maximum(0.0, yy2 - yy1 + 1)
            overlap = (width * height) / (areas[current] + areas[order[1:]] - width * height)
            remaining = np.where(overlap <= pipeline_settings.FACE_NMS_THRESHOLD)[0]
            order = order[remaining + 1]
        return keep

    def face_area(self, image_bgr: np.ndarray) -> float:
        height, width = image_bgr.shape[:2]
        image_area = float(width * height)
        if image_area <= 0:
            return 0.0
        total = sum(max(0.0, x2 - x1) * max(0.0, y2 - y1) for x1, y1, x2, y2 in self.detect(image_bgr))
        return float(min(total / image_area, 1.0))
