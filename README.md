# AI-Gate — универсальный AI-Gateway / GenAI-DLP

**Комплаенс-шлюз для безопасного использования LLM.** Перехватывает обращения
к внешним LLM (ChatGPT/Claude/Gemini и любые OpenAI-совместимые API),
детектирует чувствительные данные (ИИН/БИН/ФИО/счета/секреты), **обратимо
маскирует** их перед отправкой и восстанавливает в ответе — с tamper-evident
аудитом для регулятора.

Реализация MVP (этапы Э0–Э1) по [ТЗ](docs/TZ_universal_ai_gateway_dlp.md):
on-prem, data residency в РК, готовые политики под Закон РК № 94-V
(ст. 12 — локализация, ст. 16 — трансграничная передача) и Закон об ИИ № 230-VIII.

```
Каналы                          Ядро (общий конвейер)                Control Plane
┌─────────────────┐   ┌──────────────────────────────────────┐   ┌──────────────┐
│ Browser extension│──▶│ Ingress → Detector → Tokenizer ──────│   │ Admin console │
│ (ChatGPT/Claude/ │   │              │          │  Mapping   │◀──│ политики, RBAC│
│  Gemini)         │   │              ▼          │  Store     │   │ дашборды      │
├─────────────────┤   │        Policy Engine    │ (локально, │   ├──────────────┤
│ Egress reverse-  │──▶│      (mask/block/allow) │  эфемерно) │   │ Комплаенс-    │
│ proxy (RAG, n8n, │   │              │          └────────────┘   │ отчёты        │
│ приложения, API) │   │              ▼                       │   └──────────────┘
└─────────────────┘   │  Forwarder → LLM → Detokenizer       │
                       │        │                             │
                       │        ▼                             │
                       │  Audit Log (append-only, hash-chain) │
                       └──────────────────────────────────────┘
```

## Ключевые свойства

- **Обратимая токенизация, а не затирание**: `ИИН 900715300005` →
  `[IIN_1]` наружу, обратно `900715300005` в ответе. Пользователь получает
  осмысленный ответ, а не `[REDACTED]`.
- **Детект по контрольным суммам**: ИИН/БИН валидируются по официальному
  взвешенному алгоритму РК (не «любые 12 цифр»), IBAN — по ISO 13616 (mod-97),
  карты — по алгоритму Луна. Секреты — регэкспы известных форматов
  (OpenAI/Anthropic/AWS/GitHub/Slack/JWT/PEM/.env) + энтропия Шеннона.
- **ФИО с казахской спецификой**: суффиксы `-улы/-ұлы/-кызы/-қызы/-тегі`,
  русские отчества, инициалы, контекстные двухсловные имена («клиент: Айдос
  Смагулов»).
- **Суммы/балансы и коммерческая тайна (Э2)**: суммы с валютой (тг/₸/KZT/₽/$/€)
  и после контекстных слов («баланс:», «сумма —»); настраиваемые правила
  организации (маркеры «коммерческая тайна»/ДСП, кастомные регэкспы) —
  управление через admin API без перезапуска.
- **Fail-closed по умолчанию**: любая ошибка детекта/парсинга → блокировка,
  а не пропуск. Цена ложного пропуска (утечка + штраф до 2000 МРП по ст. 79
  КоАП) выше цены ложной блокировки.
- **Всё чувствительное — локально** (ст. 12): Mapping Store — только в памяти,
  значения зашифрованы, TTL + гарантированное уничтожение после ответа;
  в аудите значений нет — только типы и счётчики.
- **Tamper-evident аудит**: append-only JSONL с хэш-цепочкой SHA-256;
  подмена или удаление записи обнаруживается верификацией.
- **Zero-dependency ядро**: чистая stdlib Python 3.11+ — устанавливается
  и работает в air-gapped периметре без доступа к PyPI.
- **Мультипровайдер и локальная LLM (Э3)**: реестр провайдеров (OpenAI-совместимые,
  включая vLLM/Ollama/LM Studio, и Anthropic Messages API); выбор через
  `provider/model` или политику; правило «запросы с чувствительными данными —
  только на локальную LLM» (`sensitive_provider`).
- **Семантический детект (Э3)**: LLM-judge поверх уже обезличенного текста
  ловит утечки «по смыслу» (планы сделок, финпоказатели); режимы
  flag/block, fail-closed при недоступности судьи.

## Быстрый старт

