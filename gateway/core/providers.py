"""Мультипровайдерный форвард (Э3, задел из F5).

Реестр именованных провайдеров + маршрутизация:
  * kind="openai"    — OpenAI-совместимый Chat Completions API
    (OpenAI, vLLM, Ollama, LM Studio — т.е. и локальные LLM);
  * kind="anthropic" — Anthropic Messages API.

Выбор провайдера: явный параметр, префикс модели «provider/model»
(например, "local/llama3.1") или провайдер по умолчанию. Ключи
провайдеров хранятся только в файле реестра внутри периметра;
admin API никогда не отдаёт их наружу.
"""

from __future__ import annotations

import dataclasses
import json
import logging
import os
import threading
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .forwarder import ProviderError

logger = logging.getLogger("aigate.providers")

ANTHROPIC_VERSION = "2023-06-01"
DEFAULT_MAX_TOKENS = 4096  # обязательное поле Messages API


@dataclass
class ProviderConfig:
    name: str
    kind: str = "openai"          # openai | anthropic
    base_url: str = ""
    api_key: str = ""
    model: str = ""               # модель по умолчанию
    timeout: float = 120.0
    enabled: bool = True

    def to_dict(self, include_key: bool = False) -> dict:
        d = {
            "name": self.name,
            "kind": self.kind,
            "base_url": self.base_url,
            "model": self.model,
            "timeout": self.timeout,
            "enabled": self.enabled,
        }
        if include_key:
            d["api_key"] = self.api_key
        else:
            d["api_key_configured"] = bool(self.api_key)
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "ProviderConfig":
        kind = str(d.get("kind", "openai"))
        if kind not in ("openai", "anthropic"):
            raise ValueError(f"Неизвестный kind провайдера: {kind}")
        return cls(
            name=str(d["name"]),
            kind=kind,
            base_url=str(d.get("base_url", "")),
            api_key=str(d.get("api_key", "")),
            model=str(d.get("model", "")),
            timeout=float(d.get("timeout", 120.0)),
            enabled=bool(d.get("enabled", True)),
        )


class ProviderRegistry:
    """Хранилище провайдеров (JSON внутри периметра) + выбор по имени."""

    def __init__(self, path: Path, seed: ProviderConfig | None = None):
        self._path = path
        self._lock = threading.Lock()
        self._providers: dict[str, ProviderConfig] = {}
        self._default = ""
        self._load(seed)

    def _load(self, seed: ProviderConfig | None) -> None:
        if self._path.exists():
            try:
                data = json.loads(self._path.read_text())
                self._providers = {
                    p["name"]: ProviderConfig.from_dict(p)
                    for p in data.get("providers", [])
                }
                self._default = data.get("default", "")
            except Exception:
                logger.exception("Повреждённый реестр провайдеров")
        if not self._providers and seed is not None:
            self._providers = {seed.name: seed}
            self._default = seed.name

    def save(self, providers: list[ProviderConfig], default: str) -> None:
        if default and default not in {p.name for p in providers}:
            raise ValueError(f"Провайдер по умолчанию '{default}' не в списке")
        with self._lock:
            # PUT без ключа сохраняет существующий ключ (ключи не гоняются туда-сюда)
            for p in providers:
                if not p.api_key and p.name in self._providers:
                    p.api_key = self._providers[p.name].api_key
            self._providers = {p.name: p for p in providers}
            self._default = default
            self._path.parent.mkdir(parents=True, exist_ok=True)
            payload = {
                "default": default,
                "providers": [p.to_dict(include_key=True) for p in providers],
            }
            self._path.write_text(json.dumps(payload, ensure_ascii=False, indent=2))
            try:
                os.chmod(self._path, 0o600)  # файл с ключами — только владельцу
            except OSError:
                pass

    @property
    def default_name(self) -> str:
        return self._default

    def list(self) -> list[ProviderConfig]:
        # копии: мутация возвращённых конфигов не должна менять реестр
        with self._lock:
            return [dataclasses.replace(p) for p in self._providers.values()]

    def get(self, name: str) -> ProviderConfig:
        with self._lock:
            cfg = self._providers.get(name)
        if cfg is None or not cfg.enabled:
            raise ProviderError(f"Провайдер '{name}' не найден или отключён")
        return dataclasses.replace(cfg)

    def names(self) -> set[str]:
        with self._lock:
            return set(self._providers.keys())


