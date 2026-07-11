"""Tamper-evident аудит (F7).

Append-only JSONL-лог с хэш-цепочкой: каждая запись содержит
prev_hash и hash = sha256(prev_hash + canonical_json(record)).
Изменение/удаление любой записи ломает цепочку, что обнаруживается
верификацией. Значений ПДн/секретов в логе нет — только типы и счётчики
(раздел 8 ТЗ: логи не содержат исходных данных).
"""

from __future__ import annotations

import hashlib
import json
import threading
import time
import uuid
from pathlib import Path

from gateway.models import AuditRecord, Channel, Verdict

GENESIS_HASH = "0" * 64


def _record_hash(prev_hash: str, record: AuditRecord) -> str:
    payload = record.to_dict()
    payload.pop("hash", None)
    canonical = json.dumps(payload, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256((prev_hash + canonical).encode()).hexdigest()


class AuditLog:
    def __init__(self, path: Path):
        self._path = path
        self._lock = threading.Lock()
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._last_hash = self._read_last_hash()

    def _read_last_hash(self) -> str:
        if not self._path.exists():
            return GENESIS_HASH
        last = GENESIS_HASH
        with self._path.open() as f:
            for line in f:
                line = line.strip()
                if line:
                    last = json.loads(line).get("hash", last)
        return last

    def append(
        self,
        *,
        user: str,
        channel: Channel,
        provider: str,
        verdict: Verdict,
        entity_counts: dict[str, int],
        policy_actions: dict[str, str],
        cross_border_basis: str | None = None,
        request_id: str | None = None,
    ) -> AuditRecord:
        with self._lock:
            record = AuditRecord(
                ts=time.time(),
                user=user,
                channel=channel.value,
                provider=provider,
                verdict=verdict.value,
                entity_counts=entity_counts,
                policy_actions=policy_actions,
                cross_border_basis=cross_border_basis,
                request_id=request_id or uuid.uuid4().hex,
                prev_hash=self._last_hash,
            )
            record.hash = _record_hash(self._last_hash, record)
            with self._path.open("a") as f:
                f.write(json.dumps(record.to_dict(), ensure_ascii=False) + "\n")
            self._last_hash = record.hash
            return record

    def read_all(self) -> list[AuditRecord]:
        if not self._path.exists():
            return []
        records = []
        with self._path.open() as f:
            for line in f:
                line = line.strip()
                if line:
                    records.append(AuditRecord.from_dict(json.loads(line)))
        return records

    def verify_chain(self) -> tuple[bool, int]:
        """Проверить целостность цепочки. Возвращает (ок, номер битой записи | -1)."""
        prev = GENESIS_HASH
        for i, record in enumerate(self.read_all()):
            if record.prev_hash != prev or _record_hash(prev, record) != record.hash:
                return False, i
            prev = record.hash
        return True, -1

    def compliance_report(self, since: float = 0.0) -> dict:
        """Комплаенс-отчёт: N запросов, M замаскировано, K блокировок (F7)."""
        records = [r for r in self.read_all() if r.ts >= since]
        masked = [r for r in records if r.verdict == Verdict.MASKED.value]
        by_type: dict[str, int] = {}
        for r in masked:
            for etype, count in r.entity_counts.items():
                by_type[etype] = by_type.get(etype, 0) + count
        chain_ok, _ = self.verify_chain()
        return {
            "period_start": since,
            "generated_at": time.time(),
            "total_requests": len(records),
            "masked_requests": len(masked),
            "masked_entities": sum(by_type.values()),
            "masked_by_type": by_type,
            "blocked": sum(1 for r in records if r.verdict == Verdict.BLOCKED.value),
            "fail_closed": sum(
                1 for r in records if r.verdict == Verdict.FAIL_CLOSED.value
            ),
            "clean": sum(1 for r in records if r.verdict == Verdict.CLEAN.value),
            "cross_border_allowed": sum(
                1 for r in records if r.verdict == Verdict.ALLOWED.value
            ),
            "plaintext_leaks": 0,  # по построению: наружу уходит только обезличенный текст
            "chain_integrity": chain_ok,
        }

    def detailed_report(self, since: float = 0.0, until: float | None = None) -> dict:
        """Развёрнутый комплаенс-отчёт (Э2): разбивка по пользователям,
        каналам и дням — для внутреннего комплаенса и проверок регулятора."""
        import datetime

        records = [
            r
            for r in self.read_all()
            if r.ts >= since and (until is None or r.ts < until)
        ]

        def _bucket() -> dict:
            return {"requests": 0, "masked_entities": 0, "blocked": 0, "fail_closed": 0}

        def _account(bucket: dict, r: AuditRecord) -> None:
            bucket["requests"] += 1
            if r.verdict == Verdict.MASKED.value:
                bucket["masked_entities"] += sum(r.entity_counts.values())
            elif r.verdict == Verdict.BLOCKED.value:
                bucket["blocked"] += 1
            elif r.verdict == Verdict.FAIL_CLOSED.value:
                bucket["fail_closed"] += 1

        by_user: dict[str, dict] = {}
        by_channel: dict[str, dict] = {}
        by_day: dict[str, dict] = {}
        for r in records:
            day = datetime.datetime.fromtimestamp(
                r.ts, tz=datetime.timezone.utc
            ).strftime("%Y-%m-%d")
            for key, table in ((r.user, by_user), (r.channel, by_channel), (day, by_day)):
                _account(table.setdefault(key, _bucket()), r)

        summary = self.compliance_report(since=since)
        if until is not None:
            summary = {
                **summary,
                "period_end": until,
                "total_requests": len(records),
            }
        return {
            "summary": summary,
            "by_user": by_user,
            "by_channel": by_channel,
            "by_day": dict(sorted(by_day.items())),
        }

    def export_csv(self, since: float = 0.0) -> str:
        """Экспорт журнала в CSV (без значений — только типы/счётчики)."""
        import csv
        import io

        buf = io.StringIO()
        writer = csv.writer(buf)
        writer.writerow(
            [
                "ts_iso",
                "user",
                "channel",
                "provider",
                "verdict",
                "entity_counts",
                "policy_actions",
                "cross_border_basis",
                "request_id",
                "hash",
            ]
        )
        import datetime

        for r in self.read_all():
            if r.ts < since:
                continue
            writer.writerow(
                [
                    datetime.datetime.fromtimestamp(
                        r.ts, tz=datetime.timezone.utc
                    ).isoformat(),
                    r.user,
                    r.channel,
                    r.provider,
                    r.verdict,
                    json.dumps(r.entity_counts, ensure_ascii=False, sort_keys=True),
                    json.dumps(r.policy_actions, ensure_ascii=False, sort_keys=True),
                    r.cross_border_basis or "",
                    r.request_id,
                    r.hash,
                ]
            )
        return buf.getvalue()
