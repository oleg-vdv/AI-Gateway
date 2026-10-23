"""HTTP-слой шлюза на стандартной библиотеке (ThreadingHTTPServer).

Каналы (F1):
  POST /v1/check            — канал «Браузер»/API: проверка/маскирование текста
  POST /v1/restore          — обратная подстановка для dry_run-каналов
  POST /v1/chat/completions — канал «Egress»: OpenAI-совместимый reverse-proxy (stream=true — SSE)
  POST /v1/file             — обезличить файл (DOCX/XLSX — копия, PDF/текст — текст)

Control Plane (F8):
  GET  /admin/api/policy, PUT /admin/api/policy
  GET  /admin/api/audit, /admin/api/audit/verify, /admin/api/report, /admin/api/config
  GET  /admin — консоль (статический UI)
  GET  /admin/api/report/monthly?month=YYYY-MM — отчёт владельцу (JSON)
  GET  /admin/report/monthly?month=YYYY-MM     — он же, страница для печати
  POST /admin/api/report/send?month=YYYY-MM    — отправить в Telegram

Страница «Проверь свой текст» (выключается AIGATE_TRY_PAGE=0):
  GET  /try                 — страница
  POST /try/api/preview     — что ушло бы провайдеру; ничего не хранит и не отправляет
  POST /try/api/file        — обезличенная копия файла для скачивания

Облачный режим (AIGATE_MULTI_TENANT=1): клиент определяется по ключу
(agk_<id>_… — канал, aga_<id>_… — админ клиента), у каждого свои политика,
журнал, правила и хранилище меток. Операторский API (AIGATE_OPERATOR_TOKEN):
  GET/POST /operator/api/tenants, PUT /operator/api/tenants/<id>,
  POST /operator/api/tenants/<id>/keys, POST /operator/api/tenants/<id>/keys/revoke

Корпоративный чат (выключается AIGATE_CHAT_PAGE=0):
  GET  /chat                — страница чата поверх /v1/chat/completions
  GET  /v1/models           — модели для выбора («провайдер/модель»)

Безопасность (раздел 8 ТЗ): ключи каналов (X-AIGate-Key / Bearer),
админ-токен (без него Control Plane доступен только с localhost),
сравнение ключей — constant-time (hmac.compare_digest).
"""

from __future__ import annotations

import base64
import datetime
import binascii
import hmac
import itertools
import json
import logging
import re
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from gateway import __version__
from gateway.config import Settings
from gateway.core import files, owner_report
from gateway.core.pipeline import Pipeline
from gateway.core.tenants import SeatLimitExceeded, TenantRegistry
from gateway.core.tokenizer import StreamDetokenizer
from gateway.core.providers import ProviderConfig
from gateway.models import Channel, CheckRequest, DetectorRule, Policy, Verdict

logger = logging.getLogger("aigate.server")

_STATIC_DIR = Path(__file__).parent / "static"
_MAX_BODY = 10 * 1024 * 1024  # 10 МБ
_MAX_PREVIEW_CHARS = 20_000  # страница проверки — для фрагментов, не для документов

# Дневные счётчики запросов по пользователям (AIGATE_DAILY_REQUEST_LIMIT).
# В памяти процесса: после перезапуска счёт начинается заново.
_usage: dict[tuple[str, str], int] = {}
_usage_lock = threading.Lock()


class ApiError(Exception):
    def __init__(self, status: int, detail: str):
        super().__init__(detail)
        self.status = status
        self.detail = detail


