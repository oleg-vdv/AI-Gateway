# AI-Gate — LLM compliance gateway / GenAI-DLP

[![License: AGPL v3](https://img.shields.io/badge/license-AGPL--3.0-1F5C4E)](LICENSE)
[![Commercial licence](https://img.shields.io/badge/commercial%20licence-available-8A6100)](COMMERCIAL.md)

![Python 3.11+](https://img.shields.io/badge/Python-3.11+-3776AB.svg)
![Zero dependencies](https://img.shields.io/badge/dependencies-stdlib_only-9ece6a.svg)
![Tests](https://img.shields.io/badge/tests-214-success.svg)

*[Русская версия](docs/README.ru.md)*
 
**Let people use LLMs without leaking data.** AI-Gate sits between your users
and external LLMs (ChatGPT / Claude / Gemini and any OpenAI-compatible API),
detects sensitive data in the prompt — national IDs, names, bank accounts,
API keys — **reversibly masks** it before the request leaves your network, and
restores the real values in the response. Every decision lands in a
tamper-evident audit log.

The point is that nobody has to change how they work. The user sends a normal
prompt and gets a normal answer; the vendor never receives a single piece of
personal data.

```
Prompt from your app:
    "Customer Yerzhan Nursultanuly, ID 900715300005, disputes a charge..."

What actually leaves the network:
    "Customer [PERSON_1], ID [IIN_1], disputes a charge..."

What the app receives back:
    "...for Yerzhan Nursultanuly (ID 900715300005), the charge can be..."
```

## Architecture

```
Channels                        Core (shared pipeline)               Control Plane
┌──────────────────┐   ┌──────────────────────────────────────┐   ┌───────────────┐
│ Browser extension│──▶│ Ingress → Detector → Tokenizer ──────│   │ Admin console  │
│ (ChatGPT/Claude/ │   │              │          │  Mapping   │◀──│ policies, RBAC │
│  Gemini)         │   │              ▼          │  Store     │   │ dashboards     │
├──────────────────┤   │        Policy Engine    │ (in-memory,│   ├───────────────┤
│ Egress reverse-  │──▶│      (mask/block/allow) │  ephemeral)│   │ Compliance     │
│ proxy (apps, RAG,│   │              │          └────────────┘   │ reports        │
│ n8n, APIs)       │   │              ▼                       │   └───────────────┘
├──────────────────┤   │  Forwarder → LLM → Detokenizer       │
│ Endpoint agent   │──▶│        │                             │
│ (clipboard, IDEs)│   │        ▼                             │
└──────────────────┘   │  Audit log (append-only, hash-chain) │
                       └──────────────────────────────────────┘
```

## Key properties

- **Reversible tokenization, not redaction.** `ID 900715300005` leaves as
  `[IIN_1]` and comes back as `900715300005`. Users get a useful
  answer instead of a wall of `[REDACTED]`.
- **Checksum-validated detection.** National IDs (Kazakhstan IIN/BIN) are
  validated with the official weighted algorithm rather than "any 12 digits",
  IBANs by ISO 13616 (mod-97), card numbers by Luhn. Secrets are matched by
  known formats (OpenAI / Anthropic / AWS / GitHub / Slack / JWT / PEM / .env)
  plus Shannon entropy.
- **Name detection with regional specifics.** Kazakh patronymic suffixes
  (`-uly/-ұлы/-kyzy/-қызы/-tegi`), Russian patronymics, initials,
  context-driven names ("client: Aidos Smagulov"), and names *without* a
  patronymic in either order ("Aigul Akhmetova", "Akhmetova Aigul") via a
  Kazakh/Russian first-name lexicon (Cyrillic, Latin, case forms) plus
  surname endings. Measured on a synthetic corpus: [docs/QUALITY.md](docs/QUALITY.md).
- **Office personal data.** Phone numbers (+7/8 and Central Asian codes),
  email, ID card and passport numbers, birth dates (by context), postal
  addresses (Russian and Kazakh word order) and Kazakhstan licence plates.
- **Amounts and trade secrets.** Currency amounts (KZT/₸/RUB/USD/EUR) and
  values following context words ("balance:", "amount —"); organization-specific
  rules (confidentiality markers, custom regexes) managed through the admin API
  without a restart.
- **Fail-closed by default.** Any detection or parsing error blocks the request
  instead of passing it through. A false block costs a retry; a false pass costs
  a breach notification.
- **Sensitive values never leave the host.** The Mapping Store is in-memory
  only, values encrypted, with a TTL and guaranteed destruction once the
  response is detokenized. The audit log stores types and counts — never values.
- **Tamper-evident audit.** Append-only JSONL with a SHA-256 hash chain;
  altering or deleting a record is detected by verification.
- **Zero-dependency core.** Pure Python 3.11+ standard library — installs and
  runs inside an air-gapped perimeter with no access to PyPI, and the supply-chain
  attack surface of a tool that sees every prompt stays at zero.
- **Multi-provider, including local models.** A provider registry covers
  OpenAI-compatible endpoints (vLLM, Ollama, LM Studio) and the Anthropic
  Messages API. A policy rule can route every request containing sensitive data
  to a local model only, so that data never leaves the perimeter at all.
- **Semantic detection.** An LLM-judge runs over the *already anonymized* text
  to catch meaning-level leaks that regexes structurally cannot — deal plans,
  financial forecasts. Modes: flag or block, fail-closed if the judge is
  unreachable.

## Quick start

```bash
cp .env.example .env      # set AIGATE_PROVIDER_API_KEY
python -m gateway.main    # gateway on :8080
```

or on-prem in a container:

```bash
docker compose up -d
```

Control Plane console: `http://localhost:8080/admin`
(without `AIGATE_ADMIN_TOKEN` it is reachable from localhost only).

### Channel 1 — Egress proxy (internal apps, RAG, n8n/Make)

The application changes the `base_url` of its OpenAI client. That is the
entire integration:

```python
from openai import OpenAI

client = OpenAI(base_url="http://aigate.internal:8080/v1", api_key="<channel key>")
resp = client.chat.completions.create(
    model="gpt-4o-mini",
    messages=[{"role": "user", "content": "Customer Yerzhan Nursultanuly, ID 900715300005..."}],
)
# what left the network: "Customer [PERSON_1], ID [IIN_1]..."
# what came back: real values restored
```

The real provider API key lives **only on the gateway**
(`AIGATE_PROVIDER_API_KEY`) — never in applications, never in a browser.
Applications hold channel keys, so revoking one team's access is a config
change rather than a redeployment.

### Channel 2 — Browser (ChatGPT / Claude / Gemini)

The extension intercepts a message **before** the site sends it, has the
gateway mask it and sends only the anonymized text. The reply on the site
keeps its placeholders; the extension's own panel lists what each placeholder
stands for (`[PERSON_1]` — the real name) and copies the reply with real
values. The web LLM never sees them.

> **Since 0.3.2 the extension never writes real values into the site's page.**
> Earlier versions replaced placeholders in the reply's DOM. ChatGPT keeps the
> conversation in the browser and syncs it, and its editable reply blocks treat
> a DOM change as a user edit — the restored values were saved to OpenAI after
> the fact. The panel lives in a closed shadow root outside `<body>`.

**Install (2 minutes):**

1. Start the gateway (see Quick start). For the browser channel set
   `AIGATE_MAPPING_TTL_SECONDS=28800` in `.env`: the default 600 s is too short
   for a chat, and after it expires old replies keep their placeholders.
   No provider API key is needed — the browser channel only masks
   (`dry_run`), it never forwards anything.
2. `chrome://extensions` (or `edge://extensions`) → Developer mode →
   Load unpacked → the `extension/` directory. For an organization, deploy
   via GPO/MDM.
3. Click the extension icon → **Check connection**. The badge shows `ON` when
   the gateway answers and `!` when it does not.

**Verify it actually works** — check what the vendor stores, not what your
browser shows:

1. Send `Customer Yerzhan Nursultanuly, IIN 900715300005, disputes a charge of
   150000 tenge. Draft a short reply that mentions his name and IIN.`
   A green notice lists what was masked; the reply on the page keeps
   `[PERSON_1]`, the AI-Gate panel shows the real name.
2. Open the same conversation **on another device** (phone app, or a browser
   profile without the extension). It must show `[PERSON_1]`, `[IIN_1]`,
   `[AMOUNT_1]` — exactly what the LLM vendor stores. Reloading the page in the
   same browser is not proof: ChatGPT may show its local copy.
3. DevTools → Network → the `conversation` request → Payload: the `parts`
   field holds placeholders only.
4. Stop the gateway and try to send again: the message is **not** sent
   (fail-closed), a red notice explains why.

**How it behaves:**

- One gateway session per conversation, so the same person keeps the same
  placeholder across messages and the model never confuses two people.
- Fail-closed: if the gateway is down or returns `block`, nothing is sent.
- Secrets (API keys, tokens) are blocked, not masked.
- Placeholders are found even when the site splits them across several DOM
  nodes. Real values are never written into the site's page — neither the
  message box nor the replies.
- Permissions are narrow: `localhost` only by default; a remote gateway's
  address is requested at runtime when you set it.
- Diagnostics: DevTools → Console, filter `AI-Gate` (no personal data is logged).

Manifest V3, Chrome / Edge / Firefox 121+. Changes: [extension/CHANGELOG.md](extension/CHANGELOG.md).

### Channel 3 — Endpoint agent (clipboard, Cursor / Claude Desktop)

The workstation agent catches sensitive data in the clipboard before it is
pasted into local LLM applications — the gap neither the browser nor the
network perimeter can see — and detokenizes copied responses:

```bash
export AIGATE_AGENT_GATEWAY_URL=http://aigate.internal:8080
export AIGATE_AGENT_CHANNEL_KEY=<channel key>
python -m agent.main
```

Fail-closed: if the gateway is unreachable, a clipboard containing personal
data or secrets is cleared. Details: [docs/endpoint-agent.md](docs/endpoint-agent.md).

### Direct API

```bash
# check/mask without forwarding (dry run)
curl -s localhost:8080/v1/check -H 'Content-Type: application/json' \
  -d '{"text":"ID 900715300005","channel":"api","dry_run":true}'

# full cycle: mask → LLM → restore
curl -s localhost:8080/v1/check -H 'Content-Type: application/json' \
  -d '{"text":"Check ID 900715300005"}'
```

### Streaming

`/v1/chat/completions` accepts `"stream": true` and answers with OpenAI-format
`chat.completion.chunk` events. Values are restored on the fly; a placeholder
split across chunks (`[PERS` + `ON_1]`) is held back until it is complete, so
the user never sees a broken token. Works with OpenAI-compatible and
Anthropic providers.

### Files

```bash
curl -s localhost:8080/v1/file -H 'Content-Type: application/json' \
  -d '{"filename":"contract.docx","content_base64":"'$(base64 -w0 contract.docx)'"}'
```

DOCX and XLSX come back as a masked copy in the same format (a name split
across Word runs is caught; an IIN in a numeric Excel cell becomes a
placeholder string). PDF (text layer) and text files come back as masked
text. The tokens live in the caller's session, so an AI answer about the file
is restored with `/v1/restore`.

### Office AI chat

`http://localhost:8080/chat` — a chat for employees over the egress channel:
streaming answers, model choice (`GET /v1/models`, local models marked), and
under each message a fold showing exactly what the AI received. History lives
only in the browser tab. `AIGATE_DAILY_REQUEST_LIMIT` caps requests per
employee per day.

### Monthly report for the owner

Requests, employees, values hidden by type, blocked requests, who pastes the
most client data, and audit-chain integrity for a calendar month — counters
only, no values. Printable page in the console (save as PDF), JSON at
`/admin/api/report/monthly`, and a Telegram message on
`AIGATE_REPORT_DAY` when `AIGATE_TELEGRAM_BOT_TOKEN` and
`AIGATE_TELEGRAM_CHAT_ID` are set.

### Cloud mode

`AIGATE_MULTI_TENANT=1` serves several companies from one gateway: each has
its own policy, audit log, detector rules and in-memory Mapping Store; only
the operator's provider keys are shared. The tenant is resolved from its key;
keys are stored as hashes only. Seats limit distinct employees per month.
Operator API and deployment: [docs/CLOUD.md](docs/CLOUD.md).

### "Check your text" page

`http://localhost:8080/try` — paste a letter or a form and see, side by side,
what stays with you and what an AI service would receive, or mask a whole
file and download the copy. Used for demos and
for letting a prospective customer test their own documents. The preview
endpoint (`POST /try/api/preview`) stores nothing — no Mapping Store entry, no
audit record — and forwards nothing. Turn it off with `AIGATE_TRY_PAGE=0`.

## Policies

Per entity type: `mask` (reversible tokenization), `block` (the request never
leaves), `allow` (pass through as-is — **only** with a recorded justification;
logged as a cross-border transfer). Overrides per channel and per user group.
Defaults: all personal data `mask`, secrets `block`.

Managed through the `/admin` console or `GET/PUT /admin/api/policy`.

## Audit and reporting

```bash
curl -s localhost:8080/admin/api/report -H 'Authorization: Bearer <token>'
# {"total_requests": 152, "masked_entities": 340, "blocked": 12,
#  "plaintext_leaks": 0, "chain_integrity": true, ...}

# detailed report: by user, channel, day
curl -s 'localhost:8080/admin/api/report/detailed?since=1750000000' -H 'Authorization: Bearer <token>'

# CSV export for a regulator (contains no personal data values)
curl -s localhost:8080/admin/api/report.csv -H 'Authorization: Bearer <token>' -o audit.csv

# verify the hash chain
curl -s localhost:8080/admin/api/audit/verify -H 'Authorization: Bearer <token>'
# {"chain_integrity": true, "broken_at_index": -1}
```

## Multi-provider and local LLM routing

Providers are managed via `PUT /admin/api/providers` (or directly in
`data/providers.json`); a PUT without `api_key` keeps the stored key:

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

Routing: `"model": "local/llama3.1"` in a request selects the `local` provider.
The policy field `"sensitive_provider": "local"` sends **every request
containing masked data** to the local model, so that data never leaves the
perimeter — the air-gapped deployment pattern.

Semantic detection: `AIGATE_SEMANTIC_GUARD=flag|block` plus
`AIGATE_SEMANTIC_GUARD_PROVIDER=local` — the judge only ever sees anonymized text.

## Tests

```bash
python -m unittest discover -s tests -t .   # 214 tests, no external dependencies
python -I tools/eval_detection.py            # detection quality report
```

The suite covers the acceptance criteria: no sensitive value ever reaches the
provider (verified by inspecting "outbound traffic" through a stub provider),
response restoration, ID checksums, fail-closed behavior, audit-chain integrity,
and the egress channel end to end. `tests/test_quality.py` fails the build if
detection recall on the synthetic corpus drops below the published thresholds.

## Repository layout

```
gateway/
  main.py                 # entry point
  server.py               # HTTP layer (stdlib), channels + Control Plane API
  config.py               # settings from env/.env
  models.py               # data model
  core/
    pipeline.py           # Ingress→Detector→Tokenizer→Policy→Forwarder→Detokenizer→Audit
    detectors/            # national IDs (checksum), names, contacts, documents, addresses, IBAN/PAN, secrets
    tokenizer.py          # reversible tokenization / detokenization
    mapping_store.py      # ephemeral encrypted token↔value store (TTL, purge)
    policy.py             # policy engine (mask/block/allow, fail-closed)
    forwarder.py          # OpenAI-compatible provider
    audit.py              # append-only log with hash chain + compliance report
    crypto.py             # Mapping Store encryption (stdlib, PRF-CTR + HMAC)
    files.py              # DOCX/XLSX masked copies, PDF text layer
    owner_report.py       # monthly report: page, Telegram, schedule
    tenants.py            # cloud mode: tenants, keys, seats, isolation
  static/index.html       # admin console
  static/try.html         # "check your text" page and file masking
  static/chat.html        # office AI chat
extension/                # browser extension (Manifest V3, Chrome/Edge/Firefox)
agent/                    # endpoint agent: clipboard + local apps
integrations/             # Claude Code IDE hook, git pre-commit, 1C, MCP server
tests/                    # unittest, 214 tests; synthetic quality corpus
tools/                    # quality report, extension icons and store packaging
docs/                     # specification, channel guides, QUALITY.md, extension/ (privacy, store listing)
```

## Regional compliance

The default policies are built for Kazakhstan's Personal Data Law No. 94-V
(art. 12 — localization, art. 16 — cross-border transfer) and AI Law
No. 230-VIII, with on-prem deployment and in-country data residency. The
detection logic generalizes: the entity types, checksum validators and name
patterns are configurable, and the architecture applies unchanged to GDPR or
any regime where prompts must not carry personal data to a foreign vendor.

## Limitations

Stated plainly, because a security tool that oversells itself earns the
criticism it gets:

- **Name detection is rule- and lexicon-based, not ML.** It misses an unknown
  first name next to a surname without a typical ending ("Emil Seitkali"),
  names written in lower case, and deliberately leaves a lone first name
  ("Hello, Aigul!") unmasked. The quality figures in
  [docs/QUALITY.md](docs/QUALITY.md) come from a synthetic corpus and are
  optimistic; real numbers are measured on a customer's own documents during a
  pilot. An optional NER model is the next step.
- **The browser extension does not inspect uploaded files**, only typed
  text; it warns when a file is attached. Files are masked through
  `/v1/file` or the "Mask a file" button on `/try`, then uploaded as a copy.
  Web LLM sites change their markup often, so interception may need updating
  after a site redesign.
- **PDF: text layer only.** Scans and fonts without a Unicode map are
  refused (fail-closed), not passed; a masked PDF comes back as text.
- **Cloud mode keeps counters in process memory.** Daily limits and seat
  counts restart with the process (seats are rebuilt from the audit log on
  first use), and one gateway process serves all tenants — run it behind a
  TLS reverse proxy in a Kazakhstan data centre, see [docs/CLOUD.md](docs/CLOUD.md).
- **This reduces leakage risk; it does not by itself create legal compliance.**
  What it provides is the technical control and the evidence trail a compliance
  program needs underneath it. Enforcement practice around LLM cross-border
  transfer is still unsettled.

## License

**AGPL-3.0-or-later** — see [LICENSE](LICENSE).

In practice that means:

- Run it inside your own company, change it, study it — freely, at no cost.
- **Offer it to third parties over a network** — as a hosted gateway, as part of
  a SaaS product, as a managed service — and the AGPL obliges you to release the
  source of that service under the same terms.
- If you cannot or will not open your source, a **commercial licence** removes
  that obligation: see [COMMERCIAL.md](COMMERCIAL.md).

Versions up to and including **0.1.0 remain under Apache-2.0**; nothing published
under that licence is taken back. The change applies from 0.2.0 onward, and the
copyright is held by a single author, so no contributor's rights are affected.
