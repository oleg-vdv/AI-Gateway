"""Детект ИИН и БИН Республики Казахстан.

Оба — 12 цифр с контрольной суммой по взвешенному алгоритму
(ГОСТ РК, применяется в ИИН и БИН одинаково):

    s = (Σ d[i] * w1[i]) mod 11,  w1 = 1..11
    если s == 10: s = (Σ d[i] * w2[i]) mod 11,  w2 = 3,4,...,11,1,2
    если s == 10 снова — номер некорректен
    иначе s должен совпасть с 12-й цифрой.

Различение (управляющие разряды):
  * ИИН: разряды 1–6 — дата рождения YYMMDD, 7-й разряд — век/пол (1–6).
    5-й разряд (первая цифра дня) ∈ 0–3.
  * БИН: разряды 1–4 — год и месяц регистрации, 5-й разряд ∈ 4–6
    (признак резидентства/типа), 6-й разряд ∈ 0–3 (тип подразделения).

Требование ТЗ (5.2): валидация по формату И контрольной сумме, иначе
детектор либо ловит любые 12-значные числа, либо пропускает валидные.
"""

from __future__ import annotations

import re

from gateway.models import Detection, EntityType

_W1 = (1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11)
_W2 = (3, 4, 5, 6, 7, 8, 9, 10, 11, 1, 2)

# 12 цифр, не являющиеся частью более длинной цифровой последовательности
_TWELVE_DIGITS = re.compile(r"(?<!\d)\d{12}(?!\d)")


def checksum_valid(digits: str) -> bool:
    """Проверка контрольной суммы 12-значного номера (ИИН/БИН)."""
    if len(digits) != 12 or not digits.isdigit():
        return False
    d = [int(c) for c in digits]
    s = sum(x * w for x, w in zip(d[:11], _W1)) % 11
    if s == 10:
        s = sum(x * w for x, w in zip(d[:11], _W2)) % 11
        if s == 10:
            return False
    return s == d[11]


def _is_iin_structure(digits: str) -> bool:
    """Структурная проверка ИИН: YYMMDD + век/пол."""
    mm = int(digits[2:4])
    dd = int(digits[4:6])
    century_sex = int(digits[6])
    return 1 <= mm <= 12 and 1 <= dd <= 31 and 1 <= century_sex <= 6


def _is_bin_structure(digits: str) -> bool:
    """Структурная проверка БИН: месяц регистрации + управляющие разряды."""
    mm = int(digits[2:4])
    residency = int(digits[4])
    subdivision = int(digits[5])
    return 1 <= mm <= 12 and 4 <= residency <= 6 and 0 <= subdivision <= 3


def classify(digits: str) -> EntityType | None:
    """Классифицировать валидный 12-значный номер как ИИН или БИН.

    Возвращает None, если контрольная сумма или структура не сходятся.
    Разряды не пересекаются: у ИИН 5-й разряд ∈ 0–3 (первая цифра дня),
    у БИН 5-й разряд ∈ 4–6 — поэтому однозначно.
    """
    if not checksum_valid(digits):
        return None
    if _is_bin_structure(digits):
        return EntityType.BIN
    if _is_iin_structure(digits):
        return EntityType.IIN
    return None


def detect_iin_bin(text: str) -> list[Detection]:
    result: list[Detection] = []
    for m in _TWELVE_DIGITS.finditer(text):
        entity = classify(m.group())
        if entity is not None:
            result.append(
                Detection(
                    entity_type=entity,
                    value=m.group(),
                    start=m.start(),
                    end=m.end(),
                    detector="iin_bin_checksum",
                )
            )
    return result
