# -*- coding: utf-8 -*-
import os as _os
import sys as _sys

_HERE = _os.path.dirname(_os.path.abspath(__file__))
_TESTS = _HERE if _os.path.basename(_HERE) != "snapshot" else _os.path.dirname(_HERE)
_REPO = _os.path.dirname(_TESTS)
_sys.path.insert(0, _TESTS)
_sys.path.insert(0, _REPO)

import json
import os
import sys
import tempfile
import threading

os.environ["ASTRA_STUDIO_SETTINGS"] = os.path.join(
    tempfile.mkdtemp(prefix="openai_cfg_"), "settings.json")
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from clipper.backends import get_backend
from clipper.backends.base import SCHEMA
from clipper.backends.openai_compat import OpenAICompatBackend
from clipper.config import Config

ok = []


def check(label, value, extra=""):
    ok.append(bool(value))
    print("  %-58s %s%s" % (label, "ок" if value else "ПРОВАЛ",
                            ("  " + str(extra)) if extra else ""))


ANSWER = {"clips": [{"start_sentence": 1, "end_sentence": 4, "title": "Спор",
                     "hook": "А вот и нет", "reason": "поворот", "score": 8.5}]}


class Fake:
    def __init__(self):
        self.refuse = set()
        self.refuse_think = False
        self.refuse_effort = False
        self.think_text = False
        self.requests = []
        self.auth = []
        self.models = ["qwen/qwen3-8b", "google/gemma-3-12b"]

    def reset(self):
        self.__init__()


FAKE = Fake()


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_args):
        pass

    def _send(self, code, data):
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        FAKE.auth.append(self.headers.get("Authorization"))
        if self.path != "/v1/models":
            return self._send(404, {"error": "нет"})
        self._send(200, {"object": "list",
                         "data": [{"id": n, "object": "model"} for n in FAKE.models]})

    def do_POST(self):
        FAKE.auth.append(self.headers.get("Authorization"))
        size = int(self.headers.get("Content-Length") or 0)
        payload = json.loads(self.rfile.read(size).decode("utf-8"))
        FAKE.requests.append(payload)
        kind = (payload.get("response_format") or {}).get("type")
        if kind in FAKE.refuse:
            return self._send(400, {"error": "'response_format.type' must be one of "
                                             "'json_schema' or 'text'"})
        if FAKE.refuse_effort and "reasoning_effort" in payload:
            return self._send(400, {"error": "Unrecognized request argument supplied: "
                                             "reasoning_effort"})
        if FAKE.refuse_think and "chat_template_kwargs" in payload:
            return self._send(400, {"error": "Unrecognized request argument supplied: "
                                             "chat_template_kwargs"})
        text = json.dumps(ANSWER, ensure_ascii=False)
        if FAKE.think_text:
            text = "<think>сначала подумаю {\"clips\": []}</think>\n```json\n" + text + "\n```"
        self._send(200, {"choices": [{"index": 0, "message": {"role": "assistant",
                                                              "content": text}}]})


server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
threading.Thread(target=server.serve_forever, daemon=True).start()
URL = "http://127.0.0.1:%d" % server.server_address[1]

cfg = Config()
cfg.min_clip_s, cfg.max_clip_s = 20.0, 90.0
META = dict(chunk_no=1, chunk_total=1, lo=0, hi=10, sec_per_sentence=5.0, limit=3)


def backend(**kw):
    kw.setdefault("model", "qwen/qwen3-8b")
    return OpenAICompatBackend(URL + "/v1/", timeout=10, **kw)


print("\n--- json_schema принят сразу ---")
FAKE.reset()
b = backend()
clips = b.propose_chunk("[1] привет", META, cfg)
first = FAKE.requests[0]
fmt = first.get("response_format") or {}
check("один запрос", len(FAKE.requests) == 1, len(FAKE.requests))
check("адрес с /v1/ на конце не удваивается", b.base_url == URL, b.base_url)
check("response_format = json_schema", fmt.get("type") == "json_schema", fmt.get("type"))
check("схема та, что дал LLMBackend", fmt.get("json_schema", {}).get("schema") == SCHEMA)
check("strict и имя clips", fmt.get("json_schema", {}).get("strict") is True
      and fmt.get("json_schema", {}).get("name") == "clips")
