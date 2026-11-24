import os
import subprocess
import sys
import threading

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)

from core import ffmpeg as _ff

_ff.use_bundled_tools(ROOT)

from core import service as _svc


def open_studio():
    subprocess.Popen([_svc.pythonw(), os.path.join(ROOT, "studio", "app.pyw")], cwd=ROOT,
                     **_ff._NOWIN)


def main():
    lock = _svc.instance_lock()
    if not lock.take():
        return 1
    from studio import tray as _tray

    if not _tray.available():
        _svc.write_log("нет pystray — служба работает без значка в трее")
        return run_bare()
    import pystray

    icon = None

    def notify(text):
        _svc.write_log(text)
        if icon is not None:
            try:
                icon.notify(str(text)[:250], "Astra Studio")
            except Exception:
                pass

    service = _svc.Service(notify=notify)
    done = threading.Event()

    def lines():
        return service.status()[:8]

    def line_item(number):
        return pystray.MenuItem(lambda _item: (lines()[number] if number < len(lines())
                                               else "")[:120],
                                None, enabled=False,
                                visible=lambda _item: number < len(lines()))

    def quit_service(*_):
        service.stop()
        done.set()
        icon.stop()

    menu = pystray.Menu(
        pystray.MenuItem("Открыть Студию", lambda *_: open_studio(), default=True),
        pystray.Menu.SEPARATOR,
        *[line_item(number) for number in range(8)],
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Остановить службу", quit_service))
    icon = pystray.Icon("astra-service", _tray.picture(), "Astra Studio — служба", menu)

    def pump():
        shown = None
        while not done.wait(2.0):
            recording = service.recording()
            if recording != shown:
                shown = recording
                icon.icon = _tray.picture(recording)
            text = "; ".join(service.status())
            icon.title = ("Astra — " + text)[:127]
            try:
                icon.update_menu()
            except Exception:
                pass

    service.start()
    threading.Thread(target=pump, daemon=True).start()
    icon.run()
    service.stop()
    return 0


def run_bare():
    service = _svc.Service()
    service.start()
    threading.Event().wait()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
