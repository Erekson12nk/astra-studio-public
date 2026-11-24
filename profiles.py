import argparse
import os
import sys
import time

if sys.platform == "win32":
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass

_ROOT = os.path.dirname(os.path.abspath(__file__))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from core import profiles as _pro


def check_face(photo):
    sys.path.insert(0, os.path.join(_ROOT, "astra"))
    try:
        import auto_vertical_editor as ave
        from core import faceid as _faceid
    except ImportError as error:
        print("  не могу проверить фотографию (%s) — принимаю как есть" % error)
        return None
    if not _faceid.available():
        print("  нет onnxruntime, лицо со снимка не снято:"
              "  py -m pip install onnxruntime")
        return None
    backend = ave.FaceBackend(None)
    try:
        embedder = _faceid.Embedder(report=lambda message: print("  " + message))
        vector = ave.face_reference(photo, backend, embedder)
    finally:
        backend.close()
    return vector


def show(profile, marker=""):
    keep = profile.get("keep_days", _pro.DEFAULT_KEEP_DAYS)
    print("%s%s" % (marker, profile["name"]))
    print("   фото       %s" % (_pro.face_path(profile) or "НЕТ"))
    print("   удалять    %s" % ("через %d дн." % keep if keep > 0 else "никогда"))
    print("   расшифровка %s (%s)" % (profile.get("whisper"), profile.get("language")))
    print("   раскладка  %s" % profile.get("layout"))
    print("   ролики     %s" % (profile.get("out_dir") or "по умолчанию"))
    print("   папка      %s" % profile["folder"])


def cmd_list(args):
    found = _pro.names()
    if not found:
        print("Профилей пока нет.  py profiles.py new <имя> --photo фото.png")
        return 1
    current = _pro.active_name()
    for name in found:
        show(_pro.load(name), "* " if name == current else "  ")
    return 0


def cmd_new(args):
    vector = check_face(args.photo)
    if vector is None and not args.force:
        print("Лицо со снимка снять не удалось. Возьмите другой кадр "
              "или повторите с --force.")
        return 1
    profile = _pro.create(args.name, args.photo, keep_days=args.keep_days,
                          whisper=args.whisper, language=args.language,
                          out_dir=args.out, layout=args.layout)
    _pro.set_active(profile["name"])
    print("Профиль создан и выбран:")
    show(profile, "* ")
    return 0


def cmd_use(args):
    print("Выбран профиль %s" % _pro.set_active(args.name))
    return 0


def cmd_show(args):
    profile = _pro.active()
    if profile is None:
        print("Активного профиля нет.  py profiles.py use <имя>")
        return 1
    show(profile, "* ")
    return 0


def cmd_set(args):
    profile = _pro.active() if not args.name else _pro.load(args.name)
    if profile is None:
        print("Активного профиля нет.")
        return 1
    if args.keep_days is not None:
        profile["keep_days"] = args.keep_days
    if args.whisper:
        profile["whisper"] = args.whisper
    if args.language:
        profile["language"] = args.language
    if args.out:
        profile["out_dir"] = args.out
    if args.layout:
        profile["layout"] = args.layout
    if args.photo:
        if check_face(args.photo) is None and not args.force:
            print("Лицо со снимка снять не удалось.")
            return 1
        import shutil
        from pathlib import Path
        source = Path(args.photo).expanduser()
        target = Path(profile["folder"]) / (_pro.FACE_STEM + source.suffix.lower())
        shutil.copy2(str(source), str(target))
        profile["face"] = target.name
    _pro.save(profile)
    show(profile, "* ")
    return 0


def cmd_remove(args):
    _pro.remove(args.name)
    print("Профиль %s удалён" % args.name)
    return 0


def cmd_clean(args):
    profile = _pro.active() if not args.name else _pro.load(args.name)
    if profile is None:
        print("Активного профиля нет.")
        return 1
    place = args.folder or profile.get("out_dir")
    if not place:
        print("Не задана папка с роликами: profiles.py set --out <папка>")
        return 1
    keep = profile.get("keep_days", _pro.DEFAULT_KEEP_DAYS)
    guard = _pro.protected(os.path.join(
        os.environ.get("CLIPPER_WORK", r"C:\clipper-work"), "clipper.sqlite3"))
    stale = _pro.expired(place, keep, protect=guard)
    if not stale:
        print("Старше %d дн. в %s ничего нет." % (keep, place))
        return 0
    print("Старше %d дн. — %d файлов:" % (keep, len(stale)))
    for path in stale:
        age = (time.time() - path.stat().st_mtime) / 86400.0
        print("   %-58s %.1f дн." % (path.name[:58], age))
    if args.dry_run:
        print("\nЭто список, ничего не удалено. Без --dry-run файлы будут удалены.")
        return 0
    removed = _pro.sweep(place, keep, protect=guard)
    print("\nУдалено файлов: %d. Записи в базе остаются." % len(removed))
    return 0


def build_parser():
    parser = argparse.ArgumentParser(prog="profiles", description="Профили стримеров")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("list", help="показать профили")
    p.set_defaults(fn=cmd_list)

    p = sub.add_parser("new", help="создать профиль")
    p.add_argument("name")
    p.add_argument("--photo", required=True, help="фотография стримера, обязательна")
    p.add_argument("--keep-days", type=int, default=_pro.DEFAULT_KEEP_DAYS)
    p.add_argument("--whisper", default=_pro.DEFAULT_WHISPER)
    p.add_argument("--language", default="ru")
    p.add_argument("--out", default=None, help="папка с готовыми роликами")
    p.add_argument("--layout", default="auto",
                   choices=["auto", "split", "cam", "screen"])
    p.add_argument("--force", action="store_true", help="принять снимок без лица")
    p.set_defaults(fn=cmd_new)

    p = sub.add_parser("use", help="сделать профиль активным")
    p.add_argument("name")
    p.set_defaults(fn=cmd_use)

    p = sub.add_parser("show", help="активный профиль")
    p.set_defaults(fn=cmd_show)

    p = sub.add_parser("set", help="поменять настройки профиля")
    p.add_argument("--name", default=None)
    p.add_argument("--keep-days", type=int, default=None)
    p.add_argument("--whisper", default=None)
    p.add_argument("--language", default=None)
    p.add_argument("--out", default=None)
    p.add_argument("--layout", default=None,
                   choices=["auto", "split", "cam", "screen"])
    p.add_argument("--photo", default=None)
    p.add_argument("--force", action="store_true")
    p.set_defaults(fn=cmd_set)

    p = sub.add_parser("remove", help="удалить профиль")
    p.add_argument("name")
    p.set_defaults(fn=cmd_remove)

    p = sub.add_parser("clean", help="удалить ролики старше срока")
    p.add_argument("--name", default=None)
    p.add_argument("--folder", default=None)
    p.add_argument("--dry-run", action="store_true")
    p.set_defaults(fn=cmd_clean)
    return parser


def main():
    args = build_parser().parse_args()
    return args.fn(args)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as error:
        sys.stderr.write("Ошибка: %s\n" % error)
        sys.exit(1)
