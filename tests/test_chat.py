"""Корпоративный чат (этап 1): страница /chat, список моделей, дневной
лимит на пользователя, «что увидел ИИ» в ответе egress-канала."""

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
from gateway.core.providers import MultiForwarder, ProviderConfig, ProviderRegistry
from gateway.server import make_server

from .helpers import VALID_IIN


class TestChat(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        tmp = Path(cls._tmp.name)
        cls.settings = Settings(
            host="127.0.0.1",
            port=0,
            channel_api_keys="chan",
            policy_path=tmp / "policy.json",
            audit_log_path=tmp / "audit.log",
            daily_request_limit=3,
        )
        registry = ProviderRegistry(tmp / "providers.json")
        registry.save(
            [
                ProviderConfig(name="openai", base_url="https://api.openai.com/v1", api_key="k", model="gpt-x"),
                ProviderConfig(name="local", base_url="http://127.0.0.1:11434/v1", model="llama"),
            ],
            "openai",
        )
        cls.sent = []

        def transport(cfg, messages, model):
            cls.sent.append(messages[-1]["content"])
            return "Готово для " + messages[-1]["content"]

        cls.pipeline = Pipeline(
            policy_engine=PolicyEngine(cls.settings.policy_path),
            mapping_store=MappingStore(),
            forwarder=MultiForwarder(registry, transport=transport),
            audit=AuditLog(cls.settings.audit_log_path),
        )
        cls.server = make_server(cls.pipeline, cls.settings)
        cls.base = f"http://127.0.0.1:{cls.server.server_address[1]}"
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls._tmp.cleanup()

    def _call(self, method, path, body=None, key="chan"):
        headers = {"Content-Type": "application/json"}
        if key:
            headers["X-AIGate-Key"] = key
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(self.base + path, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                raw = resp.read().decode()
                return resp.status, (json.loads(raw) if raw.startswith("{") else raw)
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read().decode())

    def test_chat_page(self):
        status, body = self._call("GET", "/chat", key=None)
        self.assertEqual(status, 200)
        self.assertIn("Рабочий ИИ-чат", body)

    def test_models(self):
        self.assertEqual(self._call("GET", "/v1/models", key=None)[0], 401)
        status, data = self._call("GET", "/v1/models")
        self.assertEqual(status, 200)
        models = {m["id"]: m for m in data["data"]}
        self.assertEqual(set(models), {"openai/gpt-x", "local/llama"})
        self.assertTrue(models["openai/gpt-x"]["default"])
        self.assertTrue(models["local/llama"]["local"])
        self.assertFalse(models["openai/gpt-x"]["local"])

    def test_masked_last_user_and_restore(self):
        status, data = self._call(
            "POST",
            "/v1/chat/completions",
            {
                "user": "masked-test",
                "messages": [
                    {"role": "user", "content": "Привет"},
                    {"role": "assistant", "content": "Здравствуйте"},
                    {"role": "user", "content": f"ИИН {VALID_IIN}"},
                ],
            },
        )
        self.assertEqual(status, 200)
        self.assertEqual(data["aigate"]["masked_last_user"], "ИИН [IIN_1]")
        self.assertEqual(data["choices"][0]["message"]["content"], f"Готово для ИИН {VALID_IIN}")
        self.assertEqual(self.sent[-1], "ИИН [IIN_1]")

    def test_daily_limit_per_user(self):
        body = {"user": "limit-test", "messages": [{"role": "user", "content": "x"}]}
        for _ in range(3):
            self.assertEqual(self._call("POST", "/v1/chat/completions", body)[0], 200)
        status, data = self._call("POST", "/v1/chat/completions", body)
        self.assertEqual(status, 429)
        self.assertIn("лимит", data["detail"])
        # у другого пользователя свой счётчик
        other = {**body, "user": "limit-other"}
        self.assertEqual(self._call("POST", "/v1/chat/completions", other)[0], 200)


if __name__ == "__main__":
    unittest.main()
