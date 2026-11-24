from __future__ import annotations

import hashlib
import os
import subprocess
import tempfile

import numpy as np

from . import ffmpeg as _ff
from . import models as _models

MODEL_NAME = "wespeaker_en_voxceleb_CAM++_LM.onnx"
MODEL_URL = ("https://github.com/k2-fsa/sherpa-onnx/releases/download/"
             "speaker-recongition-models/" + MODEL_NAME)
RATE = 16000
WINDOW = 2.0
STEP = 1.0
LOUD_MARGIN = 8.0
SAME = 0.4
GROUP = 0.5
REFINE = 3
VERSION = 1

STREAMER = "стример"
ROBOT = "робот"
OTHER = "другой"
QUIET = "тишина"


def runtime():
    try:
        import sherpa_onnx
    except ImportError:
        return None
    return sherpa_onnx


def available() -> bool:
    return runtime() is not None


def model_path(given=None, report=None) -> str:
    if given and os.path.isfile(str(given)):
        return _models.ascii_safe_path(given)
    cache = os.path.join(os.path.expanduser("~"), ".cache", "auto_vertical_editor")
    return _models.ensure(MODEL_NAME, cache, url=MODEL_URL, report=report)


def unit(vector):
    vector = np.asarray(vector, dtype=np.float32)
    size = float(np.linalg.norm(vector))
    return vector / size if size > 1e-9 else vector


class Embedder:
    def __init__(self, model=None, report=None, threads=4):
        onnx = runtime()
        if onnx is None:
            raise RuntimeError("нет sherpa-onnx — голос стримера не узнаётся:"
                               "  py -m pip install sherpa-onnx")
        config = onnx.SpeakerEmbeddingExtractorConfig(model=model_path(model, report),
                                                      num_threads=threads)
        self.extractor = onnx.SpeakerEmbeddingExtractor(config)
        self.size = int(self.extractor.dim)

    def embed(self, samples):
        samples = np.asarray(samples, dtype=np.float32)
        if samples.size and np.abs(samples).max() > 1.5:
            samples = samples / 32768.0
        stream = self.extractor.create_stream()
        stream.accept_waveform(RATE, samples)
        stream.input_finished()
        return unit(self.extractor.compute(stream))


def _stamp(path, *extra):
    try:
        text = "%s|%d|%d|" % (os.path.abspath(path), os.path.getsize(path),
                              int(os.path.getmtime(path)))
    except OSError:
        return None
    text += "|".join(str(part) for part in extra)
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:16]


def _cache_file(prefix, name, ext):
    if not name:
        return None
    folder = os.path.join(tempfile.gettempdir(), "ave_cache")
    try:
        os.makedirs(folder, exist_ok=True)
    except OSError:
        return None
    return os.path.join(folder, "%s_%s.%s" % (prefix, name, ext))


def raw_audio(path, samples=None, progress=None):
    if samples and os.path.isfile(samples):
        return np.memmap(samples, dtype=np.int16, mode="r")
    target = samples or _cache_file("raw", _stamp(path, RATE), "s16")
    if target and os.path.isfile(target):
        return np.memmap(target, dtype=np.int16, mode="r")
    if not target:
        raise RuntimeError("некуда положить звук стрима")
    total = _ff.duration(path) if progress else 0.0
    partial = target + ".part"
    process = subprocess.Popen(
        [_ff.find("ffmpeg"), "-v", "error", "-i", str(path), "-vn", "-ac", "1",
         "-ar", str(RATE), "-f", "s16le", "-"],
        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, **_ff._NOWIN)
    done = 0
    with open(partial, "wb") as sink:
        while True:
            data = process.stdout.read(RATE * 2 * 30)
            if not data:
                break
            sink.write(data)
            done += len(data)
            if progress:
                progress(done / (2.0 * RATE), total)
    process.stdout.close()
    process.wait()
    if process.returncode != 0 or done == 0:
        try:
            os.remove(partial)
        except OSError:
            pass
        raise RuntimeError("не читается звук: %s" % path)
    os.replace(partial, target)
    return np.memmap(target, dtype=np.int16, mode="r")


