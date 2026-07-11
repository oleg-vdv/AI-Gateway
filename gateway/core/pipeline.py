"""Общее ядро (shared pipeline, раздел 4.1 ТЗ):

    Ingress -> Detector -> Tokenizer -> Policy -> Forwarder -> Detokenizer -> Audit

Все каналы (браузер, egress, API) сводятся к единому CheckRequest и
проходят один и тот же конвейер. Fail-closed: любая ошибка на этапах
детекта/токенизации/политики приводит к блокировке запроса, а не к
пропуску «как есть» (раздел 4.2 ТЗ).
"""

from __future__ import annotations

import logging
import uuid

from gateway.models import CheckRequest, CheckResponse, Verdict

from .audit import AuditLog
from .detectors import detect_all
from .forwarder import Forwarder, ProviderError
from .mapping_store import MappingStore
from .policy import PolicyEngine
from .tokenizer import detokenize, tokenize

logger = logging.getLogger("aigate.pipeline")


class Pipeline:
    def __init__(
        self,
        policy_engine: PolicyEngine,
        mapping_store: MappingStore,
        forwarder: Forwarder,
        audit: AuditLog,
        fail_closed: bool = True,
    ):
        self.policy = policy_engine
        self.store = mapping_store
        self.forwarder = forwarder
        self.audit = audit
        self.fail_closed = fail_closed

    def sanitize(self, req: CheckRequest) -> CheckResponse:
        """Этапы Detect -> Policy -> Tokenize (без форварда).

        Используется браузерным каналом (dry_run) и как первая половина
        полного прогона process().
        """
        request_id = uuid.uuid4().hex
        session_id = req.session_id or request_id
        try:
            detections = detect_all(req.text)
            entity_counts: dict[str, int] = {}
            for det in detections:
                key = det.entity_type.value
                entity_counts[key] = entity_counts.get(key, 0) + 1

            actions, to_mask, blocked, allow_basis = self.policy.evaluate(
                detections, req.channel, req.group
            )
            actions_str = {k: v.value for k, v in actions.items()}

            if blocked:
                self.audit.append(
                    user=req.user,
                    channel=req.channel,
                    provider=self.forwarder.provider_name,
                    verdict=Verdict.BLOCKED,
                    entity_counts=entity_counts,
                    policy_actions=actions_str,
                    request_id=request_id,
                )
                blocked_types = ", ".join(
                    t for t, a in actions_str.items() if a == "block"
                )
                return CheckResponse(
                    verdict=Verdict.BLOCKED,
                    request_id=request_id,
                    session_id=session_id,
                    entity_counts=entity_counts,
                    message="Запрос заблокирован политикой: обнаружены данные, "
                    f"запрещённые к отправке во внешние LLM ({blocked_types}).",
                )

            masked_text = tokenize(req.text, to_mask, self.store, session_id)

            if not detections:
                verdict = Verdict.CLEAN
            elif allow_basis and not to_mask:
                verdict = Verdict.ALLOWED
            else:
                verdict = Verdict.MASKED

            self.audit.append(
                user=req.user,
                channel=req.channel,
                provider=self.forwarder.provider_name,
                verdict=verdict,
                entity_counts=entity_counts,
                policy_actions=actions_str,
                cross_border_basis=allow_basis,
                request_id=request_id,
            )
            return CheckResponse(
                verdict=verdict,
                request_id=request_id,
                session_id=session_id,
                masked_text=masked_text,
                entity_counts=entity_counts,
            )
        except Exception:
            logger.exception("Ошибка конвейера (request_id=%s)", request_id)
            # Fail-closed: ошибка детекции/парсинга -> блок, не пропуск
            try:
                self.audit.append(
                    user=req.user,
                    channel=req.channel,
                    provider=self.forwarder.provider_name,
                    verdict=Verdict.FAIL_CLOSED,
                    entity_counts={},
                    policy_actions={},
                    request_id=request_id,
                )
            except Exception:
                logger.exception("Не удалось записать аудит fail-closed")
            if self.fail_closed:
                return CheckResponse(
                    verdict=Verdict.FAIL_CLOSED,
                    request_id=request_id,
                    session_id=session_id,
                    message="Fail-closed: внутренняя ошибка обработки, "
                    "запрос заблокирован.",
                )
            raise

    def process(self, req: CheckRequest) -> CheckResponse:
        """Полный прогон: sanitize -> Forwarder -> Detokenizer -> purge."""
        result = self.sanitize(req)
        if result.verdict in (Verdict.BLOCKED, Verdict.FAIL_CLOSED):
            self.store.purge_session(result.session_id)
            return result
        if req.dry_run:
            # dry_run: канал сам отправляет обезличенный текст; маппинг
            # остаётся жить до /v1/restore или до истечения TTL.
            return result

        try:
            answer = self.forwarder.complete(result.masked_text or req.text)
            result.answer = detokenize(answer, self.store, result.session_id)
            return result
        except ProviderError as e:
            result.verdict = Verdict.FAIL_CLOSED
            result.message = str(e)
            result.answer = None
            return result
        finally:
            # гарантированное уничтожение маппинга после ответа (F3/F6)
            self.store.purge_session(result.session_id)

    def restore(self, session_id: str, text: str, purge: bool = True) -> str:
        """Обратная подстановка для dry_run-каналов (браузер)."""
        restored = detokenize(text, self.store, session_id)
        if purge:
            self.store.purge_session(session_id)
        return restored