# --- построение запросов (чистые функции — тестируются без сети) -------------


def build_request(
    cfg: ProviderConfig, messages: list[dict], model: str
) -> tuple[str, dict, bytes]:
    """(url, headers, body) для вызова провайдера."""
    if cfg.kind == "anthropic":
        system_parts = [
            m["content"]
            for m in messages
            if m.get("role") == "system" and isinstance(m.get("content"), str)
        ]
        payload: dict = {
            "model": model,
            "max_tokens": DEFAULT_MAX_TOKENS,
            "messages": [m for m in messages if m.get("role") != "system"],
        }
        if system_parts:
            payload["system"] = "\n".join(system_parts)
        url = cfg.base_url.rstrip("/") + "/messages"
        headers = {
            "Content-Type": "application/json",
            "x-api-key": cfg.api_key,
            "anthropic-version": ANTHROPIC_VERSION,
        }
    else:  # openai-совместимый
        payload = {"model": model, "messages": messages}
        url = cfg.base_url.rstrip("/") + "/chat/completions"
        headers = {"Content-Type": "application/json"}
        if cfg.api_key:  # локальные vLLM/Ollama могут работать без ключа
            headers["Authorization"] = f"Bearer {cfg.api_key}"
    return url, headers, json.dumps(payload).encode()


def parse_response(cfg: ProviderConfig, data: dict) -> str:
    try:
        if cfg.kind == "anthropic":
            return "".join(
                block["text"] for block in data["content"] if block.get("type") == "text"
            )
        return data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as e:
        raise ProviderError(f"Некорректный ответ провайдера '{cfg.name}': {e}") from e


def _http_transport(cfg: ProviderConfig, messages: list[dict], model: str) -> str:
    url, headers, body = build_request(cfg, messages, model)
    req = urllib.request.Request(url, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=cfg.timeout) as resp:
            return parse_response(cfg, json.loads(resp.read().decode()))
    except urllib.error.URLError as e:
        raise ProviderError(f"Провайдер '{cfg.name}' недоступен: {e}") from e
    except ValueError as e:
        raise ProviderError(f"Некорректный ответ провайдера '{cfg.name}': {e}") from e


Transport = Callable[[ProviderConfig, list[dict], str], str]


class MultiForwarder:
    """Форвардер с маршрутизацией по провайдерам (интерфейс как у Forwarder)."""

    def __init__(self, registry: ProviderRegistry, transport: Transport | None = None):
        self.registry = registry
        self._transport = transport or _http_transport

    @property
    def provider_name(self) -> str:
        return self.registry.default_name or "unconfigured"

    def resolve(
        self, provider: str | None = None, model: str | None = None
    ) -> tuple[ProviderConfig, str]:
        """Выбрать провайдера: явное имя > префикс модели > по умолчанию."""
        model_name = model or ""
        if provider is None and model_name and "/" in model_name:
            prefix, rest = model_name.split("/", 1)
            if prefix in self.registry.names():
                provider, model_name = prefix, rest
        if provider is None:
            provider = self.registry.default_name
        if not provider:
            raise ProviderError("Ни один провайдер не настроен")
        cfg = self.registry.get(provider)
        return cfg, model_name or cfg.model

    def chat(
        self,
        messages: list[dict],
        model: str | None = None,
        provider: str | None = None,
    ) -> str:
        cfg, model_name = self.resolve(provider=provider, model=model)
        if not model_name:
            raise ProviderError(f"Для провайдера '{cfg.name}' не задана модель")
        return self._transport(cfg, messages, model_name)

    def complete(
        self,
        sanitized_text: str,
        model: str | None = None,
        provider: str | None = None,
    ) -> str:
        return self.chat(
            [{"role": "user", "content": sanitized_text}],
            model=model,
            provider=provider,
        )
