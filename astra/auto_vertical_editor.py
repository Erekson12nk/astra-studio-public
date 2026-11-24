import argparse
import hashlib
import json
import os
import random
import re
import shutil
import statistics
import subprocess
import sys
import tempfile
import urllib.request

import cv2
import numpy as np

_ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
if os.path.isdir(os.path.join(_ROOT, "core")) and _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from core import asr as _asr
from core import handoff as _hand
from core import models as _models
from core import faceid as _faceid
from core import profiles as _pro
from core import webcam as _cam
from core import censor as _cen
from core import loudness as _loud
from core import ffmpeg as _ff
from core import timecode as _tc
from core import cache as _cache
from core import settings as _set

OUT_W = _set.OUT_W
OUT_H = _set.OUT_H

FACE_MODEL_NAME = "blaze_face_short_range.tflite"
FACE_MODEL_URL = _models.MODEL_URLS[FACE_MODEL_NAME]

SCAN_WINDOW = _loud.WINDOW
SCAN_RATE = _loud.RATE
SCAN_LOW = _loud.LOW
SCAN_HIGH = _loud.HIGH
SCAN_SPAN = _loud.SPAN
SCAN_GAP = _loud.GAP
SCAN_FLOOR = _loud.FLOOR
SCAN_VERSION = _loud.VERSION
SCAN_WEIGHTS = _loud.WEIGHTS
TRANSCRIBE_RATE = 16000
SCAN_BEAM = 5
STEADY_MARGIN = 0.9
SEAM_SUBS_LIFT = _set.SEAM_SUBS_LIFT
FULL_SUBS_Y = 1290
FACE_FILL = 0.42
CAM_FACE_FILL = 0.58
DEFAULT_WHISPER = "large-v3-turbo"
CAPTURE_OPEN_MS = 300000
CAPTURE_READ_MS = 120000

HIGHLIGHT_COLOR = _set.HIGHLIGHT_COLOR
KEYWORD_COLOR = _set.KEYWORD_COLOR
BASE_COLOR = _set.BASE_COLOR
TITLE_COLOR = _set.TITLE_COLOR
CENSOR_SYMBOLS = _cen.SYMBOLS
VOWELS = _cen.VOWELS
PREFIX_TAIL = _cen.PREFIX_TAIL
VIDEO_EXTENSIONS = (".mp4", ".mkv", ".mov", ".webm", ".avi", ".m4v")
RESERVED_NAMES = {
    "con", "prn", "aux", "nul",
    "com1", "com2", "com3", "com4", "lpt1", "lpt2", "lpt3", "lpt4",
}


def ass_color(value, inline=True):
    text = str(value).lstrip("#").strip()
    if len(text) != 6:
        text = "FFFFFF"
    try:
        red, green, blue = (int(text[i:i + 2], 16) for i in (0, 2, 4))
    except ValueError:
        red, green, blue = 255, 255, 255
    return "&H00%02X%02X%02X%s" % (blue, green, red, "&" if inline else "")

KEYWORDS = {
    "клип", "кайф", "имба", "жесть", "нереально", "рекорд", "победа",
    "фейл", "катка", "хайлайт", "мощно", "невероятно", "смотрите",
    "insane", "clutch", "crazy", "wow", "huge", "unreal", "perfect",
}


NO_WINDOW = 0x08000000 if os.name == "nt" else 0


def app_dir():
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.abspath(__file__))


def bundle_dir():
    return getattr(sys, "_MEIPASS", app_dir())


def find_resource(*parts):
    for base in (app_dir(), bundle_dir()):
        candidate = os.path.join(base, *parts)
        if os.path.exists(candidate):
            return candidate
    return os.path.join(app_dir(), *parts)


def use_bundled_tools():
    _ff.use_bundled_tools(app_dir(), bundle_dir())


def use_cuda_libraries():
    try:
        import nvidia
    except ImportError:
        return
    for base in getattr(nvidia, "__path__", []):
        for name in ("cublas", "cudnn", "cuda_runtime", "cuda_nvrtc"):
            folder = os.path.join(base, name, "bin")
            if not os.path.isdir(folder):
                continue
            try:
                os.add_dll_directory(folder)
            except (AttributeError, OSError):
                pass
            os.environ["PATH"] = folder + os.pathsep + os.environ.get("PATH", "")


use_bundled_tools()
use_cuda_libraries()


run = _ff.run


def require_binary(name):
    return _ff.binary(name)


parse_time = _tc.require_time


def probe_video(path):
    info = _ff.probe(path)
    if not info.has_video:
        raise RuntimeError("в файле нет видеодорожки: %s" % path)
    return {
        "width": info.width,
        "height": info.height,
        "fps": info.fps if info.fps > 0 else 30.0,
        "duration": info.duration,
        "has_audio": info.has_audio,
    }


def even(value):
    value = int(round(value))
    return value - (value % 2)


def clamp(value, low, high):
    return max(low, min(high, value))


def cut_segment(src, dst, start, duration):
    run([
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
        "-ss", "%.3f" % start, "-i", src, "-t", "%.3f" % duration,
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "18",
        "-c:a", "aac", "-b:a", "192k", "-ac", "2",
        "-avoid_negative_ts", "make_zero", dst,
    ])


def extract_audio(src, dst):
    _ff.extract_audio(src, dst, TRANSCRIBE_RATE)


def open_capture(path, open_ms=CAPTURE_OPEN_MS, read_ms=CAPTURE_READ_MS):
    try:
        capture = cv2.VideoCapture(path, cv2.CAP_FFMPEG,
                                   [cv2.CAP_PROP_OPEN_TIMEOUT_MSEC, int(open_ms),
                                    cv2.CAP_PROP_READ_TIMEOUT_MSEC, int(read_ms)])
        if capture.isOpened():
            return capture
        capture.release()
    except Exception:
        pass
    return cv2.VideoCapture(path)


media_duration = _ff.duration


smooth_series = _loud.smooth_series
delay_series = _loud.delay_series
scan_cache_path = _loud.cache_path
audio_levels = _loud.audio_levels
rolling_baseline = _loud.rolling_baseline
moment_scores = _loud.moment_scores
quiet_edge = _loud.quiet_edge
pick_moments = _loud.pick_moments
scan_highlights = _loud.scan_highlights


def detect_silence(path, noise_db, min_len):
    proc = subprocess.run(
        ["ffmpeg", "-hide_banner", "-nostats", "-i", path,
         "-af", "silencedetect=noise=%ddB:d=%.3f" % (noise_db, min_len),
         "-f", "null", "-"],
        stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, creationflags=NO_WINDOW,
    )
    text = proc.stderr.decode("utf-8", "ignore")
    spans = []
    opened = None
    for found in re.finditer(r"silence_(start|end):\s*(-?[\d.]+)", text):
        kind = found.group(1)
        value = float(found.group(2))
        if kind == "start":
            opened = max(0.0, value)
        elif opened is not None:
            spans.append((opened, value))
            opened = None
    if opened is not None:
        spans.append((opened, None))
    return spans


def keep_segments(spans, duration, pad):
    keeps = []
    cursor = 0.0
    for start, end in spans:
        end = duration if end is None else end
        if start > 0.02:
            start = min(duration, start + pad)
        if end < duration - 0.02:
            end = max(0.0, end - pad)
        if start > cursor + 0.02:
            keeps.append((cursor, start))
        cursor = max(cursor, end)
    if cursor < duration - 0.02:
        keeps.append((cursor, duration))
    return [(a, min(b, duration)) for a, b in keeps if b - a > 0.08]


def remove_pauses(source, destination, keeps, encoder, crf, preset):
    parts = []
    for index, (start, end) in enumerate(keeps):
        parts.append("[0:v]trim=start=%.3f:end=%.3f,setpts=PTS-STARTPTS[v%d]"
                     % (start, end, index))
        parts.append("[0:a]atrim=start=%.3f:end=%.3f,asetpts=PTS-STARTPTS[a%d]"
                     % (start, end, index))
    streams = "".join("[v%d][a%d]" % (index, index) for index in range(len(keeps)))
    parts.append("%sconcat=n=%d:v=1:a=1[v][a]" % (streams, len(keeps)))

    command = [
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
        "-i", source, "-filter_complex", ";".join(parts),
        "-map", "[v]", "-map", "[a]",
        "-c:a", "aac", "-b:a", "192k",
    ]
    command += video_encoder_args(encoder, crf, preset)
    command += ["-pix_fmt", "yuv420p", destination]
    try:
        run(command)
    except RuntimeError:
        if encoder == "libx264":
            raise
        command = [item for item in command]
        return remove_pauses(source, destination, keeps, "libx264", crf, preset)
    return True


def invert_ranges(drops, duration):
    ordered = []
    for start, end in sorted(drops):
        start = max(0.0, min(start, duration))
        end = max(0.0, min(end, duration))
        if end - start < 0.02:
            continue
        if ordered and start <= ordered[-1][1] + 0.02:
            ordered[-1][1] = max(ordered[-1][1], end)
        else:
            ordered.append([start, end])
    keeps = []
    cursor = 0.0
    for start, end in ordered:
        if start > cursor + 0.05:
            keeps.append((cursor, start))
        cursor = max(cursor, end)
    if cursor < duration - 0.05:
        keeps.append((cursor, duration))
    return keeps


def drop_ranges(source, destination, drops, duration, encoder, crf, preset):
    keeps = invert_ranges(drops, duration)
    if not keeps:
        print("      manual cuts would remove everything, skipping")
        return None
    kept = sum(end - start for start, end in keeps)
    if duration - kept < 0.05:
        return None
    remove_pauses(source, destination, keeps, encoder, crf, preset)
    print("      manual cuts: %.1fs removed, %d fragments left"
          % (duration - kept, len(keeps)))
    return kept


