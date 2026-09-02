# AI-Gate — LLM compliance gateway / GenAI-DLP

[![License: Apache 2.0](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](LICENSE)
![Python 3.11+](https://img.shields.io/badge/Python-3.11+-3776AB.svg)
![Zero dependencies](https://img.shields.io/badge/dependencies-stdlib_only-9ece6a.svg)
![Tests](https://img.shields.io/badge/tests-137-success.svg)

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
    "Customer [PERSON_1], ID [NATIONAL_ID_1], disputes a charge..."

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
  `[NATIONAL_ID_1]` and comes back as `900715300005`. Users get a useful
  answer instead of a wall of `[REDACTED]`.
- **Checksum-validated detection.** National IDs (Kazakhstan IIN/BIN) are
  validated with the official weighted algorithm rather than "any 12 digits",
  IBANs by ISO 13616 (mod-97), card numbers by Luhn. Secrets are matched by
  known formats (OpenAI / Anthropic / AWS / GitHub / Slack / JWT / PEM / .env)
  plus Shannon entropy.
- **Name detection with regional specifics.** Kazakh patronymic suffixes
  (`-uly/-ұлы/-kyzy/-қызы/-tegi`), Russian patronymics, initials, and
  context-driven two-word names ("client: Aidos Smagulov").
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
# what left the network: "Customer [PERSON_1], ID [NATIONAL_ID_1]..."
# what came back: real values restored
```

The real provider API key lives **only on the gateway**
(`AIGATE_PROVIDER_API_KEY`) — never in applications, never in a browser.
Applications hold channel keys, so revoking one team's access is a config
change rather than a redeployment.

### Channel 2 — Browser (ChatGPT / Claude / Gemini)

1. `chrome://extensions` → Developer mode → Load unpacked → the `extension/`
   directory (deploy via GPO/MDM for an organization).
2. Set the gateway address and channel key in the extension settings.
3. Text is intercepted **before** it reaches the site, masked by the gateway,
   and only anonymized text enters the web LLM; placeholders in the
   assistant's reply are substituted back as it renders.

Manifest V3, works in Chrome, Edge and Firefox.

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
python -m unittest discover -s tests -t .   # 137 tests, no external dependencies
```

The suite covers the acceptance criteria: no sensitive value ever reaches the
provider (verified by inspecting "outbound traffic" through a stub provider),
response restoration, ID checksums, fail-closed behavior, audit-chain integrity,
and the egress channel end to end.

## Repository layout

```
gateway/
  main.py                 # entry point
  server.py               # HTTP layer (stdlib), channels + Control Plane API
  config.py               # settings from env/.env
  models.py               # data model
  core/
    pipeline.py           # Ingress→Detector→Tokenizer→Policy→Forwarder→Detokenizer→Audit
    detectors/            # national IDs (checksum), names, IBAN/PAN, secrets
    tokenizer.py          # reversible tokenization / detokenization
    mapping_store.py      # ephemeral encrypted token↔value store (TTL, purge)
    policy.py             # policy engine (mask/block/allow, fail-closed)
    forwarder.py          # OpenAI-compatible provider
    audit.py              # append-only log with hash chain + compliance report
    crypto.py             # Mapping Store encryption (stdlib, PRF-CTR + HMAC)
  static/index.html       # admin console
extension/                # browser extension (Manifest V3, Chrome/Edge/Firefox)
agent/                    # endpoint agent: clipboard + local apps
integrations/             # Claude Code IDE hook, git pre-commit, 1C, MCP server
tests/                    # unittest, 137 tests
docs/                     # specification, channel guides
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

- **Streaming responses through the egress proxy are not supported** —
  detokenization needs the complete response. An incremental detokenizer is planned.
- **Name detection is pattern-based**: high recall on typical formats, but names
  are the hard case in every language. An NER model is the next step.
- **This reduces leakage risk; it does not by itself create legal compliance.**
  What it provides is the technical control and the evidence trail a compliance
  program needs underneath it. Enforcement practice around LLM cross-border
  transfer is still unsettled.

## License

Apache 2.0 — see [LICENSE](LICENSE).
