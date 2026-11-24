#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
clipmaker - нарезка роликов из длинного стрима + концовка с переходом.

Что делает:
  1. вырезает кусок стрима (начало / конец)
  2. в конце куска: плавное затемнение картинки + приглушение звука
  3. переходит к концовке (outro), которая размыта и появляется из черного
  4. поверх концовки играет музыка (любой файл, меняется параметром)

Пример:
  python clipmaker.py -i stream.mp4 -s 1:23:45 -e 1:31:10 \
      --outro outro.mp4 --music track.mp3 -o clip01.mp4

Почему тут три прохода, а не один filter_complex:
  в ffmpeg 9.0 граф, где в одном filter_complex сидят и видео-, и аудиоветка,
  на длинных кусках начинает молча терять видеокадры (проверено: 55 000 кадров
  вместо 78 000 за 21 минуту, число плавает от запуска к запуску). Картинка
  замирает, звук идет дальше. Поэтому кусок и концовка кодируются отдельно
  простыми -vf/-af, а потом склеиваются без перекодирования.
"""

import argparse
import os
import sys

_ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
if os.path.isdir(os.path.join(_ROOT, "core")) and _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from core import ffmpeg as _ff
from core import handoff as _hand
from core import timecode as _tc

FFMPEG = _ff.find("ffmpeg")
FFPROBE = _ff.find("ffprobe")


# ---------------------------------------------------------------- утилиты

parse_time = _tc.parse_time
fmt_time = _tc.format_time


def probe(path):
    """Инфо о файле: длительность, разрешение, fps, наличие дорожек."""
    info = _ff.probe(path)
    return {"duration": info.duration or None,
            "width": info.width or None,
            "height": info.height or None,
            "fps": info.fps or None,
            "has_audio": info.has_audio,
            "has_video": info.has_video}


def _venc(opt):
    crf = str(opt.get("crf") or 20)
    if opt.get("gpu"):
        return ["-c:v", "h264_nvenc", "-preset", "p5", "-rc", "vbr",
                "-cq", crf, "-b:v", "0"]
    return ["-c:v", "libx264", "-preset", opt.get("preset") or "veryfast",
            "-crf", crf]


AENC = ["-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2"]
AFMT = "aformat=sample_fmts=fltp:sample_rates=48000:channel_layouts=stereo"


# ---------------------------------------------------------------- планирование

SEAM = 0.08


def keep_pieces(start, dur, drops):
    end = start + dur
    cuts = sorted((max(start, float(a)), min(end, float(b)))
                  for a, b, *_ in (drops or ()) if float(b) > start and float(a) < end)
    keeps = []
    cursor = start
    for left, right in cuts:
        if left - cursor > 0.5:
            keeps.append((cursor, left - cursor))
        cursor = max(cursor, right)
    if end - cursor > 0.5:
        keeps.append((cursor, end - cursor))
    return keeps or [(start, dur)]


def plan(opt):
    """Считает параметры и возвращает список этапов [(название, [аргументы]), ...]."""
    src = probe(opt["input"])
    outro = probe(opt["outro"]) if opt.get("outro") else None

    start = parse_time(opt["start"]) or 0.0
    end_raw = str(opt["end"]).strip()
    if end_raw.startswith("+"):
        dur = parse_time(end_raw)
    else:
        end = parse_time(end_raw)
        if end is None or end <= start:
            raise ValueError("Конец ролика должен быть позже начала.")
        dur = end - start
    if not dur or dur <= 0:
        raise ValueError("Длительность ролика получилась нулевой.")
    if src["duration"] and start >= src["duration"]:
        raise ValueError("Начало (%s) за пределами файла (%s)."
                         % (fmt_time(start), fmt_time(src["duration"])))

    W = int(opt.get("width") or src["width"] or 1920) // 2 * 2
    H = int(opt.get("height") or src["height"] or 1080) // 2 * 2
    fps = float(opt.get("fps") or src["fps"] or 30.0)

    fade = float(opt.get("fade") or 1.5)
    blur = float(opt.get("blur") if opt.get("blur") is not None else 20)
    dark = float(opt.get("dark") or 0.0)
    mvol = float(opt.get("music_volume") or 1.0)
    fade = max(0.05, min(fade, dur / 2.0))

    scale = ("scale={W}:{H}:force_original_aspect_ratio=decrease,"
             "pad={W}:{H}:(ow-iw)/2:(oh-ih)/2,setsar=1,fps={fps},"
             "format=yuv420p").format(W=W, H=H, fps=("%.6f" % fps))

    out = opt["output"]
    base = os.path.splitext(out)[0]
    parts = []      # временные куски для склейки
    stages = []

    keeps = keep_pieces(start, dur, opt.get("drops"))
    kept = sum(length for _, length in keeps)
    fade = max(0.05, min(fade, keeps[-1][1] / 2.0))
    single = outro is None and len(keeps) == 1

    # ---- этап 1: сам кусок стрима (или несколько, если есть вырезы) -----
    for number, (piece_start, piece_dur) in enumerate(keeps, 1):
        last = number == len(keeps)
        target = out if single else "%s.part1_%02d.ts" % (base, number)
        if not single:
            parts.append(target)
        cmd = [FFMPEG, "-hide_banner", "-y",
               "-ss", fmt_time(piece_start), "-t", "%.3f" % piece_dur, "-i", opt["input"]]
        amap = "0:a:0"
        if not src["has_audio"]:
            cmd += ["-f", "lavfi", "-t", "%.3f" % piece_dur,
                    "-i", "anullsrc=r=48000:cl=stereo"]
            amap = "1:a:0"
        if last:
            vf = "%s,fade=t=out:st=%.3f:d=%.3f" % (scale, max(0.0, piece_dur - fade), fade)
            af = "%s,afade=t=out:st=%.3f:d=%.3f" % (AFMT, max(0.0, piece_dur - fade), fade)
        else:
            vf = scale
            af = "%s,afade=t=out:st=%.3f:d=%.3f" % (AFMT, max(0.0, piece_dur - SEAM),
                                                     SEAM)
        if number > 1:
            af += ",afade=t=in:st=0:d=%.3f" % SEAM
        cmd += ["-map", "0:v:0", "-vf", vf, "-map", amap, "-af", af,
                "-r", "%.6f" % fps] + _venc(opt) + ["-pix_fmt", "yuv420p"] + AENC
        if single:
            cmd += ["-movflags", "+faststart", target]
        else:
            cmd += ["-f", "mpegts", target]
        name = "кусок стрима" if len(keeps) == 1 else "кусок стрима %d из %d" % (
            number, len(keeps))
        stages.append((name, cmd, piece_dur))

    if single:
        return stages, parts, out, fps, dur, dur

    if outro is None:
        listfile = base + ".parts.txt"
        concat = [FFMPEG, "-hide_banner", "-y", "-f", "concat", "-safe", "0",
                  "-i", listfile, "-c", "copy", "-movflags", "+faststart", out]
        stages.append(("склейка", concat, 0.0))
        return stages, parts, listfile, fps, kept, kept

    # ---- этап 2: концовка ---------------------------------------------
    outro_dur = outro["duration"] or 5.0
    ofade = max(0.05, min(fade, outro_dur / 2.0))
    outro_target = base + ".part2.ts"
    parts.append(outro_target)

    vf = scale
    if blur > 0:
        vf += ",gblur=sigma=%.2f:steps=2" % blur
    if dark > 0:
        vf += ",colorlevels=rimax={m:.3f}:gimax={m:.3f}:bimax={m:.3f}".format(
            m=max(0.05, 1.0 - dark))
    vf += ",format=yuv420p,fade=t=in:st=0:d=%.3f" % ofade
    vf += ",fade=t=out:st=%.3f:d=%.3f" % (max(0.0, outro_dur - ofade), ofade)

    cmd = [FFMPEG, "-hide_banner", "-y", "-i", opt["outro"]]
    music = opt.get("music")
    if music:
        # music_start — с какой секунды трека играть (например, с припева)
        start = float(opt.get("music_start") or 0.0)
        cmd += (["-ss", "%.3f" % start] if start > 0 else []) + [
            "-stream_loop", "-1", "-i", music]
        amap, avol = "1:a:0", ",volume=%.3f" % mvol
    elif outro["has_audio"]:
        amap, avol = "0:a:0", ""
    else:
        cmd += ["-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo"]
        amap, avol = "1:a:0", ""
    cmd += [
        "-map", "0:v:0", "-vf", vf,
        "-map", amap,
        "-af", "%s%s,afade=t=in:st=0:d=%.3f,afade=t=out:st=%.3f:d=%.3f"
               % (AFMT, avol, ofade, max(0.0, outro_dur - ofade), ofade),
        "-t", "%.3f" % outro_dur,
        "-r", "%.6f" % fps,
    ] + _venc(opt) + ["-pix_fmt", "yuv420p"] + AENC + ["-f", "mpegts", outro_target]
    stages.append(("концовка", cmd, outro_dur))

    # ---- этап 3: склейка без перекодирования ---------------------------
    listfile = base + ".parts.txt"
    concat = [FFMPEG, "-hide_banner", "-y", "-f", "concat", "-safe", "0",
              "-i", listfile, "-c", "copy", "-movflags", "+faststart", out]
    stages.append(("склейка", concat, 0.0))
    return stages, parts, listfile, fps, kept + outro_dur, kept


# ---------------------------------------------------------------- запуск

_run = _ff.stream_lines
_dup_count = _ff.dup_count


def make_clip(opt, on_line=None):
    """Собирает ролик. on_line(str) - колбэк для лога. Возвращает код возврата."""
    say = on_line or (lambda s: sys.stderr.write(s + "\n"))
    stages, parts, listfile, fps, total, main_dur = plan(opt)

    if opt.get("dry_run"):
        for name, cmd, _ in stages:
            say("# " + name)
            say(" ".join('"%s"' % c if " " in str(c) else str(c) for c in cmd))
        return 0

    cleanup = []
    try:
        for name, cmd, seg_dur in stages:
            if name == "склейка":
                # перед склейкой пишем список кусков
                with open(listfile, "w", encoding="utf-8") as fh:
                    for p in parts:
                        fh.write("file '%s'\n" % p.replace("'", "'\\''"))
                cleanup.append(listfile)
            say("[%s]" % name)
            rc, stat = _run(cmd, on_line)
            if rc != 0:
                return rc
            dup = _dup_count(stat)
            if seg_dur and dup > fps:
                say("ВНИМАНИЕ: ffmpeg продублировал %d кадров (~%.1f с) в этапе "
                    "'%s' - картинка местами может стоять." % (dup, dup / fps, name))
        start = parse_time(opt["start"]) or 0.0
        if len(parts) > 1 and opt.get("drops"):
            return 0
        if _hand.write_origin(opt["output"], opt["input"], start, start + main_dur):
            say("рядом записано, откуда вырезан ролик - глушилка мата "
                "возьмёт готовую расшифровку стрима вместо новой")
        return 0
    finally:
        for p in parts + cleanup:
            if p != opt["output"] and os.path.isfile(p):
                try:
                    os.remove(p)
                except OSError:
                    pass


# ---------------------------------------------------------------- CLI

def main(argv=None):
    p = argparse.ArgumentParser(
        description="Нарезка ролика из стрима с переходом в размытую концовку.")
    p.add_argument("-i", "--input", required=True, help="исходный стрим")
    p.add_argument("-s", "--start", required=True, help="начало (1:23:45)")
    p.add_argument("-e", "--end", required=True,
                   help="конец (1:31:10) или +длительность (+7:25)")
    p.add_argument("-o", "--output", required=True, help="куда сохранить")
    p.add_argument("--outro", help="файл концовки")
    p.add_argument("--music", help="музыка поверх концовки (заменяет её звук)")
    p.add_argument("--music-volume", type=float, default=1.0,
                   help="громкость музыки, 1.0 = как есть")
    p.add_argument("--music-start", type=float, default=0.0,
                   help="с какой секунды трека играть музыку")
    p.add_argument("--fade", type=float, default=1.5,
                   help="секунды затемнения/приглушения (по умолчанию 1.5)")
    p.add_argument("--blur", type=float, default=20,
                   help="сила размытия концовки, 0 = без размытия")
    p.add_argument("--dark", type=float, default=0.0,
                   help="затемнить концовку, 0..1")
    p.add_argument("--width", type=int, help="ширина (по умолчанию как в стриме)")
    p.add_argument("--height", type=int, help="высота")
    p.add_argument("--fps", type=float, help="кадры/сек")
    p.add_argument("--crf", type=int, default=20, help="качество, меньше = лучше")
    p.add_argument("--preset", default="veryfast", help="пресет x264")
    p.add_argument("--gpu", action="store_true", help="кодировать на NVIDIA (nvenc)")
    p.add_argument("--dry-run", action="store_true", help="только показать команды")
    a = p.parse_args(argv)

    for stream in (sys.stdout, sys.stderr):   # чтобы русский текст не ломался в cmd
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass

    opt = vars(a)
    if not os.path.isfile(opt["input"]):
        sys.exit("Нет файла: %s" % opt["input"])
    if opt["outro"] and not os.path.isfile(opt["outro"]):
        sys.exit("Нет файла концовки: %s" % opt["outro"])
    if opt["music"] and not os.path.isfile(opt["music"]):
        sys.exit("Нет файла музыки: %s" % opt["music"])
    if opt["music"] and not opt["outro"]:
        sys.exit("Музыка задана, но нет концовки (--outro).")

    try:
        rc = make_clip(opt)
    except (ValueError, RuntimeError) as e:
        sys.exit(str(e))
    if rc == 0:
        print("Готово: %s" % opt["output"])
    else:
        print("ffmpeg завершился с ошибкой (код %d)" % rc)
    return rc


if __name__ == "__main__":
    sys.exit(main())
