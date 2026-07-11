"""Этап Э3: мультипровайдер (реестр, маршрутизация, форматы запросов),
маршрутизация чувствительных запросов на локальную LLM, семантический
детект (LLM-judge), admin API провайдеров, MCP-сервер."""

import json
import os
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path

from gateway.config import Settings
from gateway.core.audit import AuditLog
from gateway.core.forwarder import ProviderError
from gateway.core.mapping_store import MappingStore
from gateway.core.pipeline import Pipeline
from gateway.core.policy import PolicyEngine, default_policy
from gateway.core.providers import (
    MultiForwarder,
    ProviderConfig,
    ProviderRegistry,
    build_request,
    parse_response,
)
from gateway.core.semantic import SemanticGuard
from gateway.models import Channel, CheckRequest, Verdict
from gateway.server import make_server

from .helpers import VALID_IIN, FakeForwarder

ADMIN_TOKEN = "t3-admin"


def _registry(tmp: Path) -> ProviderRegistry:
    reg = ProviderRegistry(tmp / "providers.json")
    reg.save(
        [
            ProviderConfig(name="openai", base_url="https://api.example/v1",
                           api_key="k1", model="gpt-test"),
            ProviderConfig(name="local", base_url="http://127.0.0.1:11434/v1",
                           model="llama3"),
            ProviderConfig(name="claude", kind="anthropic",
                           base_url="https://api.anthropic.example/v1",
                           api_key="k2", model="claude-test"),
        ],
        default="openai",
    )
    return reg


class TestProviderRegistry(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)

    def test_seed_from_settings(self):
        reg = ProviderRegistry(
            self.tmp / "p.json",
            seed=ProviderConfig(name="openai", base_url="https://x/v1", api_key="k"),
        )
        self.assertEqual(reg.default_name, "openai")
        self.assertEqual(len(reg.list()), 1)

    def test_persistence_and_key_kept_on_put_without_key(self):
        reg = _registry(self.tmp)
        # PUT без ключа — старый ключ сохраняется
        providers = reg.list()
        for p in providers:
            p.api_key = "" if p.name == "openai" else p.api_key
        reg.save(providers, "openai")
        self.assertEqual(reg.get("openai").api_key, "k1")

    def test_default_must_be_in_list(self):
        reg = _registry(self.tmp)
        with self.assertRaises(ValueError):
            reg.save(reg.list(), "nonexistent")

    def test_disabled_provider_rejected(self):
        reg = _registry(self.tmp)
        providers = reg.list()
        for p in providers:
            if p.name == "local":
                p.enabled = False
        reg.save(providers, "openai")
        with self.assertRaises(ProviderError):
            reg.get("local")

    def test_file_permissions(self):
        reg = _registry(self.tmp)
        mode = (self.tmp / "providers.json").stat().st_mode & 0o777
        self.assertEqual(mode, 0o600)


class TestRequestFormats(unittest.TestCase):
    def test_openai_format(self):
        cfg = ProviderConfig(name="o", base_url="https://api.x/v1", api_key="sk")
        url, headers, body = build_request(cfg, [{"role": "user", "content": "hi"}], "m1")
        self.assertEqual(url, "https://api.x/v1/chat/completions")
        self.assertEqual(headers["Authorization"], "Bearer sk")
        payload = json.loads(body)
        self.assertEqual(payload["model"], "m1")

    def test_openai_local_no_key(self):
        cfg = ProviderConfig(name="local", base_url="http://127.0.0.1:11434/v1")
        _, headers, _ = build_request(cfg, [{"role": "user", "content": "hi"}], "llama3")
        self.assertNotIn("Authorization", headers)

    def test_anthropic_format(self):
        cfg = ProviderConfig(
            name="a", kind="anthropic", base_url="https://api.a/v1", api_key="ak"
        )
        url, headers, body = build_request(
            cfg,
            [
                {"role": "system", "content": "будь краток"},
                {"role": "user", "content": "hi"},
            ],
            "claude-x",
        )
        self.assertEqual(url, "https://api.a/v1/messages")
        self.assertEqual(headers["x-api-key"], "ak")
        payload = json.loads(body)
        self.assertEqual(payload["system"], "будь краток")
        self.assertIn("max_tokens", payload)
        self.assertTrue(all(m["role"] != "system" for m in payload["messages"]))

    def test_parse_openai_response(self):
        cfg = ProviderConfig(name="o")
        data = {"choices": [{"message": {"content": "ответ"}}]}
        self.assertEqual(parse_response(cfg, data), "ответ")

    def test_parse_anthropic_response(self):
        cfg = ProviderConfig(name="a", kind="anthropic")
        data = {"content": [{"type": "text", "text": "ответ"}]}
        self.assertEqual(parse_response(cfg, data), "ответ")

    def test_unknown_kind_rejected(self):
        with self.assertRaises(ValueError):
            ProviderConfig.from_dict({"name": "x", "kind": "gemini"})


