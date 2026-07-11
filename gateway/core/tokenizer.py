"""Обратимая токенизация (F3) и обратная подстановка (F6)."""

from __future__ import annotations

import re

from gateway.models import Detection

from .mapping_store import MappingStore

_TOKEN_RE = re.compile(r"\[(?:IIN|BIN|PERSON|IBAN|CARD|SECRET|AMOUNT)_\d+\]")


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
