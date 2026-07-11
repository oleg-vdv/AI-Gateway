"""Конфигурация endpoint-агента (env-переменные AIGATE_AGENT_*)."""

from __future__ import annotations

import os
from dataclasses import dataclass


def _env(name: str, default: str = "") -> str:
    return os.environ.get(f"AIGATE_AGENT_{name}", default)


def _env_bool(name: str, default: bool) -> bool:
    raw = _env(name, "").lower()
    if raw in ("1", "true", "yes", "on"):
        return True
    if raw in ("0", "false", "no", "off"):
        return False
    return default


@dataclass
class AgentConfig:
    gateway_url: str = "http://localhost:8080"
    channel_key: str = ""          # ключ канала (X-AIGate-Key)
    user: str = "endpoint-user"    # идентификатор для аудита
    poll_interval: float = 0.5     # период опроса буфера, сек
    min_length: int = 8            # тексты короче не проверяются
    max_length: int = 1_000_000    # защита от гигантских буферов
    # fail-closed: при недоступном шлюзе и чувствительном содержимом
    # буфер очищается; false = только предупреждение (не рекомендуется)
    fail_closed: bool = True
    # детокенизация ответов, скопированных из LLM-приложения
    restore_enabled: bool = True
    request_timeout: float = 10.0

    @classmethod
    def from_env(cls) -> "AgentConfig":
        return cls(
            gateway_url=_env("GATEWAY_URL", "http://localhost:8080"),
            channel_key=_env("CHANNEL_KEY", ""),
            user=_env("USER", os.environ.get("USER", "endpoint-user")),
            poll_interval=float(_env("POLL_INTERVAL", "0.5")),
            min_length=int(_env("MIN_LENGTH", "8")),
            max_length=int(_env("MAX_LENGTH", "1000000")),
            fail_closed=_env_bool("FAIL_CLOSED", True),
            restore_enabled=_env_bool("RESTORE_ENABLED", True),
            request_timeout=float(_env("REQUEST_TIMEOUT", "10")),
        )
