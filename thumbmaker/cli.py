"""CLI: python cli.py --video <ссылка на ролик> --stream <ссылка на стрим>"""

from __future__ import annotations

import argparse
import os
import sys

_ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
if os.path.isdir(os.path.join(_ROOT, "core")) and _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from thumbmaker.config import PLATES, Settings
from thumbmaker import pipeline


for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8")
    except Exception:
        pass


def parse_args(argv=None):
    p = argparse.ArgumentParser(
        description="Собирает превью: фон — превью ролика, справа — стример "
                    "(с фото или с яркой эмоцией, найденной в стриме).")
    p.add_argument("--video", required=True, help="ссылка на ролик (откуда берём превью)")
    src = p.add_mutually_exclusive_group(required=True)
    src.add_argument("--photo", help="фото стримера (откуда вырезаем лицо)")
    src.add_argument("--stream", help="ссылка на стрим (откуда режем лицо)")
    p.add_argument("--out", default=None, help="имя выходного файла в ./out")
    p.add_argument("--samples", type=int, default=48,
                   help="сколько кадров сканировать (только для --stream)")
    p.add_argument("--top", type=int, default=8, help="сколько кандидатов оставить")
    p.add_argument("--pick", type=int, default=1, help="какого кандидата использовать (1 = лучший)")
    p.add_argument("--side", choices=["right", "left"], default="right")
    p.add_argument("--caption", default="",
                   help="крупный текст внизу во всю ширину, капсом (переносится по "
                        "словам сам; \\n — принудительный перенос; *слово* — жёлтым)")
    p.add_argument("--badge", default="", help="бейдж в углу, например #2")
    p.add_argument("--res", default="1920x1080",
                   help="размер холста ШИРИНАxВЫСОТА, например 3840x2160")
    p.add_argument("--font", default="", help="свой .ttf для текста")
    p.add_argument("--plate", choices=sorted(set(PLATES.values())), default="band",
                   help="под текстом: band — затемнение снизу, box — плашка под строками, "
                        "parchment — пергамент, none — ничего")
    p.add_argument("--no-mirror", action="store_true", help="не зеркалить стримера")
    p.add_argument("--mirror-auto", action="store_true",
                   help="зеркалить, только если стример смотрит из кадра")
    p.add_argument("--layout", choices=["classic", "card"], default="classic",
                   help="classic — превью ролика во весь кадр; card — превью карточкой "
                        "слева сверху, за ней размытие, текст под ней")
    p.add_argument("--face-height", type=float, default=0.0,
                   help="крупный план: высота лица от высоты кадра (например 0.6); "
                        "0 — стример целиком по --scale")
    p.add_argument("--parchment-seed", type=int, default=7,
                   help="другой рисунок пятен и рваного края")
    p.add_argument("--scale", type=float, default=0.98, help="высота стримера от высоты кадра")
    p.add_argument("--offset-x", type=float, default=0.0)
    p.add_argument("--offset-y", type=float, default=0.02)
    p.add_argument("--outline", type=int, default=0, help="толщина белой обводки, 0 — без неё")
    p.add_argument("--no-shadow", action="store_true")
    p.add_argument("--min-face", type=float, default=0.05, help="мин. доля ширины кадра для лица")
    p.add_argument("--max-yaw", type=float, default=0.45, help="0 — строго в камеру, 1 — профиль")
    p.add_argument("--save-parts", action="store_true", help="сохранить ещё превью и все кандидаты")
    return p.parse_args(argv)


def parse_res(value: str) -> tuple:
    try:
        w, h = (int(v) for v in value.lower().replace("х", "x").split("x"))
    except ValueError:
        raise SystemExit(f"Не понял размер '{value}', нужен формат 1920x1080.")
    if w < 320 or h < 180:
        raise SystemExit("Слишком маленький холст, минимум 320x180.")
    return w, h


def main(argv=None) -> int:
    a = parse_args(argv)
    s = Settings()
    s.scan.coarse_samples = a.samples
    s.face.min_face_ratio = a.min_face
    s.face.max_yaw = a.max_yaw
    c = s.compose
    c.side, c.badge = a.side, a.badge
    c.caption = a.caption.replace("\\n", "\n")   # в консоли перенос пишут как \n
    c.face_scale, c.face_offset_x, c.face_offset_y = a.scale, a.offset_x, a.offset_y
    c.outline_px, c.shadow = a.outline, not a.no_shadow
    c.width, c.height = parse_res(a.res)
    c.font_path = a.font
    c.plate, c.parchment_seed, c.mirror = a.plate, a.parchment_seed, not a.no_mirror
    if a.mirror_auto:
        c.mirror = "auto"
    c.layout, c.face_height = a.layout, a.face_height

    def progress(stage, done=0, total=0):
        bar = f" {done}/{total}" if total else ""
        print(f"\r[{stage}{bar}]".ljust(70), end="", file=sys.stderr, flush=True)

    res = pipeline.build(a.video, a.stream, s, top_k=a.top, photo=a.photo, progress=progress)
    print(file=sys.stderr)

    if a.photo:
        hit = res.candidates[0].hit
        print(f"Лицо с фото: {hit.label} (score={hit.score:.1f})", file=sys.stderr)
    else:
        print("Кандидаты (по яркости эмоции):", file=sys.stderr)
        for i, cand in enumerate(res.candidates, 1):
            print(f"  {i:>2}. {cand.timecode():>8}  score={cand.hit.score:5.1f}  {cand.hit.label}",
                  file=sys.stderr)

    idx = min(max(a.pick, 1), len(res.candidates)) - 1
    image = res.thumbnail if idx == 0 else pipeline.render(res.preview, res.candidates[idx].hit, s)

    path = pipeline.save(image, a.out)
    print(path)

    if a.save_parts:
        pipeline.save(res.preview, (a.out or "thumb").rsplit(".", 1)[0] + "_preview.jpg")
        if not a.photo:
            for i, cand in enumerate(res.candidates, 1):
                pipeline.save(cand.thumb, f"candidate_{i:02d}_{int(cand.hit.score)}.jpg")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
