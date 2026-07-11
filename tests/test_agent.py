"""Endpoint-агент (Э1.5): маскирование буфера обмена через шлюз,
блокировка секретов, fail-closed при недоступном шлюзе, детокенизация
ответа LLM. Шлюз поднимается на реальном порту, буфер — фейковый."""

import tempfile
import threading
import unittest
from pathlib import Path

from agent.client import GatewayClient
from agent.clipboard import FakeClipboard
from agent.config import AgentConfig
from agent.monitor import BLOCKED_NOTICE, FAIL_CLOSED_NOTICE, ClipboardMonitor
from gateway.config import Settings
from gateway.core.audit import AuditLog
from gateway.core.mapping_store import MappingStore
from gateway.core.pipeline import Pipeline
from gateway.core.policy import PolicyEngine
from gateway.server import make_server

from .helpers import VALID_IIN, FakeForwarder

CHANNEL_KEY = "agent-key"


class AgentTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        tmp = Path(cls._tmp.name)
        settings = Settings(
            host="127.0.0.1",
            port=0,
            channel_api_keys=CHANNEL_KEY,
            policy_path=tmp / "policy.json",
            audit_log_path=tmp / "audit.log",
        )
        cls.pipeline = Pipeline(
            policy_engine=PolicyEngine(settings.policy_path),
            mapping_store=MappingStore(),
            forwarder=FakeForwarder(),
            audit=AuditLog(settings.audit_log_path),
        )
        cls.server = make_server(cls.pipeline, settings)
        cls.gateway_url = f"http://127.0.0.1:{cls.server.server_address[1]}"
        cls._thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls._thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls._tmp.cleanup()

    def _monitor(self, clipboard, **cfg_overrides) -> ClipboardMonitor:
        config = AgentConfig(
            gateway_url=cfg_overrides.pop("gateway_url", self.gateway_url),
            channel_key=CHANNEL_KEY,
            **cfg_overrides,
        )
        return ClipboardMonitor(config, clipboard, GatewayClient(config))

    # --- сценарии -----------------------------------------------------------

    def test_iin_in_clipboard_masked(self):
        clip = FakeClipboard(f"Договор: ИИН {VALID_IIN}, оформить")
        monitor = self._monitor(clip)
        self.assertEqual(monitor.poll_once(), "masked")
        self.assertNotIn(VALID_IIN, clip.content)
        self.assertIn("[IIN_1]", clip.content)

    def test_clean_text_untouched(self):
        clip = FakeClipboard("Обычная заметка о встрече в четверг")
        monitor = self._monitor(clip)
        self.assertEqual(monitor.poll_once(), "clean")
        self.assertEqual(clip.history, [])  # буфер не перезаписывался

    def test_secret_blocked_and_quarantined(self):
        secret_text = "конфиг: OPENAI_API_KEY=sk-proj-Ab12Cd34Ef56Gh78Ij90KlMnOp"
        clip = FakeClipboard(secret_text)
        monitor = self._monitor(clip)
        self.assertEqual(monitor.poll_once(), "blocked")
        self.assertEqual(clip.content, BLOCKED_NOTICE)
        self.assertEqual(monitor.quarantine, secret_text)

    def test_fail_closed_when_gateway_down(self):
        sensitive = f"ИИН {VALID_IIN} клиента"
        clip = FakeClipboard(sensitive)
        monitor = self._monitor(clip, gateway_url="http://127.0.0.1:1")  # закрытый порт
        self.assertEqual(monitor.poll_once(), "fail_closed")
        self.assertEqual(clip.content, FAIL_CLOSED_NOTICE)
        self.assertEqual(monitor.quarantine, sensitive)

    def test_gateway_down_clean_text_untouched(self):
        clip = FakeClipboard("Ничего чувствительного здесь нет вообще")
        monitor = self._monitor(clip, gateway_url="http://127.0.0.1:1")
        self.assertEqual(monitor.poll_once(), "clean")
        self.assertEqual(clip.history, [])  # локальный детект не тронул буфер

    def test_restore_llm_answer(self):
        # шаг 1: маскирование
        clip = FakeClipboard(f"Проверь ИИН {VALID_IIN}")
        monitor = self._monitor(clip)
        self.assertEqual(monitor.poll_once(), "masked")
        masked = clip.content

        # шаг 2: пользователь «скопировал ответ LLM» с плейсхолдером
        clip.content = f"Ответ ассистента: по {masked.split()[-1]} всё корректно"
        self.assertEqual(monitor.poll_once(), "restored")
        self.assertIn(VALID_IIN, clip.content)

    def test_same_content_processed_once(self):
        clip = FakeClipboard(f"ИИН {VALID_IIN}")
        monitor = self._monitor(clip)
        self.assertEqual(monitor.poll_once(), "masked")
        masked = clip.content
        # повторные опросы того же содержимого — no-op
        self.assertIsNone(monitor.poll_once())
        self.assertEqual(clip.content, masked)

    def test_short_text_skipped(self):
        clip = FakeClipboard("1234567")  # короче min_length=8
        monitor = self._monitor(clip)
        self.assertIsNone(monitor.poll_once())

    def test_audit_channel_is_endpoint(self):
        clip = FakeClipboard(f"аудит-проверка ИИН {VALID_IIN}")
        monitor = self._monitor(clip)
        monitor.poll_once()
        records = self.pipeline.audit.read_all()
        self.assertTrue(any(r.channel == "endpoint" for r in records))


if __name__ == "__main__":
    unittest.main()
