from __future__ import annotations

import hashlib
import os
import subprocess
import tempfile

import numpy as np

from . import ffmpeg as _ff

RATE = 16000
FFT = 1024
HOP = 320
MELS = 40
LOW = 60.0
HIGH = 7600.0
THRESHOLD = 0.6
SHORTEST = 0.3
VERSION = 1


def mel_filters(fft=FFT, rate=RATE, mels=MELS, low=LOW, high=HIGH):
    def to_mel(hz):
        return 2595.0 * np.log10(1.0 + np.asarray(hz) / 700.0)

    def to_hz(mel):
        return 700.0 * (10.0 ** (np.asarray(mel) / 2595.0) - 1.0)

    edges = to_hz(np.linspace(to_mel(low), to_mel(min(high, rate / 2.0)), mels + 2))
    freqs = np.fft.rfftfreq(fft, 1.0 / rate)
    bank = np.zeros((mels, freqs.size), dtype=np.float32)
    for index in range(mels):
        left, middle, right = edges[index], edges[index + 1], edges[index + 2]
        rise = (freqs - left) / max(middle - left, 1e-6)
        fall = (right - freqs) / max(right - middle, 1e-6)
        bank[index] = np.maximum(0.0, np.minimum(rise, fall))
    return bank


class Spectrogram:
    def __init__(self, rate=RATE, fft=FFT, hop=HOP, mels=MELS):
        self.rate = rate
        self.fft = fft
        self.hop = hop
        self.taper = np.hanning(fft).astype(np.float32)
        self.bank = mel_filters(fft, rate, mels).T.copy()
        self.tail = np.zeros(fft - hop, dtype=np.float32)
        self.parts = []

    def feed(self, samples):
        samples = np.concatenate([self.tail, np.asarray(samples, dtype=np.float32)])
        count = (samples.size - self.fft) // self.hop + 1
        if count <= 0:
            self.tail = samples
            return
        frames = np.lib.stride_tricks.sliding_window_view(samples, self.fft)[::self.hop][:count]
        power = np.abs(np.fft.rfft(frames * self.taper, axis=1)) ** 2
        self.parts.append(np.log10(power.astype(np.float32) @ self.bank + 1e-3).astype(np.float16))
        self.tail = samples[count * self.hop:]

    def result(self):
        if not self.parts:
            mel = np.zeros((0, self.bank.shape[1]), dtype=np.float16)
        else:
            mel = np.concatenate(self.parts)
        return {"mel": mel, "hop": self.hop / float(self.rate)}


def spectrum_of(samples, rate=RATE):
    samples = np.asarray(samples, dtype=np.float32)
    if np.abs(samples).max(initial=0.0) <= 1.5:
        samples = samples * 32768.0
    builder = Spectrogram(rate=rate)
    step = rate * 60
    for begin in range(0, samples.size, step):
        builder.feed(samples[begin:begin + step])
    return builder.result()


def cache_path(path):
    try:
        stamp = "%s|%d|%d|%d|%d|%d|%d" % (os.path.abspath(path), os.path.getsize(path),
                                          int(os.path.getmtime(path)), RATE, FFT, HOP,
                                          MELS * 100 + VERSION)
    except OSError:
        return None
    name = hashlib.sha1(stamp.encode("utf-8")).hexdigest()[:16]
    folder = os.path.join(tempfile.gettempdir(), "ave_cache")
    try:
        os.makedirs(folder, exist_ok=True)
    except OSError:
        return None
    return os.path.join(folder, "mel_%s.npz" % name)


def spectrum(path, progress=None, cache=True, samples=None):
    store = cache_path(path) if cache else None
    if store and os.path.isfile(store):
        try:
            saved = np.load(store)
            return {"mel": saved["mel"], "hop": float(saved["hop"])}
        except Exception:
            pass
    total = _ff.duration(path) if progress else 0.0
    if samples and os.path.isfile(samples):
        process = None
        stream = open(samples, "rb")
    else:
        process = subprocess.Popen(
            [_ff.find("ffmpeg"), "-v", "error", "-i", str(path), "-vn", "-ac", "1",
             "-ar", str(RATE), "-f", "s16le", "-"],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, **_ff._NOWIN,
        )
        stream = process.stdout
    builder = Spectrogram()
    portion = RATE * 2 * 30
    spare = b""
    done = 0.0
    try:
        while True:
            data = stream.read(portion)
            if not data:
                break
            data = spare + data
            whole = len(data) // 2 * 2
            spare = data[whole:]
            chunk = np.frombuffer(data[:whole], dtype=np.int16)
            builder.feed(chunk)
            done += chunk.size / float(RATE)
            if progress:
                progress(done, total)
    finally:
        stream.close()
        if process is not None:
            process.wait()
    data = builder.result()
    if process is not None and process.returncode != 0 and data["mel"].shape[0] == 0:
        raise RuntimeError("не читается звук: %s" % path)
    if store and data["mel"].shape[0]:
        try:
            np.savez(store, mel=data["mel"], hop=data["hop"])
        except Exception:
            pass
    return data