```bash
cp .env.example .env      # указать AIGATE_PROVIDER_API_KEY
python -m gateway.main    # шлюз на :8080
```

или в контейнере (on-prem):

```bash
docker compose up -d
```

Консоль Control Plane: `http://localhost:8080/admin`
(без `AIGATE_ADMIN_TOKEN` доступна только с localhost).

### Канал «Egress» (внутренние приложения, RAG, n8n/Make)

Приложение меняет `base_url` OpenAI-клиента на шлюз — больше ничего:

```python
from openai import OpenAI
client = OpenAI(base_url="http://aigate.internal:8080/v1", api_key="<ключ канала>")
resp = client.chat.completions.create(
    model="gpt-4o-mini",
    messages=[{"role": "user", "content": "Клиент Ержан Нурсултанулы, ИИН 900715300005..."}],
)
# наружу ушло: "Клиент [PERSON_1], ИИН [IIN_1]..."; в ответе значения восстановлены
```

Ключ реального провайдера живёт **только на шлюзе** (`AIGATE_PROVIDER_API_KEY`) —
не в приложениях и не в браузере.

### Канал «Браузер» (ChatGPT / Claude / Gemini)

1. `chrome://extensions` → «Режим разработчика» → «Загрузить распакованное» →
   каталог `extension/` (корпоративно — через GPO/MDM).
2. В настройках расширения указать адрес шлюза и ключ канала.
3. При отправке текст перехватывается **до** ухода на сайт, маскируется шлюзом,
   и только обезличенный текст попадает в веб-LLM; плейсхолдеры в ответе
   ассистента подменяются обратно на значения.

### Канал «Endpoint» (буфер обмена, Cursor / Claude Desktop) — Э1.5

Агент на рабочей станции перехватывает чувствительные данные в буфере
обмена до вставки в локальные LLM-приложения (то, что не видят ни браузер,
ни сетевой периметр) и детокенизирует скопированные ответы:

```bash
export AIGATE_AGENT_GATEWAY_URL=http://aigate.internal:8080
export AIGATE_AGENT_CHANNEL_KEY=<ключ канала>
python -m agent.main
```

Fail-closed: при недоступном шлюзе буфер с ПДн/секретами очищается.
Подробнее: [docs/endpoint-agent.md](docs/endpoint-agent.md).

### Прямой API

```bash
# проверка/маскирование без форварда (dry_run)
curl -s localhost:8080/v1/check -H 'Content-Type: application/json' \
  -d '{"text":"ИИН 900715300005","channel":"api","dry_run":true}'

# полный цикл: маскирование → LLM → восстановление
curl -s localhost:8080/v1/check -H 'Content-Type: application/json' \
  -d '{"text":"Проверь ИИН 900715300005"}'
```

## Политики (F4)

Действие на тип сущности: `mask` (обратимая токенизация), `block` (запрос не
уходит), `allow` (пропуск как есть — **только** с зафиксированным основанием;
логируется как трансграничная передача по ст. 16). Переопределения по каналу
и группе пользователей. По умолчанию: все ПДн — `mask`, секреты — `block`.

Управление: консоль `/admin` или `GET/PUT /admin/api/policy`.

## Аудит и отчётность (F7)

```bash
curl -s localhost:8080/admin/api/report -H 'Authorization: Bearer <токен>'
# {"total_requests": 152, "masked_entities": 340, "blocked": 12,
#  "plaintext_leaks": 0, "chain_integrity": true, ...}

# развёрнутый отчёт: по пользователям, каналам, дням (Э2)
curl -s 'localhost:8080/admin/api/report/detailed?since=1750000000' -H 'Authorization: Bearer <токен>'

# CSV-экспорт журнала для регулятора (без значений ПДн)
curl -s localhost:8080/admin/api/report.csv -H 'Authorization: Bearer <токен>' -o audit.csv

curl -s localhost:8080/admin/api/audit/verify -H 'Authorization: Bearer <токен>'
# {"chain_integrity": true, "broken_at_index": -1}

# настраиваемые правила детекта (маркеры коммерческой тайны и т.п.)
curl -s localhost:8080/admin/api/detector-rules -H 'Authorization: Bearer <токен>'

# провайдеры (Э3): ключи наружу не отдаются
curl -s localhost:8080/admin/api/providers -H 'Authorization: Bearer <токен>'
```

## Мультипровайдер и локальная LLM (Э3)

Провайдеры управляются через `PUT /admin/api/providers` (или напрямую в
`data/providers.json`); PUT без `api_key` сохраняет уже настроенный ключ:

