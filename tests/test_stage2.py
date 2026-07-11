"""Этап Э2: суммы/балансы, настраиваемые правила (коммерческая тайна),
контекстные ФИО, развёрнутые отчёты и CSV-экспорт, admin API правил."""

import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path

from gateway.config import Settings
from gateway.core.audit import AuditLog
from gateway.core.detectors import detect_all
from gateway.core.detectors.amounts import detect_amounts
from gateway.core.detectors.custom import CustomRuleRegistry, default_rules
from gateway.core.detectors.names import detect_person
from gateway.core.mapping_store import MappingStore
from gateway.core.pipeline import Pipeline
from gateway.core.policy import PolicyEngine
from gateway.models import (
    Channel,
    CheckRequest,
    DetectorRule,
    EntityType,
    Verdict,
)
from gateway.server import make_server

from .helpers import VALID_IIN, FakeForwarder

ADMIN_TOKEN = "t-admin"


class TestAmounts(unittest.TestCase):
    def test_currency_suffix(self):
        dets = detect_amounts("зачислено 1 500 000 тг на счёт")
        self.assertEqual(len(dets), 1)
        self.assertEqual(dets[0].entity_type, EntityType.AMOUNT)
        self.assertEqual(dets[0].value, "1 500 000 тг")

    def test_currency_prefix(self):
        dets = detect_amounts("перевод $1,200.50 отправлен")
        self.assertEqual(len(dets), 1)

    def test_kzt_and_symbol(self):
        self.assertTrue(detect_amounts("остаток 2500 KZT"))
        self.assertTrue(detect_amounts("₸450000 на балансе"))

    def test_context_word(self):
        dets = detect_amounts("баланс: 3 200 000 по договору")
        self.assertEqual(len(dets), 1)
        self.assertEqual(dets[0].value, "3 200 000")
        self.assertEqual(dets[0].detector, "amount_context")

    def test_bare_numbers_ignored(self):
        self.assertEqual(detect_amounts("страница 42, версия 3.11, год 2026"), [])

    def test_iin_not_flagged_as_amount(self):
        dets = detect_all(f"ИИН {VALID_IIN} сумма: 5000")
        types = [d.entity_type for d in dets]
        self.assertIn(EntityType.IIN, types)
        self.assertIn(EntityType.AMOUNT, types)
        # ИИН не съеден детектором сумм
        iin_det = next(d for d in dets if d.entity_type == EntityType.IIN)
        self.assertEqual(iin_det.value, VALID_IIN)


