"""Детект номеров документов, удостоверяющих личность, и дат рождения.

Номер удостоверения РК — 9 цифр без контрольной суммы, по форме неотличим
от любого другого числа, поэтому ловится только рядом с контекстом
(«удостоверение №», «жеке куәлік»). Паспорт РК — латинская N + 8 цифр:
форма достаточно специфична, чтобы ловить его и без контекста.
Дата рождения — только по контексту: дат в рабочих документах много,
и маскировать каждую значит сломать смысл текста.
"""

from __future__ import annotations

import re

from gateway.models import Detection, EntityType

# Паспорт РК: N12345678 (латинская N; в сканах часто кириллическая Н)
_KZ_PASSPORT = re.compile(r"(?<![\w])[NН]\s?\d{8}(?!\d)")

_DOC_CONTEXT = (
    r"(?i:удостоверени[еяи]\s+личности|удостоверени[еяи]|уд\.\s?л\.?|уд\.\s?личн\w*"
    r"|паспорт\w*|вод(?:\.|ительск\w*)\s+удостоверени[еяи]|в/у"
    r"|жеке\s+куәлі\w*|төлқұжат\w*|ID\s?card|passport)"
)

# Контекст, затем до 25 символов «№ ..., серия ...», затем сам номер:
# 9 цифр (уд. РК), 45 06 123456 (паспорт РФ), AA1234567 (паспорт UZ/KG),
# 12 AB 345678 (вод. удостоверение), N12345678 (паспорт РК).
_DOC_WITH_CONTEXT = re.compile(
    rf"{_DOC_CONTEXT}[^\n\d]{{0,25}}?"
    r"(?P<num>[NН]\s?\d{8}|[A-ZА-Я]{2}\s?\d{6,7}|\d{2}\s?[A-ZА-Я]{2}\s?\d{6}"
    r"|\d{2}\s?\d{2}\s?\d{6}|\d{9})(?!\d)"
)

_MONTHS = (
    r"(?:января|февраля|марта|апреля|мая|июня|июля|августа|сентября|октября|ноября|декабря"
    r"|қаңтар|ақпан|наурыз|сәуір|мамыр|маусым|шілде|тамыз|қыркүйек|қазан|қараша|желтоқсан)"
)
_DATE = rf"(?:\d{{1,2}}[./-]\d{{1,2}}[./-](?:19|20)\d{{2}}|\d{{1,2}}\s+{_MONTHS}\s+(?:19|20)\d{{2}}(?:\s?г\.?)?|(?:19|20)\d{{2}}-\d{{2}}-\d{{2}})"

_BIRTH_CONTEXT = (
    r"(?i:дата\s+рождения|д\.\s?р\.|родил(?:ся|ась)|год\s+рождения"
    r"|туған\s+күні|date\s+of\s+birth|DOB|born)"
)

_BIRTHDATE = re.compile(rf"{_BIRTH_CONTEXT}\s*[:—–-]?\s*(?P<date>{_DATE})")
# «1985 г.р.», «12.03.1985 г.р.»
_BIRTHDATE_AFTER = re.compile(rf"(?P<date>{_DATE}|(?:19|20)\d{{2}})\s*г\.\s?р\.")


def detect_id_documents(text: str) -> list[Detection]:
    spans: dict[tuple[int, int], str] = {}
    for m in _DOC_WITH_CONTEXT.finditer(text):
        spans[(m.start("num"), m.end("num"))] = m.group("num")
    for m in _KZ_PASSPORT.finditer(text):
        spans.setdefault((m.start(), m.end()), m.group())
    return [
        Detection(
            entity_type=EntityType.ID_DOC,
            value=value,
            start=s,
            end=e,
            detector="id_document",
        )
        for (s, e), value in spans.items()
    ]


def detect_birthdate(text: str) -> list[Detection]:
    result: list[Detection] = []
    for pattern in (_BIRTHDATE, _BIRTHDATE_AFTER):
        for m in pattern.finditer(text):
            result.append(
                Detection(
                    entity_type=EntityType.BIRTHDATE,
                    value=m.group("date"),
                    start=m.start("date"),
                    end=m.end("date"),
                    detector="birthdate",
                )
            )
    return result
