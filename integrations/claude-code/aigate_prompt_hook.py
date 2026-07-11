#!/usr/bin/env python3
"""IDE-hook для Claude Code (Э2): проверка промпта через AI-Gate до отправки.

Подключение — в .claude/settings.json проекта или ~/.claude/settings.json:

    {
      "hooks": {
        "UserPromptSubmit": [
          {
            "hooks": [
              {
                "type": "command",
                "command": "python3 /opt/aigate/integrations/claude-code/aigate_prompt_hook.py"
              }
            ]
          }
        ]
      }
    }

Конфигурация: переменные окружения AIGATE_AGENT_GATEWAY_URL и
AIGATE_AGENT_CHANNEL_KEY (как у endpoint-агента).

Поведение:
  * чисто            -> промпт уходит как обычно;
  * найдены ПДн/секреты -> промпт БЛОКИРУЕТСЯ, в сообщении — обезличенный
    вариант, который можно вставить заново (маскировать «на лету» хук
    Claude Code не позволяет, поэтому fail-safe = блокировка);
  * шлюз недоступен  -> блокировка (fail-closed), если найдено локально...
    у хука нет локальных детекторов — блокируем при недоступности шлюза,
    отключается переменной AIGATE_HOOK_FAIL_OPEN=1 (не рекомендуется).

Только stdlib — работает на любой машине с Python 3.
"""

import json
import os
import sys
import urllib.error
import urllib.request


def main() -> int:
    try:
        event = json.load(sys.stdin)
    except ValueError:
        return 0  # некорректный ввод — не мешаем работе
    prompt = event.get("prompt", "")
    if not prompt.strip():
        return 0

    gateway = os.environ.get(
        "AIGATE_AGENT_GATEWAY_URL", "http://localhost:8080"
    ).rstrip("/")
    headers = {"Content-Type": "application/json"}
    key = os.environ.get("AIGATE_AGENT_CHANNEL_KEY", "")
    if key:
        headers["X-AIGate-Key"] = key

    req = urllib.request.Request(
        gateway + "/v1/check",
        data=json.dumps(
            {
                "text": prompt,
                "user": os.environ.get("USER", "ide-user"),
                "channel": "endpoint",
                "dry_run": True,
            }
        ).encode(),
        headers=headers,
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            result = json.loads(resp.read().decode())
    except (urllib.error.URLError, TimeoutError, ValueError) as e:
        if os.environ.get("AIGATE_HOOK_FAIL_OPEN") == "1":
            return 0
        _block(f"AI-Gate недоступен ({e}) — промпт заблокирован (fail-closed).")
        return 0

    verdict = result.get("verdict")
    if verdict == "clean":
        return 0
    if verdict == "masked":
        counts = ", ".join(
            f"{t}×{c}" for t, c in result.get("entity_counts", {}).items()
        )
        _block(
            "AI-Gate: промпт содержит чувствительные данные "
            f"({counts}) и заблокирован. Обезличенный вариант:\n\n"
            + (result.get("masked_text") or "")
        )
        return 0
    # blocked / fail_closed
    _block("AI-Gate: " + (result.get("message") or "промпт заблокирован политикой"))
    return 0


def _block(reason: str) -> None:
    print(
        json.dumps(
            {"decision": "block", "reason": reason},
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    sys.exit(main())