def window_levels(audio, window=WINDOW, step=STEP, rate=RATE):
    size = int(window * rate)
    hop = int(step * rate)
    count = max(0, (len(audio) - size) // hop + 1)
    levels = np.full(count, -120.0, dtype=np.float32)
    for index in range(count):
        piece = np.asarray(audio[index * hop:index * hop + size], dtype=np.float32)
        power = float((piece * piece).mean()) / (32768.0 * 32768.0)
        levels[index] = 10.0 * np.log10(power + 1e-12)
    return levels


def track(path, embedder=None, window=WINDOW, step=STEP, samples=None, audio=None,
          progress=None, cache=True, start=0.0, end=None):
    store = None
    if cache and path and audio is None:
        store = _cache_file("voice", _stamp(path, window, step, start, end, VERSION,
                                            MODEL_NAME), "npz")
        if store and os.path.isfile(store):
            try:
                saved = np.load(store)
                return {"time": saved["time"], "vec": saved["vec"].astype(np.float32),
                        "voiced": saved["voiced"], "level": saved["level"],
                        "window": float(window), "step": float(step)}
            except Exception:
                pass
    if audio is None:
        audio = raw_audio(path, samples=samples)
    first = int(max(0.0, start) * RATE)
    last = len(audio) if end is None else min(len(audio), int(end * RATE))
    audio = audio[first:last]
    embedder = embedder or Embedder()
    levels = window_levels(audio, window, step)
    count = levels.size
    floor = float(np.percentile(levels, 10)) if count else 0.0
    voiced = levels > floor + LOUD_MARGIN
    vectors = np.zeros((count, embedder.size), dtype=np.float32)
    size = int(window * RATE)
    hop = int(step * RATE)
    for index in range(count):
        if voiced[index]:
            piece = np.asarray(audio[index * hop:index * hop + size], dtype=np.float32)
            vectors[index] = embedder.embed(piece)
        if progress and index % 200 == 0:
            progress(index, count)
    times = (start + np.arange(count) * step + window / 2.0).astype(np.float32)
    data = {"time": times, "vec": vectors, "voiced": voiced, "level": levels,
            "window": float(window), "step": float(step)}
    if store:
        try:
            np.savez_compressed(store, time=times, vec=vectors.astype(np.float16),
                                voiced=voiced, level=levels)
        except Exception:
            pass
    return data


def clusters(vectors, same=SAME, rounds=2):
    vectors = np.asarray(vectors, dtype=np.float32)
    if vectors.shape[0] == 0:
        return []
    centers = []
    for vector in vectors:
        if centers:
            scores = np.asarray(centers) @ vector
            if float(scores.max()) >= same:
                continue
        centers.append(vector)
    centers = np.asarray(centers, dtype=np.float32)
    owner = np.zeros(vectors.shape[0], dtype=int)
    for _ in range(max(1, rounds)):
        scores = vectors @ centers.T
        owner = scores.argmax(axis=1)
        fresh = []
        for index in range(centers.shape[0]):
            members = vectors[owner == index]
            fresh.append(unit(members.mean(axis=0)) if members.size else centers[index])
        centers = np.asarray(fresh, dtype=np.float32)
    groups = [np.flatnonzero(owner == index) for index in range(centers.shape[0])]
    groups = [group for group in groups if group.size]
    groups.sort(key=lambda group: group.size, reverse=True)
    return groups


def reference(data, mask=None, least=8):
    usable = np.asarray(data["voiced"], dtype=bool).copy()
    if mask is not None:
        usable &= np.asarray(mask, dtype=bool)
    picked = np.flatnonzero(usable)
    if picked.size < least:
        return None
    vectors = np.asarray(data["vec"], dtype=np.float32)[picked]
    groups = clusters(vectors, same=GROUP)
    if not groups or groups[0].size < least:
        return None
    ref = unit(vectors[groups[0]].mean(axis=0))
    for _ in range(REFINE):
        close = vectors[vectors @ ref >= GROUP]
        if close.shape[0] < least:
            break
        ref = unit(close.mean(axis=0))
    return ref


def mask_from_spans(data, spans):
    times = np.asarray(data["time"], dtype=np.float32)
    mask = np.zeros(times.size, dtype=bool)
    for start, end, *_ in spans or ():
        mask |= (times >= float(start)) & (times <= float(end))
    return mask


def similarity(data, ref):
    vectors = np.asarray(data["vec"], dtype=np.float32)
    out = np.full(vectors.shape[0], np.nan, dtype=np.float32)
    if ref is None or vectors.shape[0] == 0:
        return out
    voiced = np.asarray(data["voiced"], dtype=bool)
    out[voiced] = vectors[voiced] @ unit(ref)
    return out


def labels(data, refs, same=SAME):
    voiced = np.asarray(data["voiced"], dtype=bool)
    out = np.full(voiced.size, QUIET, dtype=object)
    out[voiced] = OTHER
    best = np.full(voiced.size, -1.0, dtype=np.float32)
    for name, ref in (refs or {}).items():
        if ref is None:
            continue
        score = np.nan_to_num(similarity(data, ref), nan=-1.0)
        win = (score >= same) & (score > best)
        out[win] = name
        best = np.maximum(best, np.where(win, score, best))
    return out


def spans(data, marks, name, min_len=0.0, bridge=0.0):
    times = np.asarray(data["time"], dtype=np.float32)
    half = float(data["step"]) / 2.0
    hits = np.asarray([mark == name for mark in marks], dtype=bool)
    found = []
    index = 0
    while index < hits.size:
        if not hits[index]:
            index += 1
            continue
        stop = index
        while stop + 1 < hits.size and hits[stop + 1]:
            stop += 1
        start, end = float(times[index]) - half, float(times[stop]) + half
        if found and start - found[-1][1] <= bridge:
            found[-1][1] = end
        else:
            found.append([start, end])
        index = stop + 1
    return [(start, end) for start, end in found if end - start >= min_len]


def save_ref(path, vector) -> str:
    np.save(str(path), unit(vector).astype(np.float32))
    return str(path)


def load_ref(path):
    if not path or not os.path.isfile(str(path)):
        return None
    try:
        return unit(np.load(str(path)))
    except Exception:
        return None
