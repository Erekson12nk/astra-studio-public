from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, List, Optional


class WhisperMissing(RuntimeError):
    pass


CACHE_VERSION = 2

MODELS = ("tiny", "base", "small", "medium", "large-v3", "large-v3-turbo")
DEFAULT_MODEL = "large-v3-turbo"
DRAFT_MODEL = "tiny"


@dataclass
class Word:
    start: float
    end: float
    text: str
    prob: float = 1.0


@dataclass
class Segment:
    idx: int
    start: float
    end: float
    text: str
    words: List[Word] = field(default_factory=list)
    no_speech_prob: float = 0.0


@dataclass
class Transcript:
    segments: List[Segment] = field(default_factory=list)
    language: str = ""
    duration: float = 0.0

    def words(self) -> List[Word]:
        out: List[Word] = []
        for segment in self.segments:
            out.extend(segment.words)
        return out


def register_cuda_dlls() -> bool:
    if sys.platform != "win32":
        return True
    try:
        import nvidia
    except ImportError:
        return False

    dirs = [d for root in map(Path, nvidia.__path__)
            for d in root.rglob("bin") if any(d.glob("*.dll"))]
    if not dirs:
        return False

    for folder in dirs:
        try:
            os.add_dll_directory(str(folder))
        except (OSError, AttributeError):
            pass
    os.environ["PATH"] = (os.pathsep.join(str(d) for d in dirs)
                          + os.pathsep + os.environ.get("PATH", ""))
    return True


def cuda_usable() -> bool:
    register_cuda_dlls()
    try:
        import ctranslate2
        if ctranslate2.get_cuda_device_count() <= 0:
            return False
    except Exception:
        return False
    if sys.platform == "win32":
        import ctypes
        for dll in ("cublas64_12.dll", "cudnn_ops64_9.dll"):
            try:
                ctypes.WinDLL(dll)
            except OSError:
                return False
    return True


def resolve_plans(device: str, compute: str) -> list:
    if device == "auto":
        return [("cuda", "float16" if compute in ("default", "") else compute),
                ("cpu", "int8" if compute in ("default", "") else _on_cpu(compute))]
    resolved = compute
    if resolved in ("default", ""):
        resolved = "float16" if device == "cuda" else "int8"
    elif device == "cpu":
        resolved = _on_cpu(resolved)
    return [(device, resolved)]


def _on_cpu(compute: str) -> str:
    return "int8" if compute.endswith("float16") else compute


_MODEL_CACHE: dict = {}


def load_model(model_size: str, device: str = "auto", compute: str = "default",
               log: Optional[Callable[[str], None]] = None,
               verify: bool = False, cache: bool = True):
    ready = register_cuda_dlls()

    try:
        from faster_whisper import WhisperModel
    except ImportError as exc:
        raise WhisperMissing(
            "faster-whisper не установлен.\n"
            "  pip install faster-whisper\n\n"
            "Если pip ругается на отсутствие пакета ctranslate2 для вашей версии\n"
            "Python (под самые свежие колёса появляются не сразу), создайте\n"
            "окружение на Python 3.12:\n"
            "  py -3.12 -m venv .venv && .venv\\Scripts\\activate"
            " && pip install -r requirements.txt") from exc

    plans = resolve_plans(device, compute)
    if not ready:
        usable = [p for p in plans if p[0] != "cuda"]
        if usable and len(usable) != len(plans) and log:
            log("библиотеки CUDA не найдены, считаю на процессоре "
                "(примерно вчетверо медленнее); чтобы на видеокарте: "
                "py -m pip install nvidia-cublas-cu12 nvidia-cudnn-cu12")
        plans = usable or plans

    failure = None
    for target, kind in plans:
        key = (model_size, target, kind)
        if cache and key in _MODEL_CACHE:
            return _MODEL_CACHE[key]
        try:
            if log:
                log("загружаю модель %s (%s/%s); при первом запуске она "
                    "скачается с huggingface" % (model_size, target, kind))
            model = WhisperModel(model_size, device=target, compute_type=kind)
            if verify:
                import numpy as np
                silence = np.zeros(16000, dtype=np.float32)
                segments, _ = model.transcribe(silence, beam_size=1,
                                               without_timestamps=True)
                list(segments)
            if cache:
                _MODEL_CACHE[key] = model
            return model
        except Exception as error:
            failure = error
            if log and len(plans) > 1 and target == "cuda":
                log("видеокарта не годится для whisper (%s), перехожу на процессор"
                    % str(error).strip().splitlines()[0])
    raise RuntimeError("не удалось создать модель whisper: %s" % failure)


