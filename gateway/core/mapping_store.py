"""Mapping Store: локальная эфемерная таблица «токен <-> значение» (F3).

Требования (разделы 3.3, 8 ТЗ):
  * только в памяти процесса, внутри периметра (ст. 12 Закона № 94-V);
  * значения зашифрованы (см. core.crypto) — дамп памяти/своп не отдаёт
    открытый текст напрямую;
  * TTL + гарантированное уничтожение после ответа (purge_session);
  * консистентность в пределах сессии: одно значение -> один токен.
"""

from __future__ import annotations

import threading
import time

from gateway.models import EntityType, MappingEntry

from .crypto import SecretBox


class MappingStore:
    def __init__(self, encryption_key: str = "", ttl_seconds: int = 600):
        self._box = SecretBox(encryption_key)
        self._ttl = ttl_seconds
        self._lock = threading.Lock()
        # session_id -> {token -> MappingEntry}
        self._sessions: dict[str, dict[str, MappingEntry]] = {}
        # session_id -> {value -> token}  (консистентность значение->токен)
        self._reverse: dict[str, dict[str, str]] = {}
        # session_id -> счётчики по типам для нумерации плейсхолдеров
        self._counters: dict[str, dict[str, int]] = {}

    def get_or_create_token(
        self, session_id: str, value: str, entity_type: EntityType
    ) -> str:
        """Вернуть существующий токен для значения или создать новый."""
        with self._lock:
            self._evict_expired_locked()
            rev = self._reverse.setdefault(session_id, {})
            if value in rev:
                return rev[value]
            counters = self._counters.setdefault(session_id, {})
            counters[entity_type.value] = counters.get(entity_type.value, 0) + 1
            token = f"[{entity_type.value}_{counters[entity_type.value]}]"
            entry = MappingEntry(
                token=token,
                encrypted_value=self._box.encrypt(value.encode()),
                entity_type=entity_type,
                session_id=session_id,
            )
            self._sessions.setdefault(session_id, {})[token] = entry
            rev[value] = token
            return token

    def resolve(self, session_id: str, token: str) -> str | None:
        """Токен -> исходное значение (или None, если нет/истёк TTL)."""
        with self._lock:
            self._evict_expired_locked()
            entry = self._sessions.get(session_id, {}).get(token)
            if entry is None:
                return None
            return self._box.decrypt(entry.encrypted_value).decode()

    def tokens_for_session(self, session_id: str) -> list[str]:
        with self._lock:
            self._evict_expired_locked()
            return list(self._sessions.get(session_id, {}).keys())

    def purge_session(self, session_id: str) -> None:
        """Гарантированное уничтожение маппинга после ответа."""
        with self._lock:
            self._sessions.pop(session_id, None)
            self._reverse.pop(session_id, None)
            self._counters.pop(session_id, None)

    def _evict_expired_locked(self) -> None:
        now = time.time()
        for sid in list(self._sessions.keys()):
            entries = self._sessions[sid]
            expired = [t for t, e in entries.items() if now - e.created_at > self._ttl]
            for token in expired:
                del entries[token]
            if not entries:
                self._sessions.pop(sid, None)
                self._reverse.pop(sid, None)
                self._counters.pop(sid, None)
