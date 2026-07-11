"""F3/F6: обратимая токенизация, консистентность, TTL, уничтожение маппинга."""

import time
import unittest

from gateway.core.detectors import detect_all
from gateway.core.mapping_store import MappingStore
from gateway.core.tokenizer import contains_tokens, detokenize, tokenize
from gateway.models import EntityType

from .helpers import VALID_BIN, VALID_IIN


class TestTokenizer(unittest.TestCase):
    def test_roundtrip(self):
        store = MappingStore()
        text = f"Проверь ИИН {VALID_IIN} и карту 4111 1111 1111 1111"
        masked = tokenize(text, detect_all(text), store, "s1")
        self.assertNotIn(VALID_IIN, masked)
        self.assertNotIn("4111", masked)
        self.assertIn("[IIN_1]", masked)
        self.assertIn("[CARD_1]", masked)

        restored = detokenize("Итог по [IIN_1] и [CARD_1]", store, "s1")
        self.assertIn(VALID_IIN, restored)
        self.assertIn("4111 1111 1111 1111", restored)

    def test_same_value_same_token(self):
        store = MappingStore()
        text = f"ИИН {VALID_IIN} повторяется: {VALID_IIN}"
        masked = tokenize(text, detect_all(text), store, "s1")
        self.assertEqual(masked.count("[IIN_1]"), 2)
        self.assertNotIn("[IIN_2]", masked)

    def test_typed_placeholders(self):
        store = MappingStore()
        text = f"{VALID_IIN} и {VALID_BIN} и Ержан Нурсултанулы"
        masked = tokenize(text, detect_all(text), store, "s1")
        for token in ("[IIN_1]", "[BIN_1]", "[PERSON_1]"):
            self.assertIn(token, masked)

    def test_purge_destroys_mapping(self):
        store = MappingStore()
        text = f"ИИН {VALID_IIN}"
        tokenize(text, detect_all(text), store, "s1")
        store.purge_session("s1")
        self.assertIsNone(store.resolve("s1", "[IIN_1]"))
        # детокенизация после purge оставляет плейсхолдер (не падает)
        self.assertEqual(
            detokenize("ответ про [IIN_1]", store, "s1"), "ответ про [IIN_1]"
        )

    def test_ttl_expiry(self):
        store = MappingStore(ttl_seconds=0)
        store.get_or_create_token("s1", VALID_IIN, EntityType.IIN)
        time.sleep(0.01)
        self.assertIsNone(store.resolve("s1", "[IIN_1]"))

    def test_sessions_isolated(self):
        store = MappingStore()
        t1 = store.get_or_create_token("s1", VALID_IIN, EntityType.IIN)
        self.assertIsNone(store.resolve("s2", t1))
        self.assertEqual(store.resolve("s1", t1), VALID_IIN)

    def test_values_encrypted_at_rest(self):
        store = MappingStore()
        store.get_or_create_token("s1", VALID_IIN, EntityType.IIN)
        entry = store._sessions["s1"]["[IIN_1]"]
        self.assertNotIn(VALID_IIN.encode(), entry.encrypted_value)

    def test_contains_tokens(self):
        self.assertTrue(contains_tokens("текст с [SECRET_2]"))
        self.assertFalse(contains_tokens("обычный текст [NOPE_1]"))


if __name__ == "__main__":
    unittest.main()
