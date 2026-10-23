"""Детект ФИО, включая казахские форматы (-улы/-кызы/-тегі).

Два слоя, оба без ML:

1. Паттерны: русские отчества (-ович/-евна/...), казахские патронимы
   (-улы/-кызы/-тегі и латиница -uly/-kyzy), инициалы, контекст
   («клиент: ...», «Mr ...»).
2. Словарь (name_lexicon): известное имя рядом со словом, похожим на
   фамилию, в любом порядке — «Айгуль Ахметова», «Ахметова Айгуль»,
   «Aigul Akhmetova». Пара двух произвольных слов с заглавной буквы
   («Северный Казахстан») не маскируется.

После сбора кандидатов каждый найденный фрагмент расширяется на соседние
слова, которые сами являются частью ФИО (фамилия перед «Имя Патроним»,
фамилия после «Имя Отчество», инициалы), и из пересекающихся кандидатов
остаётся самый длинный — иначе часть ФИО ушла бы наружу открытым текстом.
"""

from __future__ import annotations

import re

from gateway.models import Detection, EntityType

from .name_lexicon import is_first_name, looks_like_surname

_UP = "А-ЯЁӘҒҚҢӨҰҮҺІA-Z"
_LOW = "а-яёәғқңөұүһіa-zıäöü"

# Слово с заглавной буквы (кириллица, вкл. казахские буквы, или латиница)
_CAP = rf"[{_UP}][{_LOW}]+(?:-[{_UP}][{_LOW}]+)?"

# Казахские патронимы: Нурсултанулы, Айгеримкызы, Ахметтегі; латиница — Nursultanuly
_KZ_PATRONYMIC = rf"{_CAP}(?:улы|ұлы|кызы|қызы|тегі|теги|uly|uli|ulı|kyzy|kizi|qyzy|qızı)"

# Русские отчества (и их латинская запись)
_RU_PATRONYMIC = (
    rf"{_CAP}(?:ович|евич|ьич|ич|овна|евна|ична|инична"
    rf"|ovich|evich|ovna|evna|ichna)"
)

_PATRONYMIC_RE = re.compile(rf"^(?:{_KZ_PATRONYMIC}|{_RU_PATRONYMIC})$")
_INITIALS_RE = re.compile(rf"^[{_UP}]\.(?:\s?[{_UP}]\.)?$")

# Контекстные слова, после которых 2-3 капитализированных слова — это ФИО.
# Двухсловные имена без патронима и без словарного имени ловятся только
# так, чтобы не маскировать любые пары слов с заглавной буквы.
_CONTEXT = (
    r"(?i:ФИО|Ф\.И\.О\.?|клиент|клиентка|заявитель|сотрудник|сотрудница|абонент"
    r"|пациент|пациентка|заемщик|заёмщик|вкладчик|директор|руководитель"
    r"|получатель|отправитель|арендатор|арендодатель|покупатель|продавец|исполнитель"
    r"|тұтынушы|қызметкер|өтініш\s+беруші"
    r"|customer|client|employee|patient|applicant|borrower|tenant|recipient)"
)

# Обращения, после которых одной фамилии достаточно: «г-н Ахметов», «Mr Smith»
_HONORIFIC = (
    r"(?:г-н|г-жа|господин|госпожа|гражданин|гражданка|тов\."
    r"|Mr\.?|Mrs\.?|Ms\.?|Dr\.?)"
)

