# -*- coding: utf-8 -*-
"""Снимок поведения всех обёрток над ffmpeg. Гоняется до и после правок."""
import os as _os
import sys as _sys

_HERE = _os.path.dirname(_os.path.abspath(__file__))
_TESTS = _HERE if _os.path.basename(_HERE) != "snapshot" else _os.path.dirname(_HERE)
_REPO = _os.path.dirname(_TESTS)
_sys.path.insert(0, _TESTS)
_sys.path.insert(0, _REPO)

from media import build as _build, CACHE as _CACHE

_MEDIA = _build()
_CACHE_U = _CACHE.replace("\\", "/")

import json, os, sys, tempfile

ROOT = _REPO
SECTION = sys.argv[1]
SAMPLE = sys.argv[2]
NOAUD = sys.argv[3]
out = {}

def rec(key, fn):
    try:
        out[key] = {"ok": True, "value": fn()}
    except Exception as e:
        out[key] = {"ok": False, "err": type(e).__name__, "msg": str(e)[:200]}

if SECTION == 'clipper':

    sys.path.insert(0, os.path.join(ROOT, "clipper"))
    from clipper import ffmpeg_util as CU
    rec("clipper.probe", lambda: CU.probe(__import__("pathlib").Path(SAMPLE)))
    rec("clipper.probe_noaudio", lambda: CU.probe(__import__("pathlib").Path(NOAUD)))
    rec("clipper.is_fragmented", lambda: CU.is_fragmented_mp4(__import__("pathlib").Path(SAMPLE)))
    def _extract():
        p = __import__("pathlib").Path(tempfile.mkdtemp()) / "a.wav"
        CU.extract_audio(__import__("pathlib").Path(SAMPLE), p)
        return [p.exists(), p.stat().st_size > 1000]
    rec("clipper.extract_audio", _extract)
    rec("clipper.FFmpegMissing_is_RuntimeError", lambda: issubclass(CU.FFmpegMissing, RuntimeError))

if SECTION == 'audioclean':
    sys.path.insert(0, os.path.join(ROOT, "audioclean"))
    from audioclean import ffmpeg as FF
    def _mi(m):
        return {k: (round(v, 3) if isinstance(v, float) else v)
                for k, v in m.__dict__.items() if k != "path"}
    rec("audioclean.probe", lambda: _mi(FF.probe(SAMPLE)))
    rec("audioclean.probe_noaudio", lambda: _mi(FF.probe(NOAUD)))
    rec("audioclean.have_ffmpeg", lambda: FF.have_ffmpeg())
    rec("audioclean.FFMPEG_is_str", lambda: isinstance(FF.FFMPEG, str) and "ffmpeg" in FF.FFMPEG.lower())
    rec("audioclean.NOWIN_keys", lambda: sorted(FF._NOWIN.keys()))
    rec("audioclean.hms", lambda: [FF._hms(0), FF._hms(3725.9), FF._hms(-5)])
    rec("audioclean.parse_fps", lambda: [FF._parse_fps("30/1"), FF._parse_fps("0/0"), FF._parse_fps("25"), FF._parse_fps("x")])
    def _run_prog():
        seen = []
        dst = os.path.join(tempfile.mkdtemp(), "a.wav")
        FF.extract_audio(SAMPLE, dst, 16000, progress=lambda f, t: seen.append(round(f, 1)), total=4.0)
        return [os.path.exists(dst), len(seen) > 0]
    rec("audioclean.extract_audio_progress", _run_prog)
    def _run_fail():
        FF.run(["-i", "нет-такого-файла.mp4", "-f", "null", "-"])
    rec("audioclean.run_error", _run_fail)