check("рассуждение выключено", first.get("chat_template_kwargs") == {"enable_thinking": False})
check("reasoning_effort none для LM Studio", first.get("reasoning_effort") == "none")
check("max_tokens не 2048", first.get("max_tokens") == 8192, first.get("max_tokens"))
check("ключ по умолчанию — заглушка", FAKE.auth[-1] == "Bearer local", FAKE.auth[-1])
check("фрагмент разобран", len(clips) == 1 and clips[0].get("score") == 8.5, clips)

print("\n--- json_schema не принят → json_object ---")
FAKE.reset()
FAKE.refuse = {"json_schema"}
b = backend()
clips = b.propose_chunk("[1] привет", META, cfg)
kinds = [(r.get("response_format") or {}).get("type") for r in FAKE.requests]
check("сначала schema, потом object", kinds == ["json_schema", "json_object"], kinds)
check("фрагмент разобран", len(clips) == 1, clips)
b.propose_chunk("[1] привет", META, cfg)
kinds = [(r.get("response_format") or {}).get("type") for r in FAKE.requests]
check("второй раз сразу json_object", kinds[2:] == ["json_object"], kinds)

print("\n--- ни schema, ни object → без response_format ---")
FAKE.reset()
FAKE.refuse = {"json_schema", "json_object"}
b = backend()
clips = b.propose_chunk("[1] привет", META, cfg)
kinds = [(r.get("response_format") or {}).get("type") for r in FAKE.requests]
check("три попытки до голого запроса", kinds == ["json_schema", "json_object", None], kinds)
check("фрагмент разобран", len(clips) == 1, clips)

print("\n--- сервер не знает chat_template_kwargs ---")
FAKE.reset()
FAKE.refuse_think = True
b = backend()
clips = b.propose_chunk("[1] привет", META, cfg)
check("повтор без kwargs, schema сохранена",
      len(FAKE.requests) == 2 and "chat_template_kwargs" not in FAKE.requests[1]
      and FAKE.requests[1]["response_format"]["type"] == "json_schema",
      [list(r) for r in FAKE.requests])
check("фрагмент разобран", len(clips) == 1, clips)

print("\n--- сервер не знает reasoning_effort ---")
FAKE.reset()
FAKE.refuse_effort = True
b = backend()
clips = b.propose_chunk("[1] привет", META, cfg)
second = FAKE.requests[-1]
check("повтор без reasoning_effort, остальное на месте",
      len(FAKE.requests) == 2 and "reasoning_effort" not in second
      and "chat_template_kwargs" in second
      and second["response_format"]["type"] == "json_schema",
      [list(r) for r in FAKE.requests])
check("фрагмент разобран", len(clips) == 1, clips)
b.propose_chunk("[1] привет", META, cfg)
check("второй раз сразу без reasoning_effort", len(FAKE.requests) == 3
      and "reasoning_effort" not in FAKE.requests[2])

print("\n--- <think> и ```json в ответе ---")
FAKE.reset()
FAKE.think_text = True
clips = backend().propose_chunk("[1] привет", META, cfg)
check("рассуждение отброшено, ответ разобран", len(clips) == 1, clips)

print("\n--- сравнение: схема order ---")
FAKE.reset()
backend().compare_group([{"no": 1, "text": "а"}, {"no": 2, "text": "б"}], cfg)
fmt = FAKE.requests[0].get("response_format") or {}
check("имя схемы order", fmt.get("json_schema", {}).get("name") == "order", fmt)

