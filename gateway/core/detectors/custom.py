"""Настраиваемые правила детекта (Э2): словари и регэкспы организации.

Реализует DetectorRule из модели данных (раздел 6 ТЗ) и требование F2
«исходный код и маркеры коммерческой тайны — по настраиваемым
словарям/правилам». Правила хранятся в JSON внутри периметра и
управляются через Control Plane (GET/PUT /admin/api/detector-rules).
"""

from __future__ import annotations

import json
import logging
import re
import threading
from pathlib import Path

from gateway.models import Detection, DetectorRule, EntityType

logger = logging.getLogger("aigate.detectors")


def default_rules() -> list[DetectorRule]:
    return [
        DetectorRule(
            name="trade_secret_markers",
            entity_type=EntityType.CONFIDENTIAL,
            keywords=[
                "коммерческая тайна",
                "коммерческую тайну",
                "конфиденциально",
                "для служебного пользования",
                "ДСП",
                "не для распространения",
            ],
        ),
    ]


class CustomRuleRegistry:
    """Загрузка/хранение правил + компиляция в детектор."""

    def __init__(self, path: Path):
        self._path = path
        self._lock = threading.Lock()
        self._rules = self._load()
        self._compiled = self._compile(self._rules)

    @property
    def rules(self) -> list[DetectorRule]:
        return list(self._rules)

    def _load(self) -> list[DetectorRule]:
        if self._path.exists():
            try:
                data = json.loads(self._path.read_text())
                return [DetectorRule.from_dict(r) for r in data]
            except Exception:
                logger.exception("Повреждённые правила детекта, дефолт")
                return default_rules()
        return default_rules()

    def save(self, rules: list[DetectorRule]) -> None:
        with self._lock:
            compiled = self._compile(rules)  # валидация регэкспов до записи
            self._rules = list(rules)
            self._compiled = compiled
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._path.write_text(
                json.dumps([r.to_dict() for r in rules], ensure_ascii=False, indent=2)
            )

    @staticmethod
    def _compile(
        rules: list[DetectorRule],
    ) -> list[tuple[DetectorRule, re.Pattern]]:
        compiled = []
        for rule in rules:
            if not rule.enabled:
                continue
            parts = []
            if rule.pattern:
                re.compile(rule.pattern)  # ValueError при кривом регэкспе
                parts.append(f"(?:{rule.pattern})")
            if rule.keywords:
                escaped = "|".join(re.escape(k) for k in rule.keywords if k)
                if escaped:
                    parts.append(f"(?i:{escaped})")
            if not parts:
                continue
            compiled.append((rule, re.compile("|".join(parts))))
        return compiled

    def detect(self, text: str) -> list[Detection]:
        with self._lock:
            compiled = self._compiled
        result: list[Detection] = []
        for rule, pattern in compiled:
            for m in pattern.finditer(text):
                result.append(
                    Detection(
                        entity_type=rule.entity_type,
                        value=m.group(),
                        start=m.start(),
                        end=m.end(),
                        detector=f"custom_{rule.name}",
                    )
                )
        return result