def whiten(mel):
    mel = np.asarray(mel, dtype=np.float32)
    if mel.shape[0] == 0:
        return mel
    return mel - np.median(mel[::max(1, mel.shape[0] // 20000)], axis=0)


def correlate(signal, pattern):
    count, bands = signal.shape
    length = pattern.shape[0]
    if length < 2 or count < length:
        return np.zeros(0, dtype=np.float32)
    pattern = pattern - pattern.mean()
    energy = float(np.sqrt((pattern * pattern).sum()))
    if energy < 1e-6:
        return np.zeros(count - length + 1, dtype=np.float32)
    size = 1
    while size < count + length:
        size *= 2
    numerator = np.zeros(count - length + 1, dtype=np.float64)
    for band in range(bands):
        left = np.fft.rfft(signal[:, band], size)
        right = np.fft.rfft(pattern[::-1, band], size)
        numerator += np.fft.irfft(left * right, size)[length - 1:count]
    flat = signal.astype(np.float64)
    first = np.concatenate([[0.0], np.cumsum(flat.sum(axis=1))])
    second = np.concatenate([[0.0], np.cumsum((flat * flat).sum(axis=1))])
    total = first[length:] - first[:-length]
    squares = second[length:] - second[:-length]
    spread = np.sqrt(np.maximum(squares - total * total / float(length * bands), 1e-9))
    return (numerator / (spread * energy)).astype(np.float32)


def find_repeats(data, start, end, threshold=THRESHOLD, gap=None, limit=None):
    hop = float(data["hop"])
    mel = whiten(data["mel"])
    first = max(0, int(round(start / hop)))
    last = min(mel.shape[0], int(round(end / hop)))
    if (last - first) * hop < SHORTEST:
        raise ValueError("образец слишком короткий: нужно хотя бы %.1f с" % SHORTEST)
    return _peaks(correlate(mel, mel[first:last]), last - first, hop, threshold, gap, limit)


def find_sample(data, samples, threshold=THRESHOLD, gap=None, limit=None):
    hop = float(data["hop"])
    raw = np.asarray(data["mel"], dtype=np.float32)
    if raw.shape[0] == 0:
        return []
    middle = np.median(raw[::max(1, raw.shape[0] // 20000)], axis=0)
    pattern = np.asarray(spectrum_of(samples)["mel"], dtype=np.float32) - middle
    if pattern.shape[0] * hop < SHORTEST:
        raise ValueError("образец слишком короткий: нужно хотя бы %.1f с" % SHORTEST)
    return _peaks(correlate(raw - middle, pattern), pattern.shape[0], hop, threshold,
                  gap, limit)


def _peaks(score, length, hop, threshold, gap, limit):
    if score.size == 0:
        return []
    apart = max(length, int(round((gap or 0.0) / hop)))
    above = np.flatnonzero(score >= threshold)
    if above.size == 0:
        return []
    order = above[np.argsort(score[above])[::-1]]
    taken = []
    for index in order:
        if any(abs(int(index) - other) < apart for other in taken):
            continue
        taken.append(int(index))
        if limit and len(taken) >= limit:
            break
    taken.sort()
    return [{"start": index * hop, "end": (index + length) * hop,
             "score": float(score[index])} for index in taken]


def scan_jingle(path, start, end, threshold=THRESHOLD, gap=None, limit=None,
                progress=None, cache=True, samples=None):
    data = spectrum(path, progress=progress, cache=cache, samples=samples)
    return find_repeats(data, start, end, threshold=threshold, gap=gap, limit=limit)
