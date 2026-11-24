"""thumbmaker — сборка YouTube-превью из превью ролика + яркой эмоции стримера."""

import os

# MediaPipe/TFLite иначе засыпают консоль своими INFO-строками
os.environ.setdefault("GLOG_minloglevel", "2")
os.environ.setdefault("TF_CPP_MIN_LOG_LEVEL", "3")

__version__ = "1.0.0"
