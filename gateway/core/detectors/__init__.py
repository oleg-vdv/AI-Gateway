"""Детекторы чувствительных данных (F2).

Каждый детектор возвращает список Detection. Реестр detect_all()
прогоняет все детекторы (плюс переданные дополнительные — например,
настраиваемые правила организации) и разрешает пересечения по
приоритету типа: SECRET/CONFIDENTIAL > IIN/BIN > IBAN/CARD/документы/контакты >
PERSON/адрес/госномер/дата рождения > AMOUNT.
"""

from __future__ import annotations

from typing import Callable

from gateway.models import Detection

from .address import detect_address, detect_plate
from .amounts import detect_amounts
from .contacts import detect_email, detect_phone
from .documents import detect_birthdate, detect_id_documents
from .financial import detect_card, detect_iban
from .iin_bin import detect_iin_bin
from .names import detect_person
from .secrets import detect_secrets

_PRIORITY = {
    "SECRET": 0,
    "CONFIDENTIAL": 0,
    "IIN": 1,
    "BIN": 1,
    "IBAN": 2,
    "CARD": 2,
    "ID_DOC": 2,
    "EMAIL": 2,
    "PHONE": 2,
    "PERSON": 3,
    "ADDRESS": 3,
    "PLATE": 3,
    "BIRTHDATE": 3,
    "AMOUNT": 4,
}

Detector = Callable[[str], list[Detection]]

_BUILTIN: list[Detector] = [
    detect_secrets,
    detect_iin_bin,
    detect_iban,
    detect_card,
    detect_id_documents,
    detect_email,
    detect_phone,
    detect_person,
    detect_address,
    detect_plate,
    detect_birthdate,
    detect_amounts,
]


def _overlaps(a: Detection, b: Detection) -> bool:
    return a.start < b.end and b.start < a.end


def detect_all(text: str, extra: list[Detector] | None = None) -> list[Detection]:
    """Прогнать все детекторы и убрать пересечения по приоритету типа."""
    raw: list[Detection] = []
    for detector in _BUILTIN + list(extra or []):
        raw += detector(text)

    # сортируем по приоритету типа, затем по длине (длиннее — специфичнее)
    raw.sort(key=lambda d: (_PRIORITY[d.entity_type.value], -(d.end - d.start)))
    accepted: list[Detection] = []
    for det in raw:
        if not any(_overlaps(det, a) for a in accepted):
            accepted.append(det)
    accepted.sort(key=lambda d: d.start)
    return accepted
