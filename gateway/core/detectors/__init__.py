"""Детекторы чувствительных данных (F2).

Каждый детектор возвращает список Detection. Реестр detect_all()
прогоняет все детекторы и разрешает пересечения (приоритет у более
специфичных типов: SECRET > IIN/BIN > IBAN/CARD > PERSON > AMOUNT).
"""

from gateway.models import Detection

from .financial import detect_card, detect_iban
from .iin_bin import detect_iin_bin
from .names import detect_person
from .secrets import detect_secrets

_PRIORITY = {
    "SECRET": 0,
    "IIN": 1,
    "BIN": 1,
    "IBAN": 2,
    "CARD": 2,
    "PERSON": 3,
    "AMOUNT": 4,
}


def _overlaps(a: Detection, b: Detection) -> bool:
    return a.start < b.end and b.start < a.end


def detect_all(text: str) -> list[Detection]:
    """Прогнать все детекторы и убрать пересечения по приоритету типа."""
    raw: list[Detection] = []
    raw += detect_secrets(text)
    raw += detect_iin_bin(text)
    raw += detect_iban(text)
    raw += detect_card(text)
    raw += detect_person(text)

    # сортируем по приоритету типа, затем по длине (длиннее — специфичнее)
    raw.sort(key=lambda d: (_PRIORITY[d.entity_type.value], -(d.end - d.start)))
    accepted: list[Detection] = []
    for det in raw:
        if not any(_overlaps(det, a) for a in accepted):
            accepted.append(det)
    accepted.sort(key=lambda d: d.start)
    return accepted
