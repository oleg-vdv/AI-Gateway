"""Пороги качества детекта на синтетическом корпусе (tests/corpus.py).

Пороги — нижняя граница, ниже которой изменение детекторов не принимается.
Корпус синтетический, поэтому цифры на нём — оптимистичная оценка;
реальные цифры клиента меряются на пилоте (docs/sales/pilot-report-template.md).
"""

import unittest

from .quality import evaluate


class TestDetectionQuality(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.report = evaluate()

    def test_structured_types_full_recall(self):
        for key in ("IIN", "PHONE", "EMAIL", "ID_DOC", "BIRTHDATE", "ADDRESS", "PLATE"):
            self.assertGreaterEqual(self.report["recall"][key], 0.99, key)

    def test_person_known_names(self):
        self.assertGreaterEqual(self.report["recall"]["PERSON/lexicon"], 0.98)

    def test_person_unknown_names(self):
        # имена, которых нет в словаре: ловятся по фамилии и отчеству
        self.assertGreaterEqual(self.report["recall"]["PERSON/heldout"], 0.95)

    def test_no_false_positives_on_clean_texts(self):
        self.assertEqual(self.report["negative_hits"], [])


if __name__ == "__main__":
    unittest.main()
