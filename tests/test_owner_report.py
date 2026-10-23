"""Ежемесячный отчёт владельцу (этап 1): цифры за месяц по местному
времени, отсутствие значений ПДн, текст и страница, расписание отправки
без повторов, админ-эндпоинты."""

import datetime as dt
import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path

from gateway.config import Settings
from gateway.core import owner_report
from gateway.core.audit import AuditLog
from gateway.core.mapping_store import MappingStore
from gateway.core.pipeline import Pipeline
from gateway.core.policy import PolicyEngine
from gateway.models import Channel, Verdict
from gateway.server import make_server

from .helpers import FakeForwarder

KZ = dt.timezone(dt.timedelta(hours=5))


def ts(*args) -> float:
    return dt.datetime(*args, tzinfo=KZ).timestamp()


class FixedClockAudit(AuditLog):
    """Журнал, в который можно писать записи с заданным временем."""

    clock = None

    def append(self, **kw):
        import gateway.core.audit as audit_module

        real = audit_module.time.time
        audit_module.time.time = lambda: self.clock
        try:
            return super().append(**kw)
        finally:
            audit_module.time.time = real


def make_audit(path: Path) -> FixedClockAudit:
    audit = FixedClockAudit(path)
    rows = [
        (ts(2026, 9, 15, 10), "Асель", Channel.EGRESS, Verdict.MASKED, {"IIN": 1}),
        (ts(2026, 10, 1, 0, 30), "Асель", Channel.EGRESS, Verdict.MASKED, {"IIN": 2, "PERSON": 1}),
        (ts(2026, 10, 5, 12), "Ержан", Channel.BROWSER, Verdict.MASKED, {"PHONE": 1}),
        (ts(2026, 10, 6, 12), "Ержан", Channel.BROWSER, Verdict.BLOCKED, {"SECRET": 1}),
        (ts(2026, 10, 7, 12), "Ержан", Channel.API, Verdict.CLEAN, {}),
        (ts(2026, 10, 31, 23, 59), "Асель", Channel.EGRESS, Verdict.MASKED, {"PERSON": 1}),
        (ts(2026, 11, 1, 0, 1), "Асель", Channel.EGRESS, Verdict.MASKED, {"PERSON": 5}),
    ]
    for t, user, channel, verdict, counts in rows:
        audit.clock = t
        audit.append(
            user=user, channel=channel, provider="p", verdict=verdict,
            entity_counts=counts, policy_actions={}, request_id=str(t),
        )
    return audit


class TestBuild(unittest.TestCase):
    def setUp(self):
        self.audit = make_audit(Path(tempfile.mkdtemp()) / "audit.log")

    def test_month_bounds_local_time(self):
        r = owner_report.build(self.audit, "2026-10")
        # 01.10 00:30 и 31.10 23:59 по Астане входят, 01.11 00:01 — нет
        self.assertEqual(r["requests"], 5)
        self.assertEqual(r["users"], 2)
        self.assertEqual(r["hidden"], 5)
        self.assertEqual(r["hidden_previous"], 1)
        self.assertEqual(r["blocked"], 1)
        self.assertEqual(r["by_type"], {"PERSON": 2, "IIN": 2, "PHONE": 1})
        self.assertEqual(r["top_users"][0]["user"], "Асель")
        self.assertTrue(r["chain_integrity"])

    def test_text_and_html(self):
        r = owner_report.build(self.audit, "2026-10")
        text = owner_report.render_text(r)
        self.assertIn("октябрь 2026", text)
        self.assertIn("Скрыто персональных данных: 5 (+400 % к прошлому месяцу)", text)
        self.assertIn("Асель (4)", text)
        page = owner_report.render_html(r)
        self.assertIn("<title>Отчёт AI-Gate за октябрь 2026</title>", page)
        self.assertIn("Журнал аудита цел", page)

    def test_empty_month(self):
        r = owner_report.build(self.audit, "2025-01")
        self.assertIn("не было", owner_report.render_text(r))


class TestScheduler(unittest.TestCase):
    def test_sends_once_per_month_and_after_restart(self):
        tmp = Path(tempfile.mkdtemp())
        audit = make_audit(tmp / "audit.log")
        sent = []
        sched = owner_report.MonthlyScheduler(audit, tmp / "state.json", sent.append, day=1)
        self.assertEqual(sched.tick(dt.datetime(2026, 11, 1, 9, tzinfo=KZ)), "2026-10")
        self.assertIsNone(sched.tick(dt.datetime(2026, 11, 1, 10, tzinfo=KZ)))
        restarted = owner_report.MonthlyScheduler(audit, tmp / "state.json", sent.append, day=1)
        self.assertIsNone(restarted.tick(dt.datetime(2026, 11, 20, tzinfo=KZ)))
        self.assertEqual(len(sent), 1)
        self.assertIn("октябрь 2026", sent[0])

    def test_waits_for_report_day(self):
        tmp = Path(tempfile.mkdtemp())
        sched = owner_report.MonthlyScheduler(make_audit(tmp / "a.log"), tmp / "s.json", lambda t: None, day=3)
        self.assertIsNone(sched.tick(dt.datetime(2026, 11, 2, tzinfo=KZ)))
        self.assertEqual(sched.tick(dt.datetime(2026, 11, 3, tzinfo=KZ)), "2026-10")


class TestEndpoints(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        tmp = Path(cls._tmp.name)
        settings = Settings(
            host="127.0.0.1", port=0, admin_token="adm",
            policy_path=tmp / "policy.json", audit_log_path=tmp / "audit.log",
        )
        pipeline = Pipeline(
            policy_engine=PolicyEngine(settings.policy_path),
            mapping_store=MappingStore(),
            forwarder=FakeForwarder(),
            audit=make_audit(settings.audit_log_path),
        )
        cls.server = make_server(pipeline, settings)
        cls.base = f"http://127.0.0.1:{cls.server.server_address[1]}"
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls._tmp.cleanup()

    def _req(self, method, path, token="adm"):
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        req = urllib.request.Request(self.base + path, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                return resp.status, resp.read().decode()
        except urllib.error.HTTPError as e:
            return e.code, e.read().decode()

    def test_json_and_page(self):
        self.assertEqual(self._req("GET", "/admin/api/report/monthly?month=2026-10", token=None)[0], 401)
        status, body = self._req("GET", "/admin/api/report/monthly?month=2026-10")
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["hidden"], 5)
        status, page = self._req("GET", "/admin/report/monthly?month=2026-10")
        self.assertEqual(status, 200)
        self.assertIn("октябрь 2026", page)
        self.assertEqual(self._req("GET", "/admin/api/report/monthly?month=2026-13")[0], 400)

    def test_send_without_telegram_config(self):
        status, body = self._req("POST", "/admin/api/report/send?month=2026-10")
        self.assertEqual(status, 409)
        self.assertIn("Telegram не настроен", json.loads(body)["detail"])


if __name__ == "__main__":
    unittest.main()
