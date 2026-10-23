"""Метрики качества детекта на синтетическом корпусе (tests/corpus.py).

Сущность считается пойманной, только если одна детекция того же типа
покрывает её целиком: частично замаскированное ФИО — это утечка, а не
успех. Ложное срабатывание — детекция, не пересекающаяся ни с одной
размеченной сущностью.

Запуск отчёта: python -I tools/eval_detection.py
"""

from __future__ import annotations

from collections import defaultdict

from gateway.core.detectors import detect_all

from .corpus import build_corpus


def evaluate(n: int = 500, seed: int = 2026) -> dict:
    docs, negatives = build_corpus(n, seed)
    caught: dict[str, int] = defaultdict(int)
    total: dict[str, int] = defaultdict(int)
    false_pos: dict[str, int] = defaultdict(int)
    detections_total: dict[str, int] = defaultdict(int)
    misses: list[str] = []

    for doc in docs:
        dets = detect_all(doc.text)
        for etype, start, end, pool in doc.gold:
            key = f"{etype}/{pool}" if pool else etype
            total[key] += 1
            if any(
                d.entity_type.value == etype and d.start <= start and d.end >= end
                for d in dets
            ):
                caught[key] += 1
            else:
                misses.append(f"{key}: {doc.text[start:end]!r} в {doc.text!r}")
        for d in dets:
            detections_total[d.entity_type.value] += 1
            if not any(d.start < e and s < d.end for _, s, e, _ in doc.gold):
                false_pos[d.entity_type.value] += 1

    negative_hits: list[str] = []
    for text in negatives:
        for d in detect_all(text):
            false_pos[d.entity_type.value] += 1
            detections_total[d.entity_type.value] += 1
            negative_hits.append(f"{d.entity_type.value}: {d.value!r} в {text!r}")

    recall = {k: caught[k] / total[k] for k in total}
    precision = {
        t: 1 - false_pos[t] / detections_total[t] for t in detections_total
    }
    return {
        "documents": len(docs),
        "negatives": len(negatives),
        "total": dict(total),
        "caught": dict(caught),
        "recall": recall,
        "precision": precision,
        "false_positives": dict(false_pos),
        "misses": misses,
        "negative_hits": negative_hits,
    }
