"""Детект секретов: API-ключи, токены, пароли, .env, приватные ключи.

Комбинация (требование 5.2):
  1. регэкспы известных форматов (OpenAI, Anthropic, AWS, GitHub, Google,
     Slack, JWT, приватные ключи PEM, строки подключения);
  2. пары KEY=value / key: value из .env/конфигов с чувствительным именем;
  3. оценка энтропии Шеннона для кандидатов без известного формата.
"""

from __future__ import annotations

import math
import re

from gateway.models import Detection, EntityType

# --- 1. Известные форматы ключей/токенов -----------------------------------

_KNOWN_PATTERNS: list[tuple[str, re.Pattern]] = [
    # anthropic раньше openai: sk-ant-... — частный случай sk-...
    ("anthropic_key", re.compile(r"\bsk-ant-[A-Za-z0-9_-]{20,}\b")),
    ("openai_key", re.compile(r"\bsk-(?:proj-)?[A-Za-z0-9_-]{20,}\b")),
    ("aws_access_key", re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b")),
    ("github_token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{36,}\b")),
    ("github_pat", re.compile(r"\bgithub_pat_[A-Za-z0-9_]{22,}\b")),
    ("google_api_key", re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b")),
    ("slack_token", re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b")),
    ("stripe_key", re.compile(r"\b[sr]k_(?:live|test)_[A-Za-z0-9]{20,}\b")),
    ("telegram_bot", re.compile(r"\b\d{8,10}:AA[A-Za-z0-9_-]{33}\b")),
    ("jwt", re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]+\b")),
    (
        "private_key_pem",
        re.compile(
            r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]+?-----END [A-Z ]*PRIVATE KEY-----"
        ),
    ),
    (
        "connection_string",
        re.compile(r"\b(?:postgres(?:ql)?|mysql|mongodb(?:\+srv)?|redis|amqp)://[^\s'\"]*:[^\s'\"@]+@[^\s'\"]+"),
    ),
]

# --- 2. Пары из .env / конфигов ---------------------------------------------

_SENSITIVE_NAME = (
    r"[A-Za-z0-9_.-]*(?:PASSWORD|PASSWD|SECRET|TOKEN|API[_-]?KEY|APIKEY|"
    r"ACCESS[_-]?KEY|PRIVATE[_-]?KEY|CREDENTIALS?|AUTH)[A-Za-z0-9_.-]*"
)
_ENV_PAIR = re.compile(
    rf"\b({_SENSITIVE_NAME})\s*[:=]\s*[\"']?([^\s\"']{{6,}})[\"']?",
    re.IGNORECASE,
)

# --- 3. Энтропийные кандидаты -----------------------------------------------

# длинные токеноподобные строки со смешанным алфавитом
_ENTROPY_CANDIDATE = re.compile(r"\b[A-Za-z0-9+/_=-]{24,}\b")
_ENTROPY_THRESHOLD = 4.0  # бит/символ


def shannon_entropy(s: str) -> float:
    if not s:
        return 0.0
    freq: dict[str, int] = {}
    for ch in s:
        freq[ch] = freq.get(ch, 0) + 1
    n = len(s)
    return -sum(c / n * math.log2(c / n) for c in freq.values())


def _looks_random(s: str) -> bool:
    """Высокая энтропия + смешение классов символов (не просто слово/hex-хэш)."""
    if shannon_entropy(s) < _ENTROPY_THRESHOLD:
        return False
    has_upper = any(c.isupper() for c in s)
    has_lower = any(c.islower() for c in s)
    has_digit = any(c.isdigit() for c in s)
    return (has_upper + has_lower + has_digit) >= 2


def detect_secrets(text: str) -> list[Detection]:
    result: list[Detection] = []
    taken: list[tuple[int, int]] = []

    def add(start: int, end: int, value: str, detector: str) -> None:
        if any(s < end and start < e for s, e in taken):
            return
        taken.append((start, end))
        result.append(
            Detection(
                entity_type=EntityType.SECRET,
                value=value,
                start=start,
                end=end,
                detector=detector,
            )
        )

    for name, pattern in _KNOWN_PATTERNS:
        for m in pattern.finditer(text):
            add(m.start(), m.end(), m.group(), f"secret_{name}")

    for m in _ENV_PAIR.finditer(text):
        # маскируем только значение, имя переменной остаётся читаемым
        add(m.start(2), m.end(2), m.group(2), "secret_env_pair")

    for m in _ENTROPY_CANDIDATE.finditer(text):
        if _looks_random(m.group()):
            add(m.start(), m.end(), m.group(), "secret_entropy")

    result.sort(key=lambda d: d.start)
    return result
