"""Вырезание стримера (голова + плечи) из кадра в RGBA с мягким краем."""

from __future__ import annotations

import cv2
import mediapipe as mp
import numpy as np
from mediapipe.tasks.python import BaseOptions
from mediapipe.tasks.python import vision

from .models import ensure_model

# Классы selfie_multiclass: 0 фон, 1 волосы, 2 кожа тела, 3 лицо, 4 одежда, 5 прочее
BACKGROUND_CLASS = 0


class PersonCutter:
    def __init__(self):
        opts = vision.ImageSegmenterOptions(
            base_options=BaseOptions(
                model_asset_path=str(ensure_model("selfie_multiclass_256x256.tflite"))),
            running_mode=vision.RunningMode.IMAGE,
            output_category_mask=True,
            output_confidence_masks=False,
        )
        self._seg = vision.ImageSegmenter.create_from_options(opts)

    def close(self):
        self._seg.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    # ------------------------------------------------------------------ helpers

    @staticmethod
    def roi_for_face(frame_shape, box, up=0.95, down=2.6, side=1.45):
        """Прямоугольник «голова + плечи» вокруг найденного лица."""
        h, w = frame_shape[:2]
        x, y, fw, fh = box
        cx = x + fw / 2
        x0 = int(max(cx - fw * (0.5 + side), 0))
        x1 = int(min(cx + fw * (0.5 + side), w))
        y0 = int(max(y - fh * up, 0))
        y1 = int(min(y + fh * (1 + down), h))
        return x0, y0, x1, y1

    def _person_mask(self, roi_bgr: np.ndarray) -> np.ndarray:
        rgb = cv2.cvtColor(roi_bgr, cv2.COLOR_BGR2RGB)
        res = self._seg.segment(mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb))
        cat = res.category_mask.numpy_view()
        mask = (cat != BACKGROUND_CLASS).astype(np.uint8) * 255
        if mask.shape[:2] != roi_bgr.shape[:2]:
            mask = cv2.resize(mask, (roi_bgr.shape[1], roi_bgr.shape[0]),
                              interpolation=cv2.INTER_LINEAR)
        return mask

    @staticmethod
    def _keep_blob_at(mask: np.ndarray, point) -> np.ndarray:
        """Оставляем только ту фигуру, внутри которой находится лицо."""
        n, labels = cv2.connectedComponents((mask > 127).astype(np.uint8))
        if n <= 1:
            return mask
        px, py = int(point[0]), int(point[1])
        px = min(max(px, 0), labels.shape[1] - 1)
        py = min(max(py, 0), labels.shape[0] - 1)
        lab = labels[py, px]
        if lab == 0:  # лицо попало в «фон» — берём самую крупную фигуру
            counts = np.bincount(labels.ravel())
            counts[0] = 0
            lab = int(counts.argmax())
        return np.where(labels == lab, 255, 0).astype(np.uint8)

    @staticmethod
    def _ellipse_fallback(shape, box) -> np.ndarray:
        m = np.zeros(shape[:2], np.uint8)
        x, y, w, h = box
        cv2.ellipse(m, (int(x + w / 2), int(y + h / 2)),
                    (int(w * 0.78), int(h * 0.92)), 0, 0, 360, 255, -1)
        return m

    # ------------------------------------------------------------------ public

    def cut(self, frame_bgr: np.ndarray, box, feather: int = 5,
            pad: int = 12, with_face: bool = False):
        """Возвращает RGBA-вырезку стримера, обрезанную по маске.

        with_face — вернуть ещё и место лица внутри вырезки (x, y, w, h): по нему
        сборка ставит крупный план.
        """
        x0, y0, x1, y1 = self.roi_for_face(frame_bgr.shape, box)
        roi = frame_bgr[y0:y1, x0:x1]
        if roi.size == 0:
            raise ValueError("Пустая область вокруг лица.")

        local_box = (box[0] - x0, box[1] - y0, box[2], box[3])
        try:
            mask = self._person_mask(roi)
            mask = self._keep_blob_at(mask, (local_box[0] + local_box[2] / 2,
                                             local_box[1] + local_box[3] / 2))
            if mask.mean() < 4:  # сегментация фактически ничего не нашла
                raise ValueError("empty mask")
        except Exception:
            mask = self._ellipse_fallback(roi.shape, local_box)

        k = max(3, int(min(roi.shape[:2]) * 0.012) | 1)
        mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((k, k), np.uint8))
        mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
        if feather > 0:
            fk = max(3, int(feather) | 1)
            mask = cv2.GaussianBlur(mask, (fk, fk), 0)

        ys, xs = np.where(mask > 8)
        if ys.size == 0:
            raise ValueError("Не удалось выделить стримера на кадре.")
        bx0, bx1 = max(xs.min() - pad, 0), min(xs.max() + pad + 1, mask.shape[1])
        by0, by1 = max(ys.min() - pad, 0), min(ys.max() + pad + 1, mask.shape[0])

        rgba = np.dstack([roi[by0:by1, bx0:bx1], mask[by0:by1, bx0:bx1]])
        if with_face:
            return rgba, (local_box[0] - bx0, local_box[1] - by0, box[2], box[3])
        return rgba