def tighten_clip(source, destination, duration, args, encoder):
    spans = detect_silence(source, args.pause_threshold, args.pause_min)
    if not spans:
        print("      no pauses over %.2fs found" % args.pause_min)
        return None
    keeps = keep_segments(spans, duration, args.pause_keep)
    if not keeps:
        print("      everything looks silent, leaving the clip as is")
        return None
    kept = sum(end - start for start, end in keeps)
    if duration - kept < 0.15:
        print("      nothing worth cutting")
        return None
    if kept < 1.0:
        print("      almost everything is quiet (%.1fs of %.1fs would be left), "
              "leaving the clip as is" % (kept, duration))
        return None
    if len(keeps) > args.pause_max_cuts:
        print("      too many fragments (%d), skipping pause removal" % len(keeps))
        return None
    remove_pauses(source, destination, keeps, encoder, args.crf, args.preset)
    print("      pauses cut: %.1fs removed, %d fragments joined"
          % (duration - kept, len(keeps)))
    return kept


def extract_window(source, destination, start, duration):
    run([
        "ffmpeg", "-y", "-v", "error", "-ss", "%.3f" % start, "-i", source,
        "-t", "%.3f" % duration, "-vn", "-ac", "1", "-ar", "16000",
        "-c:a", "pcm_s16le", destination,
    ])


def extract_samples(source, destination, sample_rate=TRANSCRIBE_RATE):
    run([
        "ffmpeg", "-y", "-v", "error", "-i", source, "-vn", "-ac", "1",
        "-ar", str(sample_rate), "-f", "s16le", destination,
    ])


def usable_samples(path, least=TRANSCRIBE_RATE * 2):
    try:
        return bool(path) and os.path.getsize(path) >= least
    except OSError:
        return False


def transcribe_samples(model, samples, language, beam=SCAN_BEAM):
    result = _asr.transcribe(samples, model, language=language,
                             word_timestamps=True, vad=True,
                             condition_on_previous_text=False, beam_size=beam)
    silence = [segment.no_speech_prob for segment in result.segments]
    quiet = min(silence) if silence else 1.0
    return words_from(result), 1.0 - quiet


def text_signals(words, length, rules=None):
    if not words or length <= 0:
        return {"rate": 0.0, "shouts": 0, "banned": 0, "talk": 0.0}
    text = " ".join(word["text"] for word in words)
    rate = len(words) / float(length)
    shouts = len(re.findall(r"[!?]{1,}", text))
    banned = 0
    if rules:
        banned = sum(1 for word in words if word_is_banned(word["text"], rules))
    talk = (min(1.5, rate / 2.2) + min(1.0, shouts / 4.0) * 0.6
            + min(1.0, banned / 3.0) * 0.6)
    return {"rate": rate, "shouts": shouts, "banned": banned, "talk": float(talk)}


def scan_transcript(source, candidates, model, language, progress=None,
                    rules=None, speech_floor=0.35, ready=None):
    workdir = tempfile.mkdtemp(prefix="vbrief_")
    raw = ready if usable_samples(ready) else os.path.join(workdir, "audio.raw")
    items = []
    try:
        if raw != ready:
            extract_samples(source, raw)
        samples = np.memmap(raw, dtype=np.int16, mode="r")
        for position, item in enumerate(candidates, 1):
            if progress:
                progress(position, len(candidates))
            low = max(0, int(item["start"] * TRANSCRIBE_RATE))
            high = min(samples.size, int(item["end"] * TRANSCRIBE_RATE))
            if high - low < TRANSCRIBE_RATE:
                continue
            chunk = np.asarray(samples[low:high], dtype=np.float32) / 32768.0
            try:
                words, speech = transcribe_samples(model, chunk, language)
            except Exception:
                words, speech = [], 0.0
            if speech < speech_floor or not words:
                continue
            offset = item["start"]
            signals = text_signals(words, item["end"] - item["start"], rules)
            items.append({
                "index": len(items) + 1,
                "start": item["start"],
                "end": item["end"],
                "sound": float(item["score"]),
                "speech": float(speech),
                "talk": signals["talk"],
                "score": float(item["score"]) + signals["talk"],
                "text": " ".join(word["text"] for word in words).strip(),
                "words": [[round(word["start"] + offset, 3), word["text"]] for word in words],
            })
    finally:
        shutil.rmtree(workdir, ignore_errors=True)
    items.sort(key=lambda entry: entry["score"], reverse=True)
    for position, item in enumerate(items, 1):
        item["index"] = position
    return items


BRIEF_TASK = [
    "ЗАДАЧА.",
    "Ниже фрагменты стрима, отобранные по всплескам громкости, с расшифровкой речи.",
    "Выбери самые интересные для коротких вертикальных роликов: смешное,",
    "неожиданное, цельная история или сильная реакция. Пропускай куски,",
    "где просто шумно, но ничего не происходит.",
    "",
    "Для каждого выбранного фрагмента выведи строку вида:",
    "#НОМЕР | краткое название до пяти слов | \"точная цитата начала\"",
    "",
    "Цитату бери дословно из текста фрагмента - по ней будет найдено",
    "точное место начала. Таймкоды сам не придумывай, их здесь нет намеренно.",
]


def write_brief(path, source, items, wanted):
    lines = ["# Стрим: %s" % os.path.basename(source),
             "# Фрагментов: %d, выбрать примерно: %d" % (len(items), wanted),
             ""]
    lines += ["# " + line if line else "#" for line in BRIEF_TASK]
    lines.append("")
    for item in items:
        lines.append("--- #%02d   длительность %d с   звук %.1f   речь %.0f%% ---"
                     % (item["index"], round(item["end"] - item["start"]),
                        item.get("sound", item["score"]), 100.0 * item.get("speech", 1.0)))
        lines.append(item["text"] or "(речь не распознана)")
        lines.append("")
    with open(path, "w", encoding="utf-8") as handle:
        handle.write("\n".join(lines))

    sidecar = os.path.splitext(path)[0] + ".json"
    with open(sidecar, "w", encoding="utf-8") as handle:
        json.dump({"source": source, "items": items}, handle, ensure_ascii=False, indent=1)
    return sidecar


def load_brief(path):
    sidecar = os.path.splitext(path)[0] + ".json"
    if not os.path.isfile(sidecar):
        raise RuntimeError(
            "brief data not found: %s (point --brief at the brief written by --scan-mode transcript)"
            % sidecar)
    with open(sidecar, "r", encoding="utf-8") as handle:
        return json.load(handle)


def _plain(text):
    return re.sub(r"[^\w\s]", "", str(text).lower().replace("ё", "е"), flags=re.UNICODE).split()


def locate_quote(words, quote):
    target = _plain(quote)
    if not target or not words:
        return None
    plain = [_plain(pair[1]) for pair in words]
    flat = []
    for position, pieces in enumerate(plain):
        for piece in pieces:
            flat.append((piece, position))
    for begin in range(len(flat) - len(target) + 1):
        if [flat[begin + step][0] for step in range(len(target))] == target:
            return words[flat[begin][1]][0]
    return None


def parse_selection(answer, data, lead=1.0, min_len=8.0):
    items = {item["index"]: item for item in data["items"]}
    picked = []
    seen = set()
    for line in str(answer).splitlines():
        found = re.search(r"#\s*(\d+)", line)
        if not found:
            continue
        number = int(found.group(1))
        if number in seen or number not in items:
            continue
        seen.add(number)
        item = items[number]
        start, end = item["start"], item["end"]
        quote = re.search(r"[\"«“']([^\"»”']{4,})[\"»”']", line)
        if quote:
            found_at = locate_quote(item.get("words") or [], quote.group(1))
            if found_at is not None:
                shifted = max(item["start"], found_at - lead)
                if end - shifted >= min_len:
                    start = shifted
        title = ""
        parts = [piece.strip() for piece in line.split("|")]
        if len(parts) >= 2:
            title = re.sub(r"[\"«»“”']", "", parts[1]).strip()
        picked.append({"index": number, "start": start, "end": end, "title": title})
    return picked


_mediapipe_module = _models.module
ascii_safe_path = _models.ascii_safe_path


def face_model_path(explicit):
    if explicit:
        path = os.path.abspath(explicit)
        if not os.path.isfile(path):
            raise RuntimeError("face model not found: %s" % path)
        return ascii_safe_path(path)
    bundled = find_resource("models", FACE_MODEL_NAME)
    if os.path.isfile(bundled):
        return ascii_safe_path(bundled)
    cache = os.path.join(os.path.expanduser("~"), ".cache", "auto_vertical_editor")
    return _models.ensure(FACE_MODEL_NAME, cache, url=FACE_MODEL_URL,
                          report=lambda message: print("      " + message))


