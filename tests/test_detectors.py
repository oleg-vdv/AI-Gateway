"""Детекторы: секреты (ключи/.env/энтропия), ФИО (казахские форматы),
IBAN KZ, PAN, и разрешение пересечений в detect_all."""

import unittest

from gateway.core.detectors import detect_all
from gateway.core.detectors.financial import detect_card, detect_iban
from gateway.core.detectors.names import detect_person
from gateway.core.detectors.secrets import detect_secrets, shannon_entropy
from gateway.models import EntityType

from .helpers import VALID_IIN


class TestSecrets(unittest.TestCase):
    """Критерий приёмки 4: детект секретов ловит типовые API-ключи и .env."""

    def test_openai_key(self):
        dets = detect_secrets("ключ sk-proj-Ab12Cd34Ef56Gh78Ij90KlMnOpQrStUv, проверь")
        self.assertTrue(any(d.detector == "secret_openai_key" for d in dets))

    def test_anthropic_key(self):
        dets = detect_secrets("ANTHROPIC: sk-ant-api03-AbCdEf123456789012345678901234")
        self.assertTrue(any(d.detector == "secret_anthropic_key" for d in dets))

    def test_aws_key(self):
        self.assertTrue(detect_secrets("aws: AKIAIOSFODNN7EXAMPLE"))

    def test_github_token(self):
        self.assertTrue(
            detect_secrets("token ghp_AbCdEfGhIjKlMnOpQrStUvWxYz0123456789")
        )

    def test_env_file_content(self):
        env = "DB_PASSWORD=SuperSecret123!\nAPI_KEY: my-secret-value-42\n"
        values = {d.value for d in detect_secrets(env)}
        self.assertIn("SuperSecret123!", values)
        self.assertIn("my-secret-value-42", values)

    def test_private_key_pem(self):
        pem = "-----BEGIN RSA PRIVATE KEY-----\nMIIEow...\n-----END RSA PRIVATE KEY-----"
        dets = detect_secrets(pem)
        self.assertTrue(any(d.detector == "secret_private_key_pem" for d in dets))

    def test_connection_string(self):
        self.assertTrue(
            detect_secrets("postgres://admin:hunter2pass@db.internal:5432/prod")
        )

    def test_entropy_function(self):
        self.assertLess(shannon_entropy("aaaa"), 1.0)
        self.assertGreater(shannon_entropy("x9Kf2mQ7pL4wRz8vB3nT6yH1jD5g"), 4.0)

    def test_plain_text_not_secret(self):
        self.assertEqual(detect_secrets("обычный текст про степь и планы"), [])


class TestPerson(unittest.TestCase):
    def test_russian_fio(self):
        dets = detect_person("Договор подписал Петров Сергей Владимирович вчера")
        self.assertTrue(any("Петров Сергей Владимирович" in d.value for d in dets))

    def test_kazakh_patronymic_uly(self):
        dets = detect_person("Заявитель: Ержан Нурсултанулы, г. Алматы")
        self.assertTrue(any("Нурсултанулы" in d.value for d in dets))

    def test_kazakh_patronymic_kyzy(self):
        dets = detect_person("клиент Айгерим Болаткызы обратилась")
        self.assertTrue(any("Болаткызы" in d.value for d in dets))

    def test_kazakh_patronymic_tegi(self):
        dets = detect_person("подпись: Аскар Ахметтегі")
        self.assertTrue(any("Ахметтегі" in d.value for d in dets))

    def test_initials_format(self):
        dets = detect_person("Директор Сидоров А.Б. утвердил")
        self.assertTrue(any("Сидоров" in d.value for d in dets))


class TestFinancial(unittest.TestCase):
    def test_iban_kz_valid(self):
        dets = detect_iban("счёт KZ86125KZT5004100100 открыт")
        self.assertEqual(len(dets), 1)
        self.assertEqual(dets[0].entity_type, EntityType.IBAN)

    def test_iban_kz_invalid_checksum(self):
        self.assertEqual(detect_iban("счёт KZ00125KZT5004100100"), [])

    def test_card_luhn_valid(self):
        dets = detect_card("карта 4111 1111 1111 1111 активна")
        self.assertEqual(len(dets), 1)
        self.assertEqual(dets[0].entity_type, EntityType.CARD)

    def test_card_luhn_invalid(self):
        self.assertEqual(detect_card("число 4111 1111 1111 1112"), [])


class TestDetectAll(unittest.TestCase):
    def test_no_overlaps_and_all_types(self):
        text = (
            f"ИИН {VALID_IIN}, карта 4111 1111 1111 1111, "
            "ключ sk-proj-Ab12Cd34Ef56Gh78Ij90KlMnOpQrStUv, "
            "клиент Айгерим Болаткызы"
        )
        dets = detect_all(text)
        types = {d.entity_type for d in dets}
        self.assertLessEqual(
            {EntityType.IIN, EntityType.CARD, EntityType.SECRET, EntityType.PERSON},
            types,
        )
        spans = sorted((d.start, d.end) for d in dets)
        for (s1, e1), (s2, e2) in zip(spans, spans[1:]):
            self.assertLessEqual(e1, s2, "пересечение сущностей")


if __name__ == "__main__":
    unittest.main()