print("\n--- список моделей, ключ, выбор по умолчанию ---")
FAKE.reset()
b = backend(model="", api_key="sk-test")
check("модели с сервера", b.models() == FAKE.models, b.models())
check("ключ уходит в заголовок", FAKE.auth[-1] == "Bearer sk-test", FAKE.auth[-1])
b.check()
check("пустая модель → первая с сервера", b.model == "qwen/qwen3-8b", b.model)

print("\n--- настройки → движок ---")
cfg2 = Config()
cfg2.backend = "openai-compat"
cfg2.openai_base_url = "127.0.0.1:%d" % server.server_address[1]
cfg2.openai_api_key = "sk-x"
cfg2.openai_max_tokens = 4096
cfg2.openai_think = True
b = get_backend(cfg2)
check("адрес без схемы дополнен http://", b.base_url == URL, b.base_url)
check("ключ и max_tokens из конфига", b.api_key == "sk-x" and b.max_tokens == 4096)
FAKE.reset()
b.propose_chunk("[1] привет", META, cfg)
check("think=True → kwargs и reasoning_effort не отправляются",
      "chat_template_kwargs" not in FAKE.requests[0]
      and "reasoning_effort" not in FAKE.requests[0])

print("\n--- сервер недоступен ---")
dead = OpenAICompatBackend("http://127.0.0.1:9", "m", timeout=2)
try:
    dead.models()
    check("понятная ошибка", False)
except RuntimeError as error:
    check("понятная ошибка", "не отвечает" in str(error), str(error).splitlines()[0])

try:
    backend(api_key="ключ с пробелом").models()
    check("кривой ключ — понятная ошибка", False)
except RuntimeError as error:
    check("кривой ключ — понятная ошибка", "ключе API" in str(error), str(error))

print("\n--- окно Студии ---")
import tkinter.messagebox as _mb
_mb.showerror = lambda *a, **k: None
_mb.showinfo = lambda *a, **k: None

from studio.window import Studio

FAKE.reset()
app = Studio()
app.update()
app.v_backend.set("ollama")
app._backend_hint()
check("у Ollama своё имя модели", app.v_local_model.get() == "qwen3:8b",
      app.v_local_model.get())
check("строки сервера у Ollama нет", not app.server_row.winfo_manager())
app.v_backend.set("openai-compat")
app._backend_hint()
check("строка сервера появилась для openai-compat",
      app.server_row.winfo_manager() == "pack"
      and str(app.ent_openai_url.cget("state")) == "normal"
      and str(app.ent_openai_key.cget("state")) == "normal")
check("имя Ollama не переехало в LM Studio", app.v_local_model.get() == "",
      app.v_local_model.get())
app.v_openai_url.set(URL)
app.v_openai_key.set("sk-window")
app._check_backend()
app.update()
check("«Проверить» заполнил список", tuple(app.ent_local.cget("values")) == tuple(FAKE.models),
      app.ent_local.cget("values"))
check("выбрана модель с сервера", app.v_local_model.get() == "qwen/qwen3-8b",
      app.lbl_backend["text"])
check("ключ из окна ушёл на сервер", FAKE.auth[-1] == "Bearer sk-window", FAKE.auth[-1])
app.v_local_model.set("google/gemma-3-12b")
made = app._clipper_config()
check("конфиг собран из полей окна",
      made.openai_base_url == URL and made.openai_api_key == "sk-window"
      and made.local_model == "google/gemma-3-12b", made.local_model)
app.v_backend.set("ollama")
app._backend_hint()
check("назад на Ollama — её имя вернулось", app.v_local_model.get() == "qwen3:8b")
app._remember()
app.destroy()

app = Studio()
app.update()
check("адрес и ключ запомнены", app.v_openai_url.get() == URL
      and app.v_openai_key.get() == "sk-window")
app.v_backend.set("openai-compat")
app._backend_hint()
check("модель LM Studio запомнена", app.v_local_model.get() == "google/gemma-3-12b",
      app.v_local_model.get())
app.destroy()

server.shutdown()
print("\n  итого: %d из %d" % (sum(ok), len(ok)))
sys.exit(0 if all(ok) else 1)
