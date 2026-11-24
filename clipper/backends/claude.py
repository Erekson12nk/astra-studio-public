"""Движок через Claude API. Платный, но самый точный в суждениях о вкусе."""

from __future__ import annotations

from ..config import Config
from .base import (COMPARE_SCHEMA, COMPARE_SYSTEM, COMPARE_TEMPLATE, SCHEMA,
                   SYSTEM, USER_TEMPLATE, Backend, coerce_clips, coerce_order,
                   extract_json, render_items, with_taste)


class ClaudeBackend(Backend):
    name = "claude"
    free = False
    default_chunk_minutes = 8.0

    def __init__(self, model: str, effort: str, max_tokens: int):
        self.model = model
        self.effort = effort
        self.max_tokens = max_tokens
        self._client = None

    def describe(self) -> str:
        return f"claude {self.model} (effort={self.effort})"

    def check(self) -> None:
        try:
            import anthropic  # noqa: F401
        except ImportError as exc:
            raise RuntimeError("SDK не установлен:  py -m pip install anthropic") from exc

    def _get_client(self):
        if self._client is None:
            import anthropic
            self._client = anthropic.Anthropic()
        return self._client

    def propose_chunk(self, body: str, meta: dict, cfg: Config) -> list[dict]:
        user = USER_TEMPLATE.format(body=body, min_s=cfg.min_clip_s,
                                    max_s=cfg.max_clip_s, **meta)
        text = self._ask(with_taste(SYSTEM, cfg), user, SCHEMA)
        return coerce_clips(extract_json(text))

    def compare_group(self, items: list[dict], cfg: Config) -> list[int]:
        user = COMPARE_TEMPLATE.format(body=render_items(items))
        text = self._ask(with_taste(COMPARE_SYSTEM, cfg), user, COMPARE_SCHEMA)
        return coerce_order(extract_json(text), len(items))

    def _ask(self, system: str, user: str, schema: dict) -> str:
        import anthropic

        client = self._get_client()
        try:
            resp = client.messages.create(
                model=self.model,
                max_tokens=self.max_tokens,
                system=system,
                messages=[{"role": "user", "content": user}],
                thinking={"type": "adaptive"},
                output_config={"format": {"type": "json_schema", "schema": schema},
                               "effort": self.effort},
            )
        except anthropic.NotFoundError as exc:
            raise RuntimeError(f"Модель {self.model} недоступна: {exc}") from exc
        except anthropic.RateLimitError as exc:
            raise RuntimeError(
                "Упёрлись в лимит запросов. Подождите и повторите, либо увеличьте "
                "CLIPPER_CHUNK, чтобы окон стало меньше."
            ) from exc
        except anthropic.APIStatusError as exc:
            raise RuntimeError(f"API вернул ошибку {exc.status_code}: {exc}") from exc
        except anthropic.APIConnectionError as exc:
            raise RuntimeError(f"Не удалось соединиться с API: {exc}") from exc

        if resp.stop_reason == "refusal":
            return ""

        return next((b.text for b in resp.content if b.type == "text"), "")
