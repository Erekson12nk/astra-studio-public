from __future__ import annotations

import os
import shutil
import tempfile
import urllib.request
from pathlib import Path
from typing import Callable, Optional

BASE = "https://storage.googleapis.com/mediapipe-models/"

MODEL_URLS = {
    "blaze_face_short_range.tflite":
        BASE + "face_detector/blaze_face_short_range/float16/1/"
               "blaze_face_short_range.tflite",
    "face_landmarker.task":
        BASE + "face_landmarker/face_landmarker/float16/1/face_landmarker.task",
    "selfie_multiclass_256x256.tflite":
        BASE + "image_segmenter/selfie_multiclass_256x256/float32/1/"
               "selfie_multiclass_256x256.tflite",
    "arcface_w600k_mbf.onnx":
        "https://huggingface.co/immich-app/buffalo_s/resolve/main/"
        "recognition/model.onnx",
}

CACHE_DIR = "ave_cache"


def module():
    try:
        import mediapipe
    except ImportError:
        return None
    return mediapipe


def ascii_safe_path(path) -> str:
    path = str(path)
    try:
        path.encode("ascii")
        return path
    except UnicodeEncodeError:
        pass
    for base in (tempfile.gettempdir(), os.environ.get("SystemDrive", "C:") + os.sep):
        try:
            base.encode("ascii")
        except UnicodeEncodeError:
            continue
        folder = os.path.join(base, CACHE_DIR)
        try:
            os.makedirs(folder, exist_ok=True)
            target = os.path.join(folder, os.path.basename(path))
            if (not os.path.isfile(target)
                    or os.path.getsize(target) != os.path.getsize(path)):
                shutil.copy2(path, target)
            return target
        except OSError:
            continue
    return path


def download(name: str, path, url: Optional[str] = None,
             report: Optional[Callable[[str], None]] = None) -> str:
    path = Path(str(path))
    url = url or MODEL_URLS.get(name)
    if not url:
        raise RuntimeError("не знаю, откуда качать модель %s" % name)
    path.parent.mkdir(parents=True, exist_ok=True)
    if report:
        report("качаю модель %s" % name)
    partial = path.with_suffix(path.suffix + ".part")
    urllib.request.urlretrieve(url, str(partial))
    partial.replace(path)
    return str(path)


def ensure(name: str, folder, url: Optional[str] = None,
           report: Optional[Callable[[str], None]] = None) -> str:
    path = Path(str(folder)) / name
    if path.exists() and path.stat().st_size > 0:
        return ascii_safe_path(path)
    download(name, path, url=url, report=report)
    return ascii_safe_path(path)
