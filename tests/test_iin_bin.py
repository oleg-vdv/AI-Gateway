"""Критерий приёмки 4: детект ИИН/БИН работает по контрольной сумме —
не маскирует произвольные 12-значные числа и не пропускает валидные."""

import unittest

from gateway.core.detectors.iin_bin import checksum_valid, classify, detect_iin_bin
from gateway.models import EntityType

from .helpers import VALID_BIN, VALID_IIN


class TestChecksum(unittest.TestCase):
    def test_checksum_rejects_arbitrary_number(self):
        broken = VALID_IIN[:11] + str((int(VALID_IIN[11]) + 1) % 10)
        self.assertTrue(checksum_valid(VALID_IIN))
        self.assertFalse(checksum_valid(broken))

    def test_classify_iin(self):
        self.assertEqual(classify(VALID_IIN), EntityType.IIN)

    def test_classify_bin(self):
        self.assertEqual(classify(VALID_BIN), EntityType.BIN)

    def test_non_digits_rejected(self):
        self.assertFalse(checksum_valid("12345678901a"))
        self.assertFalse(checksum_valid("123"))


class TestDetector(unittest.TestCase):
    def test_random_12_digits_not_detected(self):
        self.assertEqual(detect_iin_bin("случайное число 111111111111 в тексте"), [])

    def test_detects_valid_iin_in_text(self):
        dets = detect_iin_bin(f"ИИН клиента {VALID_IIN}, оформить договор")
        self.assertEqual(len(dets), 1)
        self.assertEqual(dets[0].entity_type, EntityType.IIN)
        self.assertEqual(dets[0].value, VALID_IIN)

    def test_detects_valid_bin_in_text(self):
        dets = detect_iin_bin(f"БИН контрагента: {VALID_BIN}")
        self.assertEqual(len(dets), 1)
        self.assertEqual(dets[0].entity_type, EntityType.BIN)

    def test_no_match_inside_longer_number(self):
        self.assertEqual(detect_iin_bin(f"номер 9{VALID_IIN}"), [])

    def test_multiple_numbers(self):
        dets = detect_iin_bin(f"ИИН {VALID_IIN} и БИН {VALID_BIN}")
        self.assertEqual(
            {d.entity_type for d in dets}, {EntityType.IIN, EntityType.BIN}
        )


if __name__ == "__main__":
    unittest.main()
