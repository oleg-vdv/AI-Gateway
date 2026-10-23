"""Синтетический корпус офисных текстов с разметкой для оценки детекта.

Каждый документ собирается из шаблона и заранее известных значений, поэтому
позиции всех сущностей известны точно. Имена берутся из двух пулов:

- LEXICON_* — имена из словаря детектора: проверяет, что словарь работает;
- HELDOUT_* — имена, которых в словаре нет: честная оценка того, что
  случится с редким именем у реального клиента.

Плюс отдельный набор текстов без ПДн (города, банки, госорганы, слова
с заглавной буквы) — на нём считаются ложные срабатывания.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field

from .helpers import make_valid_number

LEXICON_FIRST = [
    "Айгуль", "Ержан", "Асель", "Нурлан", "Динара", "Арман", "Мадина", "Дамир",
    "Сергей", "Елена", "Алексей", "Ольга", "Дмитрий", "Татьяна", "Жанна", "Тимур",
]
HELDOUT_FIRST = [
    "Бегайым", "Алтынай", "Ербулат", "Жансая", "Еламан", "Ажар", "Ақжан",
    "Ростислав", "Всеволод", "Злата", "Василиса", "Ринат", "Эмиль", "Камиль",
]
SURNAMES = [
    "Ахметова", "Смагулов", "Жумабаев", "Иванов", "Кузнецова", "Ким", "Шевченко",
    "Ковальчук", "Абдрахманов", "Нурпеисова", "Тулегенов", "Сейткали", "Омарова",
]
PATRONYMICS = ["Серикқызы", "Нурланулы", "Петрович", "Сергеевна", "Болатулы"]

PERSON_TEMPLATES = [
    "Клиент {P} просит вернуть оплату за услугу.",
    "{P} прислала договор на согласование, ответь до пятницы.",
    "Подготовь ответ для {P} по жалобе на задержку.",
    "Согласовано с {P}, можно отправлять.",
    "В отпуске до понедельника: {P}.",
    "Акт подписал {P}, оригинал у курьера.",
]

FULL_TEMPLATES = [
    "Сотрудник {P}, ИИН {IIN}, телефон {PHONE}, почта {EMAIL}.",
    "Заявитель {P} проживает по адресу: {ADDRESS}. Паспорт {ID_DOC}.",
    "Анкета: {P}, дата рождения: {BIRTHDATE}, контакт {PHONE}.",
    "Служебный автомобиль {PLATE} закреплён за {P}. Связь: {EMAIL}.",
    "Удостоверение личности № {ID_DOC9}, выдано МВД РК. Владелец {P}.",
]

NEGATIVES = [
    "Филиал в Северном Казахстане открыт с понедельника.",
    "Отчёт Алматы Менеджмент за третий квартал готов.",
    "Евразийский Банк повысил ставки по депозитам.",
    "Встреча в Министерстве Финансов перенесена на среду.",
    "Вера в успех команды — наш главный актив.",
    "Народный Банк Казахстана выпустил новые карты.",
    "Проект Астана Хаб принимает заявки до конца месяца.",
    "Компания Казахтелеком Онлайн обновила тарифы.",
    "Договор от 12.03.2025 продлён ещё на год.",
    "Артикул 041234567 снят с производства.",
    "Совет директоров одобрил бюджет на 2027 год.",
    "Сервер Prod Cluster перезапущен в 03:00.",
    "Доставка по Республике Казахстан бесплатная.",
    "Курс Python Основы стартует в ноябре.",
    "Отгрузка 120 штук, и пр. 5 штук на складе.",
    "Проверка Google Workspace и Microsoft Entra завершена.",
    "Партнёр Kaspi Магазин продлил договор.",
    "Ақ Жол и Жана Арка — названия наших складов.",
]

PHONES = ["+7 701 123 45 67", "8 (727) 250-10-10", "+77017654321", "+998 90 123 45 67", "8-705-555-12-34"]
EMAILS = ["a.ahmetova@company.kz", "ivanov@mail.ru", "info.client+1@gmail.com", "d.kim@bank.kz"]
ADDRESSES = [
    "г. Алматы, ул. Абая, д. 10, кв. 5",
    "мкр. Самал-2, 58",
    "050000, г. Алматы, пр. Достык 97",
    "ул. Розыбакиева 247а, оф. 12",
    "Абай даңғылы, 150",
    "г. Астана, проспект Республики 1/2",
]
ID_DOCS = ["N12345678", "N09876543"]
ID_DOCS_9 = ["041234567", "039876543"]
BIRTHDATES = ["12.03.1985", "5 марта 1990 г.", "1979-11-02"]
PLATES = ["123 ABC 02", "777 KZA 01", "045 BCD 17"]


@dataclass
class Doc:
    text: str
    # (тип, начало, конец, пул имени: "lexicon" | "heldout" | "")
    gold: list[tuple[str, int, int, str]] = field(default_factory=list)


def _fill(template: str, values: dict[str, tuple[str, str, str]]) -> Doc:
    """values: слот -> (значение, тип сущности, пул)."""
    text = ""
    gold: list[tuple[str, int, int, str]] = []
    rest = template
    while "{" in rest:
        before, _, after = rest.partition("{")
        slot, _, rest = after.partition("}")
        text += before
        value, etype, pool = values[slot]
        gold.append((etype, len(text), len(text) + len(value), pool))
        text += value
    text += rest
    return Doc(text=text, gold=gold)


def _person(rng: random.Random) -> tuple[str, str]:
    pool = rng.choice(["lexicon", "heldout"])
    first = rng.choice(LEXICON_FIRST if pool == "lexicon" else HELDOUT_FIRST)
    surname = rng.choice(SURNAMES)
    shape = rng.randrange(4)
    if shape == 0:
        name = f"{first} {surname}"
    elif shape == 1:
        name = f"{surname} {first}"
    elif shape == 2:
        name = f"{surname} {first} {rng.choice(PATRONYMICS)}"
    else:
        name = f"{surname} {first[0]}.{rng.choice('АБВГДЕЖКМНС')}."
    return name, pool


def _iin(rng: random.Random) -> str:
    while True:
        prefix = f"{rng.randrange(60, 99):02d}{rng.randrange(1, 13):02d}{rng.randrange(1, 29):02d}3{rng.randrange(0, 9999):04d}"
        full = make_valid_number(prefix)
        if full:
            return full


def build_corpus(n: int = 500, seed: int = 2026) -> tuple[list[Doc], list[str]]:
    rng = random.Random(seed)
    docs: list[Doc] = []
    for i in range(n):
        name, pool = _person(rng)
        values = {
            "P": (name, "PERSON", pool),
            "IIN": (_iin(rng), "IIN", ""),
            "PHONE": (rng.choice(PHONES), "PHONE", ""),
            "EMAIL": (rng.choice(EMAILS), "EMAIL", ""),
            "ADDRESS": (rng.choice(ADDRESSES), "ADDRESS", ""),
            "ID_DOC": (rng.choice(ID_DOCS), "ID_DOC", ""),
            "ID_DOC9": (rng.choice(ID_DOCS_9), "ID_DOC", ""),
            "BIRTHDATE": (rng.choice(BIRTHDATES), "BIRTHDATE", ""),
            "PLATE": (rng.choice(PLATES), "PLATE", ""),
        }
        templates = PERSON_TEMPLATES if i % 2 == 0 else FULL_TEMPLATES
        docs.append(_fill(rng.choice(templates), values))
    return docs, list(NEGATIVES)