# (pattern, номер группы со значением ФИО; 0 = весь матч)
_NAME_PATTERNS: list[tuple[re.Pattern, int]] = [
    # Фамилия Имя Отчество (русское отчество)
    (re.compile(rf"\b{_CAP}\s+{_CAP}\s+{_RU_PATRONYMIC}\b"), 0),
    # Имя Отчество Фамилия
    (re.compile(rf"\b{_CAP}\s+{_RU_PATRONYMIC}\s+{_CAP}\b"), 0),
    # Имя Отчество
    (re.compile(rf"\b{_CAP}\s+{_RU_PATRONYMIC}\b"), 0),
    # Казахский формат: Имя + патроним (с фамилией или без)
    (re.compile(rf"\b{_CAP}\s+{_KZ_PATRONYMIC}\b"), 0),
    (re.compile(rf"\b{_KZ_PATRONYMIC}\b"), 0),
    # Фамилия И.О. / И.О. Фамилия
    (re.compile(rf"\b{_CAP}\s+[{_UP}]\.\s?[{_UP}]\.(?!\w)"), 0),
    (re.compile(rf"(?<!\w)[{_UP}]\.\s?[{_UP}]\.\s*{_CAP}\b"), 0),
    # Контекст + Имя Фамилия [Отчество/патроним]
    (re.compile(rf"\b{_CONTEXT}\s*[:—–-]?[ \t]+({_CAP}[ \t]+{_CAP}(?:[ \t]+{_CAP})?)\b"), 1),
    # Обращение + Фамилия [Имя]
    (re.compile(rf"(?<!\w){_HONORIFIC}[ \t]+({_CAP}(?:[ \t]+{_CAP})?)\b"), 1),
]

_WORD_RE = re.compile(_CAP)

# Слова из названий организаций и мест: «Петров Строй», «Евразийский Банк»,
# «Kaspi Магазин» — пары с ними именем не считаются.
_ORG_WORDS = frozenset(
    """
    банк банка банке компания компании холдинг групп груп строй сервис центр центра
    хаб онлайн менеджмент университет институт министерство министерства акимат
    казахстан казахстана казахстане республика республики магазин маркет кластер
    склад филиал офис отдел департамент управление тоо ао ип llp ltd inc bank group
    company holding center centre university institute online service cluster prod
    """.split()
)

# Начала названий органов и организаций в любом падеже: «Министерстве Финансов»
_ORG_PREFIXES = (
    "министерств", "департамент", "управлени", "комитет", "агентств", "корпораци",
    "компани", "университет", "колледж", "школ", "больниц", "поликлиник", "финанс",
)

# Слова, которые часто стоят с заглавной в начале фразы или перед ФИО, и
# города (Алматы совпадает с родительным падежом имени Алмат). Без этого
# списка «Владелец Ковальчук» или «Отчёт Алматы» принимались бы за ФИО.
_COMMON_WORDS = frozenset(
    """
    отчёт отчет встреча сотрудник сотрудница заявитель заявительница владелец владелица
    клиент клиентка анкета акт договор письмо согласовано подготовь служебный служебная
    удостоверение паспорт директор менеджер бухгалтер юрист привет здравствуйте
    уважаемый уважаемая спасибо прошу срочно сегодня завтра вчера пациент пациентка
    абонент заемщик заёмщик вкладчик руководитель получатель отправитель арендатор
    арендодатель покупатель продавец исполнитель подпись ответ вопрос задача
    customer client employee patient applicant borrower tenant recipient dear hello
    алматы алмата астана астаны астане шымкент караганда актобе атырау актау павлодар
    семей костанай кызылорда уральск тараз туркестан петропавловск кокшетау
    талдыкорган экибастуз жезказган москва ташкент бишкек
    """.split()
)

# Прилагательные: «Северный», «Казахстанский», «Евразийская» — не имена
_ADJECTIVE_RE = re.compile(r"(?:ый|ий|ой|ая|яя|ое|ее|ые|ие|ого|ому|ым|ом)$")

# «Сильная» фамилия: окончание, которое у обычных слов почти не встречается.
# -ский/-цкий сюда не входят — так же оканчиваются прилагательные.
_STRONG_SURNAME_RE = re.compile(
    r"(?:(?:ов|ев|ёв|ин|ын)(?:а|у|ой|ым)?|енко|ук|юк|улы|ұлы|кызы|қызы"
    r"|(?:ov|ev|in)a?|enko|uly|kyzy)$"
)


def _is_name_part(word: str) -> bool:
    if _is_stop_word(word):
        return False
    return bool(
        is_first_name(word)
        or looks_like_surname(word)
        or _PATRONYMIC_RE.match(word)
    )


def _is_stop_word(word: str) -> bool:
    w = word.lower()
    return w in _ORG_WORDS or w in _COMMON_WORDS or w.startswith(_ORG_PREFIXES)