class TestCustomRules(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.path = Path(self._tmp.name) / "rules.json"

    def test_default_trade_secret_markers(self):
        registry = CustomRuleRegistry(self.path)
        dets = registry.detect("Документ содержит коммерческую тайну АО «Пример»")
        self.assertTrue(dets)
        self.assertEqual(dets[0].entity_type, EntityType.CONFIDENTIAL)

    def test_keyword_case_insensitive(self):
        registry = CustomRuleRegistry(self.path)
        self.assertTrue(registry.detect("гриф ДЛЯ СЛУЖЕБНОГО ПОЛЬЗОВАНИЯ"))

    def test_custom_regex_rule(self):
        registry = CustomRuleRegistry(self.path)
        registry.save(
            default_rules()
            + [
                DetectorRule(
                    name="contract_numbers",
                    entity_type=EntityType.CONFIDENTIAL,
                    pattern=r"договор №\s?\d{4}-[А-Я]{2}",
                )
            ]
        )
        dets = registry.detect("см. договор № 2024-КЗ, приложение 1")
        self.assertTrue(any(d.detector == "custom_contract_numbers" for d in dets))

    def test_disabled_rule_skipped(self):
        registry = CustomRuleRegistry(self.path)
        rules = default_rules()
        for r in rules:
            r.enabled = False
        registry.save(rules)
        self.assertEqual(registry.detect("коммерческая тайна"), [])

    def test_invalid_regex_rejected(self):
        registry = CustomRuleRegistry(self.path)
        import re as _re

        with self.assertRaises(_re.error):
            registry.save([DetectorRule(name="bad", pattern="([unclosed")])

    def test_persistence(self):
        registry = CustomRuleRegistry(self.path)
        registry.save([DetectorRule(name="only", keywords=["проект зеро"])])
        registry2 = CustomRuleRegistry(self.path)
        self.assertEqual(len(registry2.rules), 1)
        self.assertTrue(registry2.detect("статус: Проект Зеро идёт по плану"))

    def test_pipeline_blocks_confidential(self):
        tmp = Path(self._tmp.name)
        pipeline = Pipeline(
            policy_engine=PolicyEngine(tmp / "policy.json"),
            mapping_store=MappingStore(),
            forwarder=FakeForwarder(),
            audit=AuditLog(tmp / "audit.log"),
            custom_rules=CustomRuleRegistry(self.path),
        )
        result = pipeline.process(
            CheckRequest(
                text="Передаю документ с грифом коммерческая тайна",
                channel=Channel.EGRESS,
            )
        )
        self.assertEqual(result.verdict, Verdict.BLOCKED)
        self.assertEqual(pipeline.forwarder.sent, [])


class TestContextNames(unittest.TestCase):
    def test_two_word_name_with_context(self):
        dets = detect_person("Клиент: Айдос Смагулов, тел. далее")
        self.assertTrue(any("Айдос Смагулов" in d.value for d in dets))

    def test_fio_label(self):
        dets = detect_person("ФИО Жанна Ахметова Ержановна")
        self.assertTrue(any("Жанна Ахметова" in d.value for d in dets))

    def test_no_context_two_words_ignored(self):
        # без контекста пара капитализированных слов — не ФИО
        dets = detect_person("Северный Казахстан богат озёрами")
        self.assertEqual(dets, [])


class TestDetailedReports(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.audit = AuditLog(Path(self._tmp.name) / "audit.log")
        for user, verdict, counts in [
            ("ivanov", Verdict.MASKED, {"IIN": 2}),
            ("ivanov", Verdict.BLOCKED, {"SECRET": 1}),
            ("petrov", Verdict.MASKED, {"CARD": 1}),
        ]:
            self.audit.append(
                user=user,
                channel=Channel.BROWSER if user == "ivanov" else Channel.EGRESS,
                provider="openai",
                verdict=verdict,
                entity_counts=counts,
                policy_actions={},
            )

    def test_detailed_by_user(self):
        report = self.audit.detailed_report()
        self.assertEqual(report["by_user"]["ivanov"]["requests"], 2)
        self.assertEqual(report["by_user"]["ivanov"]["masked_entities"], 2)
        self.assertEqual(report["by_user"]["ivanov"]["blocked"], 1)
        self.assertEqual(report["by_user"]["petrov"]["masked_entities"], 1)

    def test_detailed_by_channel_and_day(self):
        report = self.audit.detailed_report()
        self.assertEqual(report["by_channel"]["browser"]["requests"], 2)
        self.assertEqual(report["by_channel"]["egress"]["requests"], 1)
        self.assertEqual(len(report["by_day"]), 1)  # все записи сегодня

    def test_until_filter(self):
        report = self.audit.detailed_report(until=0.0)  # до начала эпохи
        self.assertEqual(report["by_user"], {})
        self.assertEqual(report["summary"]["total_requests"], 0)

    def test_csv_export(self):
        csv_text = self.audit.export_csv()
        lines = csv_text.strip().splitlines()
        self.assertEqual(len(lines), 4)  # заголовок + 3 записи
        self.assertIn("ivanov", csv_text)
        self.assertIn('"{""IIN"": 2}"', csv_text)


class TestStage2Api(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        tmp = Path(cls._tmp.name)
        settings = Settings(
            host="127.0.0.1",
            port=0,
            admin_token=ADMIN_TOKEN,
            policy_path=tmp / "policy.json",
            audit_log_path=tmp / "audit.log",
        )
        cls.pipeline = Pipeline(
            policy_engine=PolicyEngine(settings.policy_path),
            mapping_store=MappingStore(),
            forwarder=FakeForwarder(),
            audit=AuditLog(settings.audit_log_path),
            custom_rules=CustomRuleRegistry(tmp / "rules.json"),
        )
        cls.server = make_server(cls.pipeline, settings)
        cls.base = f"http://127.0.0.1:{cls.server.server_address[1]}"
        cls._thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls._thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls._tmp.cleanup()

    def _request(self, method, path, body=None, raw=False):
        req = urllib.request.Request(
            self.base + path,
            data=json.dumps(body).encode() if body is not None else None,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {ADMIN_TOKEN}",
            },
            method=method,
        )
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                payload = resp.read().decode()
                return resp.status, payload if raw else json.loads(payload)
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read().decode())

    def test_get_detector_rules(self):
        status, rules = self._request("GET", "/admin/api/detector-rules")
        self.assertEqual(status, 200)
        self.assertTrue(any(r["name"] == "trade_secret_markers" for r in rules))

    def test_put_detector_rules_and_apply(self):
        _, rules = self._request("GET", "/admin/api/detector-rules")
        rules.append(
            {"name": "codename", "keywords": ["проект аврора"], "entity_type": "CONFIDENTIAL"}
        )
        status, _ = self._request("PUT", "/admin/api/detector-rules", rules)
        self.assertEqual(status, 200)
        # правило сразу применяется конвейером
        result = self.pipeline.sanitize(
            CheckRequest(text="обсуждаем Проект Аврора завтра", channel=Channel.API)
        )
        self.assertEqual(result.verdict, Verdict.BLOCKED)

    def test_put_invalid_regex_rejected(self):
        status, _ = self._request(
            "PUT", "/admin/api/detector-rules", [{"name": "bad", "pattern": "([("}]
        )
        self.assertEqual(status, 400)

    def test_detailed_report_endpoint(self):
        status, report = self._request("GET", "/admin/api/report/detailed")
        self.assertEqual(status, 200)
        self.assertIn("by_user", report)
        self.assertIn("by_channel", report)
        self.assertIn("summary", report)

    def test_csv_endpoint(self):
        status, body = self._request("GET", "/admin/api/report.csv", raw=True)
        self.assertEqual(status, 200)
        self.assertTrue(body.startswith("ts_iso,user,channel"))


if __name__ == "__main__":
    unittest.main()
