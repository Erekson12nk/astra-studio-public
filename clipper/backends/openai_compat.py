from __future__ import annotations

import json
import urllib.error
import urllib.request

from ..config import Config
from .base import LLMBackend

FORMATS = ("json_schema", "json_object", None)
THINK_WORDS = ("chat_template_kwargs", "enable_thinking")
EFFORT_WORDS = ("reasoning_effort",)


class OpenAICompatBackend(LLMBackend):
    name = "openai-compat"
    free = True
    default_chunk_minutes = 4.0

    def __init__(self, base_url: str, model: str, timeout: float, api_key: str = "",
                 max_tokens: int = 8192, think: bool = False):
        self.base_url = _root(base_url)
        self.model = model.strip()
        self.timeout = timeout
        self.api_key = api_key.strip() or "local"
        self.max_tokens = max_tokens
        self.think = think
        self.format_from = 0
        self.send_think = not think
        self.send_effort = not think

    def describe(self) -> str:
        return f"openai-compat {self.model or '?'} @ {self.base_url}"

    def _headers(self) -> dict:
        if not self.api_key.isascii() or any(c.isspace() for c in self.api_key):
            raise RuntimeError("В ключе API лишние символы — скопируйте ключ заново, "
                               "без пробелов и русских букв")
        return {"Content-Type": "application/json",
                "Authorization": f"Bearer {self.api_key}"}

    def models(self) -> list[str]:
        req = urllib.request.Request(f"{self.base_url}/v1/models", headers=self._headers())
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                data = json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            if exc.code in (401, 403):
                raise RuntimeError(f"Сервер {self.base_url} не принял ключ API ({exc.code})") from exc
            raise RuntimeError(f"Сервер {self.base_url} вернул {exc.code} на список моделей") from exc
        except (urllib.error.URLError, OSError) as exc:
            raise RuntimeError(
                f"Сервер не отвечает на {self.base_url}.\n"
                "  LM Studio: вкладка Developer → Start Server\n"
                "  llama.cpp: llama-server -m модель.gguf -c 8192 --port 8080"
            ) from exc
        except ValueError as exc:
            raise RuntimeError(f"Сервер {self.base_url} ответил не JSON на список моделей") from exc
        rows = data.get("data", []) if isinstance(data, dict) else []
        return [str(m.get("id", "")) for m in rows if isinstance(m, dict) and m.get("id")]

    def check(self) -> None:
        names = self.models()
        if not self.model:
            if not names:
                raise RuntimeError("Модель не выбрана, а сервер не назвал ни одной — загрузите модель")
            self.model = names[0]
            print(f"  модель не указана, беру первую с сервера: {self.model}")
        elif names and self.model not in names:
            print(f"  ! модель '{self.model}' не в списке сервера: {', '.join(names[:5])}")
            print(f"    продолжаю — многие серверы игнорируют это поле")

    def _payload(self, system: str, user: str, schema, fmt) -> dict:
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": 0.3,
            "max_tokens": self.max_tokens,
        }
        if fmt == "json_schema" and schema:
            name = next(iter(schema.get("properties") or {}), "answer")
            payload["response_format"] = {
                "type": "json_schema",
                "json_schema": {"name": name, "strict": True, "schema": schema},
            }
        elif fmt == "json_object":
            payload["response_format"] = {"type": "json_object"}
        if self.send_think:
            payload["chat_template_kwargs"] = {"enable_thinking": False}
        if self.send_effort:
            payload["reasoning_effort"] = "none"
        return payload

    def _post(self, payload: dict) -> dict:
        req = urllib.request.Request(
            f"{self.base_url}/v1/chat/completions",
            data=json.dumps(payload).encode("utf-8"),
            headers=self._headers(),
        )
        with urllib.request.urlopen(req, timeout=self.timeout) as r:
            return json.loads(r.read().decode("utf-8"))

    def complete(self, system: str, user: str, cfg: Config, schema=None) -> str:
        start = self.format_from if schema else max(1, self.format_from)
        level = start
        while True:
            payload = self._payload(system, user, schema, FORMATS[level])
            try:
                data = self._post(payload)
                break
            except urllib.error.HTTPError as exc:
                body = exc.read().decode("utf-8", "replace")[:300]
                if exc.code not in (400, 422):
                    raise RuntimeError(f"Сервер вернул {exc.code}: {body}") from exc
                if self.send_effort and any(w in body for w in EFFORT_WORDS):
                    self.send_effort = False
                    continue
                if self.send_think and any(w in body for w in THINK_WORDS):
                    self.send_think = False
                    continue
                if level + 1 < len(FORMATS):
                    level += 1
                    continue
                if self.send_effort or self.send_think:
                    self.send_effort = self.send_think = False
                    continue
                raise RuntimeError(f"Сервер вернул {exc.code}: {body}") from exc
            except (urllib.error.URLError, OSError) as exc:
                raise RuntimeError(f"Сервер недоступен: {exc}") from exc
            except ValueError as exc:
                raise RuntimeError(f"Сервер ответил не JSON: {exc}") from exc
        if level > start:
            self.format_from = level
        return _content(data)


def _root(url: str) -> str:
    url = (url or "").strip().rstrip("/") or "http://localhost:1234"
    if "://" not in url:
        url = "http://" + url
    if url.endswith("/v1"):
        url = url[:-3]
    return url


def _content(data) -> str:
    choices = data.get("choices") if isinstance(data, dict) else None
    if not choices:
        return ""
    message = choices[0].get("message") or {}
    content = message.get("content")
    if isinstance(content, list):
        content = "".join(part.get("text", "") for part in content if isinstance(part, dict))
    if not content:
        content = choices[0].get("text") or ""
    return str(content)
