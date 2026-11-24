"""Запуск Студии: режимы, прогноз по стриму, учёба на статистике, один экземпляр.

Фоновый режим — Студия в трее: следит за эфиром, сообщает о начале и пишет звук.
Рабочий режим — окно: открыть или скачать стрим, найти моменты и узнать, сколько
примерно из него выйдет тиктоков и больших роликов.
"""
from __future__ import annotations

import socket
import threading
import time

PASSIVE = "passive"
ACTIVE = "active"

# ---------------------------------------------------------------- прогноз

ROLIK_GAP = 300.0      # хорошие моменты ближе пяти минут друг к другу — один кусок истории
ROLIK_LEAST = 3        # в ролике хотя бы три хороших момента
ROLIK_SPAN = 360.0     # и он идёт хотя бы шесть минут стрима
ROLIK_LONG = 1200.0    # длинный плотный кусок делится на ролики примерно по двадцать минут


def _spans(moments):
    """Моменты по порядку, перекрытия склеены: один кусок стрима — один клип."""
    spans = []
    for start, end in sorted((float(m["start"]), float(m["end"])) for m in moments):
        if spans and start < spans[-1][1] - 1.0:
            spans[-1][1] = max(spans[-1][1], end)
        else:
            spans.append([start, end])
    return spans


def estimate(moments, least_score=5.0) -> dict:
    """Сколько примерно выйдет: тиктоков — хороших моментов, роликов — плотных кусков.

    Ролик — это кусок стрима, где хорошие моменты идут один за другим: если между
    ними не больше ROLIK_GAP, из них складывается связная история.
    """
    good = [m for m in moments if float(m.get("score") or 0) >= least_score]
    spans = _spans(good)
    roliks = 0
    group = []
    for span in spans + [None]:
        if span is not None and group and span[0] - group[-1][1] <= ROLIK_GAP:
            group.append(span)
            continue
        if group:
            length = group[-1][1] - group[0][0]
            if len(group) >= ROLIK_LEAST and length >= ROLIK_SPAN:
                roliks += max(1, int(length // ROLIK_LONG))
        group = [span] if span is not None else []
    return {"tiktoks": len(spans), "roliks": roliks, "moments": len(moments)}


def plural(number, one, few, many) -> str:
    tail = number % 100
    if 11 <= tail <= 14:
        word = many
    elif tail % 10 == 1:
        word = one
    elif 2 <= tail % 10 <= 4:
        word = few
    else:
        word = many
    return "%d %s" % (number, word)


def estimate_text(found) -> str:
    return "Из этого стрима примерно: %s и %s" % (
        plural(found["tiktoks"], "тикток", "тиктока", "тиктоков"),
        plural(found["roliks"], "большой ролик", "больших ролика", "больших роликов"))


# ---------------------------------------------------------------- учёба

LEARN_EVERY = 24 * 3600.0


def learn_due(cfg, now=None, every=LEARN_EVERY) -> bool:
    if not cfg.get("learn_auto", True):
        return False
    now = time.time() if now is None else now
    return now - float(cfg.get("learned_at") or 0) >= every


def learn_sources(cfg) -> tuple:
    from . import taste as _taste

    kept = tuple(source for source in cfg.get("learn_from") or ("own",)
                 if source in _taste.SOURCES)
    return kept or ("own",)


def learn(profile, cfg, db, engine=None, log=None, cancel=None, builder=None,
          now=None) -> dict:
    """Пересобрать правила вкуса по просмотрам своих выгрузок.

    Без языковой модели правила подождут движок.
    """
    from . import taste as _taste

    log = log or (lambda text: None)
    sources = learn_sources(cfg)
    report = {"channels": 0, "failed": 0, "rules": None, "skipped": ""}
    if engine is None:
        report["skipped"] = "нет движка с языковой моделью"
        return report
    backend, clipper_cfg = engine[0], engine[1]
    try:
        report["rules"] = (builder or _taste.build)(
            profile, db, backend, clipper_cfg, log=log, sources=sources, now=now)
    except RuntimeError as error:
        report["skipped"] = str(error)
    return report


# ---------------------------------------------------------------- один экземпляр

PORT = 47613
HELLO = b"astra-studio show\n"


class Single:
    """Студия запущена один раз: второй запуск только будит первую.

    Две Студии со сторожем писали бы один эфир дважды.
    """

    def __init__(self, port=PORT):
        self.port = port
        self.server = None
        self.on_show = None

    def claim(self) -> bool:
        server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            server.bind(("127.0.0.1", self.port))
        except OSError:
            server.close()
            return False
        server.listen(2)
        self.server = server
        threading.Thread(target=self._serve, daemon=True).start()
        return True

    def _serve(self):
        while self.server is not None:
            try:
                peer, _address = self.server.accept()
            except OSError:
                return
            try:
                peer.settimeout(2)
                if peer.recv(64).startswith(HELLO.strip()) and self.on_show:
                    self.on_show()
            except OSError:
                pass
            finally:
                peer.close()

    def wake_other(self) -> bool:
        try:
            with socket.create_connection(("127.0.0.1", self.port), timeout=2) as peer:
                peer.sendall(HELLO)
            return True
        except OSError:
            return False

    def release(self):
        server, self.server = self.server, None
        if server is not None:
            server.close()