def transcribe(audio, model, language: Optional[str] = None,
               beam_size: int = 5, vad: bool = True,
               vad_min_silence_ms: Optional[int] = None,
               condition_on_previous_text: Optional[bool] = None,
               word_timestamps: bool = True,
               progress: Optional[Callable[[float, str], None]] = None,
               log: Optional[Callable[[str], None]] = None,
               cancel: Optional[Callable[[], bool]] = None,
               duration: float = 0.0) -> Transcript:
    options = dict(
        language=(language or None),
        beam_size=beam_size,
        word_timestamps=word_timestamps,
        vad_filter=vad,
    )
    if vad and vad_min_silence_ms is not None:
        options["vad_parameters"] = {"min_silence_duration_ms": vad_min_silence_ms}
    if condition_on_previous_text is not None:
        options["condition_on_previous_text"] = condition_on_previous_text

    seg_iter, info = model.transcribe(
        audio if not isinstance(audio, (str, Path)) else str(audio), **options)

    total = duration or float(getattr(info, "duration", 0.0) or 0.0)
    if log:
        log("язык: %s (%.2f), длительность %.0f c"
            % (getattr(info, "language", "?"),
               getattr(info, "language_probability", 0.0) or 0.0, total))

    segments: List[Segment] = []
    for index, raw in enumerate(seg_iter):
        if cancel and cancel():
            raise RuntimeError("Распознавание отменено")
        words = [Word(start=w.start, end=w.end, text=w.word,
                      prob=getattr(w, "probability", 1.0))
                 for w in (raw.words or [])
                 if w.start is not None and w.end is not None]
        segments.append(Segment(idx=index, start=raw.start, end=raw.end,
                                text=raw.text, words=words,
                                no_speech_prob=float(
                                    getattr(raw, "no_speech_prob", 0.0) or 0.0)))
        if progress and total:
            progress(min(0.999, raw.end / total), "%.0f/%.0f c" % (raw.end, total))
    if progress:
        progress(1.0, "готово")

    return Transcript(segments=segments,
                      language=getattr(info, "language", language or "") or "",
                      duration=total)


def slice_transcript(transcript: Transcript, start: float, end: float,
                     rebase: bool = True) -> Transcript:
    shift = start if rebase else 0.0
    segments: List[Segment] = []
    for segment in transcript.segments:
        if segment.end <= start or segment.start >= end:
            continue
        words = [Word(max(w.start, start) - shift, min(w.end, end) - shift,
                      w.text, w.prob)
                 for w in segment.words
                 if w.end > start and w.start < end and min(w.end, end) > max(w.start, start)]
        segments.append(Segment(idx=len(segments),
                                start=max(segment.start, start) - shift,
                                end=min(segment.end, end) - shift,
                                text=segment.text, words=words,
                                no_speech_prob=segment.no_speech_prob))
    return Transcript(segments=segments, language=transcript.language,
                      duration=max(0.0, end - start))


def slice_words(transcript: Transcript, start: float, end: float,
                rebase: bool = True) -> List[Word]:
    out: List[Word] = []
    for word in transcript.words():
        if word.end <= start or word.start >= end:
            continue
        left = max(word.start, start)
        right = min(word.end, end)
        if right <= left:
            continue
        shift = start if rebase else 0.0
        out.append(Word(left - shift, right - shift, word.text, word.prob))
    out.sort(key=lambda w: w.start)
    return out


def split_segment(segment: Segment) -> List[Word]:
    parts = [p for p in (segment.text or "").split() if p]
    span = segment.end - segment.start
    if not parts or span <= 0:
        return []
    total = sum(len(p) for p in parts)
    out: List[Word] = []
    moment = segment.start
    for part in parts:
        step = span * len(part) / total
        out.append(Word(moment, moment + step, part, 0.0))
        moment += step
    return out


def cache_path(source) -> Path:
    path = Path(str(source))
    return path.with_suffix(path.suffix + ".speech.json")


def cache_key(source, model: str, language: str = "", beam_size: int = 5,
              vad: bool = True) -> dict:
    try:
        size = os.path.getsize(str(source))
    except OSError:
        size = 0
    return {"model": model, "size": size, "language": language or "",
            "beam_size": beam_size, "vad": bool(vad)}


def load_cache(source, key: dict) -> Optional[Transcript]:
    path = cache_path(source)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None

    version = data.get("version", 1)
    if version == 1:
        if data.get("model") != key["model"] or data.get("size") != key["size"]:
            return None
    else:
        if any(data.get(name) != value for name, value in key.items()):
            return None

    try:
        segments = [
            Segment(idx=d.get("idx", i), start=d["start"], end=d["end"],
                    text=d.get("text", ""),
                    words=[Word(w["start"], w["end"], w["text"], w.get("prob", 1.0))
                           for w in d.get("words", [])],
                    no_speech_prob=d.get("no_speech_prob", 0.0))
            for i, d in enumerate(data.get("segments", []))]
    except (KeyError, TypeError):
        return None
    return Transcript(segments=segments, language=data.get("detected_language", ""),
                      duration=data.get("duration", 0.0))


def save_cache(source, key: dict, transcript: Transcript) -> None:
    payload = dict(key)
    payload.update({
        "version": CACHE_VERSION,
        "detected_language": transcript.language,
        "duration": transcript.duration,
        "segments": [
            {"idx": s.idx, "start": s.start, "end": s.end, "text": s.text,
             "no_speech_prob": s.no_speech_prob,
             "words": [{"start": w.start, "end": w.end, "text": w.text,
                        "prob": w.prob} for w in s.words]}
            for s in transcript.segments],
    })
    try:
        cache_path(source).write_text(
            json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    except OSError:
        pass


def to_srt(segments: List[Segment]) -> str:
    def stamp(value: float) -> str:
        hours, rest = divmod(max(0.0, value), 3600)
        minutes, secs = divmod(rest, 60)
        return "%02d:%02d:%02d,%03d" % (int(hours), int(minutes), int(secs),
                                        int((secs % 1) * 1000))
    parts = []
    for number, segment in enumerate(segments, 1):
        parts.append("%d\n%s --> %s\n%s\n"
                     % (number, stamp(segment.start), stamp(segment.end), segment.text))
    return "\n".join(parts)
