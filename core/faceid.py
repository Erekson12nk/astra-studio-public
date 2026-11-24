from __future__ import annotations

import os
from typing import Optional

import numpy as np

from . import models as _models

MODEL_NAME = "arcface_w600k_mbf.onnx"

TEMPLATE = np.float32([
    [38.2946, 51.6963],
    [73.5318, 51.5014],
    [56.0252, 71.7366],
    [56.1396, 92.2848],
])

CHIP = 112

MIN_FACE = 48


def runtime():
    try:
        import onnxruntime
    except ImportError:
        return None
    return onnxruntime


def available() -> bool:
    return runtime() is not None


def model_path(given=None, report=None) -> str:
    if given and os.path.isfile(str(given)):
        return _models.ascii_safe_path(given)
    cache = os.path.join(os.path.expanduser("~"), ".cache", "auto_vertical_editor")
    return _models.ensure(MODEL_NAME, cache, report=report)


class Embedder:
    def __init__(self, model=None, report=None):
        ort = runtime()
        if ort is None:
            raise RuntimeError(
                "нет onnxruntime — опознание стримера недоступно:  py -m pip install onnxruntime")
        import cv2

        self.cv2 = cv2
        self.session = ort.InferenceSession(model_path(model, report),
                                            providers=["CPUExecutionProvider"])
        self.input = self.session.get_inputs()[0].name

    def chip(self, frame, box, points=None):
        cv2 = self.cv2
        if points is not None and len(points) >= 4:
            source = np.float32([points[i] for i in (0, 1, 2, 3)])
            matrix, _ = cv2.estimateAffinePartial2D(source, TEMPLATE, method=cv2.LMEDS)
            if matrix is not None:
                return cv2.warpAffine(frame, matrix, (CHIP, CHIP), borderValue=0)
        x, y, w, h = box[0], box[1], box[2], box[3]
        pad = max(w, h) * 0.1
        x0 = int(max(0, x - pad))
        y0 = int(max(0, y - pad))
        x1 = int(min(frame.shape[1], x + w + pad))
        y1 = int(min(frame.shape[0], y + h + pad))
        if x1 - x0 < 8 or y1 - y0 < 8:
            return None
        return cv2.resize(frame[y0:y1, x0:x1], (CHIP, CHIP),
                          interpolation=cv2.INTER_AREA)

    def embed(self, frame, box, points=None, strict=True) -> Optional[np.ndarray]:
        if strict and (points is None or len(points) < 4
                       or max(box[2], box[3]) < MIN_FACE):
            return None
        chip = self.chip(frame, box, points)
        if chip is None or chip.size == 0:
            return None
        image = self.cv2.cvtColor(chip, self.cv2.COLOR_BGR2RGB).astype(np.float32)
        image = (image - 127.5) / 127.5
        blob = np.transpose(image, (2, 0, 1))[None]
        vector = self.session.run(None, {self.input: blob})[0][0]
        norm = float(np.linalg.norm(vector))
        if norm < 1e-6:
            return None
        return vector / norm

    def close(self) -> None:
        self.session = None


def similarity(left, right) -> float:
    if left is None or right is None:
        return -1.0
    return float(np.dot(left, right))


def average(vectors):
    kept = [v for v in vectors if v is not None]
    if not kept:
        return None
    total = np.sum(np.stack(kept), axis=0)
    norm = float(np.linalg.norm(total))
    if norm < 1e-6:
        return None
    return total / norm
