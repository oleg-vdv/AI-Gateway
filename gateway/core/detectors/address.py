"""Детект почтовых адресов и госномеров автомобилей РК.

Адрес ловится по маркеру улицы (ул., пр., мкр., көшесі, даңғылы) и номеру
дома: «ул. Абая, д. 10, кв. 5», «мкр. Самал-2, 58», «Абай даңғылы, 150».
Город без улицы адресом не считается — «г. Алматы» сам по себе ПДн не
раскрывает и нужен модели для смысла ответа.
"""

from __future__ import annotations

import re

from gateway.models import Detection, EntityType

_STREET_MARK = (
    r"(?:ул\.|улица|пр-т|пр\.|проспект|мкр\.?|микрорайон|б-р|бульвар"
    r"|пер\.|переулок|ш\.|шоссе|наб\.|набережная|пл\.|площадь|трасса)"
)
# Название улицы и номер дома разделены запятой или пробелом обязательно:
# иначе «мкр. Самал-2, 58» резался бы на «Самал-» + дом «2», а «и пр. 5 штук»
# принималось бы за проспект.
_STREET_NAME = r"[^\n,;]{0,39}[^\n,;\s\-](?:,\s*|\s+)"
_HOUSE = r"(?:д\.|дом|үй)?\s*\d{1,4}[а-яА-Яa-zA-Z]?(?:/\d{1,4})?"
_FLAT = r"(?:,?\s*(?:кв\.|квартира|оф\.|офис|пом\.|помещение|пәтер)\s*\d{1,4}[а-яА-Я]?)?"
_CITY = r"(?:(?:г\.|город|с\.|село|п\.|пос\.)\s*[А-ЯЁӘҒҚҢӨҰҮҺІ][\w\-]+,?\s*)?"
_INDEX = r"(?:\d{6},?\s*)?"

# Русский порядок: [индекс] [г. Город,] ул. Название, д. 10, кв. 5
_ADDRESS_RU = re.compile(
    rf"{_INDEX}{_CITY}{_STREET_MARK}\s*{_STREET_NAME}{_HOUSE}{_FLAT}(?![\w/])"
)

# Казахский порядок: Абай даңғылы, 150 / Сәтпаев көшесі 22, 5 пәтер
_ADDRESS_KZ = re.compile(
    r"(?:[А-ЯЁӘҒҚҢӨҰҮҺІ][\w\-]+\s+){1,3}(?:көшесі|даңғылы|ықшамауданы|шағын\s+ауданы)"
    r",?\s*\d{1,4}[а-яА-Яa-zA-Z]?(?:/\d{1,4})?(?:,?\s*\d{1,4}\s*пәтер)?(?![\w/])"
)

# Госномер РК: 123 ABC 02 (с 2012 года), регион 01–20
_PLATE = re.compile(r"(?<![\w])\d{3}\s?[A-Z]{2,3}\s?(?:0[1-9]|1\d|20)(?![\w])")
# Похожие на номер сочетания с кодом валюты — это суммы, а не машины
_CURRENCY_CODES = {"KZT", "USD", "EUR", "RUB", "CNY", "GBP", "UZS", "KGS"}


def detect_address(text: str) -> list[Detection]:
    result: list[Detection] = []
    for pattern in (_ADDRESS_RU, _ADDRESS_KZ):
        for m in pattern.finditer(text):
            value = m.group().rstrip(" ,")
            result.append(
                Detection(
                    entity_type=EntityType.ADDRESS,
                    value=value,
                    start=m.start(),
                    end=m.start() + len(value),
                    detector="address",
                )
            )
    return result


def detect_plate(text: str) -> list[Detection]:
    result: list[Detection] = []
    for m in _PLATE.finditer(text):
        letters = re.sub(r"[^A-Z]", "", m.group())
        if letters in _CURRENCY_CODES:
            continue
        result.append(
            Detection(
                entity_type=EntityType.PLATE,
                value=m.group(),
                start=m.start(),
                end=m.end(),
                detector="plate",
            )
        )
    return result
