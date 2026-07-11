"""Детект сумм/балансов (Э2, F2: «по контексту, настраивается»).

Два класса срабатываний:
  1. Число с валютным маркером: «1 500 000 тг», «₸2500», «$1,200.50».
  2. Число после контекстного слова: «баланс: 3 200 000», «сумма — 450000».

Голые числа без валюты и контекста не трогаются (иначе детектор
замаскирует номера страниц, даты и счётчики).
"""

from __future__ import annotations

import re

from gateway.models import Detection, EntityType

_CURRENCY = (
    r"(?:тг\.?|тенге|теңге|KZT|₸|руб(?:лей|ля|\.)?|₽|RUB|"
    r"USD|\$|EUR|€|у\.е\.)"
)

_NUMBER = r"\d{1,3}(?:[  '’]?\d{3})*(?:[.,]\d{1,2})?"

# число + валюта («1 500 000 тг», «2500,50 KZT») или валюта + число («$1,200»)
_AMOUNT_CURRENCY = re.compile(
    rf"(?<![\w.,])(?:{_NUMBER}\s*{_CURRENCY}|{_CURRENCY}\s*{_NUMBER})(?![\w])",
    re.IGNORECASE,
)

# контекстное слово + число: «баланс: 3 200 000», «остаток — 45000»
_CONTEXT_WORDS = (
    r"(?:баланс|сумма|остаток|зарплата|оклад|доход|выручка|прибыль|"
    r"задолженност[ьи]|платёж|платеж|стоимость|цена)"
)
_AMOUNT_CONTEXT = re.compile(
    rf"\b{_CONTEXT_WORDS}\s*(?:на|по)?\s*[:=—–-]?\s*({_NUMBER})(?![\w.,])",
    re.IGNORECASE,
)


def detect_amounts(text: str) -> list[Detection]:
    result: list[Detection] = []
    taken: list[tuple[int, int]] = []

    def add(start: int, end: int, value: str, detector: str) -> None:
        if any(s < end and start < e for s, e in taken):
            return
        taken.append((start, end))
        result.append(
            Detection(
                entity_type=EntityType.AMOUNT,
                value=value,
                start=start,
                end=end,
                detector=detector,
            )
        )

    for m in _AMOUNT_CURRENCY.finditer(text):
        add(m.start(), m.end(), m.group(), "amount_currency")

    for m in _AMOUNT_CONTEXT.finditer(text):
        # маскируем только число, контекстное слово остаётся читаемым
        add(m.start(1), m.end(1), m.group(1), "amount_context")

    result.sort(key=lambda d: d.start)
    return result
