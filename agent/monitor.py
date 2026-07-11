"""Монитор буфера обмена — ядро endpoint-агента (Э1.5).

Цикл на каждое изменение буфера:

  1. Плейсхолдеры ([IIN_1]...) при активной сессии -> детокенизация
     через шлюз (ответ LLM, скопированный из локального приложения).
  2. Локальный детект (общие детекторы ядра, без сети). Чисто -> пропуск.
  3. Найдено чувствительное -> маскирование через шлюз (центральная
     политика + аудит), буфер подменяется обезличенным текстом.
  4. Политика block -> буфер очищается.
  5. Шлюз недоступен -> fail-closed: буфер с чувствительными данными
     очищается (оригинал остаётся в quarantine в памяти агента).
"""

from __future__ import annotations

import logging
import time

from gateway.core.detectors import detect_all
from gateway.core.tokenizer import contains_tokens

from .client import GatewayClient, GatewayUnavailable
from .clipboard import ClipboardBackend, ClipboardError
from .config import AgentConfig
from .notify import notify

logger = logging.getLogger("aigate.agent")

BLOCKED_NOTICE = "[AI-Gate] Содержимое заблокировано политикой безопасности"
FAIL_CLOSED_NOTICE = (
    "[AI-Gate] Шлюз недоступен: чувствительное содержимое изъято из буфера "
    "(fail-closed)"
)


class ClipboardMonitor:
    def __init__(
        self,
        config: AgentConfig,
        clipboard: ClipboardBackend,
        client: GatewayClient | None = None,
    ):
        self._cfg = config
        self._clip = clipboard
        self._client = client or GatewayClient(config)
        self._last_seen: str | None = None
        # session_id последнего маскирования — для детокенизации ответа
        self._active_session: str | None = None
        # оригинал, изъятый по fail-closed/block (только в памяти агента)
        self.quarantine: str | None = None

    # --- один проход (вынесен для тестируемости) -----------------------------

    def poll_once(self) -> str | None:
        """Обработать текущее содержимое буфера.

        Возвращает вердикт обработки ('masked', 'blocked', 'restored',
        'fail_closed', 'clean') или None, если буфер не менялся/пуст.
        """
        try:
            text = self._clip.read()
        except ClipboardError as e:
            logger.warning("%s", e)
            return None

        if text == self._last_seen or not text.strip():
            return None
        self._last_seen = text

        if len(text) < self._cfg.min_length or len(text) > self._cfg.max_length:
            return None

        # 1. ответ LLM с плейсхолдерами -> восстановить значения
        if (
            self._cfg.restore_enabled
            and self._active_session
            and contains_tokens(text)
        ):
            return self._restore(text)

        # 2. локальный детект без сети
        try:
            detections = detect_all(text)
        except Exception:
            logger.exception("Сбой локального детекта")
            detections = None  # неизвестно -> считаем чувствительным (fail-safe)

        if detections == []:
            return "clean"

        # 3-5. чувствительное содержимое -> шлюз / fail-closed
        return self._sanitize(text)

    # --- обработчики -----------------------------------------------------------

    def _sanitize(self, text: str) -> str:
        try:
            result = self._client.check(text)
        except GatewayUnavailable as e:
            if self._cfg.fail_closed:
                self.quarantine = text
                self._write(FAIL_CLOSED_NOTICE)
                notify(
                    "AI-Gate: fail-closed",
                    f"Шлюз недоступен, буфер изъят. {e}",
                )
                return "fail_closed"
            notify("AI-Gate: предупреждение", f"Шлюз недоступен: {e}")
            return "clean"

        verdict = result.get("verdict")
        if verdict == "masked":
            self._active_session = result.get("session_id")
            self._write(result["masked_text"])
            counts = result.get("entity_counts", {})
            notify(
                "AI-Gate: замаскировано",
                ", ".join(f"{t}×{c}" for t, c in counts.items()),
            )
            return "masked"
        if verdict in ("blocked", "fail_closed"):
            self.quarantine = text
            self._write(BLOCKED_NOTICE)
            notify(
                "AI-Gate: заблокировано",
                result.get("message") or "Политика запрещает передачу этих данных",
            )
            return "blocked"
        # clean / allowed — буфер не трогаем
        return "clean"

    def _restore(self, text: str) -> str:
        try:
            restored = self._client.restore(self._active_session, text, purge=False)
        except GatewayUnavailable as e:
            logger.warning("Детокенизация недоступна: %s", e)
            return "clean"
        if restored != text:
            self._write(restored)
            notify("AI-Gate: восстановлено", "Значения в ответе подставлены обратно")
            return "restored"
        return "clean"

    def _write(self, text: str) -> None:
        try:
            self._clip.write(text)
            self._last_seen = text  # не реагировать на собственную запись
        except ClipboardError as e:
            logger.error("Не удалось обновить буфер: %s", e)

    # --- основной цикл -----------------------------------------------------------

    def run_forever(self) -> None:
        logger.info(
            "Endpoint-агент запущен: шлюз=%s, fail_closed=%s, интервал=%.2fс",
            self._cfg.gateway_url,
            self._cfg.fail_closed,
            self._cfg.poll_interval,
        )
        while True:
            try:
                self.poll_once()
            except Exception:
                logger.exception("Ошибка цикла мониторинга")
            time.sleep(self._cfg.poll_interval)
