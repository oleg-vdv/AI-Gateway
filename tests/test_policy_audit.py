"""F4 (политики, fail-closed) и F7 (tamper-evident аудит)."""

import json
import tempfile
import unittest
from pathlib import Path

from gateway.core.audit import AuditLog
from gateway.core.policy import PolicyEngine, default_policy
from gateway.models import (
    Action,
    Channel,
    Detection,
    EntityType,
    Policy,
    PolicyRule,
    Verdict,
)

from .helpers import VALID_IIN


def _det(etype: EntityType) -> Detection:
    return Detection(entity_type=etype, value="x", start=0, end=1, detector="t")


class TestPolicy(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.path = Path(self._tmp.name) / "policy.json"
        self.addCleanup(self._tmp.cleanup)

    def test_default_policy_masks_pii_blocks_secrets(self):
        engine = PolicyEngine(self.path)
        actions, to_mask, blocked, _ = engine.evaluate(
            [_det(EntityType.IIN), _det(EntityType.SECRET)], Channel.API, "default"
        )
        self.assertEqual(actions["IIN"], Action.MASK)
        self.assertEqual(actions["SECRET"], Action.BLOCK)
        self.assertTrue(blocked)

    def test_channel_override(self):
        engine = PolicyEngine(self.path)
        policy = default_policy()
        policy.channel_overrides = {"browser": {"SECRET": Action.MASK}}
        engine.save(policy)
        action, _ = engine.action_for(EntityType.SECRET, Channel.BROWSER, "default")
        self.assertEqual(action, Action.MASK)
        action, _ = engine.action_for(EntityType.SECRET, Channel.EGRESS, "default")
        self.assertEqual(action, Action.BLOCK)

    def test_group_override_beats_channel(self):
        engine = PolicyEngine(self.path)
        policy = default_policy()
        policy.channel_overrides = {"api": {"IIN": Action.BLOCK}}
        policy.group_overrides = {"compliance": {"IIN": Action.MASK}}
        engine.save(policy)
        action, _ = engine.action_for(EntityType.IIN, Channel.API, "compliance")
        self.assertEqual(action, Action.MASK)

    def test_allow_without_basis_degrades_to_mask(self):
        engine = PolicyEngine(self.path)
        engine.save(
            Policy(rules=[PolicyRule(entity_type=EntityType.PERSON, action=Action.ALLOW)])
        )
        action, _ = engine.action_for(EntityType.PERSON, Channel.API, "default")
        self.assertEqual(action, Action.MASK)  # allow без основания недопустим (ст. 16)

    def test_allow_with_basis(self):
        engine = PolicyEngine(self.path)
        engine.save(
            Policy(
                rules=[
                    PolicyRule(
                        entity_type=EntityType.PERSON,
                        action=Action.ALLOW,
                        allow_basis="согласие субъекта, приказ №42",
                    )
                ]
            )
        )
        action, basis = engine.action_for(EntityType.PERSON, Channel.API, "default")
        self.assertEqual(action, Action.ALLOW)
        self.assertEqual(basis, "согласие субъекта, приказ №42")

    def test_corrupted_policy_falls_back_to_default(self):
        self.path.write_text("{broken json")
        engine = PolicyEngine(self.path)
        self.assertEqual(engine.policy.name, "default")
        action, _ = engine.action_for(EntityType.SECRET, Channel.API, "default")
        self.assertEqual(action, Action.BLOCK)

    def test_unknown_entity_defaults_to_mask(self):
        engine = PolicyEngine(self.path)
        engine.save(Policy(rules=[]))  # пустая политика
        action, _ = engine.action_for(EntityType.IIN, Channel.API, "default")
        self.assertEqual(action, Action.MASK)  # fail-safe

    def test_policy_persisted_and_reloaded(self):
        engine = PolicyEngine(self.path)
        policy = default_policy()
        policy.name = "custom"
        engine.save(policy)
        engine2 = PolicyEngine(self.path)
        self.assertEqual(engine2.policy.name, "custom")


class TestAudit(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.path = Path(self._tmp.name) / "audit.log"
        self.addCleanup(self._tmp.cleanup)

    def _append(self, audit, verdict=Verdict.MASKED, counts=None):
        return audit.append(
            user="u1",
            channel=Channel.BROWSER,
            provider="openai",
            verdict=verdict,
            entity_counts=counts if counts is not None else {"IIN": 1},
            policy_actions={"IIN": "mask"},
        )

    def test_chain_valid(self):
        audit = AuditLog(self.path)
        for _ in range(5):
            self._append(audit)
        ok, broken = audit.verify_chain()
        self.assertTrue(ok)
        self.assertEqual(broken, -1)

    def test_tampering_detected(self):
        audit = AuditLog(self.path)
        for _ in range(3):
            self._append(audit)
        lines = self.path.read_text().splitlines()
        record = json.loads(lines[1])
        record["entity_counts"] = {"IIN": 999}
        lines[1] = json.dumps(record, ensure_ascii=False)
        self.path.write_text("\n".join(lines) + "\n")

        ok, broken = AuditLog(self.path).verify_chain()
        self.assertFalse(ok)
        self.assertEqual(broken, 1)

    def test_deletion_detected(self):
        audit = AuditLog(self.path)
        for _ in range(3):
            self._append(audit)
        lines = self.path.read_text().splitlines()
        self.path.write_text("\n".join([lines[0], lines[2]]) + "\n")
        ok, broken = AuditLog(self.path).verify_chain()
        self.assertFalse(ok)
        self.assertEqual(broken, 1)

    def test_no_values_in_log(self):
        audit = AuditLog(self.path)
        self._append(audit)
        self.assertNotIn(VALID_IIN, self.path.read_text())

    def test_compliance_report(self):
        audit = AuditLog(self.path)
        self._append(audit, Verdict.MASKED, {"IIN": 2, "SECRET": 1})
        self._append(audit, Verdict.BLOCKED, {"SECRET": 1})
        self._append(audit, Verdict.CLEAN, {})
        report = audit.compliance_report()
        self.assertEqual(report["total_requests"], 3)
        self.assertEqual(report["masked_entities"], 3)
        self.assertEqual(report["blocked"], 1)
        self.assertEqual(report["clean"], 1)
        self.assertEqual(report["plaintext_leaks"], 0)
        self.assertTrue(report["chain_integrity"])

    def test_survives_restart(self):
        audit1 = AuditLog(self.path)
        self._append(audit1)
        audit2 = AuditLog(self.path)  # перечитывает последний хэш
        self._append(audit2)
        ok, _ = audit2.verify_chain()
        self.assertTrue(ok)


if __name__ == "__main__":
    unittest.main()