def _could_be_name(word: str, strict: bool = True) -> bool:
    """Слово может быть именем, которого нет в словаре.

    strict=False — рядом стоит явная фамилия: тогда допускаются и имена,
    похожие на прилагательные по окончанию (Жансая, Бегайым).
    """
    w = word.lower()
    if _is_stop_word(word) or len(w) < 3:
        return False
    if not strict:
        return True
    return is_first_name(word) or not _ADJECTIVE_RE.search(w)


def _is_strong_surname(word: str) -> bool:
    w = word.lower()
    return not _is_stop_word(word) and (
        bool(_STRONG_SURNAME_RE.search(w)) or looks_like_surname(word) and len(w) <= 4
    )


def _lexicon_candidates(text: str) -> list[tuple[int, int]]:
    """Пары «имя + фамилия» в любом порядке.

    Пара принимается, если одно слово — имя из словаря, а второе похоже на
    фамилию или хотя бы может быть именем; или если одно слово — явная
    фамилия («Тулегенов», «Ковальчук»), а второе может быть именем, которого
    нет в словаре («Тулегенов Ербулат»).
    """
    words = list(_WORD_RE.finditer(text))
    spans: list[tuple[int, int]] = []
    for a, b in zip(words, words[1:]):
        gap = text[a.end() : b.start()]
        if not gap or gap.strip(" \t"):
            continue  # только соседние слова через пробел/таб, не через перенос
        wa, wb = a.group(), b.group()
        known = (is_first_name(wa) and not _is_stop_word(wa) and _could_be_name(wb)) or (
            is_first_name(wb) and not _is_stop_word(wb) and _could_be_name(wa)
        )
        surname = (_is_strong_surname(wa) and _could_be_name(wb, strict=False)) or (
            _is_strong_surname(wb) and _could_be_name(wa, strict=False)
        )
        if known or surname or (is_first_name(wa) and _PATRONYMIC_RE.match(wb)):
            spans.append((a.start(), b.end()))
    return spans


def _extend(text: str, start: int, end: int, max_words: int = 4) -> tuple[int, int]:
    """Расширить фрагмент на соседние части ФИО (фамилия, отчество, инициалы).

    Если во фрагменте есть отчество или патроним, соседнее слово с заглавной
    почти наверняка фамилия, даже без типичного окончания: «Сейткали Жансая
    Болатулы».
    """

    def accepts(token: str) -> bool:
        if _INITIALS_RE.match(token) or _is_name_part(token):
            return True
        has_patronymic = any(_PATRONYMIC_RE.match(w) for w in text[start:end].split())
        return has_patronymic and _could_be_name(token, strict=False)

    # вправо
    while len(text[start:end].split()) < max_words:
        m = re.match(rf"[ \t]+({_CAP}|[{_UP}]\.(?:\s?[{_UP}]\.)?)", text[end:])
        if not m or not accepts(m.group(1)):
            break
        end += m.end()
    # влево
    while len(text[start:end].split()) < max_words:
        m = re.search(rf"(?<!\w)({_CAP})[ \t]+$", text[:start])
        if not m or not accepts(m.group(1)):
            break
        start = m.start(1)
    return start, end


def _trim(text: str, start: int, end: int) -> tuple[int, int]:
    """Срезать служебные слова по краям: «Уважаемый Сергей Иванович» -> «Сергей Иванович»."""
    while True:
        m = re.match(rf"({_CAP})[ \t]+", text[start:end])
        if not m or not _is_stop_word(m.group(1)):
            return start, end
        start += m.end()


def detect_person(text: str) -> list[Detection]:
    candidates: list[tuple[int, int]] = []
    for pattern, group in _NAME_PATTERNS:
        for m in pattern.finditer(text):
            candidates.append((m.start(group), m.end(group)))
    candidates += _lexicon_candidates(text)

    # Пересекающиеся кандидаты объединяются: лучше замаскировать лишнее
    # слово, чем выпустить наружу половину ФИО.
    merged: list[list[int]] = []
    for s, e in sorted(_trim(text, *_extend(text, s, e)) for s, e in candidates):
        if merged and s < merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], e)
        else:
            merged.append([s, e])
    return [
        Detection(
            entity_type=EntityType.PERSON,
            value=text[s:e],
            start=s,
            end=e,
            detector="person_pattern",
        )
        for s, e in merged
    ]