class GatewayHandler(BaseHTTPRequestHandler):
    # заполняются фабрикой make_server()
    pipeline: Pipeline = None  # type: ignore[assignment]
    settings: Settings = None  # type: ignore[assignment]
    # облачный режим: реестр клиентов; pipeline выбирается по ключу запроса
    tenants: TenantRegistry | None = None
    tenant = None

    server_version = f"AI-Gate/{__version__}"
    protocol_version = "HTTP/1.1"

    # --- инфраструктура -------------------------------------------------------

    def log_message(self, fmt, *args):  # не логировать URL с потенц. данными
        logger.info("%s %s", self.command, self.path.split("?")[0])

    def _drain_body(self) -> None:
        """Дочитать непрочитанное тело запроса перед ответом.

        Отказ (401, 403, 404) часто отправляется до чтения тела. Если закрыть
        соединение с непрочитанными данными, Windows рвёт его (WSAECONNABORTED)
        и клиент не получает даже код ошибки; при keep-alive остаток тела
        к тому же испортил бы следующий запрос.
        """
        if getattr(self, "_body_consumed", True):
            return
        self._body_consumed = True
        length = int(self.headers.get("Content-Length") or 0)
        if 0 < length <= _MAX_BODY:
            self.rfile.read(length)
        elif length > _MAX_BODY:
            self.close_connection = True  # столько читать не будем — соединение закроется

    def _send_json(self, status: int, payload: dict | list) -> None:
        self._drain_body()
        body = json.dumps(payload, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header(
            "Access-Control-Allow-Headers", "Content-Type, Authorization, X-AIGate-Key"
        )
        self.end_headers()
        self.wfile.write(body)

    def _read_json(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0:
            raise ApiError(400, "Пустое тело запроса")
        if length > _MAX_BODY:
            raise ApiError(413, "Тело запроса слишком большое")
        self._body_consumed = True
        try:
            return json.loads(self.rfile.read(length).decode())
        except (ValueError, UnicodeDecodeError):
            raise ApiError(400, "Некорректный JSON")

    def _query(self) -> dict[str, str]:
        if "?" not in self.path:
            return {}
        from urllib.parse import parse_qsl

        return dict(parse_qsl(self.path.split("?", 1)[1]))

    def _route(self) -> str:
        return self.path.split("?", 1)[0].rstrip("/") or "/"

    # --- аутентификация ---------------------------------------------------------

    def _presented_key(self) -> str:
        auth = self.headers.get("Authorization", "")
        return self.headers.get("X-AIGate-Key") or auth.removeprefix("Bearer ").strip()

    def _bind_tenant(self, admin: bool) -> None:
        found = self.tenants.authenticate(self._presented_key())
        if found is None:
            raise ApiError(401, "Неверный ключ")
        tenant, role = found
        if admin and role != "admin":
            raise ApiError(403, "Нужен админ-ключ компании")
        self.tenant = tenant
        self.pipeline = self.tenants.pipeline_for(tenant)

    def _check_seat(self, user: str) -> None:
        if self.tenant is None:
            return
        try:
            self.tenants.check_seat(self.tenant, user, self.settings.report_utc_offset)
        except SeatLimitExceeded as e:
            raise ApiError(403, str(e))

    def _require_operator(self) -> None:
        token = self.settings.operator_token
        if self.tenants is None or not token:
            raise ApiError(404, "Не найдено")
        presented = self._presented_key()
        if not presented or not hmac.compare_digest(presented, token):
            raise ApiError(401, "Неверный токен оператора")

    def _require_channel_auth(self) -> None:
        if self.tenants is not None:
            self._bind_tenant(admin=False)
            return
        allowed = [
            k.strip() for k in self.settings.channel_api_keys.split(",") if k.strip()
        ]
        if not allowed:
            return  # dev-режим: аутентификация каналов не настроена
        presented = self._presented_key()
        if not presented or not any(
            hmac.compare_digest(presented, k) for k in allowed
        ):
            raise ApiError(401, "Неверный ключ канала")

    def _require_admin_auth(self) -> None:
        if self.tenants is not None:
            # в облаке «с localhost без токена» не бывает: только админ-ключ клиента
            self._bind_tenant(admin=True)
            return
        if self.settings.admin_token:
            presented = self._presented_key()
            if not presented or not hmac.compare_digest(
                presented, self.settings.admin_token
            ):
                raise ApiError(401, "Неверный админ-токен")
            return
        client = self.client_address[0] if self.client_address else ""
        if client not in ("127.0.0.1", "::1"):
            raise ApiError(
                403,
                "Админ-токен не настроен: Control Plane доступен только с localhost",
            )

    # --- HTTP-методы ------------------------------------------------------------

    def do_OPTIONS(self):  # CORS preflight для браузерного расширения
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, PUT, OPTIONS")
        self.send_header(
            "Access-Control-Allow-Headers", "Content-Type, Authorization, X-AIGate-Key"
        )
        self.send_header("Content-Length", "0")
        self.end_headers()

    def _begin_request(self) -> None:
        # keep-alive: один экземпляр обработчика обслуживает несколько
        # запросов, привязка к клиенту не должна переходить между ними
        self.__dict__.pop("pipeline", None)
        self.tenant = None
        self._body_consumed = self.command not in ("POST", "PUT")

    def do_GET(self):
        self._begin_request()
        try:
            route = self._route()
            if route == "/healthz":
                self._send_json(200, {"status": "ok", "version": __version__})
            elif route == "/v1/models":
                self._require_channel_auth()
                self._send_json(200, {"object": "list", "data": self._models()})
            elif route == "/operator/api/tenants":
                self._require_operator()
                self._send_json(200, [t.public() for t in self.tenants.list()])
            elif route == "/chat" and self.settings.chat_page:
                self._send_static("chat.html")
            elif route == "/admin/api/policy":
                self._require_admin_auth()
                self._send_json(200, self.pipeline.policy.policy.to_dict())
            elif route == "/admin/api/audit":
                self._require_admin_auth()
                limit = int(self._query().get("limit", "100"))
                records = self.pipeline.audit.read_all()[-limit:]
                self._send_json(200, [r.to_dict() for r in records])
            elif route == "/admin/api/audit/verify":
                self._require_admin_auth()
                ok, broken_at = self.pipeline.audit.verify_chain()
                self._send_json(
                    200, {"chain_integrity": ok, "broken_at_index": broken_at}
                )
            elif route == "/admin/api/report":
                self._require_admin_auth()
                since = float(self._query().get("since", "0"))
                self._send_json(200, self.pipeline.audit.compliance_report(since))
            elif route == "/admin/api/report/detailed":
                self._require_admin_auth()
                q = self._query()
                until = float(q["until"]) if "until" in q else None
                self._send_json(
                    200,
                    self.pipeline.audit.detailed_report(
                        since=float(q.get("since", "0")), until=until
                    ),
                )
            elif route == "/admin/api/report/monthly":
                self._require_admin_auth()
                self._send_json(200, self._monthly_report())
            elif route == "/admin/report/monthly":
                self._require_admin_auth()
                body = owner_report.render_html(self._monthly_report()).encode()
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            elif route == "/admin/api/report.csv":
                self._require_admin_auth()
                since = float(self._query().get("since", "0"))
                csv_body = self.pipeline.audit.export_csv(since=since).encode()
                self.send_response(200)
                self.send_header("Content-Type", "text/csv; charset=utf-8")
                self.send_header(
                    "Content-Disposition", 'attachment; filename="aigate-audit.csv"'
                )
                self.send_header("Content-Length", str(len(csv_body)))
                self.end_headers()
                self.wfile.write(csv_body)
            elif route == "/admin/api/detector-rules":
                self._require_admin_auth()
                if self.pipeline.custom_rules is None:
                    raise ApiError(404, "Настраиваемые правила не сконфигурированы")
                self._send_json(
                    200, [r.to_dict() for r in self.pipeline.custom_rules.rules]
                )
            elif route == "/admin/api/providers":
                self._require_admin_auth()
                registry = getattr(self.pipeline.forwarder, "registry", None)
                if registry is None:
                    raise ApiError(404, "Мультипровайдер не сконфигурирован")
                # ключи наружу не отдаются — только флаг api_key_configured
                self._send_json(
                    200,
                    {
                        "default": registry.default_name,
                        "providers": [p.to_dict() for p in registry.list()],
                    },
                )
            elif route == "/admin/api/config":
                self._require_admin_auth()
                s = self.settings
                self._send_json(
                    200,
                    {
                        "provider_name": s.provider_name,
                        "provider_base_url": s.provider_base_url,
                        "provider_model": s.provider_model,
                        "provider_key_configured": bool(s.provider_api_key),
                        "fail_closed": s.fail_closed,
                        "mapping_ttl_seconds": s.mapping_ttl_seconds,
                    },
                )
            elif route in ("/admin", "/admin/ui", "/admin/ui/index.html"):
                self._send_static("index.html")
            elif route == "/try" and self.settings.try_page:
                self._send_static("try.html")
            else:
                self._send_json(404, {"detail": "Не найдено"})
        except ApiError as e:
            self._send_json(e.status, {"detail": e.detail})
        except Exception:
            logger.exception("GET %s", self.path)
            self._send_json(500, {"detail": "Внутренняя ошибка"})

    def do_POST(self):
        self._begin_request()
        try:
            route = self._route()
            if route == "/v1/check":
                self._require_channel_auth()
                req = self._parse_check_request()
                self._check_seat(req.user)
                self._send_json(200, self.pipeline.process(req).to_dict())
            elif route == "/v1/restore":
                self._require_channel_auth()
                body = self._read_json()
                if not isinstance(body.get("session_id"), str) or not isinstance(
                    body.get("text"), str
                ):
                    raise ApiError(400, "Требуются поля session_id и text")
                restored = self.pipeline.restore(
                    body["session_id"], body["text"], purge=body.get("purge", True)
                )
                self._send_json(200, {"text": restored})
            elif route == "/v1/chat/completions":
                self._require_channel_auth()
                self._chat_completions()
            elif route == "/try/api/preview" and self.settings.try_page:
                text = self._read_json().get("text")
                if not isinstance(text, str) or not text.strip():
                    raise ApiError(400, "Вставьте текст для проверки")
                if len(text) > _MAX_PREVIEW_CHARS:
                    raise ApiError(
                        413, f"Текст длиннее {_MAX_PREVIEW_CHARS} символов — проверьте его частями"
                    )
                self._send_json(200, self.pipeline.preview(text))
            elif route == "/operator/api/tenants":
                self._require_operator()
                self._create_tenant()
            elif route.startswith("/operator/api/tenants/") and route.endswith("/keys"):
                self._require_operator()
                try:
                    key = self.tenants.issue_channel_key(route.split("/")[4])
                except KeyError as e:
                    raise ApiError(404, str(e))
                self._send_json(200, {"channel_key": key})
            elif route.startswith("/operator/api/tenants/") and route.endswith("/keys/revoke"):
                self._require_operator()
                try:
                    self.tenants.revoke_channel_keys(route.split("/")[4])
                except KeyError as e:
                    raise ApiError(404, str(e))
                self._send_json(200, {"revoked": True})
            elif route == "/admin/api/report/send":
                self._require_admin_auth()
                s = self.settings
                if not (s.telegram_bot_token and s.telegram_chat_id):
                    raise ApiError(
                        409, "Telegram не настроен: задайте AIGATE_TELEGRAM_BOT_TOKEN и AIGATE_TELEGRAM_CHAT_ID"
                    )
                report = self._monthly_report()
                try:
                    owner_report.send_telegram(
                        s.telegram_bot_token, s.telegram_chat_id, owner_report.render_text(report)
                    )
                except Exception as e:
                    raise ApiError(502, f"Отчёт не отправлен: {e}")
                self._send_json(200, {"sent": report["month"]})
            elif route == "/v1/file":
                self._require_channel_auth()
                self._file(keep_session=True)
            elif route == "/try/api/file" and self.settings.try_page:
                # страница проверки: обезличенная копия для скачивания,
                # восстанавливать по ней нечего — сессия сразу удаляется
                self._file(keep_session=False)
            else:
                self._send_json(404, {"detail": "Не найдено"})
        except ApiError as e:
            self._send_json(e.status, {"detail": e.detail})
        except Exception:
            logger.exception("POST %s", self.path)
            # fail-closed и на уровне HTTP: непредвиденная ошибка -> отказ
            self._send_json(500, {"detail": "Внутренняя ошибка, запрос отклонён"})

    def do_PUT(self):
        self._begin_request()
        try:
            route = self._route()
            if route.startswith("/operator/api/tenants/"):
                self._require_operator()
                body = self._read_json()
                try:
                    tenant = self.tenants.update(
                        route.split("/")[4],
                        enabled=body.get("enabled"),
                        seats=body.get("seats"),
                        name=body.get("name"),
                    )
                except KeyError as e:
                    raise ApiError(404, str(e))
                except (ValueError, TypeError) as e:
                    raise ApiError(400, str(e))
                self._send_json(200, tenant.public())
            elif route == "/admin/api/policy":
                self._require_admin_auth()
                try:
                    policy = Policy.from_dict(self._read_json())
                except (ValueError, KeyError, TypeError) as e:
                    raise ApiError(400, f"Некорректная политика: {e}")
                self.pipeline.policy.save(policy)
                self._send_json(200, policy.to_dict())
            elif route == "/admin/api/detector-rules":
                self._require_admin_auth()
                if self.pipeline.custom_rules is None:
                    raise ApiError(404, "Настраиваемые правила не сконфигурированы")
                body = self._read_json()
                if not isinstance(body, list):
                    raise ApiError(400, "Ожидается массив правил")
                try:
                    rules = [DetectorRule.from_dict(r) for r in body]
                    self.pipeline.custom_rules.save(rules)
                except (ValueError, KeyError, TypeError, re.error) as e:
                    raise ApiError(400, f"Некорректные правила: {e}")
                self._send_json(200, [r.to_dict() for r in rules])
            elif route == "/admin/api/providers":
                self._require_admin_auth()
                if self.tenants is not None:
                    raise ApiError(403, "В облаке провайдерами ИИ управляет оператор")
                registry = getattr(self.pipeline.forwarder, "registry", None)
                if registry is None:
                    raise ApiError(404, "Мультипровайдер не сконфигурирован")
                body = self._read_json()
                try:
                    providers = [
                        ProviderConfig.from_dict(p) for p in body.get("providers", [])
                    ]
                    registry.save(providers, str(body.get("default", "")))
                except (ValueError, KeyError, TypeError) as e:
                    raise ApiError(400, f"Некорректные провайдеры: {e}")
                self._send_json(
                    200,
                    {
                        "default": registry.default_name,
                        "providers": [p.to_dict() for p in registry.list()],
                    },
                )
            else:
                self._send_json(404, {"detail": "Не найдено"})
        except ApiError as e:
            self._send_json(e.status, {"detail": e.detail})
        except Exception:
            logger.exception("PUT %s", self.path)
            self._send_json(500, {"detail": "Внутренняя ошибка"})

    # --- обработчики -----------------------------------------------------------

    def _parse_check_request(self) -> CheckRequest:
        body = self._read_json()
        try:
            return CheckRequest.from_dict(body)
        except ValueError as e:
            raise ApiError(400, str(e))

    def _create_tenant(self) -> None:
        body = self._read_json()
        try:
            tenant, admin_key, channel_key = self.tenants.create(
                str(body.get("id", "")),
                str(body.get("name") or body.get("id", "")),
                int(body.get("seats", 10)),
            )
        except (ValueError, TypeError) as e:
            raise ApiError(400, str(e))
        self._send_json(
            201,
            {
                **tenant.public(),
                # показываются один раз: в реестре хранятся только хеши
                "admin_key": admin_key,
                "channel_key": channel_key,
            },
        )

    def _monthly_report(self) -> dict:
        """Отчёт за ?month=YYYY-MM; по умолчанию — за прошлый месяц."""
        month = self._query().get("month") or owner_report.previous_month(datetime.date.today())
        if not re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", month):
            raise ApiError(400, "month: формат YYYY-MM")
        return owner_report.build(
            self.pipeline.audit, month, self.settings.report_utc_offset
        )

    def _models(self) -> list[dict]:
        """Модели для выбора в чате: «провайдер/модель» из реестра."""
        registry = getattr(self.pipeline.forwarder, "registry", None)
        if registry is None:
            name = self.pipeline.forwarder.provider_name
            return [{"id": name, "object": "model", "owned_by": name, "local": False}]
        sensitive = self.pipeline.policy.policy.sensitive_provider
        return [
            {
                "id": f"{p.name}/{p.model}",
                "object": "model",
                "owned_by": p.name,
                "default": p.name == registry.default_name,
                # локальная модель: данные не покидают периметр вовсе
                "local": p.name == sensitive or "localhost" in p.base_url or "127.0.0.1" in p.base_url,
            }
            for p in registry.list()
            if p.enabled and p.model
        ]

    def _check_daily_limit(self, user: str) -> None:
        limit = self.settings.daily_request_limit
        if limit <= 0:
            return
        day = time.strftime("%Y-%m-%d")
        with _usage_lock:
            for key in [k for k in _usage if k[1] != day]:
                del _usage[key]  # вчерашние счётчики не копятся
            user = f"{self.tenant.id}:{user}" if self.tenant else user
            used = _usage.get((user, day), 0)
            if used >= limit:
                raise ApiError(
                    429, f"Дневной лимит {limit} запросов исчерпан. Он обновится завтра."
                )
            _usage[(user, day)] = used + 1

    def _file(self, keep_session: bool) -> None:
        """Обезличить файл: {filename, content_base64[, user, group, session_id]}."""
        body = self._read_json()
        filename = body.get("filename")
        content = body.get("content_base64")
        if not isinstance(filename, str) or not isinstance(content, str):
            raise ApiError(400, "Требуются поля filename и content_base64")
        try:
            data = base64.b64decode(content, validate=True)
        except (binascii.Error, ValueError):
            raise ApiError(400, "content_base64: некорректный base64")
        self._check_seat(str(body.get("user") or "file-user"))
        try:
            result = self.pipeline.sanitize_file(
                filename,
                data,
                user=str(body.get("user") or "file-user"),
                group=str(body.get("group") or "default"),
                session_id=body.get("session_id") if keep_session else None,
            )
        except files.UnsupportedFile as e:
            raise ApiError(415, str(e))
        if not keep_session:
            self.pipeline.store.purge_session(result["session_id"])
        stem = re.sub(r"[^\w\-. ]+", "_", Path(filename).stem)[:80] or "file"
        output = result.pop("data")
        suffix = result.pop("out_suffix")
        result["filename"] = f"{stem}-обезличен{suffix}" if output is not None else None
        result["content_base64"] = base64.b64encode(output).decode() if output is not None else None
        if not keep_session:
            result.pop("session_id")
        self._send_json(200, result)

    def _sanitize_messages(
        self, messages: list, user: str, session_id: str, channel: Channel
    ) -> tuple[list[dict], dict[str, int]]:
        """Пропустить каждое сообщение через конвейер (dry_run, одна сессия)."""
        sanitized: list[dict] = []
        total_counts: dict[str, int] = {}
        for msg in messages:
            content = msg.get("content") if isinstance(msg, dict) else None
            if not isinstance(content, str) or not content:
                sanitized.append(msg)
                continue
            result = self.pipeline.sanitize(
                CheckRequest(
                    text=content,
                    user=user,
                    channel=channel,
                    session_id=session_id,
                    dry_run=True,
                )
            )
            if result.verdict == Verdict.BLOCKED:
                raise ApiError(403, result.message or "Заблокировано политикой")
            if result.verdict == Verdict.FAIL_CLOSED:
                raise ApiError(502, result.message or "Fail-closed")
            for k, v in result.entity_counts.items():
                total_counts[k] = total_counts.get(k, 0) + v
            sanitized.append({**msg, "content": result.masked_text})
        return sanitized, total_counts

    def _chat_completions(self, channel: Channel = Channel.EGRESS) -> None:
        """Канал «Egress»: внутренние приложения/RAG/пайплайны меняют base_url
        на шлюз; каждое сообщение проходит конвейер, ответ детокенизируется.

        stream=true: ответ уходит клиенту по частям (SSE в формате OpenAI),
        значения восстанавливаются на лету StreamDetokenizer'ом."""
        body = self._read_json()
        messages = body.get("messages")
        if not isinstance(messages, list) or not messages:
            raise ApiError(400, "Требуется непустой массив messages")

        session_id = uuid.uuid4().hex
        user = str(body.get("user") or "egress-app")
        model = body.get("model")
        self._check_daily_limit(user)
        self._check_seat(user)
        try:
            sanitized_messages, total_counts = self._sanitize_messages(
                messages, user, session_id, channel
            )
            # что именно увидел провайдер в последнем сообщении пользователя:
            # только метки, без значений — чат показывает это по кнопке
            masked_last = next(
                (
                    m.get("content")
                    for m in reversed(sanitized_messages)
                    if isinstance(m, dict) and m.get("role") == "user"
                    and isinstance(m.get("content"), str)
                ),
                None,
            )
            aigate_info = {"masked_entities": total_counts, "masked_last_user": masked_last}
            # Э3: чувствительные запросы — на выделенный провайдер политики
            provider = None
            if total_counts and self.pipeline.policy.policy.sensitive_provider:
                provider = self.pipeline.policy.policy.sensitive_provider

            if body.get("stream"):
                self._stream_chat(
                    sanitized_messages, model, provider, session_id, aigate_info
                )
                return

            try:
                answer = self.pipeline.forwarder.chat(
                    sanitized_messages, model=model, provider=provider
                )
            except Exception as e:
                raise ApiError(502, f"Провайдер недоступен: {e}")
            restored = self.pipeline.restore(session_id, answer, purge=True)
        finally:
            self.pipeline.store.purge_session(session_id)

        self._send_json(
            200,
            {
                "id": f"aigate-{session_id}",
                "object": "chat.completion",
                "created": int(time.time()),
                "model": model or self.pipeline.forwarder.provider_name,
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": restored},
                        "finish_reason": "stop",
                    }
                ],
                "aigate": {**aigate_info, "session_id": session_id},
            },
        )

    def _stream_chat(
        self,
        messages: list[dict],
        model: str | None,
        provider: str | None,
        session_id: str,
        aigate_info: dict,
    ) -> None:
        forwarder = self.pipeline.forwarder
        try:
            if hasattr(forwarder, "chat_stream"):
                chunks = iter(forwarder.chat_stream(messages, model=model, provider=provider))
                first = next(chunks, "")
            else:  # провайдер без потоков: весь ответ одним куском
                first, chunks = forwarder.chat(messages, model=model, provider=provider), iter(())
        except Exception as e:
            # до первого байта ответа ещё можно вернуть обычную ошибку
            raise ApiError(502, f"Провайдер недоступен: {e}")

        completion_id = f"aigate-{session_id}"
        created = int(time.time())
        model_name = model or forwarder.provider_name

        def event(delta: dict, finish: str | None = None, extra: dict | None = None) -> bytes:
            payload = {
                "id": completion_id,
                "object": "chat.completion.chunk",
                "created": created,
                "model": model_name,
                "choices": [{"index": 0, "delta": delta, "finish_reason": finish}],
            }
            if extra:
                payload.update(extra)
            return b"data: " + json.dumps(payload, ensure_ascii=False).encode() + b"\n\n"

        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Connection", "close")  # конец потока = закрытие соединения
        self.end_headers()
        self.close_connection = True

        detok = StreamDetokenizer(self.pipeline.store, session_id)
        try:
            self.wfile.write(event({"role": "assistant", "content": ""}))
            for chunk in itertools.chain([first], chunks):
                text = detok.feed(chunk)
                if text:
                    self.wfile.write(event({"content": text}))
                    self.wfile.flush()
            tail = detok.flush()
            if tail:
                self.wfile.write(event({"content": tail}))
            self.wfile.write(
                event({}, "stop", {"aigate": aigate_info})
            )
            self.wfile.write(b"data: [DONE]\n\n")
        except (BrokenPipeError, ConnectionResetError):
            logger.info("Клиент закрыл поток %s", completion_id)
        except Exception as e:
            logger.warning("Поток %s прерван: %s", completion_id, e)
            err = {"error": {"message": f"Поток прерван: {e}", "type": "provider_error"}}
            try:
                self.wfile.write(b"data: " + json.dumps(err, ensure_ascii=False).encode() + b"\n\n")
            except OSError:
                pass

    def _send_static(self, name: str) -> None:
        path = (_STATIC_DIR / name).resolve()
        if not path.is_file() or _STATIC_DIR.resolve() not in path.parents:
            raise ApiError(404, "Не найдено")
        body = path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def make_server(
    pipeline: Pipeline, settings: Settings, tenants: TenantRegistry | None = None
) -> ThreadingHTTPServer:
    handler = type(
        "BoundGatewayHandler",
        (GatewayHandler,),
        {"pipeline": pipeline, "settings": settings, "tenants": tenants},
    )
    return ThreadingHTTPServer((settings.host, settings.port), handler)
