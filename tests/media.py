from __future__ import annotations

import os
import subprocess
import sys
import tempfile

CACHE = os.path.join(tempfile.gettempdir(), "astra_studio_tests")
os.makedirs(CACHE, exist_ok=True)
os.environ.setdefault("ASTRA_STUDIO_SETTINGS",
                      os.path.join(CACHE, "settings.json"))


def _run(args):
    subprocess.run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error"] + args,
                   check=True)


def _ok(path, least=1000):
    return os.path.isfile(path) and os.path.getsize(path) > least


def speech_wav(folder):
    path = os.path.join(folder, "speech.wav")
    if _ok(path):
        return path
    raw = os.path.join(folder, "speech_raw.wav")
    if not _ok(raw):
        if sys.platform != "win32":
            _run(["-f", "lavfi", "-i", "sine=frequency=440:duration=15",
                  "-ac", "1", "-ar", "16000", raw])
        else:
            script = (
                "Add-Type -AssemblyName System.Speech; "
                "$s = New-Object System.Speech.Synthesis.SpeechSynthesizer; "
                "$s.SetOutputToWaveFile('%s'); $s.Rate = -1; "
                "$s.Speak('Hello there. This is a test recording for the "
                "transcription pipeline. One two three four five. The quick "
                "brown fox jumps over the lazy dog.'); $s.Dispose()" % raw)
            subprocess.run(["powershell", "-NoProfile", "-Command", script],
                           check=True, capture_output=True)
    _run(["-i", raw, "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", path])
    return path


def build(folder=CACHE) -> dict:
    os.makedirs(folder, exist_ok=True)
    out = {}

    out["sample"] = os.path.join(folder, "sample.mp4")
    if not _ok(out["sample"]):
        _run(["-f", "lavfi", "-i", "testsrc2=size=640x360:rate=30:duration=4",
              "-f", "lavfi", "-i", "sine=frequency=440:duration=4",
              "-c:v", "libx264", "-preset", "ultrafast", "-crf", "30",
              "-c:a", "aac", "-shortest", out["sample"]])

    out["silent"] = os.path.join(folder, "novideo_audioless.mp4")
    if not _ok(out["silent"]):
        _run(["-f", "lavfi", "-i", "testsrc2=size=320x240:rate=25:duration=2",
              "-c:v", "libx264", "-preset", "ultrafast", "-crf", "35", "-an",
              out["silent"]])

    out["outro"] = os.path.join(folder, "outro.mp4")
    if not _ok(out["outro"]):
        _run(["-f", "lavfi", "-i", "testsrc2=size=640x360:rate=30:duration=2",
              "-f", "lavfi", "-i", "sine=frequency=880:duration=2",
              "-c:v", "libx264", "-preset", "ultrafast", "-crf", "30",
              "-c:a", "aac", "-shortest", out["outro"]])

    out["speech_wav"] = speech_wav(folder)
    out["speech"] = os.path.join(folder, "speech.mp4")
    if not _ok(out["speech"]):
        _run(["-f", "lavfi", "-i", "color=c=black:s=320x240:r=15",
              "-i", out["speech_wav"], "-c:v", "libx264", "-preset", "ultrafast",
              "-crf", "40", "-c:a", "aac", "-shortest", out["speech"]])

    out["long_wav"] = os.path.join(folder, "long.wav")
    if not _ok(out["long_wav"]):
        _run(["-stream_loop", "15", "-i", out["speech_wav"], "-t", "240",
              "-c:a", "pcm_s16le", "-ar", "16000", "-ac", "1", out["long_wav"]])

    out["long"] = os.path.join(folder, "long.mp4")
    if not _ok(out["long"]):
        _run(["-f", "lavfi", "-i", "testsrc2=size=1920x1080:rate=30:duration=240",
              "-i", out["long_wav"], "-c:v", "libx264", "-preset", "ultrafast",
              "-crf", "32", "-c:a", "aac", "-shortest", out["long"]])

    out["fragmented"] = os.path.join(folder, "bigfrag.mp4")
    out["flat"] = os.path.join(folder, "bigflat.mp4")
    if not _ok(out["fragmented"]):
        _run(["-f", "lavfi", "-i", "testsrc2=size=320x180:rate=10:duration=1800",
              "-c:v", "libx264", "-preset", "ultrafast", "-crf", "45",
              "-g", "10", "-keyint_min", "10", "-an",
              "-movflags", "frag_keyframe+empty_moov+default_base_moof",
              out["fragmented"]])
    if not _ok(out["flat"]):
        _run(["-i", out["fragmented"], "-c", "copy", out["flat"]])

    return out


if __name__ == "__main__":
    where = sys.argv[1] if len(sys.argv) > 1 else CACHE
    files = build(where)
    for name in sorted(files):
        size = os.path.getsize(files[name]) / 1024.0 ** 2
        print("  %-12s %8.1f МБ  %s" % (name, size, files[name]))
