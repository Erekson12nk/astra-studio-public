from __future__ import annotations

import os
import sys
from pathlib import Path

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if os.path.isdir(os.path.join(_ROOT, "core")) and _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from core import models as _models

from .config import MODELS_DIR

MODEL_URLS = {name: _models.MODEL_URLS[name] for name in
              ("face_landmarker.task", "selfie_multiclass_256x256.tflite")}


def ensure_model(name: str) -> Path:
    return Path(_models.ensure(name, MODELS_DIR, url=MODEL_URLS[name],
                               report=lambda message: print("[models] " + message)))
