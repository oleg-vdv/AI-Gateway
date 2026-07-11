"""HTTP-клиент агента к шлюзу AI-Gate (stdlib urllib)."""

from __future__ import annotations

import json
import urllib.error
import urllib.request

from .config import AgentConfig


class GatewayUnavailable(Exception):
    """Шлюз недоступен или вернул ошибку — повод для fail-closed."""


class GatewayClient:
    def __init__(self, config: AgentConfig):
        self._cfg = config

    def _post(self, path: str, payload: dict) -> dict:
        headers = {"Content-Type": "application/json"}
        if self._cfg.channel_key:
            headers["X-AIGate-Key"] = self._cfg.channel_key
        req = urllib.request.Request(
            self._cfg.gateway_url.rstrip("/") + path,
            data=json.dumps(payload).encode(),
            headers=headers,
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=self._cfg.request_timeout) as resp:
                return json.loads(resp.read().decode())
        except urllib.error.HTTPError as e:
            try:
                detail = json.loads(e.read().decode()).get("detail", "")
            except Exception:
                detail = ""
            raise GatewayUnavailable(f"Шлюз ответил {e.code}: {detail}") from e
        except (urllib.error.URLError, TimeoutError, ValueError) as e:
            raise GatewayUnavailable(f"Шлюз недоступен: {e}") from e

    def check(self, text: str) -> dict:
        """Проверить/замаскировать текст (dry_run: буфер подменяет сам агент)."""
        return self._post(
            "/v1/check",
            {
                "text": text,
                "user": self._cfg.user,
                "channel": "endpoint",
                "dry_run": True,
            },
        )

    def restore(self, session_id: str, text: str, purge: bool = False) -> str:
        """Детокенизировать текст ответа LLM (маппинг остаётся до TTL)."""
        data = self._post(
            "/v1/restore",
            {"session_id": session_id, "text": text, "purge": purge},
        )
        return data["text"]
