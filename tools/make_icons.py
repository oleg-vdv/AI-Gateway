"""Иконки расширения (16/32/48/128 px) на стандартной библиотеке.

Рисунок: лист в цвет штемпельной краски (#4B3F9E) с тремя строками текста,
одна строка закрыта жёлтым маркером — «данные скрыты». Сглаживание —
отрисовка в 8 раз крупнее и усреднение.

    python -I tools/make_icons.py
"""

from __future__ import annotations

import struct
import zlib
from pathlib import Path

INK = (0x4B, 0x3F, 0x9E)
WHITE = (0xFB, 0xFC, 0xFD)
MARKER = (0xF2, 0xC1, 0x4E)
SS = 8  # коэффициент сглаживания

OUT = Path(__file__).resolve().parent.parent / "extension" / "icons"


def _in_round_rect(x, y, x0, y0, x1, y1, r):
    if not (x0 <= x <= x1 and y0 <= y <= y1):
        return False
    cx = min(max(x, x0 + r), x1 - r)
    cy = min(max(y, y0 + r), y1 - r)
    return (x - cx) ** 2 + (y - cy) ** 2 <= r * r


def _shape(u: float, v: float):
    """Цвет точки в единичном квадрате [0,1]x[0,1] или None (прозрачно)."""
    if not _in_round_rect(u, v, 0.02, 0.02, 0.98, 0.98, 0.2):
        return None
    lines = [  # (y, x_end, marker)
        (0.30, 0.78, False),
        (0.50, 0.80, True),
        (0.70, 0.58, False),
    ]
    for y, x_end, marked in lines:
        if _in_round_rect(u, v, 0.20, y - 0.055, x_end, y + 0.055, 0.055):
            if marked and 0.36 <= u <= x_end:
                return MARKER
            return WHITE
    return INK


def render(size: int) -> bytes:
    rows = []
    n = size * SS
    for py in range(size):
        row = bytearray([0])  # фильтр строки PNG: None
        for px in range(size):
            acc = [0, 0, 0, 0]
            for sy in range(SS):
                for sx in range(SS):
                    color = _shape((px * SS + sx + 0.5) / n, (py * SS + sy + 0.5) / n)
                    if color:
                        acc[0] += color[0]
                        acc[1] += color[1]
                        acc[2] += color[2]
                        acc[3] += 1
            covered = acc[3]
            if covered:
                row += bytes(
                    [acc[0] // covered, acc[1] // covered, acc[2] // covered, covered * 255 // (SS * SS)]
                )
            else:
                row += bytes(4)
        rows.append(bytes(row))
    raw = b"".join(rows)

    def chunk(tag: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data))

    ihdr = struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0)  # RGBA, 8 бит
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", ihdr)
        + chunk(b"IDAT", zlib.compress(raw, 9))
        + chunk(b"IEND", b"")
    )


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    for size in (16, 32, 48, 128):
        (OUT / f"icon-{size}.png").write_bytes(render(size))
        print(OUT / f"icon-{size}.png")


if __name__ == "__main__":
    main()