```json
{
  "default": "openai",
  "providers": [
    {"name": "openai", "kind": "openai", "base_url": "https://api.openai.com/v1",
     "api_key": "sk-...", "model": "gpt-4o-mini"},
    {"name": "local", "kind": "openai", "base_url": "http://ollama.internal:11434/v1",
     "model": "llama3.1"},
    {"name": "claude", "kind": "anthropic", "base_url": "https://api.anthropic.com/v1",
     "api_key": "sk-ant-...", "model": "claude-sonnet-5"}
  ]
}
```

Маршрутизация: `"model": "local/llama3.1"` в запросе → провайдер `local`;
поле политики `"sensitive_provider": "local"` направляет **все запросы с
замаскированными данными** на локальную LLM — данные вообще не покидают
периметр (air-gapped сценарий Э3).

Семантический детект: `AIGATE_SEMANTIC_GUARD=flag|block` +
`AIGATE_SEMANTIC_GUARD_PROVIDER=local` — судья получает только
обезличенный текст.

## Тесты

```bash
python -m unittest discover -s tests -t .   # 137 тестов, без внешних зависимостей
```

Тесты покрывают критерии приёмки MVP (раздел 11 ТЗ): наружу не уходит ни одно
чувствительное значение (проверка «исходящего трафика» через провайдер-заглушку),
восстановление ответа, контрольные суммы ИИН/БИН, fail-closed, целостность
аудита, egress-канал end-to-end.

## Структура репозитория

```
gateway/
  main.py                 # точка входа
  server.py               # HTTP-слой (stdlib), каналы + Control Plane API
  config.py               # настройки из env/.env
  models.py               # модель данных (раздел 6 ТЗ)
  core/
    pipeline.py           # Ingress→Detector→Tokenizer→Policy→Forwarder→Detokenizer→Audit
    detectors/            # ИИН/БИН (контрольная сумма), ФИО, IBAN/PAN, секреты
    tokenizer.py          # обратимая токенизация / детокенизация
    mapping_store.py      # эфемерный шифрованный token↔value (TTL, purge)
    policy.py             # Policy Engine (mask/block/allow, fail-closed)
    forwarder.py          # OpenAI-совместимый провайдер (один в MVP)
    audit.py              # append-only лог с хэш-цепочкой + комплаенс-отчёт
    crypto.py             # шифрование Mapping Store (stdlib, PRF-CTR + HMAC)
  static/index.html       # админ-консоль
extension/                # браузерное расширение (Manifest V3, Chrome/Edge/Firefox)
agent/                    # endpoint-агент: clipboard + локальные приложения (Э1.5)
integrations/             # IDE-hook Claude Code, git pre-commit, 1С, MCP-сервер
tests/                    # unittest, 137 тестов
docs/                     # ТЗ, инструкции по каналам
```

## Дорожная карта (по ТЗ)

- **Э1.5** ✅: endpoint-агент (clipboard, Cursor/Claude Desktop) —
  см. [docs/endpoint-agent.md](docs/endpoint-agent.md).
- **Э2** (частично ✅): суммы/балансы, настраиваемые правила (коммерческая
  тайна, кастомные регэкспы), контекстные ФИО, IDE-hook Claude Code,
  git pre-commit, коннектор 1С, развёрнутые отчёты + CSV —
  см. [integrations/](integrations/README.md). Осталось: модуль Bitrix24,
  NER-модель для ФИО.
- **Э3** (частично ✅): мультипровайдер (OpenAI-совместимые + Anthropic),
  локальная LLM и маршрутизация чувствительных запросов, MCP-сервер для
  Claude Desktop, семантический ИИ-детект (LLM-judge). Осталось:
  стриминг, Kubernetes, экспансия детекторов на СНГ/ЦА.

## Ограничения MVP

- Стриминг ответов через egress-proxy не поддерживается (детокенизация
  требует полного ответа) — уберётся с инкрементальным детокенизатором.
- Детект ФИО — паттерновый (высокий recall на типовых форматах, на этапе 2
  дополняется NER-моделью с казахской спецификой).
- Один LLM-провайдер (любой OpenAI-совместимый endpoint, включая локальные
  vLLM/Ollama).
- Продукт снижает риск утечки, но не гарантирует «100% соответствие» —
  правоприменительная практика по трансграничной передаче в LLM в РК не
  устоялась (раздел 12 ТЗ).
