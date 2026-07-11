"""Детект финансовых реквизитов: IBAN KZ и номера карт (PAN).

IBAN KZ: KZ + 2 контрольные цифры + 16 знаков (казахстанский формат,
20 символов), проверка по ISO 13616 (mod-97 == 1).
PAN: 13–19 цифр (допускаются пробелы/дефисы группами), алгоритм Луна.
"""

from __future__ import annotations

import re

from gateway.models import Detection, EntityType

_IBAN_KZ = re.compile(r"\bKZ\d{2}[A-Z0-9]{16}\b", re.IGNORECASE)

# 13-19 цифр, возможно сгруппированных по 4 через пробел/дефис
_PAN = re.compile(r"(?<![\dA-Za-z])(?:\d[ -]?){12,18}\d(?![\dA-Za-z])")


def _iban_mod97_valid(iban: str) -> bool:
    """ISO 13616: переставить первые 4 символа в конец, буквы -> числа, mod 97 == 1."""
    rearranged = iban[4:] + iban[:4]
    digits = "".join(str(int(ch, 36)) for ch in rearranged)
    return int(digits) % 97 == 1


def _luhn_valid(digits: str) -> bool:
    total = 0
    for i, ch in enumerate(reversed(digits)):
        d = int(ch)
        if i % 2 == 1:
            d *= 2
            if d > 9:
                d -= 9
        total += d
    return total % 10 == 0


def detect_iban(text: str) -> list[Detection]:
    result = []
    for m in _IBAN_KZ.finditer(text):
        if _iban_mod97_valid(m.group().upper()):
            result.append(
                Detection(
                    entity_type=EntityType.IBAN,
                    value=m.group(),
                    start=m.start(),
                    end=m.end(),
                    detector="iban_kz_mod97",
                )
            )
    return result


def detect_card(text: str) -> list[Detection]:
    result = []
    for m in _PAN.finditer(text):
        digits = re.sub(r"[ -]", "", m.group())
        if 13 <= len(digits) <= 19 and _luhn_valid(digits):
            result.append(
                Detection(
                    entity_type=EntityType.CARD,
                    value=m.group(),
                    start=m.start(),
                    end=m.end(),
                    detector="pan_luhn",
                )
            )
    return result
