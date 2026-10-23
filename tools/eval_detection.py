"""Отчёт о качестве детекта на синтетическом корпусе.

    python -I tools/eval_detection.py            # таблица в консоль
    python -I tools/eval_detection.py --misses   # плюс все пропуски

Цифры из этого отчёта публикуются в docs/QUALITY.md.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tests.quality import evaluate  # noqa: E402

_LABELS = {
    "IIN": "ИИН/БИН",
    "PERSON/lexicon": "ФИО, имя из словаря",
    "PERSON/heldout": "ФИО, имени нет в словаре",
    "PHONE": "Телефон",
    "EMAIL": "Email",
    "ID_DOC": "Удостоверение/паспорт",
    "BIRTHDATE": "Дата рождения",
    "ADDRESS": "Адрес",
    "PLATE": "Госномер",
}


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    r = evaluate()
    print(f"Документов: {r['documents']}, чистых текстов: {r['negatives']}\n")
    print(f"| {'Тип':<26} | Поймано | Всего | Полнота |")
    print(f"|{'-' * 28}|---------|-------|---------|")
    for key in sorted(r["total"], key=lambda k: list(_LABELS).index(k) if k in _LABELS else 99):
        label = _LABELS.get(key, key)
        print(
            f"| {label:<26} | {r['caught'].get(key, 0):>7} | {r['total'][key]:>5} "
            f"| {r['recall'][key]:>7.1%} |"
        )
    fp = sum(r["false_positives"].values())
    print(f"\nЛожных срабатываний: {fp} (в чистых текстах: {len(r['negative_hits'])})")
    if "--misses" in sys.argv:
        for line in r["misses"] + r["negative_hits"]:
            print(" -", line)


if __name__ == "__main__":
    main()
