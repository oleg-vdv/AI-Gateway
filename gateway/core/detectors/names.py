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

# Контекстные слова, после которых 2-3 капитализированных слова — это ФИО
# (Э2: расширение казахской специфики — двухсловные имена без патронима,
# например «Айдос Смагулов», ловятся только по контексту, чтобы не маскировать
# любые пары слов с заглавной буквы вроде «Северный Казахстан»).
_CONTEXT = r"(?i:ФИО|Ф\.И\.О\.?|клиент|заявитель|сотрудник|абонент|пациент|заемщик|заёмщик|вкладчик|директор|руководитель)"

# (pattern, номер группы со значением ФИО; 0 = весь матч)
_NAME_PATTERNS: list[tuple[re.Pattern, int]] = [
    # Фамилия Имя Отчество (русское отчество)
    (re.compile(rf"\b{_CAP}\s+{_CAP}\s+{_RU_PATRONYMIC}\b"), 0),
    # Имя Отчество Фамилия
    (re.compile(rf"\b{_CAP}\s+{_RU_PATRONYMIC}\s+{_CAP}\b"), 0),
    # Казахский формат: Имя + патроним (с фамилией или без)
    (re.compile(rf"\b{_CAP}\s+{_KZ_PATRONYMIC}\b"), 0),
    (re.compile(rf"\b{_KZ_PATRONYMIC}\b"), 0),
    # Фамилия И.О. / И.О. Фамилия
    (re.compile(rf"\b{_CAP}\s+[А-ЯЁӘҒҚҢӨҰҮҺІA-Z]\.\s?[А-ЯЁӘҒҚҢӨҰҮҺІA-Z]\.(?!\w)"), 0),
    (re.compile(rf"\b[А-ЯЁӘҒҚҢӨҰҮҺІA-Z]\.\s?[А-ЯЁӘҒҚҢӨҰҮҺІA-Z]\.\s+{_CAP}\b"), 0),
    # Контекст + Имя Фамилия [Отчество/патроним]
    (re.compile(rf"\b{_CONTEXT}\s*[:—–-]?\s+({_CAP}\s+{_CAP}(?:\s+{_CAP})?)\b"), 1),
]


def detect_person(text: str) -> list[Detection]:
    result: list[Detection] = []
    taken: list[tuple[int, int]] = []
    for pattern, group in _NAME_PATTERNS:
        for m in pattern.finditer(text):
            span = (m.start(group), m.end(group))
            if any(s < span[1] and span[0] < e for s, e in taken):
                continue
            taken.append(span)
            result.append(
                Detection(
                    entity_type=EntityType.PERSON,
                    value=m.group(group),
                    start=span[0],
                    end=span[1],
                    detector="person_pattern",
                )
            )
    return result