class FaceBackend:
    def __init__(self, model_arg):
        self.mp = _mediapipe_module()
        self.detector = None
        self.mode = None
        if self.mp is not None and hasattr(self.mp, "solutions"):
            self.detector = self.mp.solutions.face_detection.FaceDetection(
                model_selection=1, min_detection_confidence=0.5
            )
            self.mode = "solutions"
            return
        if self.mp is not None:
            from mediapipe.tasks.python import BaseOptions
            from mediapipe.tasks.python.vision import (
                FaceDetector, FaceDetectorOptions, RunningMode,
            )

            options = FaceDetectorOptions(
                base_options=BaseOptions(model_asset_path=face_model_path(model_arg)),
                running_mode=RunningMode.IMAGE,
                min_detection_confidence=0.4,
            )
            self.detector = FaceDetector.create_from_options(options)
            self.mode = "tasks"
            return
        cascade_file = os.path.join(cv2.data.haarcascades, "haarcascade_frontalface_default.xml")
        if os.path.isfile(cascade_file):
            self.detector = cv2.CascadeClassifier(cascade_file)
            self.mode = "cascade"
            return
        raise RuntimeError("no face detector available: pip install mediapipe")

    def _raw(self, image):
        height, width = image.shape[:2]
        if self.mode == "solutions":
            result = self.detector.process(cv2.cvtColor(image, cv2.COLOR_BGR2RGB))
            boxes = []
            for det in result.detections or []:
                rel = det.location_data.relative_bounding_box
                if rel.width <= 0 or rel.height <= 0:
                    continue
                points = [(point.x * width, point.y * height)
                          for point in det.location_data.relative_keypoints]
                boxes.append((rel.xmin * width, rel.ymin * height,
                              rel.width * width, rel.height * height,
                              points or None))
            return boxes
        if self.mode == "tasks":
            rgb = np.ascontiguousarray(cv2.cvtColor(image, cv2.COLOR_BGR2RGB))
            wrapped = self.mp.Image(image_format=self.mp.ImageFormat.SRGB, data=rgb)
            result = self.detector.detect(wrapped)
            boxes = []
            for det in result.detections or []:
                box = det.bounding_box
                if box.width <= 0 or box.height <= 0:
                    continue
                points = [(point.x * width, point.y * height)
                          for point in (det.keypoints or [])]
                boxes.append((float(box.origin_x), float(box.origin_y),
                              float(box.width), float(box.height),
                              points or None))
            return boxes
        gray = cv2.equalizeHist(cv2.cvtColor(image, cv2.COLOR_BGR2GRAY))
        faces = self.detector.detectMultiScale(gray, 1.15, 6, minSize=(70, 70))
        return [(float(x), float(y), float(w), float(h), None) for x, y, w, h in faces]

    def detect(self, frame):
        boxes = self._raw(frame)
        if boxes:
            return boxes
        height, width = frame.shape[:2]
        tile_w = width // 2
        tile_h = height // 2
        offsets = [
            (0, 0), (width - tile_w, 0), (0, height - tile_h),
            (width - tile_w, height - tile_h),
            ((width - tile_w) // 2, (height - tile_h) // 2),
        ]
        found = []
        for ox, oy in offsets:
            tile = np.ascontiguousarray(frame[oy:oy + tile_h, ox:ox + tile_w])
            for x, y, bw, bh, points in self._raw(tile):
                moved = [(px + ox, py + oy) for px, py in points] if points else None
                found.append((x + ox, y + oy, bw, bh, moved))
        return found

    def close(self):
        if self.mode in ("solutions", "tasks"):
            self.detector.close()


def group_faces(per_frame):
    groups = []
    for number, boxes in enumerate(per_frame):
        for box in boxes:
            cx = box[0] + box[2] / 2.0
            cy = box[1] + box[3] / 2.0
            hit = None
            for group in groups:
                if (abs(cx - group["cx"]) < group["size"]
                        and abs(cy - group["cy"]) < group["size"]):
                    hit = group
                    break
            if hit is None:
                hit = {"boxes": [], "frames": set(), "cx": cx, "cy": cy,
                       "size": max(box[2], box[3])}
                groups.append(hit)
            hit["boxes"].append(box)
            hit["frames"].add(number)
            hit["cx"] = statistics.median([b[0] + b[2] / 2.0 for b in hit["boxes"]])
            hit["cy"] = statistics.median([b[1] + b[3] / 2.0 for b in hit["boxes"]])
            hit["size"] = statistics.median([max(b[2], b[3]) for b in hit["boxes"]])
    return groups


def group_vectors(group):
    return [box[5] for box in group["boxes"] if len(box) > 5 and box[5] is not None]


def recognised_face(groups, reference, threshold):
    best = None
    best_score = threshold
    for group in groups:
        scores = sorted((_faceid.similarity(reference, vector)
                         for vector in group_vectors(group)), reverse=True)
        if not scores:
            continue
        top = scores[:3]
        value = sum(top) / float(len(top))
        if value > best_score:
            best = group
            best_score = value
    return best, best_score


def steadiest_face(groups, frames):
    scored = []
    for group in groups:
        coverage = len(group["frames"]) / float(max(1, frames))
        drift = statistics.median([
            max(abs(b[0] + b[2] / 2.0 - group["cx"]),
                abs(b[1] + b[3] / 2.0 - group["cy"]))
            for b in group["boxes"]
        ]) / max(1.0, group["size"])
        scored.append((coverage / (1.0 + drift), group))
    best = max(score for score, _ in scored)
    near = [group for score, group in scored if score >= best * STEADY_MARGIN]
    return max(near, key=lambda group: group["size"])


def sample_faces(capture, indices, samples, backend, embedder=None):
    per_frame = []

    def take(frame):
        found = backend.detect(frame)
        if embedder is None:
            per_frame.append([box + (None,) for box in found])
            return
        per_frame.append([box + (embedder.embed(frame, box, box[4]),) for box in found])

    if indices:
        for index in indices:
            capture.set(cv2.CAP_PROP_POS_FRAMES, index)
            ok, frame = capture.read()
            if ok:
                take(frame)
        return per_frame
    read = 0
    while read < samples:
        ok, frame = capture.read()
        if not ok:
            break
        read += 1
        take(frame)
    return per_frame


def survey_faces(video_path, samples, model_arg, t0=None, t1=None, backend=None,
                 embedder=None, reference=None, threshold=0.4, quiet=False):
    capture = open_capture(video_path)
    if not capture.isOpened():
        raise RuntimeError("cannot open video: %s" % video_path)
    total = int(capture.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    width = int(capture.get(cv2.CAP_PROP_FRAME_WIDTH))
    height = int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT))
    fps = capture.get(cv2.CAP_PROP_FPS) or 0.0

    owned = backend is None
    if owned:
        backend = FaceBackend(model_arg)
    if not quiet:
        print("      detector backend: %s" % backend.mode)

    indices = []
    if t0 is not None and t1 is not None and fps > 0:
        first = max(0, int(t0 * fps))
        last = int(t1 * fps)
        if total > 0:
            last = min(last, total)
        last = max(last, first + 1)
        step = max(1, (last - first) // max(1, samples))
        indices = list(range(first, last, step))[:samples]
    elif total > 0:
        step = max(1, total // max(1, samples))
        indices = list(range(0, total, step))[:samples]

    per_frame = sample_faces(capture, indices, samples, backend, embedder)
    capture.release()
    if owned:
        backend.close()

    groups = group_faces(per_frame)
    survey = {"groups": groups, "frames": len(per_frame), "size": (width, height),
              "chosen": None, "recognised": False, "score": 0.0,
              "asked": reference is not None}
    if not groups:
        return survey

    if reference is not None:
        chosen, score = recognised_face(groups, reference, threshold)
        if chosen is not None:
            survey["chosen"] = chosen
            survey["recognised"] = True
            survey["score"] = score
            if not quiet:
                print("      узнан стример, схожесть %.2f" % score)
            return survey
        if not quiet:
            print("      стример не узнан, беру самое устойчивое лицо")
    survey["chosen"] = steadiest_face(groups, len(per_frame))
    return survey


def region_from_survey(survey, fill, aspect, headroom=0.42):
    chosen = survey["chosen"]
    width, height = survey["size"]
    if chosen is None:
        return None, (width, height)
    kept = chosen["boxes"]
    cx = statistics.median([b[0] + b[2] / 2.0 for b in kept])
    cy = statistics.median([b[1] + b[3] / 2.0 for b in kept])
    fh = max(b[3] for b in kept)

    box_h = fh / max(0.05, min(0.95, fill))
    box_w = box_h * aspect
    box = fit_box(cx, cy + box_h * (0.5 - headroom), box_w, box_h, aspect, width, height)
    return box, (width, height)


def detect_face_region(video_path, samples, fill, aspect, model_arg,
                       t0=None, t1=None, backend=None, headroom=0.42,
                       embedder=None, reference=None, threshold=0.4, survey=None):
    if survey is None:
        survey = survey_faces(video_path, samples, model_arg, t0=t0, t1=t1,
                              backend=backend, embedder=embedder,
                              reference=reference, threshold=threshold)
    return region_from_survey(survey, fill, aspect, headroom=headroom)


def scan_webcam(video_path, samples, locator, t0=None, t1=None):
    if locator is None or not locator.usable():
        return None
    capture = open_capture(video_path)
    if not capture.isOpened():
        return None
    total = int(capture.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    fps = capture.get(cv2.CAP_PROP_FPS) or 0.0
    indices = []
    if t0 is not None and t1 is not None and fps > 0:
        first = max(0, int(t0 * fps))
        last = min(int(t1 * fps), total) if total > 0 else int(t1 * fps)
        step = max(1, (last - first) // max(1, samples))
        indices = list(range(first, last, step))[:samples]
    elif total > 0:
        step = max(1, total // max(1, samples))
        indices = list(range(0, total, step))[:samples]
    found = []
    for index in indices:
        capture.set(cv2.CAP_PROP_POS_FRAMES, index)
        ok, frame = capture.read()
        if not ok:
            continue
        hit = locator.find(frame)
        if hit is not None:
            found.append(hit)
    capture.release()
    return _cam.consensus(found)


ZOOM_WIDTH = 960
FACE_SHAPE = (0.6, 1.6)


def face_within(video_path, box, samples, model_arg, t0=None, t1=None,
                backend=None, embedder=None):
    x, y, w, h = (int(round(v)) for v in box)
    if w < 8 or h < 8:
        return None
    capture = open_capture(video_path)
    if not capture.isOpened():
        return None
    total = int(capture.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    fps = capture.get(cv2.CAP_PROP_FPS) or 0.0

    indices = []
    if t0 is not None and t1 is not None and fps > 0:
        first = max(0, int(t0 * fps))
        last = int(t1 * fps)
        if total > 0:
            last = min(last, total)
        last = max(last, first + 1)
        step = max(1, (last - first) // max(1, samples))
        indices = list(range(first, last, step))[:samples]
    elif total > 0:
        step = max(1, total // max(1, samples))
        indices = list(range(0, total, step))[:samples]

    owned = backend is None
    if owned:
        backend = FaceBackend(model_arg)
    zoom = max(1.0, ZOOM_WIDTH / float(w))
    per_frame = []
    for index in indices:
        capture.set(cv2.CAP_PROP_POS_FRAMES, index)
        ok, frame = capture.read()
        if not ok:
            continue
        top, left = max(0, y), max(0, x)
        piece = frame[top:top + h, left:left + w]
        if piece.size == 0:
            continue
        if zoom > 1.0:
            piece = cv2.resize(piece, None, fx=zoom, fy=zoom,
                               interpolation=cv2.INTER_CUBIC)
        found = backend.detect(piece)
        shifted = []
        for hit in found:
            bx, by, bw, bh = hit[0] / zoom, hit[1] / zoom, hit[2] / zoom, hit[3] / zoom
            points = hit[4]
            if points is not None:
                points = [(px / zoom + left, py / zoom + top) for px, py in points]
            vector = None
            if embedder is not None:
                vector = embedder.embed(piece, hit, hit[4])
            shifted.append((bx + left, by + top, bw, bh, points, vector))
        per_frame.append(shifted)
    capture.release()
    if owned:
        backend.close()

    groups = group_faces(per_frame)
    if not groups:
        return None
    chosen = steadiest_face(groups, len(per_frame))
    kept = chosen["boxes"]
    cx = statistics.median([b[0] + b[2] / 2.0 for b in kept])
    cy = statistics.median([b[1] + b[3] / 2.0 for b in kept])
    bw = statistics.median([b[2] for b in kept])
    bh = statistics.median([b[3] for b in kept])
    if bh <= 0 or not FACE_SHAPE[0] <= bw / bh <= FACE_SHAPE[1]:
        return None
    return (cx - bw / 2.0, cy - bh / 2.0, bw, bh)


def scan_anchor(source, spans, locator, samples):
    if locator is None or not locator.usable():
        return [], 0
    capture = open_capture(source)
    if not capture.isOpened():
        return [], 0
    fps = capture.get(cv2.CAP_PROP_FPS) or 0.0
    total = int(capture.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    boxes = []
    looked = 0
    for start, end in spans:
        if fps <= 0:
            break
        first = max(0, int(start * fps))
        last = int(end * fps)
        if total > 0:
            last = min(last, total)
        last = max(last, first + 1)
        step = max(1, (last - first) // max(1, samples))
        found = []
        for index in list(range(first, last, step))[:samples]:
            capture.set(cv2.CAP_PROP_POS_FRAMES, index)
            ok, frame = capture.read()
            if not ok:
                continue
            hit = locator.find(frame)
            if hit is not None:
                found.append(hit)
        looked += 1
        report = _cam.consensus(found)
        if report is None or not report["confident"] or _cam.closeup(report):
            continue
        boxes.append(report["box"])
    capture.release()
    return boxes, looked


def setup_cam_anchor(source, jobs, args, state):
    if args.cam_box or args.layout == "screen" or len(jobs) < 2:
        return
    if state.get("locator") is None:
        return
    print("ищу обычное место вебки на этом стриме…", flush=True)
    boxes, looked = scan_anchor(source, jobs, state["locator"], args.anchor_samples)
    if not boxes:
        print("      уверенной рамки не нашлось, якоря не будет")
        return
    state["cam_seen"] = list(boxes)
    state["cam_anchor"] = _cam.median_box(boxes)
    print("      вебка нашлась в %d клипах из %d, якорь %s"
          % (len(boxes), looked,
             tuple(int(v) for v in state["cam_anchor"])))


def face_reference(path, backend, embedder):
    frame = cv2.imdecode(np.fromfile(str(path), dtype=np.uint8), cv2.IMREAD_COLOR)
    if frame is None:
        raise RuntimeError("не читается снимок для опознания: %s" % path)
    found = backend.detect(frame)
    if not found:
        raise RuntimeError("на снимке %s не видно лица" % path)
    box = max(found, key=lambda item: item[2] * item[3])
    vector = embedder.embed(frame, box, box[4], strict=False)
    if vector is None:
        raise RuntimeError("не получилось снять признак лица со снимка %s" % path)
    return vector


def scan_identity(source, spans, backend, embedder, samples, threshold=0.35):
    capture = open_capture(source)
    if not capture.isOpened():
        return None
    fps = capture.get(cv2.CAP_PROP_FPS) or 0.0
    total = int(capture.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    people = []
    for number, (start, end) in enumerate(spans):
        if fps <= 0:
            break
        first = max(0, int(start * fps))
        last = int(end * fps)
        if total > 0:
            last = min(last, total)
        step = max(1, (last - first) // max(1, samples))
        seen = sample_faces(capture, list(range(first, last, step))[:samples],
                            samples, backend, embedder)
        for boxes in seen:
            for box in boxes:
                vector = box[5]
                if vector is None:
                    continue
                hit = None
                for person in people:
                    if (_faceid.similarity(person["mean"], vector) >= threshold
                            and _faceid.similarity(person["vectors"][0], vector) >= threshold):
                        hit = person
                        break
                if hit is None:
                    hit = {"vectors": [], "clips": set(), "mean": vector}
                    people.append(hit)
                hit["vectors"].append(vector)
                hit["clips"].add(number)
                hit["mean"] = _faceid.average(hit["vectors"])
    capture.release()
    if not people:
        return None
    best = max(people, key=lambda person: (len(person["clips"]), len(person["vectors"])))
    return best["mean"], len(best["clips"]), len(spans)


def fit_box(cx, cy, bw, bh, aspect, frame_w, frame_h):
    bh = max(bh, bw / aspect)
    bw = bh * aspect
    if bw > frame_w:
        bw = float(frame_w)
        bh = bw / aspect
    if bh > frame_h:
        bh = float(frame_h)
        bw = bh * aspect
    x = clamp(cx - bw / 2.0, 0.0, frame_w - bw)
    y = clamp(cy - bh / 2.0, 0.0, frame_h - bh)
    w = even(bw)
    h = even(bh)
    x = even(clamp(x, 0, frame_w - w))
    y = even(clamp(y, 0, frame_h - h))
    return x, y, w, h


def clamp_box(x, y, w, h, frame_w, frame_h):
    w = even(max(16, min(w, frame_w)))
    h = even(max(16, min(h, frame_h)))
    x = even(max(0, min(x, frame_w - w)))
    y = even(max(0, min(y, frame_h - h)))
    return x, y, w, h


def fallback_cam_box(frame_w, frame_h, aspect, corner):
    bw = frame_w * 0.32
    bh = bw / aspect
    if bh > frame_h * 0.5:
        bh = frame_h * 0.5
        bw = bh * aspect
    positions = {
        "top-left": (0.0, 0.0),
        "top-right": (frame_w - bw, 0.0),
        "bottom-left": (0.0, frame_h - bh),
        "bottom-right": (frame_w - bw, frame_h - bh),
        "center": ((frame_w - bw) / 2.0, (frame_h - bh) / 2.0),
    }
    x, y = positions.get(corner, positions["top-right"])
    return fit_box(x + bw / 2.0, y + bh / 2.0, bw, bh, aspect, frame_w, frame_h)


def gameplay_box(frame_w, frame_h, aspect, y_ratio):
    crop_h = float(frame_h)
    crop_w = crop_h * aspect
    if crop_w > frame_w:
        crop_w = float(frame_w)
        crop_h = crop_w / aspect
    w = even(crop_w)
    h = even(crop_h)
    x = even(clamp((frame_w - w) / 2.0, 0, frame_w - w))
    y = even(clamp((frame_h - h) * y_ratio, 0, frame_h - h))
    return x, y, w, h


def build_model(model_size, device, compute_type):
    return _asr.load_model(model_size, device, compute_type,
                           log=lambda message: print("      " + message),
                           verify=True)


def words_from(result):
    words = []
    for segment in result.segments:
        for word in segment.words:
            text = word.text.strip()
            if not text:
                continue
            start = float(word.start)
            end = float(word.end)
            if end <= start:
                end = start + 0.08
            words.append({"text": text, "start": start, "end": end})
    words.sort(key=lambda item: item["start"])
    return words


def transcribe(model, audio_path, language):
    return words_from(_asr.transcribe(audio_path, model, language=language,
                                      word_timestamps=True, vad=True, beam_size=5))


def group_words(words, max_words, max_chars, max_gap):
    groups = []
    current = []
    length = 0
    for word in words:
        clean = word.get("display", word["text"])
        gap_break = current and (word["start"] - current[-1]["end"]) > max_gap
        size_break = len(current) >= max_words or (length + len(clean) + 1) > max_chars
        if current and (gap_break or size_break):
            groups.append(current)
            current = []
            length = 0
        current.append(word)
        length += len(clean) + 1
    if current:
        groups.append(current)
    return groups


def ass_time(seconds):
    seconds = max(0.0, seconds)
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = seconds % 60
    return "%d:%02d:%05.2f" % (hours, minutes, secs)


def ass_escape(text):
    return text.replace("\\", "\\\\").replace("{", "(").replace("}", ")").replace("\n", " ")


def is_keyword(text):
    clean = re.sub(r"[^\w]", "", text, flags=re.UNICODE).lower()
    return clean in KEYWORDS or len(clean) >= 9


def build_ass(words, path, font, font_size, pos_x, pos_y, uppercase, max_words, max_chars, max_gap,
              title=None, title_y=None, title_size=64, duration=0.0,
              base=BASE_COLOR, highlight=HIGHLIGHT_COLOR, keyword=KEYWORD_COLOR,
              title_color=TITLE_COLOR, title_font=None):
    base_inline = ass_color(base)
    highlight_inline = ass_color(highlight)
    keyword_inline = ass_color(keyword)
    header = [
        "[Script Info]",
        "ScriptType: v4.00+",
        "PlayResX: %d" % OUT_W,
        "PlayResY: %d" % OUT_H,
        "WrapStyle: 0",
        "ScaledBorderAndShadow: yes",
        "YCbCr Matrix: TV.709",
        "",
        "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, "
        "BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, "
        "BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding",
        "Style: Karaoke,%s,%d,%s,&H000000FF,&H00101010,&HB4000000,-1,0,0,0,100,100,1,0,1,%d,%d,5,%d,%d,%d,204"
        % (font, font_size, ass_color(base, False),
           max(4, font_size // 12), max(2, font_size // 24), 70, 70, 60),
        "Style: Title,%s,%d,%s,&H000000FF,&H00202020,&HA0101010,-1,0,0,0,100,100,0,0,3,%d,0,5,40,40,20,204"
        % (title_font or font, title_size, ass_color(title_color, False),
           max(8, title_size // 5)),
        "",
        "[Events]",
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
    ]
    lines = []
    if title and title_y is not None and duration > 0:
        lines.append(
            "Dialogue: 1,%s,%s,Title,,0,0,0,,{\\an5\\pos(%d,%d)}%s"
            % (ass_time(0.0), ass_time(duration), OUT_W // 2, title_y, ass_escape(title))
        )
    groups = group_words(words, max_words, max_chars, max_gap)
    for group_index, group in enumerate(groups):
        next_start = groups[group_index + 1][0]["start"] if group_index + 1 < len(groups) else None
        for index, word in enumerate(group):
            start = word["start"]
            if index + 1 < len(group):
                end = group[index + 1]["start"]
            else:
                end = word["end"] + 0.12
                if next_start is not None:
                    end = min(end, next_start)
            if end <= start:
                end = start + 0.08
            longest = max(len(item.get("display", item["text"])) for item in group)
            scale = 100 if longest <= max_chars else max(55, int(100.0 * max_chars / longest))
            chunks = []
            for pos, item in enumerate(group):
                shown = item.get("display", item["text"])
                text = shown.upper() if uppercase else shown
                text = ass_escape(text)
                if pos == index:
                    chunks.append(
                        "{\\c%s\\fscx%d\\fscy%d\\bord%d}%s"
                        % (highlight_inline, int(scale * 1.12), int(scale * 1.12),
                           max(5, font_size // 10), text)
                    )
                elif "display" not in item and is_keyword(item["text"]):
                    chunks.append(
                        "{\\c%s\\fscx%d\\fscy%d\\bord%d}%s"
                        % (keyword_inline, scale, scale, max(4, font_size // 12), text)
                    )
                else:
                    chunks.append(
                        "{\\c%s\\fscx%d\\fscy%d\\bord%d}%s"
                        % (base_inline, scale, scale, max(4, font_size // 12), text)
                    )
            body = "{\\an5\\pos(%d,%d)\\fad(60,60)}" % (pos_x, pos_y) + " ".join(chunks)
            lines.append(
                "Dialogue: 0,%s,%s,Karaoke,,0,0,0,,%s"
                % (ass_time(start), ass_time(end), body)
            )
    with open(path, "w", encoding="utf-8-sig", newline="\n") as handle:
        handle.write("\n".join(header + lines) + "\n")
    return len(lines)


read_text = _cen.read_text
load_censor_rules = _cen.load_stem_rules
normalize_word = _cen.normalize_word
mask_word = _cen.mask_word
apply_censor = _cen.apply_stem_censor


def word_is_banned(text, rules):
    return _cen.stem_is_banned(text, rules)


def build_mute_filter(spans):
    if not spans:
        return None
    condition = "+".join("between(t,%.3f,%.3f)" % (start, end) for start, end in spans)
    return "[0:a]volume=0:enable='%s'[a]" % condition


def escape_filter_path(path):
    path = os.path.abspath(path).replace("\\", "/")
    path = path.replace(":", "\\:")
    path = path.replace("'", "\\'")
    path = path.replace("[", "\\[").replace("]", "\\]")
    return path


def build_filter(cam_box, game_box, top_h, bottom_h, ass_path, blur_bg, mute_chain=None,
                 layout="split"):
    cx, cy, cw, ch = cam_box
    gx, gy, gw, gh = game_box
    parts = []
    if layout == "cam":
        parts.append(
            "[0:v]crop=%d:%d:%d:%d,scale=%d:%d:force_original_aspect_ratio=increase,"
            "crop=%d:%d,setsar=1[stack]"
            % (cw, ch, cx, cy, OUT_W, OUT_H, OUT_W, OUT_H)
        )
    elif layout == "screen":
        parts.append(
            "[0:v]crop=%d:%d:%d:%d,scale=%d:%d:force_original_aspect_ratio=increase,"
            "crop=%d:%d,boxblur=24:2,setsar=1[bg]"
            % (gw, gh, gx, gy, OUT_W, OUT_H, OUT_W, OUT_H)
        )
        parts.append(
            "[0:v]crop=%d:%d:%d:%d,scale=%d:%d:force_original_aspect_ratio=decrease,"
            "setsar=1[fg]"
            % (gw, gh, gx, gy, OUT_W, OUT_H)
        )
        parts.append("[bg][fg]overlay=(W-w)/2:(H-h)/2,setsar=1[stack]")
    elif blur_bg:
        parts.append(
            "[0:v]crop=%d:%d:%d:%d,scale=%d:%d,boxblur=24:2,setsar=1[bg]"
            % (gw, gh, gx, gy, OUT_W, top_h)
        )
        parts.append(
            "[0:v]crop=%d:%d:%d:%d,scale=%d:%d:force_original_aspect_ratio=decrease,setsar=1[camfg]"
            % (cw, ch, cx, cy, OUT_W, top_h)
        )
        parts.append("[bg][camfg]overlay=(W-w)/2:(H-h)/2,setsar=1[cam]")
    else:
        parts.append(
            "[0:v]crop=%d:%d:%d:%d,scale=%d:%d:force_original_aspect_ratio=increase,"
            "crop=%d:%d,setsar=1[cam]"
            % (cw, ch, cx, cy, OUT_W, top_h, OUT_W, top_h)
        )
    if layout == "split":
        parts.append(
            "[0:v]crop=%d:%d:%d:%d,scale=%d:%d,setsar=1[game]"
            % (gw, gh, gx, gy, OUT_W, bottom_h)
        )
        parts.append("[cam][game]vstack=inputs=2[stack]")
    if ass_path:
        parts.append("[stack]ass='%s'[v]" % escape_filter_path(ass_path))
    else:
        parts.append("[stack]null[v]")
    if mute_chain:
        parts.append(mute_chain)
    return ";".join(parts)


encoder_available = _ff.encoder_available
encoder_works = _ff.encoder_works
video_encoder_args = _ff.video_encoder_args


def pick_encoder(requested):
    return _ff.pick_encoder(requested, report=print)


def render(clip_path, output_path, filter_complex, crf, preset, has_audio,
           encoder="libx264", muted=False):
    cmd = [
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-stats",
        "-i", clip_path,
        "-filter_complex", filter_complex,
        "-map", "[v]",
    ]
    if has_audio:
        cmd += ["-map", "[a]" if muted else "0:a:0?",
                "-c:a", "aac", "-b:a", "192k", "-ar", "48000"]
    cmd += video_encoder_args(encoder, crf, preset)
    cmd += [
        "-profile:v", "high", "-pix_fmt", "yuv420p",
        "-r", "30", "-g", "60", "-movflags", "+faststart",
        output_path,
    ]
    try:
        run(cmd)
    except RuntimeError:
        if encoder == "libx264":
            raise
        print("      %s failed, falling back to libx264" % encoder)
        render(clip_path, output_path, filter_complex, crf, preset, has_audio,
               "libx264", muted)


def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        prog="auto_vertical_editor",
        description="Automatic 9:16 highlight editor for long streams",
    )
    parser.add_argument("input", help="path to source .mp4")
    parser.add_argument("start_time", nargs="?", help="highlight start (SS, MM:SS or HH:MM:SS)")
    parser.add_argument("end_time", nargs="?", help="highlight end (SS, MM:SS or HH:MM:SS)")
    parser.add_argument("output", nargs="?", help="output file name")
    parser.add_argument("--scan", type=int, default=0, metavar="N",
                        help="print N loudest highlight candidates and exit")
    parser.add_argument("--scan-min", type=float, default=35.0,
                        help="shortest candidate clip, seconds")
    parser.add_argument("--scan-max", type=float, default=75.0,
                        help="longest candidate clip, seconds")
    parser.add_argument("--scan-lead", type=float, default=2.0,
                        help="how far to look for a pause when placing clip edges")
    parser.add_argument("--scan-gap", type=float, default=SCAN_GAP,
                        help="smallest distance between two candidates, seconds")
    parser.add_argument("--scan-no-cache", action="store_true",
                        help="ignore cached audio levels and read the file again")
    parser.add_argument("--scan-mode", default="audio", choices=["audio", "transcript"],
                        help="audio: loudness peaks only; transcript: also transcribe "
                             "candidates and write a brief for a language model")
    parser.add_argument("--scan-pool", type=int, default=40,
                        help="how many loud candidates to transcribe in transcript mode")
    parser.add_argument("--scan-model", default=DEFAULT_WHISPER,
                        help="whisper model used for the candidate brief")
    parser.add_argument("--brief", default=None,
                        help="where to write the brief (transcript mode)")
    parser.add_argument("--select", default=None,
                        help="file with the model answer; renders the chosen fragments")
    parser.add_argument("--timecodes", default=None,
                        help="text file with lines like '00:01:23 - 00:02:33 (name)'")
    parser.add_argument("--batch", action="store_true",
                        help="with --scan, render every candidate found")
    parser.add_argument("--ranges", default=None,
                        help="render several clips: 'start-end,start-end'")
    parser.add_argument("--name-template", default="{index}_{start}_{text}",
                        help="auto naming when the output is a folder; placeholders: "
                             "{index} {basename} {start} {end} {dur} {title} {text}")
    parser.add_argument("--out", default=None,
                        help="output file or folder; use this instead of the positional "
                             "argument with --batch or --ranges")
    parser.add_argument("--encoder", default="auto",
                        help="auto, libx264, h264_nvenc, h264_qsv, h264_amf")
    parser.add_argument("--censor-file", default=None,
                        help="word list for automatic censoring (default: censor.txt)")
    parser.add_argument("--no-censor", action="store_true")
    parser.add_argument("--no-remux", action="store_true")
    parser.add_argument("--drop", default=None,
                        help="ranges to cut out of the clip: '1.5-3.0,10-12'")
    parser.add_argument("--cut-pauses", action="store_true",
                        help="remove silent gaps from the clip")
    parser.add_argument("--pause-min", type=float, default=_set.PAUSE_MIN,
                        help="shortest silence worth cutting, seconds")
    parser.add_argument("--pause-threshold", type=int, default=-32,
                        help="what counts as silence, dB")
    parser.add_argument("--pause-keep", type=float, default=0.12,
                        help="silence left around speech so cuts do not sound abrupt")
    parser.add_argument("--pause-max-cuts", type=int, default=150)
    parser.add_argument("--censor-lead", type=float, default=0.12,
                        help="seconds of the word left audible before muting")
    parser.add_argument("--censor-tail", type=float, default=0.05)
    parser.add_argument("--top-height", type=int, default=_set.TOP_HEIGHT)
    parser.add_argument("--face-fill", type=float, default=None,
                        help="face height as a share of the webcam block")
    parser.add_argument("--face-y", type=float, default=0.42,
                        help="vertical position of the face inside the block")
    parser.add_argument("--samples", type=int, default=40)
    parser.add_argument("--cam-samples", type=int, default=10,
                        help="frames per clip used to find the webcam by the photo")
    parser.add_argument("--cam-box", default=None, help="manual webcam crop x,y,w,h")
    parser.add_argument("--cam-exact", action="store_true",
                        help="use --cam-box as given instead of fitting it to the block aspect")
    parser.add_argument("--face-model", default=None,
                        help="path to %s (downloaded to ~/.cache if omitted)" % FACE_MODEL_NAME)
    parser.add_argument("--face-ref", default=None,
                        help="photo of the streamer; the webcam is then picked by "
                             "who is on screen, not by what moves least")
    parser.add_argument("--face-match", type=float, default=0.4,
                        help="how close a face must be to the reference, 0..1")
    parser.add_argument("--no-face-id", action="store_true",
                        help="do not recognise the streamer at all")
    parser.add_argument("--face-id-samples", type=int, default=8,
                        help="frames per clip used to work out who the streamer is")
    parser.add_argument("--cam-face-samples", type=int, default=6,
                        help="frames used to find the streamer face inside the webcam")
    parser.add_argument("--no-face-in-cam", action="store_true",
                        help="do not look for the face inside the webcam box")
    parser.add_argument("--anchor-samples", type=int, default=4,
                        help="frames per clip used to find where the webcam usually sits")
    parser.add_argument("--no-anchor", action="store_true",
                        help="do not look for the usual webcam position beforehand")
    parser.add_argument("--fallback-corner", default="top-right",
                        choices=["top-left", "top-right", "bottom-left", "bottom-right", "center"])
    parser.add_argument("--game-y", type=float, default=0.5)
    parser.add_argument("--game-box", default=None, help="manual gameplay crop x,y,w,h")
    parser.add_argument("--game-exact", action="store_true",
                        help="use --game-box as given instead of fitting it to the block aspect")
    parser.add_argument("--blur-bg", action="store_true")
    parser.add_argument("--no-subs", action="store_true")
    parser.add_argument("--profile", default=None,
                        help="profile name; the active one is used when omitted")
    parser.add_argument("--no-profile", action="store_true",
                        help="ignore profiles entirely")
    parser.add_argument("--model", default=None)
    parser.add_argument("--language", default=None)
    parser.add_argument("--device", default="auto")
    parser.add_argument("--compute-type", default="default")
    parser.add_argument("--font", default=_set.SUBS_FONT)
    parser.add_argument("--font-size", type=int, default=96)
    parser.add_argument("--subs-x", type=int, default=OUT_W // 2)
    parser.add_argument("--subs-y", type=int, default=None,
                        help="by default the karaoke line sits just above the seam "
                             "in split layout and low in the frame otherwise")
    parser.add_argument("--layout", default=None,
                        choices=["auto", "split", "cam", "screen"],
                        help="auto: split when the webcam sits in a corner, cam when "
                             "the streamer fills the screen, screen when the streamer "
                             "is not in the frame at all; split: webcam over "
                             "gameplay; cam: webcam only; screen: main screen only")
    parser.add_argument("--title", default=None,
                        help="caption drawn at the top of the frame")
    parser.add_argument("--title-size", type=int, default=_set.TITLE_SIZE)
    parser.add_argument("--title-font", default=None)
    parser.add_argument("--title-color", default=TITLE_COLOR)
    parser.add_argument("--color-base", default=BASE_COLOR)
    parser.add_argument("--color-highlight", default=HIGHLIGHT_COLOR)
    parser.add_argument("--color-keyword", default=KEYWORD_COLOR)
    parser.add_argument("--max-words", type=int, default=3)
    parser.add_argument("--max-chars", type=int, default=16)
    parser.add_argument("--max-gap", type=float, default=0.9)
    parser.add_argument("--no-uppercase", action="store_true")
    parser.add_argument("--crf", type=int, default=19)
    parser.add_argument("--preset", default="medium")
    parser.add_argument("--keep-temp", action="store_true")
    parsed = parser.parse_args(argv)
    if parsed.censor_file is None:
        parsed.censor_file = find_resource("censor.txt")
    apply_profile(parsed)
    settle(parsed)
    return parsed


def settle(args):
    args.render = _set.from_args(args)
    args.layout = args.render.layout
    args.cam_box = (",".join(str(v) for v in args.render.cam_box)
                    if args.render.cam_box else None)
    args.cam_exact = not args.render.cam_fit
    args.subs_y = args.render.subs_y
    return args.render


def apply_profile(args):
    profile = None
    if not args.no_profile:
        try:
            profile = _pro.load(args.profile) if args.profile else _pro.active()
        except (FileNotFoundError, ValueError) as error:
            raise RuntimeError("профиль: %s" % error)
    args.profile_data = profile
    if profile is None:
        args.model = args.model or DEFAULT_WHISPER
        args.layout = args.layout or "split"
        return
    print("профиль: %s" % profile["name"])
    args.model = args.model or profile.get("whisper") or DEFAULT_WHISPER
    if args.language is None:
        args.language = profile.get("language") or None
    if args.face_ref is None:
        face = _pro.face_path(profile)
        if face is not None:
            args.face_ref = str(face)
    if args.layout is None:
        args.layout = profile.get("layout") or "split"


def cached_source_words(source, start, end, args):
    if args.drop or args.cut_pauses:
        return None
    key = _asr.cache_key(source, args.model, args.language or "", SCAN_BEAM, True)
    result = _asr.load_cache(source, key)
    if result is None:
        return None
    return words_from(_asr.Transcript(
        segments=[_asr.Segment(idx=0, start=start, end=end, text="",
                               words=_asr.slice_words(result, start, end))]))


def seekable_source(source, clips, enabled=True):
    if not enabled or clips < 2:
        return source
    if not _ff.is_fragmented_mp4(source):
        return source
    folder = os.path.join(tempfile.gettempdir(), "ave_cache")
    try:
        os.makedirs(folder, exist_ok=True)
    except OSError:
        return source
    target = str(_cache.remux_beside(source))
    if not os.path.isfile(target):
        gb = os.path.getsize(source) / 1024.0 ** 3
        print("container is fragmented, remuxing once (%.1f GB, no re-encode)" % gb)
    try:
        return str(_ff.make_seekable(source, target))
    except Exception as error:
        print("      remux failed (%s), reading source as is" % str(error)[:80])
        return source


def needs_survey(spotted):
    if _cam.closeup(spotted):
        return False
    return not (spotted is not None and spotted["confident"])


def pick_layout(spotted, survey):
    if _cam.closeup(spotted):
        return "cam", "крупный план, один блок"
    if spotted is not None and spotted["confident"]:
        return "split", "вебка в углу, два блока"
    if survey is None or survey["chosen"] is None:
        return "screen", "лица в кадре нет — беру только экран"
    if survey["asked"] and not survey["recognised"]:
        return "screen", "лицо есть, но это не стример — беру только экран"
    return "split", "вебка в углу, два блока"


def render_clip(source, info, start, end, output, args, state, folder=None, index=1,
                job_title=None, read_from=None, extra_drops=None):
    duration = end - start
    layout = getattr(args, "layout", "split") or "split"
    caption = job_title or args.title
    if output is not None:
        os.makedirs(os.path.dirname(output) or ".", exist_ok=True)

    workdir = tempfile.mkdtemp(prefix="vshorts_")
    clip = os.path.join(workdir, "clip.mp4")
    wav = os.path.join(workdir, "clip.wav")
    ass_path = os.path.join(workdir, "subs.ass")

    try:
        print("[1/5] cutting %.2fs -> %.2fs" % (start, end))
        cut_segment(read_from or source, clip, start, duration)

        drops = [tuple(pair) for pair in (extra_drops or ())]
        if args.drop:
            for chunk in str(args.drop).split(","):
                chunk = chunk.strip()
                if not chunk:
                    continue
                left, right = chunk.rsplit("-", 1)
                drops.append((parse_time(left), parse_time(right)))
        if drops:
            print("      applying %d manual cuts" % len(drops))
            trimmed = os.path.join(workdir, "trimmed.mp4")
            shorter = drop_ranges(clip, trimmed, drops, duration,
                                  state["encoder"], args.crf, args.preset)
            if shorter:
                clip = trimmed
                duration = shorter

        if args.cut_pauses and info.get("has_audio", True):
            print("      looking for pauses")
            tight = os.path.join(workdir, "tight.mp4")
            shortened = tighten_clip(clip, tight, duration, args, state["encoder"])
            if shortened:
                clip = tight
                duration = shortened

        spotted = None
        if layout != "screen" and not args.cam_box and state.get("locator") is not None:
            spotted = scan_webcam(clip, args.cam_samples, state["locator"])
            if spotted is not None:
                print("      вебка по снимку: %s, кадров %d из %d, опорных %d"
                      % (tuple(int(v) for v in spotted["box"]), spotted["seen"],
                         spotted["sampled"], spotted["inliers"]))
                anchor = state.get("cam_anchor")
                if not _cam.plausible(spotted["box"], info["width"], info["height"]):
                    print("      рамка не похожа на вебку, откладываю")
                    spotted = None
                elif anchor and not _cam.agrees(spotted["box"], anchor):
                    print("      рамка разошлась с обычным местом вебки, откладываю")
                    spotted = None
                elif not spotted["confident"] and _cam.agrees(spotted["box"], anchor):
                    spotted["confident"] = True
                    print("      совпало с обычным местом вебки на этом стриме")
            if spotted is not None:
                if spotted["confident"] and not _cam.closeup(spotted):
                    seen = state.setdefault("cam_seen", [])
                    seen.append(spotted["box"])
                    state["cam_anchor"] = _cam.median_box(seen)
            else:
                print("      вебку по снимку не нашёл")

            anchor = state.get("cam_anchor")
            if anchor and (spotted is None or not spotted["confident"]):
                spotted = {"box": tuple(anchor), "confident": True,
                           "seen": 0, "sampled": 0, "inliers": 0,
                           "cover": 0.0, "width_share": 0.0, "from_anchor": True}
                print("      беру обычное место вебки на этом стриме: %s"
                      % (tuple(int(v) for v in anchor),))

        survey = None
        if layout == "auto":
            if needs_survey(spotted):
                survey = survey_faces(clip, args.samples, args.face_model,
                                      embedder=state.get("embedder"),
                                      reference=state.get("identity"),
                                      threshold=args.face_match)
            layout, why = pick_layout(spotted, survey)
            print("      " + why)

        top_h = even(args.top_height) if layout == "split" else OUT_H
        bottom_h = OUT_H - top_h
        if layout == "split" and bottom_h < 200:
            raise RuntimeError("top-height leaves no room for gameplay")
        if args.face_fill is not None:
            face_fill = args.face_fill
        elif layout == "cam":
            face_fill = CAM_FACE_FILL
        else:
            face_fill = FACE_FILL
        if args.subs_y is not None:
            subs_y = args.subs_y
        elif layout == "split":
            subs_y = top_h - int(args.font_size * SEAM_SUBS_LIFT)
        else:
            subs_y = FULL_SUBS_Y

        cam_aspect = OUT_W / float(even(args.top_height))
        game_aspect = OUT_W / float(bottom_h) if bottom_h > 0 else OUT_W / float(OUT_H)

        if spotted is not None and spotted["confident"] and layout == "split":
            found = clamp_box(spotted["box"][0], spotted["box"][1],
                              spotted["box"][2], spotted["box"][3],
                              info["width"], info["height"])
            face = None
            if not args.no_face_in_cam:
                face = face_within(clip, found, args.cam_face_samples,
                                   args.face_model, embedder=None)
            cam_box = clamp_box(*_cam.crop_to_aspect(found, cam_aspect, focus=face),
                                frame_w=info["width"], frame_h=info["height"])
            frame_size = (info["width"], info["height"])
            if face is not None:
                print("      лицо в вебке: %s"
                      % (tuple(int(v) for v in face),))
            else:
                print("      лица в вебке не нашёл, режу по середине рамки")
            print("[2/5] рамка вебки со снимка: %s (из %s)" % (cam_box, found))
        elif layout == "screen" and not args.cam_box:
            cam_box = (0, 0, even(info["width"]), even(info["height"]))
            frame_size = (info["width"], info["height"])
            print("[2/5] main screen only, face not needed")
        elif args.cam_box:
            values = [float(v) for v in args.cam_box.replace(" ", "").split(",")]
            if len(values) != 4:
                raise RuntimeError("--cam-box must be x,y,w,h")
            if args.cam_exact:
                cam_box = clamp_box(values[0], values[1], values[2], values[3],
                                    info["width"], info["height"])
            else:
                cam_box = fit_box(
                    values[0] + values[2] / 2.0, values[1] + values[3] / 2.0,
                    values[2], values[3], cam_aspect, info["width"], info["height"],
                )
            frame_size = (info["width"], info["height"])
            print("[2/5] webcam box from arguments: %s" % (cam_box,))
        else:
            print("[2/5] detecting streamer face")
            cam_box, frame_size = detect_face_region(
                clip, args.samples, face_fill, cam_aspect, args.face_model,
                headroom=args.face_y, embedder=state.get("embedder"),
                reference=state.get("identity"), threshold=args.face_match,
                survey=survey,
            )
            if cam_box is None:
                cam_box = fallback_cam_box(
                    frame_size[0], frame_size[1], cam_aspect, args.fallback_corner
                )
                print("      no face found, fallback %s: %s" % (args.fallback_corner, cam_box))
            else:
                print("      webcam box: %s" % (cam_box,))

        if args.game_box:
            values = [float(v) for v in args.game_box.replace(" ", "").split(",")]
            if len(values) != 4:
                raise RuntimeError("--game-box must be x,y,w,h")
            if args.game_exact:
                game_box = clamp_box(values[0], values[1], values[2], values[3],
                                     frame_size[0], frame_size[1])
            else:
                game_box = fit_box(
                    values[0] + values[2] / 2.0, values[1] + values[3] / 2.0,
                    values[2], values[3], game_aspect, frame_size[0], frame_size[1],
                )
        elif layout == "screen":
            game_box = (0, 0, even(frame_size[0]), even(frame_size[1]))
        else:
            game_box = gameplay_box(frame_size[0], frame_size[1], game_aspect,
                                    clamp(args.game_y, 0.0, 1.0))
        if layout != "cam":
            print("      gameplay box: %s" % (game_box,))

        subs = None
        words = []
        mute_chain = None
        censoring = bool(state.get("censor") and state["censor"]["deny"])
        if not info.get("has_audio", True):
            print("[3/5] source has no audio track, subtitles and censoring skipped")
        elif not args.no_subs or censoring:
            if args.no_subs:
                print("[3/5] extracting audio and transcribing for censoring (%s)"
                      % args.model)
            else:
                print("[3/5] extracting audio and transcribing (%s)" % args.model)
            words = cached_source_words(source, start, end, args)
            if words is not None:
                print("      taken from the source transcript, no recognition needed")
            else:
                extract_audio(clip, wav)
                if state.get("model") is None:
                    state["model"] = build_model(args.model, args.device,
                                                 args.compute_type)
                words = transcribe(state["model"], wav, args.language)
            if not words:
                print("      no speech detected")
            elif censoring:
                hits, spans = apply_censor(words, state["censor"],
                                           args.censor_lead, args.censor_tail)
                if hits:
                    mute_chain = build_mute_filter(spans)
                    print("      censored %d words, %d muted spans" % (hits, len(spans)))
                else:
                    print("      nothing matched the censor list")
        else:
            print("[3/5] subtitles disabled")

        if output is None:
            clean = [word["text"] for word in words if "display" not in word]
            spoken = slugify(" ".join(clean[:10]))
            fields = {
                "index": "%02d" % index,
                "basename": slugify(os.path.splitext(os.path.basename(source))[0], 30),
                "start": stamp_time(start),
                "end": stamp_time(end),
                "dur": "%ds" % int(round(duration)),
                "title": slugify(caption or ""),
                "text": slugify(caption or "") or spoken or "clip",
            }
            output = auto_name(args.name_template, folder, fields)
            print("      auto name: %s" % os.path.basename(output))

        shown = [] if args.no_subs else words
        if shown or args.title:
            count = build_ass(
                shown, ass_path, args.font, args.font_size, args.subs_x, subs_y,
                not args.no_uppercase, args.max_words, args.max_chars, args.max_gap,
                title=args.title, title_y=args.title_size, title_size=args.title_size,
                duration=duration, base=args.color_base, highlight=args.color_highlight,
                keyword=args.color_keyword, title_color=args.title_color,
                title_font=args.title_font,
            )
            subs = ass_path
            print("      %d words, %d overlay events" % (len(shown), count))

        print("[4/5] building filter graph")
        graph = build_filter(cam_box, game_box, top_h, bottom_h, subs, args.blur_bg,
                             mute_chain, layout)

        print("[5/5] rendering %dx%d with %s" % (OUT_W, OUT_H, state["encoder"]))
        render(clip, output, graph, args.crf, args.preset, info.get("has_audio", True),
               state["encoder"], mute_chain is not None)
        print("done: %s" % output)
    finally:
        if args.keep_temp:
            print("temp kept: %s" % workdir)
        else:
            shutil.rmtree(workdir, ignore_errors=True)


TIMECODE_PATTERN = _hand.PATTERN


def parse_timecodes(path):
    if not os.path.isfile(path):
        raise RuntimeError("timecode file not found: %s" % path)
    items, skipped = _hand.parse_timecodes_text(read_text(path))
    if skipped:
        print("      lines without a valid range: %d" % skipped)
    return items


def parse_ranges(text):
    jobs = []
    for chunk in str(text).split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        if "-" not in chunk:
            raise RuntimeError("bad range: %s" % chunk)
        left, right = chunk.rsplit("-", 1)
        jobs.append((parse_time(left), parse_time(right)))
    return jobs


def slugify(text, limit=42):
    text = re.sub(r"\s+", " ", str(text)).strip()
    text = re.sub(r"[\\/:*?\"<>|]+", "", text)
    text = re.sub(r"[.,!?;…]+", "", text)
    text = text.replace(" ", "_")
    if len(text) > limit:
        text = text[:limit].rsplit("_", 1)[0]
    return text.strip(" -_")


def stamp_time(seconds):
    seconds = int(max(0.0, float(seconds)))
    return "%02d-%02d-%02d" % (seconds // 3600, (seconds % 3600) // 60, seconds % 60)


def unique_path(path):
    if not os.path.exists(path):
        return path
    base, extension = os.path.splitext(path)
    counter = 2
    while os.path.exists("%s_%d%s" % (base, counter, extension)):
        counter += 1
    return "%s_%d%s" % (base, counter, extension)


def auto_name(template, folder, fields):
    name = template
    for key, value in fields.items():
        name = name.replace("{%s}" % key, str(value))
    name = re.sub(r"_{2,}", "_", name).strip(" -_.")
    if not name or name.lower() in RESERVED_NAMES:
        name = "clip_%s" % name if name else "clip"
    return unique_path(os.path.join(folder, name + ".mp4"))


def job_outputs(output, count):
    target = os.path.abspath(output)
    base, extension = os.path.splitext(target)
    if os.path.isdir(target) or extension.lower() not in VIDEO_EXTENSIONS:
        os.makedirs(target, exist_ok=True)
        return [None] * count, target
    if count <= 1:
        return [target], None
    return ["%s_%02d%s" % (base, index + 1, extension) for index in range(count)], None


def setup_locator(args, state):
    if args.cam_box or args.layout == "screen" or not args.face_ref:
        return
    try:
        locator = _cam.Locator(args.face_ref)
    except Exception as error:
        print("поиск вебки по снимку недоступен: %s" % str(error)[:120])
        return
    if not locator.usable():
        print("на снимке слишком мало примет, искать вебку по нему нечем")
        return
    print("вебку буду искать по снимку: примет %d" % len(locator.keypoints))
    state["locator"] = locator


def setup_identity(source, jobs, args, state):
    if args.no_face_id or args.cam_box or args.layout == "screen":
        return
    if not _faceid.available():
        if args.face_ref:
            print("нет onnxruntime, опознание стримера пропущено:"
                  "  py -m pip install onnxruntime")
        return
    try:
        embedder = _faceid.Embedder(report=lambda message: print("  " + message))
    except RuntimeError as error:
        print("опознание стримера недоступно: %s" % error)
        return
    backend = FaceBackend(args.face_model)
    try:
        if args.face_ref:
            state["identity"] = face_reference(args.face_ref, backend, embedder)
            print("лицо стримера взято со снимка %s" % os.path.basename(args.face_ref))
        elif len(jobs) > 1:
            print("определяю, кто из лиц на экране — стример…", flush=True)
            found = scan_identity(source, jobs, backend, embedder,
                                  args.face_id_samples, args.face_match)
            if found is None:
                print("      лиц не нашлось, опознание выключено")
                return
            state["identity"], seen, total = found
            print("      узнан в %d клипах из %d" % (seen, total))
        else:
            return
    finally:
        backend.close()
    state["embedder"] = embedder


def main():
    args = parse_args()
    require_binary("ffmpeg")
    require_binary("ffprobe")

    source = os.path.abspath(args.input)
    if not os.path.isfile(source):
        raise RuntimeError("input not found: %s" % source)

    found = []
    chosen = []

    if args.timecodes:
        chosen = parse_timecodes(args.timecodes)
        if not chosen:
            raise RuntimeError("no valid ranges in %s" % args.timecodes)
        print("timecodes loaded: %d" % len(chosen))
        for item in chosen:
            print("   #%02d  %s -> %s   %s" % (
                item["index"], ass_time(item["start"]), ass_time(item["end"]),
                item["title"] or "(без названия)"))

    elif args.select:
        brief = args.brief or os.path.splitext(source)[0] + "_brief.txt"
        data = load_brief(brief)
        answer = read_text(args.select)
        chosen = parse_selection(answer, data)
        if not chosen:
            raise RuntimeError("no '#NN' entries found in %s" % args.select)
        print("selected %d fragments from the answer" % len(chosen))
        for item in chosen:
            print("   #%02d  %s -> %s   %s" % (
                item["index"], ass_time(item["start"]), ass_time(item["end"]), item["title"]))

    elif args.scan > 0:
        print("scanning audio for loud moments")
        pool = args.scan_pool if args.scan_mode == "transcript" else args.scan
        seen = [-1]

        def told(done, total):
            if not total:
                return
            step = int(100.0 * done / total) // 10
            if step != seen[0]:
                seen[0] = step
                print("      %d%%" % min(100, step * 10))

        heard = tempfile.mkdtemp(prefix="vscan_")
        spare = os.path.join(heard, "audio.raw")
        levels = audio_levels(source, cache=not args.scan_no_cache, progress=told,
                              samples=spare if args.scan_mode == "transcript" else None)
        found = pick_moments(levels, pool, args.scan_min, args.scan_max,
                             lead=args.scan_lead, gap=args.scan_gap)
        if not found:
            shutil.rmtree(heard, ignore_errors=True)
            print("no candidates found")
            return

        if args.scan_mode == "transcript":
            brief = args.brief or os.path.splitext(source)[0] + "_brief.txt"
            print("transcribing %d candidates with '%s'" % (len(found), args.scan_model))
            model = build_model(args.scan_model, args.device, args.compute_type)
            if not usable_samples(spare):
                print("      звук придётся распаковать заново (уровни взяты из кеша)")
            try:
                items = scan_transcript(
                    source, found, model, args.language,
                    lambda done, total: print("      %d/%d" % (done, total)),
                    rules=None if args.no_censor else load_censor_rules(args.censor_file),
                    ready=spare,
                )
            finally:
                shutil.rmtree(heard, ignore_errors=True)
            sidecar = write_brief(brief, source, items, args.scan)
            print("brief written: %s" % brief)
            print("data for --select: %s" % sidecar)
            print("paste the brief into a chat model, save its answer to a file,")
            print("then run again with --select answer.txt --batch --out <folder>")
            return

        shutil.rmtree(heard, ignore_errors=True)
        for index, item in enumerate(found, 1):
            print("%2d  %s  ->  %s   %5.1fs   peak %s   score %.2f" % (
                index, ass_time(item["start"]), ass_time(item["end"]),
                item["end"] - item["start"], ass_time(item["peak"]), item["score"]))
        if not args.batch:
            return

    info = probe_video(source)

    multi = bool(chosen or (found and args.batch) or args.ranges)
    destination = args.out or args.output
    if multi and not args.out and args.output is None and args.start_time:
        destination = args.start_time

    if chosen:
        jobs = [(item["start"], item["end"]) for item in chosen]
    elif found and args.batch:
        jobs = [(item["start"], item["end"]) for item in found]
    elif args.ranges:
        jobs = parse_ranges(args.ranges)
    else:
        if not args.start_time or not args.end_time:
            raise RuntimeError("start_time and end_time are required")
        jobs = [(parse_time(args.start_time), parse_time(args.end_time))]

    if not destination:
        raise RuntimeError("output is required: pass it last or via --out")

    for start, end in jobs:
        if end <= start:
            raise RuntimeError("end must be greater than start: %.2f-%.2f" % (start, end))
        if info["duration"] and start >= info["duration"]:
            raise RuntimeError("start is beyond source duration: %.2f" % start)

    targets, folder = job_outputs(destination, len(jobs))
    if folder:
        print("clips will be named automatically in %s" % folder)
    state = {"model": None, "encoder": pick_encoder(args.encoder), "censor": None}
    if not args.no_censor:
        state["censor"] = load_censor_rules(args.censor_file)
        if state["censor"]["deny"]:
            print("censor rules loaded: %d deny, %d allow"
                  % (len(state["censor"]["deny"]), len(state["censor"]["allow"])))

    read_from = seekable_source(source, len(jobs), not args.no_remux)
    setup_identity(read_from, jobs, args, state)
    setup_locator(args, state)
    if not args.no_anchor:
        setup_cam_anchor(read_from, jobs, args, state)

    titles = [item.get("title") for item in chosen] if chosen else [None] * len(jobs)
    cuts = ([_hand.relative_drops(item.get("drops"), item["start"], item["end"])
             for item in chosen] if chosen else [None] * len(jobs))
    for index, ((start, end), target, caption, extra) in enumerate(
            zip(jobs, targets, titles, cuts), 1):
        if len(jobs) > 1:
            print("=== clip %d/%d ===" % (index, len(jobs)))
        render_clip(source, info, start, end, target, args, state, folder, index,
                    caption, read_from, extra_drops=extra)


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        sys.stderr.write("error: %s\n" % error)
        sys.exit(1)
