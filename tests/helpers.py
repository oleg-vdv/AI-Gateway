"""Общие помощники тестов: генерация валидных ИИН/БИН по контрольной сумме
и провайдер-заглушка, фиксирующая «исходящий трафик»."""

from __future__ import annotations

from gateway.core.detectors.iin_bin import checksum_valid
from gateway.core.forwarder import ProviderError


def make_valid_number(prefix11: str) -> str | None:
    """Достроить 12-й (контрольный) разряд к 11 цифрам; None если сумма даёт 10."""
    assert len(prefix11) == 11 and prefix11.isdigit()
    for check in range(10):
        if checksum_valid(prefix11 + str(check)):
            return prefix11 + str(check)
    return None


def make_valid_iin(base: str = "90071530012") -> str:
    """Валидный по контрольной сумме и структуре ИИН (дата 900715, 7-й разряд 3)."""
    for tail in range(100):
        candidate = base[:9] + f"{tail:02d}"
        full = make_valid_number(candidate)
        if full:
            return full
    raise RuntimeError("не удалось сгенерировать ИИН")


def make_valid_bin(base: str = "10084000012") -> str:
    """Валидный БИН: год 10, месяц 08, 5-й разряд 4, 6-й разряд 0."""
    for tail in range(100):
        candidate = base[:9] + f"{tail:02d}"
        full = make_valid_number(candidate)
        if full:
            return full
    raise RuntimeError("не удалось сгенерировать БИН")


VALID_IIN = make_valid_iin()
VALID_BIN = make_valid_bin()


class FakeForwarder:
    """Провайдер-заглушка: запоминает, что реально «ушло наружу»."""

    provider_name = "fake"

    def __init__(self, reply_template: str = "Ответ про {text}", fail: bool = False):
        self.sent: list[str] = []
        self.reply_template = reply_template
        self.fail = fail

    def complete(self, sanitized_text: str) -> str:
        if self.fail:
            raise ProviderError("provider down")
        self.sent.append(sanitized_text)
        # LLM «цитирует» плейсхолдеры в ответе
        return self.reply_template.format(text=sanitized_text)

    def chat(self, messages, model=None) -> str:
        joined = " ".join(
            m["content"] for m in messages if isinstance(m.get("content"), str)
        )
        return self.complete(joined)
