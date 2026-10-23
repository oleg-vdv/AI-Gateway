"""Страница «Проверь свой текст»: /try и /try/api/preview.

Превью ничего не хранит (ни Mapping Store, ни аудит) и ничего не отправляет
провайдеру; выключается AIGATE_TRY_PAGE=0."""

import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path

from gateway.config import Settings
from gateway.core.audit import AuditLog
from gateway.core.mapping_store import MappingStore
from gateway.core.pipeline import Pipeline
from gateway.core.policy import PolicyEngine
from gateway.server import make_server

from .helpers import VALID_IIN, FakeForwarder


class _TryServer(unittest.TestCase):
    try_page = True

    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        tmp = Path(cls._tmp.name)
        settings = Settings(
            host="127.0.0.1",
            port=0,
            channel_api_keys="some-channel-key",  # превью работает и без ключа
            policy_path=tmp / "policy.json",
            audit_log_path=tmp / "audit.log",
            try_page=cls.try_page,
        )
        cls.store = MappingStore()
        cls.forwarder = FakeForwarder()
        cls.pipeline = Pipeline(
            policy_engine=PolicyEngine(settings.policy_path),
            mapping_store=cls.store,
            forwarder=cls.forwarder,
            audit=AuditLog(settings.audit_log_path),
        )
        cls.server = make_server(cls.pipeline, settings)
        cls.base = f"http://127.0.0.1:{cls.server.server_address[1]}"
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls._tmp.cleanup()

    def _preview(self, text):
        req = urllib.request.Request(
            self.base + "/try/api/preview",
            data=json.dumps({"text": text}).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                return resp.status, json.loads(resp.read().decode())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read().decode())


class TestTryPage(_TryServer):
    def test_page_served(self):
        with urllib.request.urlopen(self.base + "/try", timeout=10) as resp:
            self.assertEqual(resp.status, 200)
            self.assertIn("Что увидит ChatGPT", resp.read().decode())

    def test_preview_masks_without_storing(self):
        text = f"Клиент Айгуль Ахметова, ИИН {VALID_IIN}, тел. +7 701 123 45 67"
        status, data = self._preview(text)
        self.assertEqual(status, 200)
        self.assertEqual(data["verdict"], "masked")
        self.assertNotIn(VALID_IIN, data["masked_text"])
        self.assertNotIn("Ахметова", data["masked_text"])
        self.assertEqual({i["type"] for i in data["items"]}, {"PERSON", "IIN", "PHONE"})
        self.assertEqual(self.pipeline.audit.read_all(), [])
        self.assertEqual(self.store._reverse, {})
        self.assertEqual(self.forwarder.sent, [])

    def test_same_value_same_token(self):
        _, data = self._preview(f"ИИН {VALID_IIN}, повторно {VALID_IIN}")
        self.assertEqual(data["masked_text"], "ИИН [IIN_1], повторно [IIN_1]")

    def test_blocked_secret_hides_text(self):
        status, data = self._preview("key sk-proj-Ab12Cd34Ef56Gh78Ij90KlMnOpQrStUv")
        self.assertEqual(status, 200)
        self.assertEqual(data["verdict"], "blocked")
        self.assertIsNone(data["masked_text"])

    def test_rejects_empty_and_huge(self):
        self.assertEqual(self._preview("  ")[0], 400)
        self.assertEqual(self._preview("а" * 20_001)[0], 413)


class TestTryPageDisabled(_TryServer):
    try_page = False

    def test_disabled(self):
        self.assertEqual(self._preview("x")[0], 404)
        with self.assertRaises(urllib.error.HTTPError):
            urllib.request.urlopen(self.base + "/try", timeout=10)


del _TryServer  # базовый класс без тестов не должен запускаться сам

if __name__ == "__main__":
    unittest.main()
