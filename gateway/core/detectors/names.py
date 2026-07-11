"""Детект ФИО, включая казахские форматы (-улы/-кызы/-тегі).

MVP-подход (без ML): паттерны на кириллические/латинские последовательности
из 2–3 капитализированных слов + казахские патронимические суффиксы +
русские отчества (-ович/-евна/...). На этапе 2 заменяется/дополняется
NER-моделью (Presidio + кастомные recognizer'ы).
"""

from __future__ import annotations

import re

from gateway.models import Detection, EntityType

# Слово с заглавной буквы (кириллица, вкл. казахские буквы, или латиница)
_CAP = r"[А-ЯЁӘҒҚҢӨҰҮҺІA-Z][а-яёәғқңөұүһіa-z]+"

# Казахские патронимические суффиксы: Нурсултанулы, Айгеримкызы, Ахметтегі
_KZ_PATRONYMIC = rf"{_CAP}(?:улы|ұлы|кызы|қызы|тегі|теги)"

# Русские отчества
_RU_PATRONYMIC = rf"{_CAP}(?:ович|евич|ьич|ич|овна|евна|ична|инична)"

_NAME_PATTERNS = [
    # Фамилия Имя Отчество (русское отчество)
    re.compile(rf"\b{_CAP}\s+{_CAP}\s+{_RU_PATRONYMIC}\b"),
    # Имя Отчество Фамилия
    re.compile(rf"\b{_CAP}\s+{_RU_PATRONYMIC}\s+{_CAP}\b"),
    # Казахский формат: Имя + патроним (с фамилией или без)
    re.compile(rf"\b{_CAP}\s+{_KZ_PATRONYMIC}\b"),
    re.compile(rf"\b{_KZ_PATRONYMIC}\b"),
    # Фамилия И.О. / И.О. Фамилия
    re.compile(rf"\b{_CAP}\s+[А-ЯЁӘҒҚҢӨҰҮҺІA-Z]\.\s?[А-ЯЁӘҒҚҢӨҰҮҺІA-Z]\.(?!\w)"),
    re.compile(rf"\b[А-ЯЁӘҒҚҢӨҰҮҺІA-Z]\.\s?[А-ЯЁӘҒҚҢӨҰҮҺІA-Z]\.\s+{_CAP}\b"),
]


def detect_person(text: str) -> list[Detection]:
    result: list[Detection] = []
    taken: list[tuple[int, int]] = []
    for pattern in _NAME_PATTERNS:
        for m in pattern.finditer(text):
            span = (m.start(), m.end())
            if any(s < span[1] and span[0] < e for s, e in taken):
                continue
            taken.append(span)
            result.append(
                Detection(
                    entity_type=EntityType.PERSON,
                    value=m.group(),
                    start=m.start(),
                    end=m.end(),
                    detector="person_pattern",
                )
            )
    return result
