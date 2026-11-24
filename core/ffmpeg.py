from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import threading
from collections import deque
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable, Optional

NO_WINDOW = 0x08000000 if os.name == "nt" else 0
_NOWIN = {"creationflags": subprocess.CREATE_NO_WINDOW} if sys.platform == "win32" else {}


class FFmpegError(RuntimeError):
    pass


class FFmpegMissing(FFmpegError):
    pass


_BINARIES: dict = {}


def own_scripts() -> str:
    """Папка Scripts окружения, из которого запущен Python: там yt-dlp и streamlink.

    Студию запускают прямо .venv\\Scripts\\pythonw.exe, без activate, и эта папка
    в PATH не попадает, а инструменты ищутся через PATH.
    """
    if getattr(sys, "frozen", False):
        return ""
    here = os.path.dirname(os.path.abspath(sys.executable))
    for place in (here, os.path.join(here, "Scripts")):
        if any(os.path.isfile(os.path.join(place, name))
               for name in ("yt-dlp.exe", "yt-dlp", "streamlink.exe", "streamlink")):
            return place
    return ""


def use_bundled_tools(*roots) -> bool:
    found = False
    scripts = own_scripts()
    if scripts and scripts not in os.environ.get("PATH", "").split(os.pathsep):
        os.environ["PATH"] = scripts + os.pathsep + os.environ.get("PATH", "")
    for base in roots:
        if not base:
            continue
        local = os.path.join(str(base), "ffmpeg", "bin")
        if os.path.isdir(local):
            os.environ["PATH"] = local + os.pathsep + os.environ.get("PATH", "")
            found = True
            break
    _BINARIES.clear()
    return found


def binary(name: str) -> str:
    if name in _BINARIES:
        return _BINARIES[name]
    path = shutil.which(name)
    if not path:
        raise FFmpegMissing(
            "%s не найден в PATH. Поставьте: winget install Gyan.FFmpeg" % name)
    _BINARIES[name] = path
    return path


def find(name: str) -> str:
    return shutil.which(name) or name


def have_ffmpeg() -> bool:
    return shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None


@dataclass
class MediaInfo:
    path: str = ""
    duration: float = 0.0
    width: int = 0
    height: int = 0
    fps: float = 0.0
    has_video: bool = False
    has_audio: bool = False
    vcodec: str = ""
    acodec: str = ""


def parse_fps(text: str) -> float:
    try:
        if "/" in text:
            num, den = text.split("/")
            return float(num) / float(den) if float(den) else 0.0
        return float(text)
    except Exception:
        return 0.0


_PROBE_CACHE: dict = {}


def probe_cache_path(path) -> Path:
    path = Path(path)
    return path.with_suffix(path.suffix + ".probe.json")


def _probe_stored(path, key):
    try:
        with open(probe_cache_path(path), "r", encoding="utf-8") as handle:
            data = json.load(handle)
        if data.get("size") != key[1] or data.get("mtime") != key[2]:
            return None
        return MediaInfo(**{name: data["info"][name] for name in MediaInfo.__annotations__})
    except (OSError, ValueError, KeyError, TypeError):
        return None


def _probe_store(path, key, info):
    try:
        with open(probe_cache_path(path), "w", encoding="utf-8", newline="\n") as handle:
            json.dump({"size": key[1], "mtime": key[2], "info": asdict(info)}, handle,
                      ensure_ascii=False)
    except OSError:
        pass


def probe(path, use_cache: bool = True) -> MediaInfo:
    key = None
    if use_cache:
        try:
            stat = os.stat(path)
            key = (str(path), stat.st_size, int(stat.st_mtime))
            if key in _PROBE_CACHE:
                return _PROBE_CACHE[key]
            stored = _probe_stored(path, key)
            if stored is not None:
                _PROBE_CACHE[key] = stored
                return stored
        except OSError:
            key = None
    info = _probe_now(path)
    if key is not None:
        _PROBE_CACHE[key] = info
        _probe_store(path, key, info)
    return info


def _probe_now(path) -> MediaInfo:
    cmd = [find("ffprobe"), "-v", "error", "-print_format", "json",
           "-show_format", "-show_streams", str(path)]
    try:
        out = subprocess.run(cmd, capture_output=True, text=True,
                             encoding="utf-8", errors="replace", **_NOWIN)
    except FileNotFoundError as exc:
        raise FFmpegMissing("ffprobe не найден в PATH") from exc
    if out.returncode != 0:
        raise FFmpegError("ffprobe: %s" % out.stderr.strip()[:400])

    data = json.loads(out.stdout or "{}")
    info = MediaInfo(path=str(path))
    try:
        info.duration = float(data.get("format", {}).get("duration", 0.0))
    except (TypeError, ValueError):
        info.duration = 0.0

    for stream in data.get("streams", []):
        kind = stream.get("codec_type")
        if kind == "video" and not info.has_video:
            if stream.get("disposition", {}).get("attached_pic"):
                continue
            info.has_video = True
            info.width = int(stream.get("width") or 0)
            info.height = int(stream.get("height") or 0)
            info.fps = (parse_fps(stream.get("avg_frame_rate") or "0")
                        or parse_fps(stream.get("r_frame_rate") or "0"))
            info.vcodec = stream.get("codec_name", "")
            if not info.duration:
                try:
                    info.duration = float(stream.get("duration", 0.0))
                except (TypeError, ValueError):
                    pass
        elif kind == "audio" and not info.has_audio:
            info.has_audio = True
            info.acodec = stream.get("codec_name", "")
    return info


