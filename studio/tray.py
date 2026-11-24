"""Значок Студии в трее: фоновый режим, уведомления о начале эфира.

pystray живёт в своём потоке, а Tk трогать можно только из главного: нажатия
меню кладутся в очередь, окно забирает их само (pump).
"""
from __future__ import annotations

import queue

try:
    import pystray
    from PIL import Image, ImageDraw
except Exception:          # без pystray фонового режима нет, окно работает как раньше
    pystray = None


def available() -> bool:
    return pystray is not None


def picture(recording=False, size=64):
    from . import icons as _icons

    image = _icons.logo(size)
    draw = ImageDraw.Draw(image)
    if recording:
        dot = size * .34
        draw.ellipse((size - dot - 1, 1, size - 1, dot + 1), fill=(235, 50, 50, 255),
                     outline=(255, 255, 255, 255), width=2)
    return image


class Tray:
    """Меню: открыть окно, писать звук эфира, выход. Действия — в очередь."""

    def __init__(self, title="Astra Studio", recording=lambda: False, watching=lambda: True):
        self.box = queue.Queue()
        self.recording = recording
        self.watching = watching
        self.icon = None
        self.title = title
        self._shown_recording = None

    def start(self):
        if not available() or self.icon is not None:
            return False
        menu = pystray.Menu(
            pystray.MenuItem("Открыть Студию", lambda *_: self.box.put("show"), default=True),
            pystray.MenuItem("Следить за эфиром и писать звук",
                             lambda *_: self.box.put("watch"),
                             checked=lambda _item: bool(self.watching())),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Выход", lambda *_: self.box.put("quit")))
        self.icon = pystray.Icon("astra-studio", picture(), self.title, menu)
        self.icon.run_detached()
        return True

    def take(self) -> list:
        out = []
        while True:
            try:
                out.append(self.box.get_nowait())
            except queue.Empty:
                return out

    def status(self, text):
        if self.icon is None:
            return
        recording = bool(self.recording())
        if recording != self._shown_recording:
            self._shown_recording = recording
            self.icon.icon = picture(recording)
        self.icon.title = ("Astra Studio — " + text)[:127] if text else "Astra Studio"

    def notify(self, text, title="Astra Studio"):
        if self.icon is not None:
            try:
                self.icon.notify(text, title)
            except Exception:
                pass

    def stop(self):
        icon, self.icon = self.icon, None
        if icon is not None:
            try:
                icon.stop()
            except Exception:
                pass
