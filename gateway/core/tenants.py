"""Облачный режим (этап 1): один шлюз — несколько компаний-клиентов.

Изоляция по построению: у каждого клиента свой каталог данных
(политика, журнал аудита, настраиваемые правила) и своё хранилище меток
в памяти. Общий только форвардер к провайдерам ИИ — ключи провайдеров
принадлежат оператору облака.

Клиент определяется по ключу в запросе. Ключи хранятся только в виде
SHA-256: файл реестра не позволяет войти от имени клиента. Ключ
показывается один раз — при выдаче.

  agk_<id>_<секрет>  — ключ канала (чат, расширение, API, файлы)
  aga_<id>_<секрет>  — админ-ключ клиента (его политика, журнал, отчёт)

Места (seats): сколько разных сотрудников клиента может работать в
текущем месяце. Новый сотрудник сверх лимита получает отказ; те, кто уже
работал в этом месяце, продолжают.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import hmac
import json
import re
import secrets
import threading
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .audit import AuditLog
from .detectors.custom import CustomRuleRegistry
from .mapping_store import MappingStore
from .pipeline import Pipeline
from .policy import PolicyEngine

_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{1,30}[a-z0-9]$")


def _hash(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()


@dataclass
class Tenant:
    id: str
    name: str
    seats: int = 10
    enabled: bool = True
    created: float = field(default_factory=time.time)
    admin_key_hashes: list[str] = field(default_factory=list)
    channel_key_hashes: list[str] = field(default_factory=list)

    def public(self) -> dict:
        """Без хешей ключей — для ответов API."""
        d = asdict(self)
        d.pop("admin_key_hashes")
        d.pop("channel_key_hashes")
        d["channel_keys"] = len(self.channel_key_hashes)
        return d


class SeatLimitExceeded(Exception):
    pass


class TenantRegistry:
    def __init__(
        self,
        path: Path,
        data_root: Path,
        forwarder,
        mapping_ttl_seconds: int = 600,
        fail_closed: bool = True,
    ):
        self._path = path
        self._root = data_root
        self._forwarder = forwarder
        self._ttl = mapping_ttl_seconds
        self._fail_closed = fail_closed
        self._lock = threading.Lock()
        self._tenants: dict[str, Tenant] = {}
        self._pipelines: dict[str, Pipeline] = {}
        self._seat_users: dict[tuple[str, str], set[str]] = {}
        self._load()

    # --- хранение ---------------------------------------------------------------

    def _load(self) -> None:
        if self._path.exists():
            raw = json.loads(self._path.read_text(encoding="utf-8"))
            self._tenants = {t["id"]: Tenant(**t) for t in raw.get("tenants", [])}

    def _save_locked(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_suffix(".tmp")
        tmp.write_text(
            json.dumps({"tenants": [asdict(t) for t in self._tenants.values()]}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        tmp.replace(self._path)

    # --- управление (операторский API) -------------------------------------------

    def list(self) -> list[Tenant]:
        return sorted(self._tenants.values(), key=lambda t: t.created)

    def get(self, tenant_id: str) -> Tenant:
        try:
            return self._tenants[tenant_id]
        except KeyError:
            raise KeyError(f"Клиент '{tenant_id}' не найден") from None

    def create(self, tenant_id: str, name: str, seats: int = 10) -> tuple[Tenant, str, str]:
        """Новый клиент. Возвращает (клиент, админ-ключ, ключ канала) — ключи один раз."""
        if not _ID_RE.match(tenant_id):
            raise ValueError("id: 3–32 символа, латиница в нижнем регистре, цифры и дефис")
        if seats < 1:
            raise ValueError("seats: не меньше 1")
        with self._lock:
            if tenant_id in self._tenants:
                raise ValueError(f"Клиент '{tenant_id}' уже есть")
            tenant = Tenant(id=tenant_id, name=name, seats=seats)
            admin_key = f"aga_{tenant_id}_{secrets.token_urlsafe(24)}"
            channel_key = f"agk_{tenant_id}_{secrets.token_urlsafe(24)}"
            tenant.admin_key_hashes.append(_hash(admin_key))
            tenant.channel_key_hashes.append(_hash(channel_key))
            self._tenants[tenant_id] = tenant
            self._save_locked()
        return tenant, admin_key, channel_key

    def issue_channel_key(self, tenant_id: str) -> str:
        with self._lock:
            tenant = self.get(tenant_id)
            key = f"agk_{tenant_id}_{secrets.token_urlsafe(24)}"
            tenant.channel_key_hashes.append(_hash(key))
            self._save_locked()
        return key

    def revoke_channel_keys(self, tenant_id: str) -> None:
        """Отозвать все ключи канала (например, после увольнения админа клиента)."""
        with self._lock:
            self.get(tenant_id).channel_key_hashes.clear()
            self._save_locked()

    def update(self, tenant_id: str, *, enabled: bool | None = None, seats: int | None = None,
               name: str | None = None) -> Tenant:
        with self._lock:
            tenant = self.get(tenant_id)
            if enabled is not None:
                tenant.enabled = enabled
            if seats is not None:
                if seats < 1:
                    raise ValueError("seats: не меньше 1")
                tenant.seats = seats
            if name:
                tenant.name = name
            self._save_locked()
        return tenant

    # --- аутентификация -----------------------------------------------------------

    def authenticate(self, key: str) -> tuple[Tenant, str] | None:
        """(клиент, роль «admin» | «channel») по ключу или None.

        Клиент ищется по префиксу ключа, хеш сравнивается за постоянное время.
        Отключённый клиент не проходит.
        """
        m = re.match(r"^ag([ak])_([a-z0-9-]+)_", key or "")
        if not m:
            return None
        tenant = self._tenants.get(m.group(2))
        if tenant is None or not tenant.enabled:
            return None
        role = "admin" if m.group(1) == "a" else "channel"
        hashes = tenant.admin_key_hashes if role == "admin" else tenant.channel_key_hashes
        digest = _hash(key)
        if any(hmac.compare_digest(digest, h) for h in hashes):
            return tenant, role
        return None

    # --- изоляция данных ------------------------------------------------------------

    def data_dir(self, tenant_id: str) -> Path:
        return self._root / tenant_id

    def pipeline_for(self, tenant: Tenant) -> Pipeline:
        with self._lock:
            pipeline = self._pipelines.get(tenant.id)
            if pipeline is None:
                d = self.data_dir(tenant.id)
                d.mkdir(parents=True, exist_ok=True)
                pipeline = Pipeline(
                    policy_engine=PolicyEngine(d / "policy.json"),
                    mapping_store=MappingStore(ttl_seconds=self._ttl),
                    forwarder=self._forwarder,
                    audit=AuditLog(d / "audit.log"),
                    fail_closed=self._fail_closed,
                    custom_rules=CustomRuleRegistry(d / "detector_rules.json"),
                )
                self._pipelines[tenant.id] = pipeline
            return pipeline

    # --- места ------------------------------------------------------------------------

    def check_seat(self, tenant: Tenant, user: str, utc_offset_hours: int = 5) -> None:
        """Пустить сотрудника, если для него есть место в этом месяце."""
        tz = dt.timezone(dt.timedelta(hours=utc_offset_hours))
        now = dt.datetime.now(tz)
        month = now.strftime("%Y-%m")
        key = (tenant.id, month)
        with self._lock:
            users = self._seat_users.get(key)
        if users is None:
            # первый запрос клиента в этом месяце после старта: кто уже работал
            start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0).timestamp()
            audit = self.pipeline_for(tenant).audit
            users = {r.user for r in audit.read_all() if r.ts >= start}
        with self._lock:
            users = self._seat_users.setdefault(key, users)
            if user in users:
                return
            if len(users) >= tenant.seats:
                raise SeatLimitExceeded(
                    f"Все места заняты: {tenant.seats} сотрудников уже работают в этом месяце. "
                    "Администратор вашей компании может увеличить число мест."
                )
            users.add(user)