if SECTION == 'clipmaker':
    sys.path.insert(0, os.path.join(ROOT, "clipmaker"))
    import clipmaker as CM
    rec("clipmaker.probe", lambda: CM.probe(SAMPLE))
    rec("clipmaker.probe_noaudio", lambda: CM.probe(NOAUD))
    rec("clipmaker.parse_time", lambda: [CM.parse_time(x) for x in
        ["1:23:45", "23:45", "3725", "3725.5", "+7:25", "1:23:45.5", "0"]])
    rec("clipmaker.parse_time_none", lambda: [CM.parse_time(None), CM.parse_time(""), CM.parse_time("  ")])
    rec("clipmaker.parse_time_comma", lambda: CM.parse_time("12,5"))
    rec("clipmaker.parse_time_bad", lambda: CM.parse_time("абв"))
    rec("clipmaker.parse_time_toomany", lambda: CM.parse_time("1:2:3:4"))
    rec("clipmaker.fmt_time", lambda: [CM.fmt_time(0), CM.fmt_time(3725.5), CM.fmt_time(-3)])
    rec("clipmaker.venc_cpu", lambda: CM._venc({"crf": 20}))
    rec("clipmaker.venc_gpu", lambda: CM._venc({"crf": 18, "gpu": True}))
    rec("clipmaker.dup_count", lambda: [CM._dup_count("frame= 10 dup=5 drop=0"), CM._dup_count("size=1"), CM._dup_count("dup=x")])

if SECTION == 'astra':
    sys.path.insert(0, os.path.join(ROOT, "astra"))
    import auto_vertical_editor as AVE
    rec("astra.probe_video", lambda: AVE.probe_video(SAMPLE))
    rec("astra.probe_video_noaudio", lambda: AVE.probe_video(NOAUD))
    rec("astra.parse_time", lambda: [AVE.parse_time(x) for x in
        ["1:23:45", "23:45", "3725", "3725.5", "0", "1:23:45.5"]])
    rec("astra.parse_time_empty", lambda: AVE.parse_time(""))
    rec("astra.parse_time_bad", lambda: AVE.parse_time("абв"))
    rec("astra.parse_time_toomany", lambda: AVE.parse_time("1:2:3:4"))
    rec("astra.media_duration", lambda: round(AVE.media_duration(SAMPLE), 2))
    rec("astra.media_duration_missing", lambda: AVE.media_duration("нет-такого.mp4"))
    rec("astra.run_ok", lambda: len(AVE.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", SAMPLE])) > 0)
    rec("astra.run_fail", lambda: AVE.run(["ffprobe", "-v", "error", "нет-такого.mp4"]))
    rec("astra.require_binary", lambda: os.path.basename(AVE.require_binary("ffmpeg")).lower().startswith("ffmpeg"))
    rec("astra.require_binary_missing", lambda: AVE.require_binary("нетутакого"))
    rec("astra.encoder_available", lambda: AVE.encoder_available("libx264"))
    rec("astra.encoder_works", lambda: AVE.encoder_works("libx264"))
    rec("astra.pick_encoder_explicit", lambda: AVE.pick_encoder("libx264"))
    rec("astra.venc_args", lambda: [AVE.video_encoder_args(e, 20, "medium")
        for e in ("libx264", "h264_nvenc", "h264_qsv", "h264_amf", "hevc_x")])
    def _cut():
        dst = os.path.join(tempfile.mkdtemp(), "c.mp4")
        AVE.cut_segment(SAMPLE, dst, 0.5, 1.0)
        return os.path.exists(dst) and os.path.getsize(dst) > 1000
    rec("astra.cut_segment", _cut)
    def _ea():
        dst = os.path.join(tempfile.mkdtemp(), "a.wav")
        AVE.extract_audio(SAMPLE, dst)
        return os.path.exists(dst) and os.path.getsize(dst) > 1000
    rec("astra.extract_audio", _ea)

if SECTION == 'thumbmaker':
    sys.path.insert(0, os.path.join(ROOT, "thumbmaker"))
    from thumbmaker import sources as SRC
    rec("thumbmaker.FFMPEG_is_str", lambda: isinstance(SRC.FFMPEG, str) and "ffmpeg" in SRC.FFMPEG.lower())

print(json.dumps(out, ensure_ascii=False, indent=1, default=str))
