"""Обратимая токенизация (F3) и обратная подстановка (F6)."""

from __future__ import annotations

import re

from gateway.models import Detection, EntityType

from .mapping_store import MappingStore

# Строится из EntityType, чтобы новый тип сущности не забыть добавить сюда:
# незнакомый токен остался бы в ответе пользователю как есть.
_TOKEN_RE = re.compile(
    r"\[(?:" + "|".join(sorted((t.value for t in EntityType), key=len, reverse=True)) + r")_\d+\]"
)


def tokenize(
    text: str, detections: list[Detection], store: MappingStore, session_id: str
) -> str:
    """Заменить найденные сущности на типизированные плейсхолдеры.

    Замена справа налево, чтобы не сдвигать позиции ещё не обработанных
    сущностей. Одно значение в пределах сессии всегда даёт один токен.
    """
    out = text
    for det in sorted(detections, key=lambda d: d.start, reverse=True):
        token = store.get_or_create_token(session_id, det.value, det.entity_type)
        out = out[: det.start] + token + out[det.end :]
    return out


def detokenize(text: str, store: MappingStore, session_id: str) -> str:
    """Заменить плейсхолдеры в ответе LLM обратно на исходные значения."""

    def _replace(m: re.Match) -> str:
        value = store.resolve(session_id, m.group())
        return value if value is not None else m.group()

    return _TOKEN_RE.sub(_replace, text)


def contains_tokens(text: str) -> bool:
    return bool(_TOKEN_RE.search(text))


# Хвост, который может оказаться началом токена: «[», «[PER», «[PERSON_1».
_PARTIAL_TOKEN_RE = re.compile(r"\[[A-Z_]*\d*$")
_MAX_TOKEN_LEN = max(len(t.value) for t in EntityType) + 12  # «[», «_», до 10 цифр, «]»


class StreamDetokenizer:
    """Обратная подстановка для потокового ответа (SSE).

    Провайдер режет текст на куски где угодно, в том числе посреди токена:
    «Клиент [PERS» + «ON_1] просит». Выпускать «[PERS» нельзя — пользователь
    увидит обрывок метки, а следующий кусок уже не с чем будет склеить.
    Поэтому хвост, похожий на начало токена, придерживается до следующего
    куска, а всё остальное отдаётся сразу с восстановленными значениями.
    """

    def __init__(self, store: MappingStore, session_id: str):
        self._store = store
        self._session_id = session_id
        self._pending = ""

    def feed(self, chunk: str) -> str:
        text = self._pending + chunk
        m = _PARTIAL_TOKEN_RE.search(text)
        if m and len(text) - m.start() <= _MAX_TOKEN_LEN:
            self._pending, text = text[m.start() :], text[: m.start()]
        else:
            self._pending = ""
        return detokenize(text, self._store, self._session_id)

    def flush(self) -> str:
        text, self._pending = self._pending, ""
        return detokenize(text, self._store, self._session_id)
