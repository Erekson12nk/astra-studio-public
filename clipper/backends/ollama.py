"""Локальный движок через Ollama. Бесплатно, без интернета, без ключей.

Установка:
    winget install Ollama.Ollama
    ollama pull qwen2.5:7b-instruct

Общение идёт по HTTP через стандартную библиотеку — никаких дополнительных
пакетов ставить не нужно.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request

from ..config import Config
from .base import SCHEMA, LLMBackend


class OllamaBackend(LLMBackend):
    name = "ollama"
    free = True
    # У локальных моделей контекст меньше и внимание рассеивается быстрее,
    # поэтому окно вдвое короче, чем у облачной модели.
    default_chunk_minutes = 4.0

    def __init__(self, host: str, model: str, num_ctx: int, timeout: float):
        self.host = host.rstrip("/")
        self.model = model
        self.num_ctx = num_ctx
        self.timeout = timeout

    def describe(self) -> str:
        return f"ollama {self.model} @ {self.host} (num_ctx={self.num_ctx})"

    def check(self) -> None:
        """Проверяет, что сервер жив и модель скачана."""
        try:
            with urllib.request.urlopen(f"{self.host}/api/tags", timeout=10) as r:
                tags = json.loads(r.read().decode("utf-8"))
        except urllib.error.URLError as exc:
            raise RuntimeError(
                f"Ollama не отвечает на {self.host}.\n"
                "  Установить:  winget install Ollama.Ollama\n"
                "  Запустить:   ollama serve"
            ) from exc

        names = [m.get("name", "") for m in tags.get("models", [])]
        if not names:
            raise RuntimeError(
                f"В Ollama нет ни одной модели. Скачайте:\n"
                f"  ollama pull {self.model}"
            )
        # Ollama различает "qwen2.5:7b" и "qwen2.5:7b-instruct" — сверяем по префиксу.
        base = self.model.split(":")[0]
        if not any(n == self.model or n.split(":")[0] == base for n in names):
            raise RuntimeError(
                f"Модель {self.model} не найдена. Есть: {', '.join(names)}\n"
                f"Скачать:  ollama pull {self.model}"
            )
        # Если модель уже в памяти, низкий остаток — это норма, а не проблема.
        if not self._already_loaded():
            self._warn_low_vram()

    def _already_loaded(self) -> bool:
        try:
            with urllib.request.urlopen(f"{self.host}/api/ps", timeout=5) as r:
                running = json.loads(r.read().decode("utf-8")).get("models", [])
        except (urllib.error.URLError, ValueError, TimeoutError):
            return False
        base = self.model.split(":")[0]
        return any(
            m.get("name") == self.model or m.get("name", "").split(":")[0] == base
            for m in running
        )

    @staticmethod
    def _warn_low_vram(need_mib: int = 6500) -> None:
        """Мягкое предупреждение о нехватке видеопамяти.

        Модель на 8B с контекстом 8192 занимает ~6.2 ГБ. Если свободного
        меньше, Ollama не падает, а тихо переносит часть слоёв на процессор —
        и скорость обваливается в десятки раз без единого сообщения об ошибке.
        Лучше сказать заранее, чем оставить человека гадать.
        """
        import shutil
        import subprocess

        exe = shutil.which("nvidia-smi")
        if not exe:
            return
        try:
            out = subprocess.run(
                [exe, "--query-gpu=memory.free", "--format=csv,noheader,nounits"],
                capture_output=True, text=True, timeout=10,
            )
            free = int(out.stdout.strip().splitlines()[0])
        except (OSError, ValueError, IndexError, subprocess.SubprocessError):
            return

        if free < need_mib:
            print(f"  ! свободно всего {free} МиБ видеопамяти (нужно ~{need_mib})")
            print("    Ollama может увести часть слоёв на процессор — это в десятки")
            print("    раз медленнее. Закройте браузер и игры, либо возьмите модель")
            print("    поменьше:  --local-model qwen3:4b")

    def _post(self, payload: dict) -> dict:
        req = urllib.request.Request(
            f"{self.host}/api/chat",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=self.timeout) as r:
            return json.loads(r.read().decode("utf-8"))

    def complete(self, system: str, user: str, cfg: Config, schema=None) -> str:
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "stream": False,
            # Гибридные reasoning-модели (Qwen3) по умолчанию долго рассуждают
            # вслух. Для извлечения структурированного ответа это чистая трата
            # времени на локальном железе — отключаем.
            "think": False,
            # Каждое окно расшифровки — отдельный запрос. По умолчанию Ollama
            # выгружает модель из видеопамяти через 5 минут простоя, и на
            # длинном видео это добавляет перезагрузку на ровном месте.
            "keep_alive": "15m",
            # Схема поддерживается свежими версиями Ollama; на старых параметр
            # игнорируется, и ответ разбирается терпимым парсером.
            "format": schema or SCHEMA,
            "options": {
                # Ключевой момент: без явного num_ctx Ollama берёт маленький контекст
                # по умолчанию и молча обрезает расшифровку.
                "num_ctx": self.num_ctx,
                "temperature": 0.3,
            },
        }
        try:
            data = self._post(payload)
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", "replace")[:300]
            # Модели без поддержки рассуждения ругаются на поле "think" —
            # повторяем без него.
            if exc.code == 400 and "think" in body.lower():
                payload.pop("think", None)
                data = self._post(payload)
            # Старые сборки Ollama не понимают схему в "format".
            elif exc.code == 400 and "format" in body.lower():
                payload["format"] = "json"
                data = self._post(payload)
            else:
                raise RuntimeError(f"Ollama вернула ошибку {exc.code}: {body}") from exc
        except urllib.error.URLError as exc:
            raise RuntimeError(f"Ollama недоступна: {exc}") from exc
        except TimeoutError as exc:
            raise RuntimeError(
                f"Ollama не ответила за {self.timeout:.0f} сек. Возможно, модель "
                "не влезла в видеопамять и считается на процессоре — возьмите "
                "модель поменьше."
            ) from exc

        return data.get("message", {}).get("content", "")
