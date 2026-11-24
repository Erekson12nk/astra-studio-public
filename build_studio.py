from __future__ import annotations

import os
import shutil
import subprocess
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
NAME = "AstraStudio"
ENTRY = os.path.join(ROOT, "studio", "app.pyw")
OUT = os.path.join(ROOT, "build_out")

PACKAGES = ("studio", "core", "clipper", "audioclean", "clipmaker",
            "thumbmaker")
HIDDEN = ("faster_whisper", "ctranslate2",
          "onnxruntime", "sherpa_onnx", "numpy", "cv2", "mediapipe", "PIL",
          "clipper.cli", "audioclean.pipeline", "clipmaker")
SKIP = ("torch", "torchvision", "torchaudio", "matplotlib", "pytest",
        "IPython", "notebook", "gradio", "PyQt5", "PySide2")
COLLECT = ("faster_whisper", "mediapipe", "ctranslate2", "onnxruntime", "sherpa_onnx",
           "cv2", "tokenizers", "huggingface_hub")


def sources():
    kept = []
    for name in PACKAGES:
        place = os.path.join(ROOT, name)
        if os.path.isdir(place):
            kept.append((place, name))
    astra = os.path.join(ROOT, "astra")
    if os.path.isdir(astra):
        for item in os.listdir(astra):
            if item.endswith(".py") or item == "censor.txt":
                kept.append((os.path.join(astra, item), "astra"))
    return kept


def ffmpeg_folder():
    packed = os.path.join(ROOT, "astra", "dist", "Astra", "ffmpeg")
    if os.path.isdir(packed):
        return packed
    found = shutil.which("ffmpeg")
    return os.path.dirname(found) if found else ""


def command():
    args = [sys.executable, "-m", "PyInstaller", "--noconfirm", "--windowed",
            "--name", NAME, "--distpath", os.path.join(OUT, "dist"),
            "--workpath", os.path.join(OUT, "work"),
            "--specpath", OUT]
    for place, target in sources():
        args += ["--add-data", "%s%s%s" % (place, os.pathsep, target)]
    bin_place = ffmpeg_folder()
    if bin_place:
        args += ["--add-data", "%s%s%s" % (bin_place, os.pathsep, "ffmpeg")]
    for name in HIDDEN:
        args += ["--hidden-import", name]
    for name in COLLECT:
        args += ["--collect-all", name]
    for name in SKIP:
        args += ["--exclude-module", name]
    return args + [ENTRY]


def main():
    if not os.path.isfile(ENTRY):
        print("нет точки входа:", ENTRY)
        return 1
    args = command()
    print("собираю %s" % NAME, flush=True)
    print(" ".join(args[:8]), "…", flush=True)
    done = subprocess.run(args, cwd=ROOT)
    if done.returncode != 0:
        print("сборка не удалась, код", done.returncode)
        return done.returncode
    place = os.path.join(OUT, "dist", NAME)
    print("готово:", place)
    return 0


if __name__ == "__main__":
    sys.exit(main())
