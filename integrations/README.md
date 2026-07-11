# Интеграции (этап Э2)

## claude-code/ — IDE-hook для Claude Code

Проверка каждого промпта через шлюз до отправки в LLM. Чувствительные
промпты блокируются с подсказкой обезличенного варианта.

Подключение в `.claude/settings.json`:

```json
{
  "hooks": {
    "UserPromptSubmit": [
      {"hooks": [{"type": "command",
        "command": "python3 /opt/aigate/integrations/claude-code/aigate_prompt_hook.py"}]}
    ]
  }
}
```

Переменные: `AIGATE_AGENT_GATEWAY_URL`, `AIGATE_AGENT_CHANNEL_KEY`.
Fail-closed: при недоступном шлюзе промпты блокируются
(`AIGATE_HOOK_FAIL_OPEN=1` отключает — не рекомендуется).

## git/ — pre-commit hook против секретов

Локальный (офлайн) скан staged-изменений детекторами ядра: API-ключи,
`.env`, приватные ключи не попадут в историю репозитория.

```bash
cp integrations/git/pre-commit .git/hooks/pre-commit
chmod +x .git/hooks/pre-commit
```

## 1c/ — коннектор 1С (экспериментальный)

Общий модуль `АйГейтКлиент.bsl`: функции `ПроверитьТекст()` (dry-run
маскирование) и `СпроситьLLM()` (полный цикл через шлюз, канал egress).
Ключ LLM-провайдера в 1С не хранится; блокировки политики поднимаются
исключением (fail-closed).

Установка: добавить общий модуль в конфигурацию, создать константы
`АйГейтАдресШлюза` и `АйГейтКлючКанала`. Код — пример под 8.3.10+,
проверьте на своей конфигурации.

## Bitrix24 и другие веб-приложения

Всё, что умеет OpenAI-совместимый API, подключается без коннектора —
сменой `base_url` на `http://aigate.internal:8080/v1` (канал egress).
Нативный модуль Bitrix24 — в бэклоге этапа Э2.
