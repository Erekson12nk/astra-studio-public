"""Консольный режим.

    python -m audioclean.cli стрим.mp4
    python -m audioclean.cli стрим.mp4 -o чисто.mp4 --beep --model medium
    python -m audioclean.cli стрим.mp4 --dry-run --report найдено.txt
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from core import asr as _asr

from . import ffmpeg as FF
from . import pipeline as P
from .config import Settings


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="audioclean",
        description="Глушит мат и запрещённые площадками слова в звуке видео. "
                    "Видео не перекодируется.",
        formatter_class=argparse.RawDescriptionHelpFormatter, epilog=__doc__)
    p.add_argument("video", help="видео или аудиофайл")
    p.add_argument("-o", "--output", default="", help="итоговый файл (по умолчанию *_clean)")

    g = p.add_argument_group("что глушить")
    g.add_argument("--no-ru", action="store_true", help="не трогать русский мат")
    g.add_argument("--no-en", action="store_true", help="не трогать английскую брань")
    g.add_argument("--no-platform", action="store_true",
                   help="не трогать список TikTok/Shorts")
    g.add_argument("--word", action="append", default=[],
                   help="добавить своё слово (можно несколько раз)")

    g = p.add_argument_group("как глушить")
    g.add_argument("--beep", action="store_true", help="писк вместо тишины")
    g.add_argument("--hz", type=int, default=1000, help="частота писка")
    g.add_argument("--pad", type=float, default=0.12,
                   help="запас вокруг слова, сек (когда слово глушится целиком)")
    g.add_argument("--keep", type=float, default=0.07, metavar="СЕК",
                   help="слышные начало и конец слова, сек; 0 — глушить слово целиком")

    g = p.add_argument_group("распознавание")
    g.add_argument("--model", default=_asr.DEFAULT_MODEL,
                   help="|".join(_asr.MODELS))
    g.add_argument("--lang", default="ru", help="язык речи, пусто = определить самой")
    g.add_argument("--device", default="auto", choices=["auto", "cpu", "cuda"])
    g.add_argument("--no-cache", action="store_true", help="не брать сохранённый текст")

    g = p.add_argument_group("прочее")
    g.add_argument("--dry-run", action="store_true", help="только найти слова, файл не писать")
    g.add_argument("--report", default="", help="сохранить список найденного в файл")
    return p


def main(argv=None) -> int:
    a = build_parser().parse_args(argv)
    if not FF.have_ffmpeg():
        print("Не найден ffmpeg. Поставьте его и добавьте в PATH.", file=sys.stderr)
        return 3
    if not Path(a.video).exists():
        print("Файл не найден: %s" % a.video, file=sys.stderr)
        return 3

    s = Settings(
        video=str(Path(a.video).resolve()), output=a.output,
        model=a.model, language=a.lang, device=a.device,
        use_ru=not a.no_ru, use_en=not a.no_en, use_platform=not a.no_platform,
        extra_words=list(a.word),
        mode="beep" if a.beep else "silence", beep_hz=a.hz,
        pad_before=a.pad, pad_after=a.pad + 0.03,
        keep_edges=a.keep > 0, keep_head=max(0.0, a.keep), keep_tail=max(0.0, a.keep))

    last = [-1]

    def prog(p: float, note: str) -> None:
        pct = int(p * 100)
        if pct != last[0]:
            last[0] = pct
            print("\r%3d%%  %-40s" % (pct, note[:40]), end="", flush=True)
            if pct >= 100:
                print()

    def log(m: str) -> None:
        print(m, flush=True)

    res = P.analyze(s, progress=prog, log=log, use_cache=not a.no_cache)

    print()
    for f in res.found:
        print("%s  %-18s %-14s %-5s %s"
              % (P.hms(f.start), f.text[:18], "(" + f.rule + ")",
                 "~" if f.prob <= 0 else "%.2f" % f.prob, f.context[:64]))
    spans = P.spans_of(res.found, s, res.duration)
    print("\nВсего %d слов, %d участков, %.1f секунд звука будет заглушено."
          % (len(res.found), len(spans), sum(b - a for a, b in spans)))

    if a.report:
        Path(a.report).write_text(P.report(res, s), encoding="utf-8")
        log("Отчёт: " + a.report)

    if a.dry_run:
        log("Пробный прогон: файл не писался.")
        return 0

    out = P.clean(s, res, s.output, progress=prog, log=log)
    log("Готово: " + out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