class TestRouting(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.calls: list[tuple[str, str]] = []

        def transport(cfg, messages, model):
            self.calls.append((cfg.name, model))
            return f"ответ от {cfg.name}"

        self.forwarder = MultiForwarder(
            _registry(Path(self._tmp.name)), transport=transport
        )

    def test_default_provider(self):
        self.forwarder.complete("привет")
        self.assertEqual(self.calls, [("openai", "gpt-test")])

    def test_model_prefix_routing(self):
        self.forwarder.chat([{"role": "user", "content": "x"}], model="local/llama3.1")
        self.assertEqual(self.calls, [("local", "llama3.1")])

    def test_explicit_provider(self):
        self.forwarder.complete("x", provider="claude")
        self.assertEqual(self.calls, [("claude", "claude-test")])

    def test_unknown_provider_error(self):
        with self.assertRaises(ProviderError):
            self.forwarder.complete("x", provider="nope")

    def test_slash_in_model_without_provider_match(self):
        # "org/model" где org — не провайдер: уходит провайдеру по умолчанию
        self.forwarder.chat([{"role": "user", "content": "x"}], model="meta/llama")
        self.assertEqual(self.calls, [("openai", "meta/llama")])


class TestSensitiveRouting(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        tmp = Path(self._tmp.name)
        self.forwarder = FakeForwarder()
        engine = PolicyEngine(tmp / "policy.json")
        policy = default_policy()
        policy.sensitive_provider = "local"
        engine.save(policy)
        self.pipeline = Pipeline(
            policy_engine=engine,
            mapping_store=MappingStore(),
            forwarder=self.forwarder,
            audit=AuditLog(tmp / "audit.log"),
        )

    def test_sensitive_goes_to_local(self):
        self.pipeline.process(
            CheckRequest(text=f"ИИН {VALID_IIN}", channel=Channel.API)
        )
        self.assertEqual(self.forwarder.providers_used, ["local"])

    def test_clean_goes_to_default(self):
        self.pipeline.process(
            CheckRequest(text="напиши хайку про степь", channel=Channel.API)
        )
        self.assertEqual(self.forwarder.providers_used, [None])


class TestSemanticGuard(unittest.TestCase):
    class JudgeForwarder(FakeForwarder):
        """Судья: YES для текстов с меткой «секретный план», иначе NO."""

        def __init__(self, fail=False):
            super().__init__()
            self.judge_fail = fail

        def chat(self, messages, model=None, provider=None):
            if self.judge_fail:
                raise ProviderError("judge down")
            user_text = messages[-1]["content"]
            return "YES" if "секретный план" in user_text else "NO"

    def _pipeline(self, mode: str, judge_fail=False) -> Pipeline:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        tmp = Path(self._tmp.name)
        judge = self.JudgeForwarder(fail=judge_fail)
        forwarder = FakeForwarder()
        return Pipeline(
            policy_engine=PolicyEngine(tmp / "policy.json"),
            mapping_store=MappingStore(),
            forwarder=forwarder,
            audit=AuditLog(tmp / "audit.log"),
            semantic_guard=SemanticGuard(judge, mode=mode),
        )

    def test_block_mode_blocks_suspicious(self):
        pipeline = self._pipeline("block")
        result = pipeline.process(
            CheckRequest(text="вот наш секретный план поглощения", channel=Channel.API)
        )
        self.assertEqual(result.verdict, Verdict.BLOCKED)
        self.assertEqual(pipeline.forwarder.sent, [])

    def test_block_mode_passes_normal(self):
        pipeline = self._pipeline("block")
        result = pipeline.process(
            CheckRequest(text="напиши поздравление коллеге", channel=Channel.API)
        )
        self.assertEqual(result.verdict, Verdict.CLEAN)
        self.assertEqual(len(pipeline.forwarder.sent), 1)

    def test_flag_mode_passes_but_audits(self):
        pipeline = self._pipeline("flag")
        result = pipeline.process(
            CheckRequest(text="вот наш секретный план поглощения", channel=Channel.API)
        )
        self.assertEqual(result.verdict, Verdict.CLEAN)  # прошёл
        records = pipeline.audit.read_all()
        self.assertEqual(records[-1].policy_actions.get("SEMANTIC"), "flag")

    def test_block_mode_fail_closed_when_judge_down(self):
        pipeline = self._pipeline("block", judge_fail=True)
        result = pipeline.process(
            CheckRequest(text="любой обычный текст запроса", channel=Channel.API)
        )
        self.assertEqual(result.verdict, Verdict.BLOCKED)

    def test_flag_mode_skips_when_judge_down(self):
        pipeline = self._pipeline("flag", judge_fail=True)
        result = pipeline.process(
            CheckRequest(text="любой обычный текст запроса", channel=Channel.API)
        )
        self.assertEqual(result.verdict, Verdict.CLEAN)


class TestProvidersApi(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        tmp = Path(cls._tmp.name)
        settings = Settings(
            host="127.0.0.1",
            port=0,
            admin_token=ADMIN_TOKEN,
            policy_path=tmp / "policy.json",
            audit_log_path=tmp / "audit.log",
        )
        cls.pipeline = Pipeline(
            policy_engine=PolicyEngine(settings.policy_path),
            mapping_store=MappingStore(),
            forwarder=MultiForwarder(
                _registry(tmp), transport=lambda c, m, mo: "ok"
            ),
            audit=AuditLog(settings.audit_log_path),
        )
        cls.server = make_server(cls.pipeline, settings)
        cls.base = f"http://127.0.0.1:{cls.server.server_address[1]}"
        cls._thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls._thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls._tmp.cleanup()

    def _request(self, method, path, body=None):
        req = urllib.request.Request(
            self.base + path,
            data=json.dumps(body).encode() if body is not None else None,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {ADMIN_TOKEN}",
            },
            method=method,
        )
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                return resp.status, json.loads(resp.read().decode())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read().decode())

    def test_get_providers_hides_keys(self):
        status, data = self._request("GET", "/admin/api/providers")
        self.assertEqual(status, 200)
        self.assertEqual(data["default"], "openai")
        for p in data["providers"]:
            self.assertNotIn("api_key", p)
            self.assertIn("api_key_configured", p)

    def test_put_providers(self):
        status, data = self._request("GET", "/admin/api/providers")
        body = {
            "default": "local",
            "providers": [
                {"name": "local", "kind": "openai",
                 "base_url": "http://127.0.0.1:11434/v1", "model": "llama3"},
            ],
        }
        status, data = self._request("PUT", "/admin/api/providers", body)
        self.assertEqual(status, 200)
        self.assertEqual(data["default"], "local")

    def test_put_invalid_default_rejected(self):
        status, _ = self._request(
            "PUT",
            "/admin/api/providers",
            {"default": "ghost", "providers": [{"name": "local"}]},
        )
        self.assertEqual(status, 400)


class TestMcpServer(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import importlib.util

        spec = importlib.util.spec_from_file_location(
            "aigate_mcp_server",
            Path(__file__).resolve().parent.parent
            / "integrations/mcp/aigate_mcp_server.py",
        )
        cls.mcp = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.mcp)

        cls._tmp = tempfile.TemporaryDirectory()
        tmp = Path(cls._tmp.name)
        settings = Settings(
            host="127.0.0.1", port=0,
            policy_path=tmp / "policy.json", audit_log_path=tmp / "audit.log",
        )
        cls.pipeline = Pipeline(
            policy_engine=PolicyEngine(settings.policy_path),
            mapping_store=MappingStore(),
            forwarder=FakeForwarder(),
            audit=AuditLog(settings.audit_log_path),
        )
        cls.server = make_server(cls.pipeline, settings)
        os.environ["AIGATE_AGENT_GATEWAY_URL"] = (
            f"http://127.0.0.1:{cls.server.server_address[1]}"
        )
        cls._thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls._thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls._tmp.cleanup()
        os.environ.pop("AIGATE_AGENT_GATEWAY_URL", None)

    def test_initialize(self):
        resp = self.mcp.handle_request(
            {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}
        )
        self.assertEqual(resp["result"]["serverInfo"]["name"], "aigate")

    def test_notification_ignored(self):
        self.assertIsNone(
            self.mcp.handle_request({"jsonrpc": "2.0", "method": "notifications/initialized"})
        )

    def test_tools_list(self):
        resp = self.mcp.handle_request({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
        names = {t["name"] for t in resp["result"]["tools"]}
        self.assertEqual(names, {"aigate_check", "aigate_ask", "aigate_report"})

    def test_tool_check_masks(self):
        resp = self.mcp.handle_request(
            {
                "jsonrpc": "2.0", "id": 3, "method": "tools/call",
                "params": {
                    "name": "aigate_check",
                    "arguments": {"text": f"ИИН {VALID_IIN} клиента"},
                },
            }
        )
        text = resp["result"]["content"][0]["text"]
        self.assertIn("[IIN_1]", text)
        self.assertNotIn(VALID_IIN, text)
        self.assertFalse(resp["result"]["isError"])

    def test_tool_ask_restores(self):
        resp = self.mcp.handle_request(
            {
                "jsonrpc": "2.0", "id": 4, "method": "tools/call",
                "params": {
                    "name": "aigate_ask",
                    "arguments": {"prompt": f"проверь ИИН {VALID_IIN}"},
                },
            }
        )
        text = resp["result"]["content"][0]["text"]
        self.assertIn(VALID_IIN, text)  # восстановлено в ответе

    def test_tool_ask_blocked_secret(self):
        resp = self.mcp.handle_request(
            {
                "jsonrpc": "2.0", "id": 5, "method": "tools/call",
                "params": {
                    "name": "aigate_ask",
                    "arguments": {"prompt": "ключ sk-proj-Ab12Cd34Ef56Gh78Ij90KlMnOp"},
                },
            }
        )
        self.assertTrue(resp["result"]["isError"])

    def test_unknown_method(self):
        resp = self.mcp.handle_request({"jsonrpc": "2.0", "id": 6, "method": "resources/list"})
        self.assertIn("error", resp)


if __name__ == "__main__":
    unittest.main()
