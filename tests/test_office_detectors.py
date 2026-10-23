"""Офисные ПДн (этап 0): ФИО без отчества, латиница, падежи; телефоны,
email, документы, даты рождения, адреса, госномера; восстановление новых
типов токенов."""

import unittest

from gateway.core.detectors import detect_all
from gateway.core.detectors.names import detect_person
from gateway.core.mapping_store import MappingStore
from gateway.core.tokenizer import detokenize, tokenize
from gateway.models import EntityType

from .helpers import VALID_IIN


def _found(text: str) -> list[tuple[str, str]]:
    return [(d.entity_type.value, d.value) for d in detect_all(text)]


class TestPersonWithoutPatronymic(unittest.TestCase):
    def assertPerson(self, text: str, expected: str):
        values = [d.value for d in detect_person(text)]
        self.assertIn(expected, values, f"{text!r} -> {values}")

    def test_name_surname(self):
        self.assertPerson("Айгуль Ахметова прислала договор", "Айгуль Ахметова")

    def test_surname_name(self):
        self.assertPerson("Ахметова Айгуль позвонила", "Ахметова Айгуль")

    def test_surname_after_patronymic_does_not_leak(self):
        self.assertPerson("Сергей Петрович Иванов подписал", "Сергей Петрович Иванов")

    def test_surname_before_kazakh_patronymic(self):
        self.assertPerson("Ахметова Айгуль Серикқызы", "Ахметова Айгуль Серикқызы")

    def test_genitive(self):
        self.assertPerson("письмо от Сергея Иванова", "Сергея Иванова")

    def test_latin(self):
        self.assertPerson("Customer Aigul Akhmetova called", "Aigul Akhmetova")
        self.assertPerson("Yerzhan Nursultanuly disputes", "Yerzhan Nursultanuly")

    def test_short_korean_surname(self):
        self.assertPerson("Айдос Смагулов и Жанна Ким", "Жанна Ким")

    def test_honorific(self):
        self.assertPerson("г-н Ахметов приехал", "Ахметов")

    def test_no_false_positive_on_places_and_companies(self):
        for text in (
            "Северный Казахстан и Алматы Менеджмент",
            "Евразийский Банк повысил ставки",
            "Вера в успех",
            "Народный Банк Казахстана",
        ):
            self.assertEqual(detect_person(text), [], text)


class TestContacts(unittest.TestCase):
    def test_phones(self):
        found = _found("звонить +7 701 123 45 67, 8 (727) 250-10-10, +998 90 123 45 67")
        phones = [v for t, v in found if t == "PHONE"]
        self.assertEqual(
            phones, ["+7 701 123 45 67", "8 (727) 250-10-10", "+998 90 123 45 67"]
        )

    def test_phone_does_not_eat_iin(self):
        found = _found(f"ИИН {VALID_IIN}")
        self.assertEqual(found, [("IIN", VALID_IIN)])

    def test_email(self):
        self.assertIn(("EMAIL", "a.b@mail.kz"), _found("почта a.b@mail.kz."))


class TestDocuments(unittest.TestCase):
    def test_kz_id_card_with_context(self):
        self.assertIn(
            ("ID_DOC", "041234567"),
            _found("удостоверение личности № 041234567 выдано МВД"),
        )

    def test_nine_digits_without_context_ignored(self):
        self.assertNotIn("ID_DOC", [t for t, _ in _found("артикул 041234567")])

    def test_kz_passport(self):
        self.assertIn(("ID_DOC", "N12345678"), _found("паспорт N12345678"))

    def test_ru_passport(self):
        self.assertIn(("ID_DOC", "45 06 123456"), _found("паспорт серия 45 06 123456"))

    def test_birthdate_context(self):
        self.assertIn(("BIRTHDATE", "12.03.1985"), _found("дата рождения: 12.03.1985"))
        self.assertIn(("BIRTHDATE", "5 марта 1990 г."), _found("родилась 5 марта 1990 г."))

    def test_plain_date_is_not_birthdate(self):
        self.assertEqual(_found("договор от 12.03.2025"), [])


class TestAddressAndPlate(unittest.TestCase):
    def test_ru_address(self):
        self.assertIn(
            ("ADDRESS", "г. Алматы, ул. Абая, д. 10, кв. 5"),
            _found("адрес: г. Алматы, ул. Абая, д. 10, кв. 5"),
        )

    def test_microdistrict(self):
        self.assertIn(("ADDRESS", "мкр. Самал-2, 58"), _found("мкр. Самал-2, 58"))

    def test_kazakh_order(self):
        self.assertIn(("ADDRESS", "Абай даңғылы, 150"), _found("Абай даңғылы, 150"))

    def test_abbreviation_not_address(self):
        self.assertEqual(_found("и пр. 5 штук"), [])

    def test_plate(self):
        self.assertIn(("PLATE", "123 ABC 02"), _found("машина 123 ABC 02 стоит"))

    def test_currency_is_not_plate(self):
        self.assertNotIn("PLATE", [t for t, _ in _found("перевели 100 USD 12 раз")])


class TestNewTokensRestore(unittest.TestCase):
    def test_roundtrip_all_new_types(self):
        text = (
            "Айгуль Ахметова, +7 701 123 45 67, a.b@mail.kz, паспорт N12345678, "
            "дата рождения: 12.03.1985, ул. Абая, д. 10, машина 123 ABC 02"
        )
        dets = detect_all(text)
        types = {d.entity_type for d in dets}
        self.assertLessEqual(
            {
                EntityType.PERSON,
                EntityType.PHONE,
                EntityType.EMAIL,
                EntityType.ID_DOC,
                EntityType.BIRTHDATE,
                EntityType.ADDRESS,
                EntityType.PLATE,
            },
            types,
        )
        store = MappingStore(ttl_seconds=60)
        masked = tokenize(text, dets, store, "s1")
        for det in dets:
            self.assertNotIn(det.value, masked)
        self.assertEqual(detokenize(masked, store, "s1"), text)


if __name__ == "__main__":
    unittest.main()
