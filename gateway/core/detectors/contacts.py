"""Детект контактов: телефоны (РК/РФ и страны ЦА) и адреса электронной почты."""

from __future__ import annotations

import re

from gateway.models import Detection, EntityType

_SEP = r"[\s\-‐–.]?"

# +7 / 8 + 10 цифр: +7 701 123 45 67, 8 (727) 250-10-10, +77011234567
_PHONE_KZ_RU = re.compile(
    rf"(?<![\d+])(?:\+7|8){_SEP}\(?\d{{3}}\)?{_SEP}\d{{3}}{_SEP}\d{{2}}{_SEP}\d{{2}}(?!\d)"
)

# Узбекистан +998, Кыргызстан +996, Таджикистан +992, Туркменистан +993:
# код страны + 9 (TM — 8) цифр в группах 2-3-2-2.
_PHONE_CA = re.compile(
    rf"(?<![\d+])\+99[2368]{_SEP}\(?\d{{2,3}}\)?{_SEP}\d{{3}}{_SEP}\d{{2}}{_SEP}\d{{2}}(?!\d)"
)

_EMAIL = re.compile(
    r"(?<![\w.+-])[A-Za-z0-9][A-Za-z0-9._%+-]{0,63}@"
    r"(?:[A-Za-z0-9-]+\.)+[A-Za-z]{2,24}(?![\w-])"
)


def detect_phone(text: str) -> list[Detection]:
    result: list[Detection] = []
    for pattern in (_PHONE_KZ_RU, _PHONE_CA):
        for m in pattern.finditer(text):
            result.append(
                Detection(
                    entity_type=EntityType.PHONE,
                    value=m.group(),
                    start=m.start(),
                    end=m.end(),
                    detector="phone",
                )
            )
    return result


def detect_email(text: str) -> list[Detection]:
    return [
        Detection(
            entity_type=EntityType.EMAIL,
            value=m.group(),
            start=m.start(),
            end=m.end(),
            detector="email",
        )
        for m in _EMAIL.finditer(text)
    ]
