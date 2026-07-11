"""Модель данных шлюза (раздел 6 ТЗ).

Privacy by design: значения ПДн живут только в эфемерном MappingEntry
с TTL; в AuditRecord значений нет — только типы и счётчики.

Реализация на stdlib (dataclasses + enum): ядро шлюза не тянет внешних
зависимостей — важно для on-prem/air-gapped инсталляций (раздел 9 ТЗ).
"""

from __future__ import annotations

import enum
import time
from dataclasses import dataclass, field
from typing import Optional


class EntityType(str, enum.Enum):
    IIN = "IIN"          # ИИН (12 цифр, контрольная сумма)
    BIN = "BIN"          # БИН (12 цифр, контрольная сумма)
    PERSON = "PERSON"    # ФИО, включая казахские форматы
    IBAN = "IBAN"        # счёт IBAN KZ
    CARD = "CARD"        # PAN банковской карты
    SECRET = "SECRET"    # API-ключи, токены, приватные ключи, .env
    AMOUNT = "AMOUNT"    # суммы/балансы (по контексту, настраивается)


class Action(str, enum.Enum):
    MASK = "mask"    # обратимая токенизация (по умолчанию)
    BLOCK = "block"  # запрос не уходит к провайдеру
    ALLOW = "allow"  # пропустить как есть (= трансграничная передача, логируется особо)


class Channel(str, enum.Enum):
    BROWSER = "browser"   # браузерное расширение
    EGRESS = "egress"     # reverse-proxy / API middleware
    API = "api"           # прямой вызов /v1/check


class Verdict(str, enum.Enum):
    MASKED = "masked"            # текст обезличен и отправлен
    BLOCKED = "blocked"          # заблокировано политикой
    ALLOWED = "allowed"          # пропущено с ПДн (явное разрешение)
    CLEAN = "clean"              # чувствительных данных не найдено
    FAIL_CLOSED = "fail_closed"  # ошибка обработки -> блок


@dataclass
class Detection:
    """Найденная чувствительная сущность в тексте."""

    entity_type: EntityType
    value: str
    start: int
    end: int
    detector: str  # имя детектора — для отладки и аудита


@dataclass
class MappingEntry:
    """token <-> значение. Живёт только в памяти, шифруется, умирает по TTL."""

    token: str
    encrypted_value: bytes
    entity_type: EntityType
    session_id: str
    created_at: float = field(default_factory=time.time)


@dataclass
class AuditRecord:
    """Запись аудита. НЕ содержит значений ПДн/секретов — только типы и количества."""

    ts: float
    user: str
    channel: str
    provider: str
    verdict: str
    entity_counts: dict[str, int]   # {"IIN": 2, "SECRET": 1}
    policy_actions: dict[str, str]  # {"IIN": "mask", ...}
    request_id: str
    cross_border_basis: Optional[str] = None  # основание для allow (ст. 16)
    # tamper-evident цепочка: hash = sha256(prev_hash + canonical_json(record))
    prev_hash: str = ""
    hash: str = ""

    def to_dict(self) -> dict:
        return {
            "ts": self.ts,
            "user": self.user,
            "channel": self.channel,
            "provider": self.provider,
            "verdict": self.verdict,
            "entity_counts": self.entity_counts,
            "policy_actions": self.policy_actions,
            "request_id": self.request_id,
            "cross_border_basis": self.cross_border_basis,
            "prev_hash": self.prev_hash,
            "hash": self.hash,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "AuditRecord":
        return cls(**d)


@dataclass
class PolicyRule:
    """Правило политики для типа сущности."""

    entity_type: EntityType
    action: Action = Action.MASK
    # основание для allow — обязательно при действии allow (фиксация по ст. 16)
    allow_basis: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "entity_type": self.entity_type.value,
            "action": self.action.value,
            "allow_basis": self.allow_basis,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "PolicyRule":
        return cls(
            entity_type=EntityType(d["entity_type"]),
            action=Action(d.get("action", "mask")),
            allow_basis=d.get("allow_basis"),
        )


@dataclass
class Policy:
    """Политика: действия по типам сущностей, с overrides по каналу/группе."""

    name: str = "default"
    rules: list[PolicyRule] = field(default_factory=list)
    # переопределения: канал -> {entity_type -> action}
    channel_overrides: dict[str, dict[str, Action]] = field(default_factory=dict)
    # переопределения: группа пользователей -> {entity_type -> action}
    group_overrides: dict[str, dict[str, Action]] = field(default_factory=dict)
    fail_closed: bool = True

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "rules": [r.to_dict() for r in self.rules],
            "channel_overrides": {
                ch: {t: a.value for t, a in m.items()}
                for ch, m in self.channel_overrides.items()
            },
            "group_overrides": {
                g: {t: a.value for t, a in m.items()}
                for g, m in self.group_overrides.items()
            },
            "fail_closed": self.fail_closed,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Policy":
        return cls(
            name=d.get("name", "default"),
            rules=[PolicyRule.from_dict(r) for r in d.get("rules", [])],
            channel_overrides={
                ch: {t: Action(a) for t, a in m.items()}
                for ch, m in d.get("channel_overrides", {}).items()
            },
            group_overrides={
                g: {t: Action(a) for t, a in m.items()}
                for g, m in d.get("group_overrides", {}).items()
            },
            fail_closed=d.get("fail_closed", True),
        )


# --- API-схемы -------------------------------------------------------------


@dataclass
class CheckRequest:
    """Единый внутренний формат «запрос на проверку» (F1)."""

    text: str
    user: str = "anonymous"
    group: str = "default"
    channel: Channel = Channel.API
    session_id: Optional[str] = None
    # true = только проверить/маскировать, не отправлять провайдеру
    dry_run: bool = False

    @classmethod
    def from_dict(cls, d: dict) -> "CheckRequest":
        if not isinstance(d.get("text"), str):
            raise ValueError("поле 'text' обязательно и должно быть строкой")
        return cls(
            text=d["text"],
            user=str(d.get("user") or "anonymous"),
            group=str(d.get("group") or "default"),
            channel=Channel(d.get("channel", "api")),
            session_id=d.get("session_id"),
            dry_run=bool(d.get("dry_run", False)),
        )


@dataclass
class CheckResponse:
    verdict: Verdict
    request_id: str
    session_id: str
    masked_text: Optional[str] = None   # обезличенный текст (для dry_run/браузера)
    answer: Optional[str] = None        # ответ LLM с восстановленными значениями
    entity_counts: dict[str, int] = field(default_factory=dict)
    message: Optional[str] = None       # причина блокировки и т.п.

    def to_dict(self) -> dict:
        return {
            "verdict": self.verdict.value,
            "request_id": self.request_id,
            "session_id": self.session_id,
            "masked_text": self.masked_text,
            "answer": self.answer,
            "entity_counts": self.entity_counts,
            "message": self.message,
        }
