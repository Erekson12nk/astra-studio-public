from __future__ import annotations

import queue
import threading
import time
import traceback


class Runner:
    def __init__(self, widget, on_done=None):
        self.widget = widget
        self.on_done = on_done
        self.queue = queue.Queue()
        self.thread = None
        self.cancelled = False
        self.listeners = []
        self.started = 0.0

    def busy(self):
        return self.thread is not None and self.thread.is_alive()

    def cancel(self):
        self.cancelled = True

    def start(self, work, done=None):
        if self.busy():
            return False
        self.cancelled = False
        self.started = time.time()

        def wrapper():
            try:
                result = work(self)
                self.queue.put(("done", result))
            except Exception as error:
                self.queue.put(("error", "%s: %s" % (type(error).__name__, error)))
                self.queue.put(("trace", traceback.format_exc()))

        self.finish = done
        self.thread = threading.Thread(target=wrapper, daemon=True)
        self.thread.start()
        self.widget.after(60, self._pump)
        return True

    def log(self, text):
        self.queue.put(("log", str(text)))

    def progress(self, value, note=""):
        self.queue.put(("progress", (value, note)))

    def stage(self, text):
        self.queue.put(("stage", str(text)))

    def listen(self, callback):
        self.listeners.append(callback)

    def _emit(self, kind, payload):
        for callback in self.listeners:
            callback(kind, payload)

    def _pump(self):
        alive = self.busy()
        while True:
            try:
                kind, payload = self.queue.get_nowait()
            except queue.Empty:
                break
            if kind == "trace":
                continue
            self._emit(kind, payload)
            if kind in ("done", "error"):
                if getattr(self, "finish", None):
                    self.finish(kind, payload)
                if self.on_done:
                    self.on_done(kind, payload)
        if alive:
            self.widget.after(60, self._pump)
