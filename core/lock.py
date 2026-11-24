"""Замок между процессами: служба, окно Студии и часовая задача не режут стримы разом.

Замок — открытый файл с заблокированным первым байтом. Процесс умер — Windows
снимает блокировку сама, протухших замков не бывает.
"""

from __future__ import annotations

import os
import time

try:
    import msvcrt
except ImportError:            # не Windows
    msvcrt = None
    import fcntl


class Lock:
    def __init__(self, path):
        self.path = str(path)
        self.handle = None

    def take(self) -> bool:
        if self.handle is not None:
            return True
        os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
        handle = open(self.path, "a+b")
        try:
            if msvcrt:
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            handle.close()
            return False
        self.handle = handle
        return True

    def wait(self, cancel=None, every=5.0, sleep=time.sleep) -> bool:
        while not self.take():
            if cancel and cancel():
                return False
            sleep(every)
        return True

    def free(self):
        handle, self.handle = self.handle, None
        if handle is None:
            return
        try:
            if msvcrt:
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        except OSError:
            pass
        handle.close()

    def busy(self) -> bool:
        if self.handle is not None:
            return True
        if self.take():
            self.free()
            return False
        return True

    def __enter__(self):
        self.take()
        return self

    def __exit__(self, *_exc):
        self.free()
