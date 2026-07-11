#!/usr/bin/env python3
"""MCP-сервер AI-Gate (Э3): инструменты шлюза для MCP-хостов.

Позволяет Claude Desktop, Claude Code и другим MCP-хостам работать с LLM
через комплаенс-шлюз: агент вызывает инструмент вместо прямой отправки
чувствительного текста. Протокол — MCP по stdio (JSON-RPC 2.0,
newline-delimited), реализация на чистой stdlib.

Инструменты:
  * aigate_check   — проверить/замаскировать текст (dry-run);
  * aigate_ask     — задать вопрос LLM через шлюз (маскирование ->
                     провайдер -> восстановленный ответ);
  * aigate_report  — краткий комплаенс-отчёт (требует админ-токен).

Подключение в Claude Desktop (claude_desktop_config.json):

    {
      "mcpServers": {
        "aigate": {
          "command": "python3",
          "args": ["/opt/aigate/integrations/mcp/aigate_mcp_server.py"],
          "env": {
            "AIGATE_AGENT_GATEWAY_URL": "http://aigate.internal:8080",
            "AIGATE_AGENT_CHANNEL_KEY": "<ключ канала>"
          }
        }
      }
    }
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request

PROTOCOL_VERSION = "2024-11-05"
SERVER_INFO = {"name": "aigate", "version": "0.3.0"}

TOOLS = [
    {
        "name": "aigate_check",
        "description": (
            "Проверить текст на чувствительные данные (ИИН/БИН/ФИО/счета/"
            "секреты) и получить обезличенную версию. Используй ПЕРЕД тем, "
            "как передавать куда-либо текст с возможными персональными данными."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {"text": {"type": "string", "description": "Проверяемый текст"}},
            "required": ["text"],
        },
    },
    {
        "name": "aigate_ask",
        "description": (
            "Задать вопрос LLM через комплаенс-шлюз AI-Gate: чувствительные "
            "данные маскируются перед отправкой провайдеру и восстанавливаются "
            "в ответе. Используй для запросов, содержащих данные клиентов."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {"prompt": {"type": "string", "description": "Запрос к LLM"}},
            "required": ["prompt"],
        },
    },
    {
        "name": "aigate_report",
        "description": "Краткий комплаенс-отчёт шлюза (запросы/маскирования/блокировки).",
        "inputSchema": {"type": "object", "properties": {}},
    },
]


def _gateway(path: str, payload: dict | None, method: str = "POST") -> dict:
    base = os.environ.get("AIGATE_AGENT_GATEWAY_URL", "http://localhost:8080").rstrip("/")
    headers = {"Content-Type": "application/json"}
    key = os.environ.get("AIGATE_AGENT_CHANNEL_KEY", "")
    if key:
        headers["X-AIGate-Key"] = key
    admin = os.environ.get("AIGATE_AGENT_ADMIN_TOKEN", "")
    if admin and path.startswith("/admin/"):
        headers["Authorization"] = f"Bearer {admin}"
    req = urllib.request.Request(
        base + path,
        data=json.dumps(payload).encode() if payload is not None else None,
        headers=headers,
        method=method,
    )
    with urllib.request.urlopen(req, timeout=120) as resp:
        return json.loads(resp.read().decode())


def _tool_text(text: str, is_error: bool = False) -> dict:
    return {"content": [{"type": "text", "text": text}], "isError": is_error}


def call_tool(name: str, args: dict) -> dict:
    user = os.environ.get("USER", "mcp-user")
    try:
        if name == "aigate_check":
            r = _gateway(
                "/v1/check",
                {"text": args["text"], "user": user, "channel": "endpoint", "dry_run": True},
            )
            lines = [f"Вердикт: {r['verdict']}"]
            if r.get("entity_counts"):
                lines.append(
                    "Найдено: "
                    + ", ".join(f"{t}×{c}" for t, c in r["entity_counts"].items())
                )
            if r.get("masked_text") is not None:
                lines.append("Обезличенный текст:\n" + r["masked_text"])
            if r.get("message"):
                lines.append(r["message"])
            return _tool_text("\n".join(lines), is_error=r["verdict"] in ("blocked", "fail_closed"))
        if name == "aigate_ask":
            r = _gateway(
                "/v1/check",
                {"text": args["prompt"], "user": user, "channel": "endpoint"},
            )
            if r["verdict"] in ("blocked", "fail_closed"):
                return _tool_text(r.get("message") or "Запрос заблокирован", is_error=True)
            return _tool_text(r.get("answer") or "")
        if name == "aigate_report":
            r = _gateway("/admin/api/report", None, method="GET")
            return _tool_text(json.dumps(r, ensure_ascii=False, indent=2))
        return _tool_text(f"Неизвестный инструмент: {name}", is_error=True)
    except urllib.error.HTTPError as e:
        try:
            detail = json.loads(e.read().decode()).get("detail", "")
        except Exception:
            detail = ""
        return _tool_text(f"Шлюз ответил {e.code}: {detail}", is_error=True)
    except Exception as e:
        # fail-closed: ошибки не превращаются в «молчаливый пропуск»
        return _tool_text(f"AI-Gate недоступен: {e}", is_error=True)


def handle_request(msg: dict) -> dict | None:
    """Обработать один JSON-RPC запрос; None для нотификаций."""
    method = msg.get("method", "")
    msg_id = msg.get("id")
    if msg_id is None:
        return None  # нотификации (notifications/initialized и пр.)

    if method == "initialize":
        result = {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {"tools": {}},
            "serverInfo": SERVER_INFO,
        }
    elif method == "tools/list":
        result = {"tools": TOOLS}
    elif method == "tools/call":
        params = msg.get("params", {})
        result = call_tool(params.get("name", ""), params.get("arguments", {}))
    elif method == "ping":
        result = {}
    else:
        return {
            "jsonrpc": "2.0",
            "id": msg_id,
            "error": {"code": -32601, "message": f"Метод не поддерживается: {method}"},
        }
    return {"jsonrpc": "2.0", "id": msg_id, "result": result}


def main() -> None:
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except ValueError:
            continue
        response = handle_request(msg)
        if response is not None:
            sys.stdout.write(json.dumps(response, ensure_ascii=False) + "\n")
            sys.stdout.flush()


if __name__ == "__main__":
    main()