def duration(path) -> float:
    try:
        return probe(path).duration
    except Exception:
        return 0.0


def run(cmd: list, check: bool = True) -> str:
    resolved = [binary(str(cmd[0]))] + [str(a) for a in cmd[1:]]
    proc = subprocess.run(resolved, stdout=subprocess.PIPE,
                          stderr=subprocess.PIPE, **_NOWIN)
    if proc.returncode != 0 and check:
        sys.stderr.write(proc.stderr.decode("utf-8", "ignore"))
        shown = [str(cmd[0])] + [str(a) for a in cmd[1:3]]
        raise FFmpegError("command failed: %s" % " ".join(shown))
    return proc.stdout.decode("utf-8", "ignore")


def run_progress(args: list, total: float = 0.0,
                 progress: Optional[Callable[[float, str], None]] = None,
                 log: Optional[Callable[[str], None]] = None,
                 cancel: Optional[Callable[[], bool]] = None) -> None:
    full = [find("ffmpeg"), "-hide_banner", "-nostdin", "-loglevel", "error",
            "-progress", "pipe:1", "-nostats"] + [str(a) for a in args]
    if log:
        log("$ ffmpeg " + " ".join(quote(str(a)) for a in args))
    proc = subprocess.Popen(full, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            text=True, encoding="utf-8", errors="replace",
                            bufsize=1, **_NOWIN)
    assert proc.stdout is not None

    err_lines: deque = deque(maxlen=200)

    def drain():
        if proc.stderr is None:
            return
        for line in proc.stderr:
            err_lines.append(line.rstrip())

    err_thread = threading.Thread(target=drain, daemon=True)
    err_thread.start()
    try:
        for line in proc.stdout:
            line = line.strip()
            if cancel and cancel():
                proc.kill()
                raise FFmpegError("Отменено пользователем")
            if line.startswith("out_time_us=") or line.startswith("out_time_ms="):
                raw = line.split("=", 1)[1]
                if raw.lstrip("-").isdigit():
                    seconds = int(raw) / 1_000_000.0
                    if progress and total > 0:
                        progress(min(1.0, max(0.0, seconds / total)),
                                 format_hms(seconds))
            elif line.startswith("progress=") and line.endswith("end"):
                if progress:
                    progress(1.0, "")
    finally:
        proc.stdout.close()
    code = proc.wait()
    err_thread.join(timeout=5)
    err = chr(10).join(err_lines)
    if code != 0:
        signed = code - 2 ** 32 if code > 2 ** 31 else code
        raise FFmpegError("ffmpeg завершился с кодом %d:\n%s"
                          % (signed, err.strip()[-2000:]))
    if err.strip() and log:
        log(err.strip()[-1500:])


def stream_lines(cmd: list, on_line: Optional[Callable[[str], None]] = None):
    proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL,
                            stderr=subprocess.PIPE, text=True,
                            encoding="utf-8", errors="replace")
    tail, stat, buf = [], "", ""
    while True:
        char = proc.stderr.read(1)
        if not char:
            break
        if char in "\r\n":
            if buf.strip():
                if buf.startswith("frame=") or buf.startswith("size="):
                    stat = buf
                else:
                    tail.append(buf)
                    del tail[:-40]
                if on_line:
                    on_line(buf)
                else:
                    sys.stderr.write("\r" + buf[:150].ljust(150))
            buf = ""
        else:
            buf += char
    code = proc.wait()
    if not on_line:
        sys.stderr.write("\n")
    elif code != 0:
        on_line("--- ошибка ffmpeg ---")
        for line in tail[-15:]:
            on_line(line)
    return code, stat


def dup_count(stat: str) -> int:
    for token in str(stat).split():
        if token.startswith("dup="):
            try:
                return int(token[4:])
            except ValueError:
                return 0
    return 0


def quote(arg: str) -> str:
    return '"%s"' % arg if " " in arg else arg


def format_hms(seconds: float) -> str:
    seconds = max(0.0, seconds)
    hours, rest = divmod(int(seconds), 3600)
    minutes, secs = divmod(rest, 60)
    return "%d:%02d:%02d" % (hours, minutes, secs)


def extract_audio(src, dst, sample_rate: int = 16000,
                  progress: Optional[Callable[[float, str], None]] = None,
                  log: Optional[Callable[[str], None]] = None,
                  total: float = 0.0, skip_existing: bool = False):
    dst_path = Path(str(dst))
    dst_path.parent.mkdir(parents=True, exist_ok=True)
    if skip_existing and dst_path.exists():
        return str(dst)
    run_progress(["-y", "-i", str(src), "-vn", "-ac", "1", "-ar", str(sample_rate),
                  "-c:a", "pcm_s16le", str(dst)],
                 total=total, progress=progress, log=log)
    return str(dst)


