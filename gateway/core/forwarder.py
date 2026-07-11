"""Форвард к LLM-провайдеру (F5).

MVP — один провайдер с OpenAI-совместимым Chat Completions API
(подходит и для OpenAI, и для локальных vLLM/Ollama/LM Studio —
задел под мультипровайдер и локальную LLM). Ключ провайдера живёт
только здесь, на стороне шлюза.

Реализация на stdlib (urllib): без внешних HTTP-зависимостей.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request

from gateway.config import Settings


class ProviderError(Exception):
    pass


class Forwarder:
    def __init__(self, settings: Settings):
        self._settings = settings

    @property
    def provider_name(self) -> str:
        return self._settings.provider_name

    def complete(self, sanitized_text: str, provider: str | None = None) -> str:
        """Отправить обезличенный текст провайдеру, вернуть текст ответа."""
        return self.chat([{"role": "user", "content": sanitized_text}])

    def chat(
        self,
        messages: list[dict],
        model: str | None = None,
        provider: str | None = None,  # совместимость с MultiForwarder (Э3)
    ) -> str:
        s = self._settings
        if not s.provider_api_key:
            raise ProviderError(
                "Ключ провайдера не настроен (AIGATE_PROVIDER_API_KEY)"
            )
        payload = json.dumps(
            {"model": model or s.provider_model, "messages": messages}
        ).encode()
        req = urllib.request.Request(
            f"{s.provider_base_url.rstrip('/')}/chat/completions",
            data=payload,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {s.provider_api_key}",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(
                req, timeout=s.provider_timeout_seconds
            ) as resp:
                data = json.loads(resp.read().decode())
            return data["choices"][0]["message"]["content"]
        except urllib.error.URLError as e:
            raise ProviderError(f"Провайдер недоступен: {e}") from e
        except (KeyError, IndexError, ValueError) as e:
            raise ProviderError(f"Некорректный ответ провайдера: {e}") from e
