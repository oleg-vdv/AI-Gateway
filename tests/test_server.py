"""HTTP-слой: /v1/check, /v1/restore, /v1/chat/completions (egress), Control Plane.
Критерий приёмки 3: внутреннее приложение через egress-proxy маскируется
и восстанавливается. Сервер поднимается на реальном порту (localhost)."""

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

ADMIN_TOKEN = "test-admin-token"
CHANNEL_KEY = "test-channel-key"


class ServerTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        tmp = Path(cls._tmp.name)
        cls.settings = Settings(
            host="127.0.0.1",
            port=0,  # свободный порт
            channel_api_keys=CHANNEL_KEY,
            admin_token=ADMIN_TOKEN,
            policy_path=tmp / "policy.json",
            audit_log_path=tmp / "audit.log",
        )
        cls.pipeline = Pipeline(
            policy_engine=PolicyEngine(cls.settings.policy_path),
            mapping_store=MappingStore(),
            forwarder=FakeForwarder(),
            audit=AuditLog(cls.settings.audit_log_path),
        )
        cls.server = make_server(cls.pipeline, cls.settings)
        cls.base = f"http://127.0.0.1:{cls.server.server_address[1]}"
        cls._thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls._thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls._tmp.cleanup()

    # --- помощники ---

    def _request(self, method, path, body=None, headers=None, key=CHANNEL_KEY):
        hdrs = {"Content-Type": "application/json"}
        if key:
            hdrs["X-AIGate-Key"] = key
        hdrs.update(headers or {})
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(
            self.base + path, data=data, headers=hdrs, method=method
        )
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                return resp.status, json.loads(resp.read().decode())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read().decode())

    def _admin(self, method, path, body=None):
        return self._request(
            method, path, body, headers={"Authorization": f"Bearer {ADMIN_TOKEN}"},
            key=None,
        )

    # --- тесты ---

    def test_healthz(self):
        status, data = self._request("GET", "/healthz", key=None)
        self.assertEqual(status, 200)
        self.assertEqual(data["status"], "ok")

    def test_check_requires_channel_key(self):
        status, _ = self._request("POST", "/v1/check", {"text": "x"}, key=None)
        self.assertEqual(status, 401)
        status, _ = self._request("POST", "/v1/check", {"text": "x"}, key="wrong")
        self.assertEqual(status, 401)

    def test_check_dry_run_masks_and_restore(self):
        status, data = self._request(
            "POST",
            "/v1/check",
            {"text": f"ИИН {VALID_IIN}", "channel": "browser", "dry_run": True},
        )
        self.assertEqual(status, 200)
        self.assertEqual(data["verdict"], "masked")
        self.assertNotIn(VALID_IIN, data["masked_text"])
        self.assertEqual(data["entity_counts"], {"IIN": 1})

        status, restored = self._request(
            "POST",
            "/v1/restore",
            {"session_id": data["session_id"], "text": data["masked_text"]},
        )
        self.assertEqual(status, 200)
        self.assertIn(VALID_IIN, restored["text"])

    def test_egress_proxy_roundtrip(self):
        """Критерий 3: OpenAI-совместимый вызов через шлюз."""
        status, data = self._request(
            "POST",
            "/v1/chat/completions",
            {
                "model": "gpt-test",
                "messages": [{"role": "user", "content": f"Клиент с ИИН {VALID_IIN}"}],
            },
        )
        self.assertEqual(status, 200)
        answer = data["choices"][0]["message"]["content"]
        self.assertIn(VALID_IIN, answer)  # восстановлено
        self.assertEqual(data["aigate"]["masked_entities"], {"IIN": 1})
        # наружу (в заглушку) значение не уходило
        self.assertTrue(all(VALID_IIN not in s for s in self.pipeline.forwarder.sent))

    def test_egress_proxy_blocks_secret(self):
        status, _ = self._request(
            "POST",
            "/v1/chat/completions",
            {
                "messages": [
                    {"role": "user", "content": "key: sk-proj-Ab12Cd34Ef56Gh78Ij90KlMnOp"}
                ]
            },
        )
        self.assertEqual(status, 403)

    def test_egress_stream_rejected(self):
        status, _ = self._request(
            "POST",
            "/v1/chat/completions",
            {"messages": [{"role": "user", "content": "hi"}], "stream": True},
        )
        self.assertEqual(status, 400)

    def test_admin_requires_token(self):
        status, _ = self._request("GET", "/admin/api/policy", key=None)
        self.assertEqual(status, 401)
        status, data = self._admin("GET", "/admin/api/policy")
        self.assertEqual(status, 200)
        self.assertTrue(any(r["entity_type"] == "IIN" for r in data["rules"]))

    def test_admin_report_audit_and_no_values(self):
        self._request(
            "POST",
            "/v1/check",
            {"text": f"ИИН {VALID_IIN}", "channel": "browser", "dry_run": True},
        )
        status, report = self._admin("GET", "/admin/api/report")
        self.assertEqual(status, 200)
        self.assertGreaterEqual(report["total_requests"], 1)
        self.assertTrue(report["chain_integrity"])
        self.assertEqual(report["plaintext_leaks"], 0)

        status, audit = self._admin("GET", "/admin/api/audit")
        self.assertEqual(status, 200)
        self.assertNotIn(VALID_IIN, json.dumps(audit))  # значений в аудите нет

        status, verify = self._admin("GET", "/admin/api/audit/verify")
        self.assertTrue(verify["chain_integrity"])

    def test_policy_update_via_api(self):
        status, policy = self._admin("GET", "/admin/api/policy")
        for rule in policy["rules"]:
            if rule["entity_type"] == "SECRET":
                rule["action"] = "mask"
        status, _ = self._admin("PUT", "/admin/api/policy", policy)
        self.assertEqual(status, 200)

        # теперь секрет маскируется, а не блокируется
        status, check = self._request(
            "POST",
            "/v1/check",
            {
                "text": "key: sk-proj-Ab12Cd34Ef56Gh78Ij90KlMnOp",
                "channel": "browser",
                "dry_run": True,
            },
        )
        self.assertEqual(check["verdict"], "masked")
        self.assertNotIn("sk-proj", check["masked_text"])

        # возвращаем block обратно (для независимости тестов)
        for rule in policy["rules"]:
            if rule["entity_type"] == "SECRET":
                rule["action"] = "block"
        self._admin("PUT", "/admin/api/policy", policy)

    def test_bad_json_rejected(self):
        req = urllib.request.Request(
            self.base + "/v1/check",
            data=b"{not json",
            headers={"Content-Type": "application/json", "X-AIGate-Key": CHANNEL_KEY},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                status = resp.status
        except urllib.error.HTTPError as e:
            status = e.code
        self.assertEqual(status, 400)

    def test_admin_ui_served(self):
        req = urllib.request.Request(self.base + "/admin")
        with urllib.request.urlopen(req, timeout=10) as resp:
            self.assertEqual(resp.status, 200)
            self.assertIn("Control Plane", resp.read().decode())


if __name__ == "__main__":
    unittest.main()
