"""Сквозные тесты конвейера — критерии приёмки MVP (раздел 11 ТЗ):
1. Наружу уходит текст без единого чувствительного значения.
2. Ответ LLM возвращается с корректно восстановленными значениями.
5. При сбое обработки срабатывает fail-closed.
"""

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from gateway.core.audit import AuditLog
from gateway.core.mapping_store import MappingStore
from gateway.core.pipeline import Pipeline
from gateway.core.policy import PolicyEngine
from gateway.models import Channel, CheckRequest, Verdict

from .helpers import VALID_BIN, VALID_IIN, FakeForwarder


class PipelineTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        tmp = Path(self._tmp.name)
        self.forwarder = FakeForwarder()
        self.pipeline = Pipeline(
            policy_engine=PolicyEngine(tmp / "policy.json"),
            mapping_store=MappingStore(),
            forwarder=self.forwarder,
            audit=AuditLog(tmp / "audit.log"),
            fail_closed=True,
        )

    def test_nothing_sensitive_leaves_gateway(self):
        """Критерий 1: «перехват исходящего трафика» — заглушка провайдера."""
        text = (
            f"Клиент Ержан Нурсултанулы, ИИН {VALID_IIN}, БИН работодателя "
            f"{VALID_BIN}, карта 4111 1111 1111 1111. Составь письмо."
        )
        result = self.pipeline.process(CheckRequest(text=text, channel=Channel.EGRESS))
        outbound = self.forwarder.sent[0]
        for sensitive in (VALID_IIN, VALID_BIN, "4111", "Нурсултанулы"):
            self.assertNotIn(sensitive, outbound)
        self.assertEqual(result.verdict, Verdict.MASKED)

    def test_answer_restored(self):
        """Критерий 2: значения в ответе восстановлены."""
        result = self.pipeline.process(
            CheckRequest(text=f"Проверь ИИН {VALID_IIN}", channel=Channel.API)
        )
        self.assertIn(VALID_IIN, result.answer)
        self.assertNotIn("[IIN_1]", result.answer)

    def test_mapping_purged_after_answer(self):
        result = self.pipeline.process(
            CheckRequest(text=f"ИИН {VALID_IIN}", channel=Channel.API)
        )
        self.assertIsNone(self.pipeline.store.resolve(result.session_id, "[IIN_1]"))

    def test_secret_blocked_by_default(self):
        result = self.pipeline.process(
            CheckRequest(
                text="почини: OPENAI_API_KEY=sk-proj-Ab12Cd34Ef56Gh78Ij90KlMnOp",
                channel=Channel.EGRESS,
            )
        )
        self.assertEqual(result.verdict, Verdict.BLOCKED)
        self.assertEqual(self.forwarder.sent, [])  # наружу ничего не ушло

    def test_clean_text_passes(self):
        result = self.pipeline.process(
            CheckRequest(text="Напиши хайку про степь", channel=Channel.BROWSER)
        )
        self.assertEqual(result.verdict, Verdict.CLEAN)
        self.assertIn("хайку", self.forwarder.sent[0])

    def test_fail_closed_on_detector_error(self):
        """Критерий 5: сбой детекции -> блок, а не пропуск."""
        with mock.patch(
            "gateway.core.pipeline.detect_all",
            side_effect=RuntimeError("detector crashed"),
        ):
            result = self.pipeline.process(
                CheckRequest(text=f"ИИН {VALID_IIN}", channel=Channel.EGRESS)
            )
        self.assertEqual(result.verdict, Verdict.FAIL_CLOSED)
        self.assertEqual(self.forwarder.sent, [])

    def test_provider_error_fail_closed(self):
        self.pipeline.forwarder = FakeForwarder(fail=True)
        result = self.pipeline.process(
            CheckRequest(text=f"ИИН {VALID_IIN}", channel=Channel.API)
        )
        self.assertEqual(result.verdict, Verdict.FAIL_CLOSED)
        self.assertIsNone(result.answer)

    def test_dry_run_returns_masked_text(self):
        """Браузерный канал: dry_run возвращает обезличенный текст, маппинг жив."""
        result = self.pipeline.process(
            CheckRequest(text=f"ИИН {VALID_IIN}", channel=Channel.BROWSER, dry_run=True)
        )
        self.assertEqual(result.verdict, Verdict.MASKED)
        self.assertNotIn(VALID_IIN, result.masked_text)
        restored = self.pipeline.restore(
            result.session_id, f"итог: {result.masked_text}"
        )
        self.assertIn(VALID_IIN, restored)
        self.assertIsNone(self.pipeline.store.resolve(result.session_id, "[IIN_1]"))

    def test_audit_written_for_each_request(self):
        self.pipeline.process(
            CheckRequest(text=f"ИИН {VALID_IIN}", channel=Channel.API)
        )
        records = self.pipeline.audit.read_all()
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].verdict, Verdict.MASKED.value)
        self.assertEqual(records[0].entity_counts, {"IIN": 1})
        ok, _ = self.pipeline.audit.verify_chain()
        self.assertTrue(ok)


if __name__ == "__main__":
    unittest.main()
