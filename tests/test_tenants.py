"""Облачный режим (этап 1): несколько компаний на одном шлюзе.

Проверяется изоляция (политика, журнал, хранилище меток), ключи только
в виде хешей, роли «канал» и «админ», отключение клиента, отзыв ключей,
лимит мест и операторский API."""

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
from gateway.core.tenants import TenantRegistry
from gateway.server import make_server

from .helpers import VALID_IIN, FakeForwarder

OPERATOR = "operator-secret"


class TestTenants(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        tmp = Path(cls._tmp.name)
        cls.tmp = tmp
        settings = Settings(
            host="127.0.0.1",
            port=0,
            policy_path=tmp / "policy.json",
            audit_log_path=tmp / "audit.log",
            multi_tenant=True,
            operator_token=OPERATOR,
            tenants_path=tmp / "tenants.json",
            tenants_data_root=tmp / "tenants",
        )
        cls.forwarder = FakeForwarder()
        default = Pipeline(
            policy_engine=PolicyEngine(settings.policy_path),
            mapping_store=MappingStore(),
            forwarder=cls.forwarder,
            audit=AuditLog(settings.audit_log_path),
        )
        cls.registry = TenantRegistry(settings.tenants_path, settings.tenants_data_root, cls.forwarder)
        cls.server = make_server(default, settings, cls.registry)
        cls.base = f"http://127.0.0.1:{cls.server.server_address[1]}"
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()

        cls.a = cls._create("alpha", "ТОО Альфа", seats=2)
        cls.b = cls._create("beta", "ТОО Бета", seats=10)

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls._tmp.cleanup()

    @classmethod
    def _call(cls, method, path, body=None, key=None):
        headers = {"Content-Type": "application/json"}
        if key:
            headers["Authorization"] = f"Bearer {key}"
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(cls.base + path, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                return resp.status, json.loads(resp.read().decode())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read().decode())

    @classmethod
    def _create(cls, tenant_id, name, seats):
        status, data = cls._call(
            "POST", "/operator/api/tenants", {"id": tenant_id, "name": name, "seats": seats}, key=OPERATOR
        )
        assert status == 201, data
        return data

    def _check(self, key, user="u1", text=None):
        return self._call(
            "POST", "/v1/check", {"text": text or f"ИИН {VALID_IIN}", "user": user, "dry_run": True}, key=key
        )

    def test_keys_stored_only_as_hashes(self):
        raw = (self.tmp / "tenants.json").read_text(encoding="utf-8")
        self.assertNotIn(self.a["admin_key"], raw)
        self.assertNotIn(self.a["channel_key"], raw)
        status, listing = self._call("GET", "/operator/api/tenants", key=OPERATOR)
        self.assertEqual(status, 200)
        self.assertNotIn("admin_key_hashes", json.dumps(listing))
        self.assertLessEqual({"alpha", "beta"}, {t["id"] for t in listing})

    def test_operator_auth(self):
        self.assertEqual(self._call("GET", "/operator/api/tenants", key="wrong")[0], 401)
        self.assertEqual(self._call("POST", "/operator/api/tenants", {"id": "x"}, key=self.a["admin_key"])[0], 401)
        status, data = self._call("POST", "/operator/api/tenants", {"id": "BAD ID"}, key=OPERATOR)
        self.assertEqual(status, 400)

    def test_unknown_or_missing_key(self):
        self.assertEqual(self._check(None)[0], 401)
        self.assertEqual(self._check("agk_alpha_forged")[0], 401)

    def test_audit_isolated(self):
        self.assertEqual(self._check(self.a["channel_key"])[0], 200)
        _, audit_a = self._call("GET", "/admin/api/audit", key=self.a["admin_key"])
        _, audit_b = self._call("GET", "/admin/api/audit", key=self.b["admin_key"])
        self.assertGreaterEqual(len(audit_a), 1)
        self.assertEqual(audit_b, [])
        # операторский журнал по умолчанию тоже пуст
        self.assertEqual(AuditLog(self.tmp / "audit.log").read_all(), [])

    def test_channel_key_is_not_admin(self):
        status, _ = self._call("GET", "/admin/api/audit", key=self.a["channel_key"])
        self.assertEqual(status, 403)

    def test_policy_isolated(self):
        _, policy = self._call("GET", "/admin/api/policy", key=self.b["admin_key"])
        for rule in policy["rules"]:
            if rule["entity_type"] == "IIN":
                rule["action"] = "block"
        self.assertEqual(self._call("PUT", "/admin/api/policy", policy, key=self.b["admin_key"])[0], 200)
        self.assertEqual(self._check(self.b["channel_key"], user="b1")[1]["verdict"], "blocked")
        self.assertEqual(self._check(self.a["channel_key"])[1]["verdict"], "masked")

    def test_mapping_store_isolated(self):
        _, data = self._check(self.a["channel_key"])
        session = data["session_id"]
        _, other = self._call(
            "POST", "/v1/restore", {"session_id": session, "text": "[IIN_1]", "purge": False},
            key=self.b["channel_key"],
        )
        self.assertEqual(other["text"], "[IIN_1]")  # у Беты нет чужих значений
        _, own = self._call(
            "POST", "/v1/restore", {"session_id": session, "text": "[IIN_1]"}, key=self.a["channel_key"]
        )
        self.assertEqual(own["text"], VALID_IIN)

    def test_providers_belong_to_operator(self):
        status, _ = self._call("PUT", "/admin/api/providers", {"providers": []}, key=self.a["admin_key"])
        self.assertEqual(status, 403)

    def test_seats(self):
        t = self._create("gamma", "ТОО Гамма", seats=2)
        key = t["channel_key"]
        self.assertEqual(self._check(key, user="Асель")[0], 200)
        self.assertEqual(self._check(key, user="Ержан")[0], 200)
        status, data = self._check(key, user="Динара")
        self.assertEqual(status, 403)
        self.assertIn("места", data["detail"])
        self.assertEqual(self._check(key, user="Асель")[0], 200)
        # оператор добавил мест
        self._call("PUT", "/operator/api/tenants/gamma", {"seats": 3}, key=OPERATOR)
        self.assertEqual(self._check(key, user="Динара")[0], 200)

    def test_disable_and_revoke(self):
        t = self._create("delta", "ТОО Дельта", seats=5)
        self.assertEqual(self._check(t["channel_key"])[0], 200)
        self._call("PUT", "/operator/api/tenants/delta", {"enabled": False}, key=OPERATOR)
        self.assertEqual(self._check(t["channel_key"])[0], 401)
        self._call("PUT", "/operator/api/tenants/delta", {"enabled": True}, key=OPERATOR)
        _, new = self._call("POST", "/operator/api/tenants/delta/keys", key=OPERATOR)
        self.assertEqual(self._check(new["channel_key"])[0], 200)
        self._call("POST", "/operator/api/tenants/delta/keys/revoke", key=OPERATOR)
        self.assertEqual(self._check(t["channel_key"])[0], 401)
        self.assertEqual(self._check(new["channel_key"])[0], 401)
        # админ-ключ при этом работает: отзывались только ключи канала
        self.assertEqual(self._call("GET", "/admin/api/audit", key=t["admin_key"])[0], 200)

    def test_operator_api_off_without_multi_tenant(self):
        tmp = Path(tempfile.mkdtemp())
        settings = Settings(host="127.0.0.1", port=0, operator_token=OPERATOR,
                            policy_path=tmp / "p.json", audit_log_path=tmp / "a.log")
        pipeline = Pipeline(PolicyEngine(settings.policy_path), MappingStore(), FakeForwarder(),
                            AuditLog(settings.audit_log_path))
        server = make_server(pipeline, settings)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            req = urllib.request.Request(
                f"http://127.0.0.1:{server.server_address[1]}/operator/api/tenants",
                headers={"Authorization": f"Bearer {OPERATOR}"},
            )
            with self.assertRaises(urllib.error.HTTPError) as ctx:
                urllib.request.urlopen(req, timeout=10)
            self.assertEqual(ctx.exception.code, 404)
        finally:
            server.shutdown()
            server.server_close()


if __name__ == "__main__":
    unittest.main()
