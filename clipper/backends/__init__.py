"""Выбор движка оценки.

Три бесплатных пути и один платный. Всё остальное в конвейере от выбора
не зависит — окна, дедупликация, границы, рендер и сбор датасета общие.
"""

from __future__ import annotations

from ..config import Config
from .base import Backend, LLMBackend, coerce_clips, extract_json

AVAILABLE = {
    "heuristic": "без модели вообще — правила по тексту, мгновенно, бесплатно",
    "ollama": "локальная модель через Ollama — бесплатно",
    "openai-compat": "локальный сервер LM Studio / llama.cpp — бесплатно",
    "claude": "Claude API — платно, самое точное качество",
}


def get_backend(cfg: Config) -> Backend:
    name = cfg.backend.lower().strip()

    if name == "heuristic":
        from .heuristic import HeuristicBackend
        return HeuristicBackend()

    if name == "ollama":
        from .ollama import OllamaBackend
        return OllamaBackend(
            host=cfg.ollama_host,
            model=cfg.local_model,
            num_ctx=cfg.local_num_ctx,
            timeout=cfg.local_timeout,
        )

    if name in ("openai-compat", "openai", "lmstudio", "llamacpp"):
        from .openai_compat import OpenAICompatBackend
        return OpenAICompatBackend(
            base_url=cfg.openai_base_url,
            model=cfg.local_model,
            timeout=cfg.local_timeout,
            api_key=cfg.openai_api_key,
            max_tokens=cfg.openai_max_tokens,
            think=cfg.openai_think,
        )

    if name == "claude":
        from .claude import ClaudeBackend
        return ClaudeBackend(cfg.model, cfg.effort, cfg.max_tokens)

    raise RuntimeError(
        f"Неизвестный движок '{cfg.backend}'. Доступны: {', '.join(AVAILABLE)}"
    )


__all__ = ["Backend", "LLMBackend", "get_backend", "AVAILABLE",
           "extract_json", "coerce_clips"]
