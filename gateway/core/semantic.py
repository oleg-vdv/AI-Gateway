"""Семантический детект контекстных утечек (Э3): LLM-judge.

Шаблонные детекторы не видят утечек «по смыслу» («наш крупнейший клиент
уходит к конкуренту в сентябре»). SemanticGuard прогоняет УЖЕ
ОБЕЗЛИЧЕННЫЙ текст через выделенного судью (рекомендуется локальная LLM —
текст не покидает периметр) и возвращает вердикт.

Режимы (AIGATE_SEMANTIC_GUARD):
  * off   — выключен (по умолчанию: латентность +1 вызов LLM);
  * flag  — подозрительные запросы помечаются в аудите, но проходят;
  * block — подозрительные запросы блокируются; недоступность судьи
            в этом режиме = fail-closed (блок).
"""

from __future__ import annotations

import logging

logger = logging.getLogger("aigate.semantic")

JUDGE_PROMPT = (
    "Ты — DLP-классификатор. Персональные данные и реквизиты уже заменены "
    "плейсхолдерами вида [IIN_1]. Определи, содержит ли текст ДРУГУЮ "
    "конфиденциальную информацию организации, раскрытие которой вовне "
    "недопустимо: коммерческие планы, финансовые показатели, сведения о "
    "сделках/клиентах/увольнениях, внутренние инциденты, исходный код "
    "закрытых систем. Обычные рабочие вопросы, общие знания и учебные "
    "примеры конфиденциальными НЕ являются.\n"
    "Ответь ровно одним словом: YES (конфиденциально) или NO."
)


class SemanticGuard:
    def __init__(self, forwarder, mode: str = "flag", provider: str | None = None):
        if mode not in ("off", "flag", "block"):
            raise ValueError(f"Неизвестный режим SemanticGuard: {mode}")
        self._forwarder = forwarder
        self.mode = mode
        self._provider = provider or None

    def review(self, masked_text: str) -> bool:
        """True = судья счёл текст конфиденциальным.

        Исключения пробрасываются наверх — политику обработки недоступности
        судьи (fail-closed в режиме block) определяет вызывающий код.
        """
        answer = self._forwarder.chat(
            [
                {"role": "system", "content": JUDGE_PROMPT},
                {"role": "user", "content": masked_text},
            ],
            provider=self._provider,
        )
        verdict = answer.strip().upper()
        suspicious = verdict.startswith("YES") or verdict.startswith("ДА")
        if not (verdict.startswith(("YES", "NO", "ДА", "НЕТ"))):
            # неожиданный ответ судьи — считаем подозрительным (fail-safe)
            logger.warning("Неожиданный ответ судьи: %r", answer[:80])
            suspicious = True
        return suspicious
