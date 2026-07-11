"""Конфигурация шлюза.

Все настройки читаются из переменных окружения (префикс AIGATE_) либо из
файла .env в рабочей директории. Ключи LLM-провайдера хранятся только на
стороне шлюза (раздел 8 ТЗ: не в браузере, не в приложениях).

Без внешних зависимостей: важно для on-prem/air-gapped инсталляций.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


def _load_dotenv(path: Path = Path(".env")) -> None:
    """Минимальный парсер .env: KEY=VALUE, строки с # игнорируются."""
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip().strip("'\"")
        os.environ.setdefault(key, value)


def _env(name: str, default: str = "") -> str:
    return os.environ.get(f"AIGATE_{name}", default)


def _env_bool(name: str, default: bool) -> bool:
    raw = _env(name, "").lower()
    if raw in ("1", "true", "yes", "on"):
        return True
    if raw in ("0", "false", "no", "off"):
        return False
    return default


@dataclass
class Settings:
    # --- Сервер ---
    host: str = "0.0.0.0"
    port: int = 8080

    # --- Провайдер LLM (MVP: один, OpenAI-совместимый API) ---
    provider_name: str = "openai"
    provider_base_url: str = "https://api.openai.com/v1"
    provider_api_key: str = ""
    provider_model: str = "gpt-4o-mini"
    provider_timeout_seconds: float = 120.0

    # --- Аутентификация каналов и админки ---
    # Ключи каналов через запятую; пусто = аутентификация выключена (dev).
    channel_api_keys: str = ""
    admin_token: str = ""  # пусто = Control Plane только с localhost

    # --- Mapping Store (ст. 12: только локально, эфемерно) ---
    mapping_ttl_seconds: int = 600
    # Fernet-ключ шифрования значений в памяти; пусто = генерируется на старте
    # (маппинг не переживает рестарт — это ожидаемое поведение).
    mapping_encryption_key: str = ""

    # --- Аудит и политика (внутри периметра) ---
    audit_log_path: Path = field(default_factory=lambda: Path("data/audit.log"))
    policy_path: Path = field(default_factory=lambda: Path("data/policy.json"))
    detector_rules_path: Path = field(
        default_factory=lambda: Path("data/detector_rules.json")
    )

    # --- Fail-closed (глобальный предохранитель; выключать только на стенде) ---
    fail_closed: bool = True

    @classmethod
    def from_env(cls) -> "Settings":
        _load_dotenv()
        return cls(
            host=_env("HOST", "0.0.0.0"),
            port=int(_env("PORT", "8080")),
            provider_name=_env("PROVIDER_NAME", "openai"),
            provider_base_url=_env("PROVIDER_BASE_URL", "https://api.openai.com/v1"),
            provider_api_key=_env("PROVIDER_API_KEY", ""),
            provider_model=_env("PROVIDER_MODEL", "gpt-4o-mini"),
            provider_timeout_seconds=float(_env("PROVIDER_TIMEOUT_SECONDS", "120")),
            channel_api_keys=_env("CHANNEL_API_KEYS", ""),
            admin_token=_env("ADMIN_TOKEN", ""),
            mapping_ttl_seconds=int(_env("MAPPING_TTL_SECONDS", "600")),
            mapping_encryption_key=_env("MAPPING_ENCRYPTION_KEY", ""),
            audit_log_path=Path(_env("AUDIT_LOG_PATH", "data/audit.log")),
            policy_path=Path(_env("POLICY_PATH", "data/policy.json")),
            detector_rules_path=Path(
                _env("DETECTOR_RULES_PATH", "data/detector_rules.json")
            ),
            fail_closed=_env_bool("FAIL_CLOSED", True),
        )