def play_range(path, start: float, length: float):
    if not shutil.which("ffplay"):
        return None
    cmd = [find("ffplay"), "-hide_banner", "-loglevel", "error", "-autoexit",
           "-window_title", "preview %.1f-%.1f" % (start, start + length),
           "-ss", "%.3f" % max(0.0, start), "-t", "%.3f" % max(0.3, length),
           str(path)]
    return subprocess.Popen(cmd)


def is_fragmented_mp4(path) -> bool:
    path = Path(str(path))
    try:
        size = path.stat().st_size
        with path.open("rb") as handle:
            offset = 0
            for _ in range(24):
                handle.seek(offset)
                header = handle.read(16)
                if len(header) < 8:
                    return False
                box = int.from_bytes(header[:4], "big")
                kind = header[4:8]
                if box == 1:
                    box = int.from_bytes(header[8:16], "big")
                elif box == 0:
                    return False
                if kind == b"moof":
                    return True
                offset += box
                if box <= 0 or offset >= size:
                    return False
    except OSError:
        return False
    return False


def make_seekable(src, dst):
    src, dst = Path(str(src)), Path(str(dst))
    dst.parent.mkdir(parents=True, exist_ok=True)
    if dst.exists() and dst.stat().st_size > 0:
        return dst
    tmp = dst.with_suffix(".part.mp4")
    cmd = [binary("ffmpeg"), "-hide_banner", "-loglevel", "error", "-y",
           "-i", str(src), "-c", "copy", "-movflags", "+faststart", str(tmp)]
    res = subprocess.run(cmd, capture_output=True, text=True,
                         encoding="utf-8", errors="replace", **_NOWIN)
    if res.returncode != 0:
        tmp.unlink(missing_ok=True)
        raise FFmpegError("Не удалось переложить контейнер:\n%s" % res.stderr.strip())
    tmp.replace(dst)
    return dst


_ENCODER_LISTING = []


def encoder_available(name: str) -> bool:
    if not _ENCODER_LISTING:
        try:
            _ENCODER_LISTING.append(run(["ffmpeg", "-hide_banner", "-encoders"]))
        except RuntimeError:
            _ENCODER_LISTING.append("")
    return name in _ENCODER_LISTING[0]


def encoder_works(name: str):
    attempt = subprocess.run(
        [find("ffmpeg"), "-v", "error", "-f", "lavfi",
         "-i", "testsrc2=size=320x192:rate=30:duration=0.2",
         "-c:v", name, "-frames:v", "3", "-f", "null", "-"],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, **_NOWIN)
    if attempt.returncode == 0:
        return True, ""
    message = attempt.stderr.decode("utf-8", "ignore").strip().splitlines()
    return False, (" ".join(message[:3]) if message else "unknown error")


def explain_encoder(name: str, reason: str) -> str:
    text = str(reason)
    need = re.search(r"Required:\s*([\d.]+)\s*Found:\s*([\d.]+)", text)
    driver = re.search(r"minimum required Nvidia driver[^\d]*([\d.]+)", text)
    if name == "h264_nvenc" and need:
        note = ("видеокарта есть, но драйвер старый: nvenc просит API %s, "
                "у драйвера %s" % (need.group(1), need.group(2)))
        if driver:
            note += ". Нужен драйвер NVIDIA %s или новее" % driver.group(1)
        return note + ". Пока рендер идёт на процессоре"
    if name == "h264_qsv" and "MFX session" in text:
        return "встроенной графики Intel на этой машине нет"
    if name == "h264_amf" and "amfrt64.dll" in text:
        return "видеокарты AMD на этой машине нет"
    if "Cannot load" in text or "not found" in text.lower():
        return "нет библиотек: %s" % text
    return text


def pick_encoder(requested: str, report: Optional[Callable[[str], None]] = None) -> str:
    if requested != "auto":
        return requested
    for candidate in ("h264_nvenc", "h264_qsv", "h264_amf"):
        if not encoder_available(candidate):
            continue
        usable, reason = encoder_works(candidate)
        if usable:
            return candidate
        if report:
            report("      %s: %s" % (candidate, explain_encoder(candidate, reason)))
    return "libx264"


def video_encoder_args(encoder: str, crf, preset: str = "medium") -> list:
    if encoder == "libx264":
        return ["-c:v", "libx264", "-preset", preset, "-crf", str(crf)]
    if encoder == "h264_nvenc":
        return ["-c:v", "h264_nvenc", "-preset", "p5", "-tune", "hq",
                "-rc", "vbr", "-cq", str(crf), "-b:v", "0"]
    if encoder == "h264_qsv":
        return ["-c:v", "h264_qsv", "-global_quality", str(crf)]
    if encoder == "h264_amf":
        return ["-c:v", "h264_amf", "-quality", "quality", "-rc", "cqp",
                "-qp_i", str(crf), "-qp_p", str(crf)]
    return ["-c:v", encoder, "-crf", str(crf)]
