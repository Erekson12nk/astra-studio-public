"""Поиск лица и оценка «яркости эмоции» через MediaPipe Face Landmarker."""

from __future__ import annotations

import math
import statistics
from dataclasses import dataclass, field

import cv2
import mediapipe as mp
import numpy as np
from mediapipe.tasks.python import BaseOptions
from mediapipe.tasks.python import vision

from .config import FaceConfig
from .models import ensure_model

# Ключевые точки контура лица в сетке из 478 точек.
LM_NOSE_TIP = 1
LM_LEFT_EDGE = 234
LM_RIGHT_EDGE = 454
# Пять опорных точек (пары усредняются): глаз слева на кадре, глаз справа, кончик носа,
# уголок рта слева, уголок рта справа — как в шаблоне FFHQ для улучшения лица.
LM_FIVE = ((33, 133), (362, 263), (1, 1), (61, 61), (291, 291))


@dataclass
class FaceHit:
    """Одно найденное лицо на кадре."""

    ts: float
    box: tuple                      # (x, y, w, h) в пикселях кадра
    score: float                    # итоговый рейтинг 0..100
    emotion: float                  # «сырая» экспрессия
    label: str                      # человеческое название эмоции
    yaw: float
    sharpness: float
    expr_n: float = 0.0             # нормированные составляющие рейтинга
    size_n: float = 0.0
    pose_n: float = 0.0
    blend: dict = field(default_factory=dict)
    frame: np.ndarray | None = None
    turn: float = 0.0               # куда повёрнута голова на кадре: + вправо, - влево
    points: tuple = ()              # глаза, нос, уголки рта (x, y) — для выравнивания лица

    @property
    def center(self):
        x, y, w, h = self.box
        return x + w / 2, y + h / 2


def _label_for(b: dict) -> str:
    g = b.get
    smile = max(g("mouthSmileLeft", 0.0), g("mouthSmileRight", 0.0))
    brow_up = max(g("browInnerUp", 0.0), g("browOuterUpLeft", 0.0), g("browOuterUpRight", 0.0))
    brow_dn = max(g("browDownLeft", 0.0), g("browDownRight", 0.0))
    eye_wide = max(g("eyeWideLeft", 0.0), g("eyeWideRight", 0.0))
    frown = max(g("mouthFrownLeft", 0.0), g("mouthFrownRight", 0.0))
    sneer = max(g("noseSneerLeft", 0.0), g("noseSneerRight", 0.0))
    jaw = g("jawOpen", 0.0)

    if jaw > 0.45 and smile > 0.35:
        return "смех"
    if jaw > 0.5 and (eye_wide > 0.35 or brow_up > 0.3):
        return "крик / шок"
    if brow_dn > 0.4 and (frown > 0.25 or sneer > 0.2):
        return "злость"
    if brow_up > 0.35 and eye_wide > 0.25:
        return "удивление"
    if smile > 0.45:
        return "улыбка"
    if frown > 0.3:
        return "недовольство"
    if jaw > 0.4:
        return "речь / возглас"
    return "экспрессия"


def _score(expr_n: float, size_n: float, pose_n: float, sharp_n: float) -> float:
    return 100.0 * expr_n * (0.62 + 0.18 * size_n + 0.12 * pose_n + 0.08 * sharp_n)


def rescore(hits: list) -> list:
    """Пересчитывает резкость относительно самого стрима.

    Абсолютный порог резкости бесполезен: у ламповой записи 2007-го вся
    картинка «мыльная», и штрафовать её не за что — важно лишь то, что кадр
    не хуже соседних по этому же стриму.
    """
    if not hits:
        return hits
    med = statistics.median(h.sharpness for h in hits) or 1.0
    for h in hits:
        sharp_n = min(h.sharpness / (med * 1.4), 1.0)
        h.score = _score(h.expr_n, h.size_n, h.pose_n, sharp_n)
    return hits


class FaceScorer:
    """Оборачивает Face Landmarker: кадр -> лучшее лицо с оценкой эмоции."""

    def __init__(self, cfg: FaceConfig | None = None, max_faces: int = 3):
        self.cfg = cfg or FaceConfig()
        opts = vision.FaceLandmarkerOptions(
            base_options=BaseOptions(model_asset_path=str(ensure_model("face_landmarker.task"))),
            running_mode=vision.RunningMode.IMAGE,
            num_faces=max_faces,
            output_face_blendshapes=True,
            output_facial_transformation_matrixes=False,
            min_face_detection_confidence=0.4,
            min_face_presence_confidence=0.4,
        )
        self._lm = vision.FaceLandmarker.create_from_options(opts)

    def close(self):
        self._lm.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def _expression(self, blend: dict) -> float:
        return sum(w * blend.get(k, 0.0) for k, w in self.cfg.weights.items())

    def analyze(self, frame_bgr: np.ndarray, ts: float = 0.0, keep_frame: bool = False):
        h, w = frame_bgr.shape[:2]
        rgb = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB)
        res = self._lm.detect(mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb))
        if not res.face_landmarks:
            return None

        best: FaceHit | None = None
        for i, lms in enumerate(res.face_landmarks):
            xs = np.array([p.x for p in lms]) * w
            ys = np.array([p.y for p in lms]) * h
            x0, x1 = float(xs.min()), float(xs.max())
            y0, y1 = float(ys.min()), float(ys.max())
            fw, fh = x1 - x0, y1 - y0
            if fw <= 2 or fh <= 2:
                continue

            size_ratio = fw / w
            if size_ratio < self.cfg.min_face_ratio:
                continue

            # поворот головы: насколько нос смещён к одному из краёв лица
            nose_x = xs[LM_NOSE_TIP]
            dl, dr = abs(nose_x - xs[LM_LEFT_EDGE]), abs(xs[LM_RIGHT_EDGE] - nose_x)
            yaw = abs(dl - dr) / max(dl + dr, 1e-6)
            if yaw > self.cfg.max_yaw:
                continue

            crop = frame_bgr[max(int(y0), 0):int(y1), max(int(x0), 0):int(x1)]
            if crop.size == 0:
                continue
            sharp = float(cv2.Laplacian(cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY),
                                        cv2.CV_64F).var())
            if sharp < self.cfg.blur_floor:
                continue

            blend = {}
            if res.face_blendshapes and i < len(res.face_blendshapes):
                blend = {c.category_name: float(c.score) for c in res.face_blendshapes[i]}
            expr = self._expression(blend)

            expr_n = math.tanh(max(expr, 0.0) / 2.2)              # 0..1, насыщается
            size_n = min(size_ratio / 0.28, 1.0)
            pose_n = 1.0 - min(yaw / max(self.cfg.max_yaw, 1e-6), 1.0)

            hit = FaceHit(ts=ts, box=(int(x0), int(y0), int(fw), int(fh)),
                          score=_score(expr_n, size_n, pose_n, min(sharp / 200.0, 1.0)),
                          emotion=expr, label=_label_for(blend), yaw=yaw,
                          sharpness=sharp, expr_n=expr_n, size_n=size_n, pose_n=pose_n,
                          blend=blend, frame=frame_bgr if keep_frame else None,
                          turn=(dl - dr) / max(dl + dr, 1e-6),
                          points=tuple(
                              (float(xs[a] + xs[b]) / 2, float(ys[a] + ys[b]) / 2)
                              for a, b in LM_FIVE))
            if best is None or hit.score > best.score:
                best = hit
        return best
