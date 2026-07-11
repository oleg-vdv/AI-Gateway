"""HTTP-слой шлюза на стандартной библиотеке (ThreadingHTTPServer).

Каналы (F1):
  POST /v1/check            — канал «Браузер»/API: проверка/маскирование текста
  POST /v1/restore          — обратная подстановка для dry_run-каналов
  POST /v1/chat/completions — канал «Egress»: OpenAI-совместимый reverse-proxy

Control Plane (F8):
  GET  /admin/api/policy, PUT /admin/api/policy
  GET  /admin/api/audit, /admin/api/audit/verify, /admin/api/report, /admin/api/config
  GET  /admin — консоль (статический UI)

Безопасность (раздел 8 ТЗ): ключи каналов (X-AIGate-Key / Bearer),
админ-токен (без него Control Plane доступен только с localhost),
сравнение ключей — constant-time (hmac.compare_digest).
"""

from __future__ import annotations

import hmac
import json
import logging
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from gateway import __version__
from gateway.config import Settings
from gateway.core.pipeline import Pipeline
from gateway.models import Channel, CheckRequest, Policy, Verdict

logger = logging.getLogger("aigate.server")

_STATIC_DIR = Path(__file__).parent / "static"
_MAX_BODY = 10 * 1024 * 1024  # 10 МБ


class ApiError(Exception):
    def __init__(self, status: int, detail: str):
        super().__init__(detail)
        self.status = status
        self.detail = detail


class GatewayHandler(BaseHTTPRequestHandler):
    # заполняются фабрикой make_server()
    pipeline: Pipeline = None  # type: ignore[assignment]
    settings: Settings = None  # type: ignore[assignment]

    server_version = f"AI-Gate/{__version__}"
    protocol_version = "HTTP/1.1"

    # --- инфраструктура -------------------------------------------------------

    def log_message(self, fmt, *args):  # не логировать URL с потенц. данными
        logger.info("%s %s", self.command, self.path.split("?")[0])

    def _send_json(self, status: int, payload: dict | list) -> None:
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

    def _require_channel_auth(self) -> None:
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

    def do_GET(self):
        try:
            route = self._route()
            if route == "/healthz":
                self._send_json(200, {"status": "ok", "version": __version__})
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
            else:
                self._send_json(404, {"detail": "Не найдено"})
        except ApiError as e:
            self._send_json(e.status, {"detail": e.detail})
        except Exception:
            logger.exception("GET %s", self.path)
            self._send_json(500, {"detail": "Внутренняя ошибка"})

    def do_POST(self):
        try:
            route = self._route()
            if route == "/v1/check":
                self._require_channel_auth()
                req = self._parse_check_request()
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
            else:
                self._send_json(404, {"detail": "Не найдено"})
        except ApiError as e:
            self._send_json(e.status, {"detail": e.detail})
        except Exception:
            logger.exception("POST %s", self.path)
            # fail-closed и на уровне HTTP: непредвиденная ошибка -> отказ
            self._send_json(500, {"detail": "Внутренняя ошибка, запрос отклонён"})

    def do_PUT(self):
        try:
            if self._route() == "/admin/api/policy":
                self._require_admin_auth()
                try:
                    policy = Policy.from_dict(self._read_json())
                except (ValueError, KeyError, TypeError) as e:
                    raise ApiError(400, f"Некорректная политика: {e}")
                self.pipeline.policy.save(policy)
                self._send_json(200, policy.to_dict())
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

    def _chat_completions(self) -> None:
        """Канал «Egress»: внутренние приложения/RAG/пайплайны меняют base_url
        на шлюз; каждое сообщение проходит конвейер, ответ детокенизируется."""
        body = self._read_json()
        messages = body.get("messages")
        if not isinstance(messages, list) or not messages:
            raise ApiError(400, "Требуется непустой массив messages")
        if body.get("stream"):
            raise ApiError(
                400,
                "Стриминг в MVP не поддерживается: детокенизация требует полного ответа.",
            )

        session_id = uuid.uuid4().hex
        user = str(body.get("user") or "egress-app")
        sanitized_messages: list[dict] = []
        total_counts: dict[str, int] = {}
        try:
            for msg in messages:
                content = msg.get("content") if isinstance(msg, dict) else None
                if not isinstance(content, str) or not content:
                    sanitized_messages.append(msg)
                    continue
                result = self.pipeline.sanitize(
                    CheckRequest(
                        text=content,
                        user=user,
                        channel=Channel.EGRESS,
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
                sanitized_messages.append(
                    {**msg, "content": result.masked_text}
                )

            try:
                answer = self.pipeline.forwarder.chat(
                    sanitized_messages, model=body.get("model")
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
                "model": body.get("model") or self.pipeline.forwarder.provider_name,
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": restored},
                        "finish_reason": "stop",
                    }
                ],
                "aigate": {
                    "masked_entities": total_counts,
                    "session_id": session_id,
                },
            },
        )

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


def make_server(pipeline: Pipeline, settings: Settings) -> ThreadingHTTPServer:
    handler = type(
        "BoundGatewayHandler",
        (GatewayHandler,),
        {"pipeline": pipeline, "settings": settings},
    )
    return ThreadingHTTPServer((settings.host, settings.port), handler)
