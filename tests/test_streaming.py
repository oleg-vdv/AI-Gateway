"""Потоковые ответы (этап 1): разбор SSE провайдеров, пошаговая обратная
подстановка без обрывков меток и stream=true в egress-канале."""

import json
import tempfile
import threading
import unittest
import urllib.request
from pathlib import Path

from gateway.config import Settings
from gateway.core.audit import AuditLog
from gateway.core.mapping_store import MappingStore
from gateway.core.pipeline import Pipeline
from gateway.core.policy import PolicyEngine
from gateway.core.providers import ProviderConfig, parse_stream
from gateway.core.tokenizer import StreamDetokenizer
from gateway.models import EntityType
from gateway.server import make_server

from .helpers import VALID_IIN, FakeForwarder


class TestStreamDetokenizer(unittest.TestCase):
    def setUp(self):
        self.store = MappingStore()
        self.token = self.store.get_or_create_token("s", "Айгуль Ахметова", EntityType.PERSON)
        self.iin = self.store.get_or_create_token("s", VALID_IIN, EntityType.IIN)

    def _run(self, chunks):
        detok = StreamDetokenizer(self.store, "s")
        outputs = [detok.feed(c) for c in chunks] + [detok.flush()]
        return outputs

    def test_token_split_across_chunks(self):
        out = self._run(["Клиент [PERS", "ON_1", "] просит ", "ИИН [IIN_", "1]."])
        self.assertEqual("".join(out), f"Клиент Айгуль Ахметова просит ИИН {VALID_IIN}.")
        # ни один отданный кусок не содержит обрывка метки
        for piece in out:
            self.assertNotIn("[PERS", piece)
            self.assertNotIn("[IIN_", piece)

    def test_plain_bracket_is_not_held_forever(self):
        out = self._run(["массив [1, 2", ", 3] готов"])
        self.assertEqual("".join(out), "массив [1, 2, 3] готов")

    def test_unknown_token_left_as_is(self):
        self.assertEqual("".join(self._run(["[PERSON_9] ok"])), "[PERSON_9] ok")

    def test_trailing_partial_flushed(self):
        out = self._run(["конец [PERS"])
        self.assertEqual(out[0], "конец ")
        self.assertEqual(out[-1], "[PERS")


class TestParseStream(unittest.TestCase):
    def test_openai(self):
        cfg = ProviderConfig(name="o", kind="openai", base_url="x", model="m")
        lines = [
            b'data: {"choices":[{"delta":{"role":"assistant"}}]}\n',
            b"\n",
            b'data: {"choices":[{"delta":{"content":"\xd0\x9f\xd1\x80\xd0\xb8"}}]}\n',
            b'data: {"choices":[{"delta":{"content":"\xd0\xb2\xd0\xb5\xd1\x82"}}]}\n',
            b"data: [DONE]\n",
            b'data: {"choices":[{"delta":{"content":"after done"}}]}\n',
        ]
        self.assertEqual("".join(parse_stream(cfg, lines)), "Привет")

    def test_anthropic(self):
        cfg = ProviderConfig(name="a", kind="anthropic", base_url="x", model="m")
        lines = [
            b"event: message_start\n",
            b'data: {"type":"message_start"}\n',
            b'data: {"type":"content_block_delta","delta":{"type":"text_delta","text":"Hel"}}\n',
            b'data: {"type":"content_block_delta","delta":{"type":"text_delta","text":"lo"}}\n',
            b'data: {"type":"message_stop"}\n',
        ]
        self.assertEqual("".join(parse_stream(cfg, lines)), "Hello")


class StreamingForwarder(FakeForwarder):
    """Отвечает кусками, разрезая метки посередине, как настоящий провайдер."""

    def chat_stream(self, messages, model=None, provider=None):
        text = self.chat(messages, model=model, provider=provider)
        return iter([text[i : i + 3] for i in range(0, len(text), 3)])


class TestEgressStreaming(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        tmp = Path(cls._tmp.name)
        settings = Settings(
            host="127.0.0.1",
            port=0,
            policy_path=tmp / "policy.json",
            audit_log_path=tmp / "audit.log",
        )
        cls.forwarder = StreamingForwarder(reply_template="Ответ: {text}")
        cls.pipeline = Pipeline(
            policy_engine=PolicyEngine(settings.policy_path),
            mapping_store=MappingStore(),
            forwarder=cls.forwarder,
            audit=AuditLog(settings.audit_log_path),
        )
        cls.server = make_server(cls.pipeline, settings)
        cls.base = f"http://127.0.0.1:{cls.server.server_address[1]}"
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls._tmp.cleanup()

    def _stream(self, content):
        req = urllib.request.Request(
            self.base + "/v1/chat/completions",
            data=json.dumps(
                {"model": "m", "stream": True, "messages": [{"role": "user", "content": content}]}
            ).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            self.assertTrue(resp.headers["Content-Type"].startswith("text/event-stream"))
            raw = resp.read().decode()
        events = [line[6:] for line in raw.split("\n") if line.startswith("data: ")]
        self.assertEqual(events[-1], "[DONE]")
        chunks = [json.loads(e) for e in events[:-1]]
        text = "".join(c["choices"][0]["delta"].get("content", "") for c in chunks)
        return text, chunks

    def test_stream_restores_values_and_hides_them_from_provider(self):
        text, chunks = self._stream(f"Клиент Айгуль Ахметова, ИИН {VALID_IIN}")
        self.assertEqual(text, f"Ответ: Клиент Айгуль Ахметова, ИИН {VALID_IIN}")
        self.assertNotIn(VALID_IIN, self.forwarder.sent[-1])
        self.assertNotIn("Ахметова", self.forwarder.sent[-1])
        for c in chunks:
            self.assertNotRegex(c["choices"][0]["delta"].get("content", ""), r"\[[A-Z_]*$")
        self.assertEqual(chunks[-1]["choices"][0]["finish_reason"], "stop")
        self.assertEqual(chunks[-1]["aigate"]["masked_entities"], {"IIN": 1, "PERSON": 1})

    def test_stream_mapping_purged_after_response(self):
        self._stream(f"ИИН {VALID_IIN}")
        self.assertEqual(self.pipeline.store._reverse, {})


if __name__ == "__main__":
    unittest.main()
