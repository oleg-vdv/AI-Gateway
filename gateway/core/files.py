"""Файлы (этап 1): текст из DOCX, XLSX, PDF и TXT и обезличенная копия.

Каждый формат раскладывается на сегменты — абзацы, ячейки, строки — так,
чтобы сущность не разрывалась между ними: ФИО в Word часто разбито на
несколько «прогонов» (<w:r>) с разным форматированием, и детект по
отдельному прогону его бы не увидел. Сегмент маскируется целиком, и
DOCX/XLSX собираются обратно в тот же формат.

Подмена идёт по сырому XML, а не через ElementTree: ElementTree
переименовывает префиксы пространств имён (w: -> ns0:) и выбрасывает
«неиспользуемые» объявления, на которые ссылается mc:Ignorable, — Word
после этого считает файл повреждённым.

PDF разбирается только по текстовому слою (операторы Tj/TJ в потоках,
сжатых FlateDecode). Если текст не извлекается — скан или шрифт без
таблицы Unicode, — файл не считается проверенным и блокируется
(fail-closed). Обезличенный PDF не собирается: возвращается текст.
"""

from __future__ import annotations

import html
import io
import re
import zipfile
import zlib
from dataclasses import dataclass, field
from typing import Callable

MAX_FILE_BYTES = 8 * 1024 * 1024
TEXT_EXTENSIONS = {".txt", ".csv", ".md", ".json", ".log", ".tsv"}


class UnsupportedFile(Exception):
    """Формат не поддерживается."""


class UnreadableFile(Exception):
    """Файл повреждён или без извлекаемого текста — проверить нельзя."""


@dataclass
class FileDocument:
    kind: str                       # docx | xlsx | pdf | text
    segments: list[str]
    _rebuild: Callable[[list[str]], bytes] = field(repr=False)
    out_suffix: str = ""            # расширение обезличенной копии

    def rebuild(self, masked: list[str]) -> bytes:
        return self._rebuild(masked)


def _ext(filename: str) -> str:
    m = re.search(r"(\.[A-Za-z0-9]+)$", filename or "")
    return m.group(1).lower() if m else ""


def open_document(filename: str, data: bytes) -> FileDocument:
    if len(data) > MAX_FILE_BYTES:
        raise UnsupportedFile(f"Файл больше {MAX_FILE_BYTES // (1024 * 1024)} МБ")
    ext = _ext(filename)
    if ext == ".docx":
        return _open_docx(data)
    if ext == ".xlsx":
        return _open_xlsx(data)
    if ext == ".pdf":
        return _open_pdf(data)
    if ext in TEXT_EXTENSIONS:
        return _open_text(data, ext)
    raise UnsupportedFile(
        "Поддерживаются DOCX, XLSX, PDF с текстовым слоем и текстовые файлы "
        f"({', '.join(sorted(TEXT_EXTENSIONS))})"
    )


# --- текст -----------------------------------------------------------------


def _open_text(data: bytes, ext: str) -> FileDocument:
    for encoding in ("utf-8-sig", "cp1251"):
        try:
            text = data.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    else:
        raise UnreadableFile("Не удалось определить кодировку текста")
    lines = text.split("\n")
    return FileDocument(
        kind="text",
        segments=lines,
        _rebuild=lambda masked: "\n".join(masked).encode("utf-8"),
        out_suffix=ext,
    )


# --- общие помощники для OOXML ---------------------------------------------


def _read_zip(data: bytes) -> dict[str, bytes]:
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            return {info.filename: zf.read(info.filename) for info in zf.infolist()}
    except (zipfile.BadZipFile, KeyError, OSError) as e:
        raise UnreadableFile(f"Файл повреждён: {e}") from e


def _write_zip(parts: dict[str, bytes], order: list[str]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name in order:
            zf.writestr(name, parts[name])
    return buf.getvalue()


def _xml_text(s: str) -> str:
    return html.unescape(s)


def _xml_escape(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


class _BlockRewriter:
    """Сегменты — блоки XML (абзац <w:p>, строка <si>), внутри которых текст
    разложен по нескольким текстовым узлам. При сборке маскированный текст
    блока пишется в первый узел, остальные узлы блока опустошаются."""

    def __init__(self, block_re: re.Pattern, text_re: re.Pattern, open_tag: str):
        self.block_re = block_re
        self.text_re = text_re
        self.open_tag = open_tag

    def segments(self, xml: str) -> list[str]:
        out = []
        for block in self.block_re.finditer(xml):
            texts = [_xml_text(m.group(2)) for m in self.text_re.finditer(block.group())]
            out.append("".join(texts))
        return out

    def rebuild(self, xml: str, masked: list[str]) -> str:
        it = iter(masked)

        def fix_block(block: re.Match) -> str:
            new_text = next(it)
            original = "".join(_xml_text(m.group(2)) for m in self.text_re.finditer(block.group()))
            if new_text == original:
                return block.group()
            first = True

            def fix_text(m: re.Match) -> str:
                nonlocal first
                content = _xml_escape(new_text) if first else ""
                first = False
                attrs = m.group(1) or ""
                if 'xml:space="preserve"' not in attrs:
                    attrs += ' xml:space="preserve"'
                return f"<{self.open_tag}{attrs}>{content}</{self.open_tag}>"

            return self.text_re.sub(fix_text, block.group())

        return self.block_re.sub(fix_block, xml)


# --- DOCX ------------------------------------------------------------------

_DOCX_PARTS = re.compile(r"^word/(document|header\d*|footer\d*|footnotes|endnotes|comments)\.xml$")
_W_P = re.compile(r"<w:p[ >].*?</w:p>|<w:p/>", re.S)
_W_T = re.compile(r"<w:t((?:\s[^>]*)?)>(.*?)</w:t>", re.S)


def _open_docx(data: bytes) -> FileDocument:
    parts = _read_zip(data)
    order = list(parts)
    targets = [n for n in order if _DOCX_PARTS.match(n)]
    if "word/document.xml" not in targets:
        raise UnreadableFile("В DOCX нет word/document.xml")
    rw = _BlockRewriter(_W_P, _W_T, "w:t")
    xml = {n: parts[n].decode("utf-8") for n in targets}
    counts = {}
    segments: list[str] = []
    for n in targets:
        seg = rw.segments(xml[n])
        counts[n] = len(seg)
        segments += seg

    def rebuild(masked: list[str]) -> bytes:
        out = dict(parts)
        pos = 0
        for n in targets:
            chunk = masked[pos : pos + counts[n]]
            pos += counts[n]
            out[n] = rw.rebuild(xml[n], chunk).encode("utf-8")
        return _write_zip(out, order)

    return FileDocument(kind="docx", segments=segments, _rebuild=rebuild, out_suffix=".docx")


# --- XLSX ------------------------------------------------------------------

_SI = re.compile(r"<si>.*?</si>|<si/>", re.S)
_X_T = re.compile(r"<t((?:\s[^>]*)?)>(.*?)</t>", re.S)
_SHEET = re.compile(r"^xl/worksheets/sheet\d+\.xml$")
# ячейка со значением без формулы: <c r="A1" s="1"><v>900715300005</v></c>;
# числовые — без атрибута t или с t="n" (t="s" — индекс общей строки, не трогаем)
_VALUE_CELL = re.compile(r"<c(\s[^>]*)?><v>([^<]*)</v></c>")
_CELL_TYPE = re.compile(r'\st="([^"]*)"')


def _numeric_cells(xml: str) -> list[re.Match]:
    out = []
    for m in _VALUE_CELL.finditer(xml):
        t = _CELL_TYPE.search(m.group(1) or "")
        if t is None or t.group(1) == "n":
            out.append(m)
    return out
_INLINE_CELL = re.compile(r'<c[^>]*\st="inlineStr"[^>]*>.*?</c>', re.S)


def _open_xlsx(data: bytes) -> FileDocument:
    parts = _read_zip(data)
    order = list(parts)
    rw = _BlockRewriter(_SI, _X_T, "t")
    inline_rw = _BlockRewriter(_INLINE_CELL, _X_T, "t")
    shared = parts.get("xl/sharedStrings.xml", b"").decode("utf-8")
    sheets = sorted(n for n in order if _SHEET.match(n))
    if not sheets:
        raise UnreadableFile("В XLSX нет листов")
    sheet_xml = {n: parts[n].decode("utf-8") for n in sheets}

    segments = rw.segments(shared) if shared else []
    n_shared = len(segments)
    layout: list[tuple[str, int, int]] = []  # (лист, число inline, число чисел)
    for n in sheets:
        inline = inline_rw.segments(sheet_xml[n])
        nums = [m.group(2) for m in _numeric_cells(sheet_xml[n])]
        layout.append((n, len(inline), len(nums)))
        segments += inline + nums

    def rebuild(masked: list[str]) -> bytes:
        out = dict(parts)
        if shared:
            out["xl/sharedStrings.xml"] = rw.rebuild(shared, masked[:n_shared]).encode("utf-8")
        pos = n_shared
        for n, n_inline, n_nums in layout:
            xml = inline_rw.rebuild(sheet_xml[n], masked[pos : pos + n_inline])
            pos += n_inline
            new_values = masked[pos : pos + n_nums]
            pos += n_nums
            # позиции числовых ячеек берутся из уже пересобранного XML листа:
            # inline-строки до них могли поменять длину
            pieces, last = [], 0
            for m, new in zip(_numeric_cells(xml), new_values):
                if new == m.group(2):
                    continue
                # число с ПДн (ИИН в числовой ячейке) превращается в строку с меткой
                attrs = _CELL_TYPE.sub("", m.group(1) or "")
                pieces.append(xml[last : m.start()])
                pieces.append(
                    f'<c{attrs} t="inlineStr"><is><t xml:space="preserve">'
                    f"{_xml_escape(new)}</t></is></c>"
                )
                last = m.end()
            pieces.append(xml[last:])
            out[n] = "".join(pieces).encode("utf-8")
        return _write_zip(out, order)

    return FileDocument(kind="xlsx", segments=segments, _rebuild=rebuild, out_suffix=".xlsx")


# --- PDF (только текстовый слой) -------------------------------------------

_STREAM = re.compile(rb"<<(.*?)>>\s*stream\r?\n(.*?)\r?\nendstream", re.S)
_TEXT_OP = re.compile(
    rb"\((?:\\.|[^\\)])*\)\s*Tj|\[(?:[^\]]*)\]\s*TJ|\((?:\\.|[^\\)])*\)\s*'|T\*|ET|Td|TD",
    re.S,
)
_PDF_STRING = re.compile(rb"\(((?:\\.|[^\\)])*)\)", re.S)
_PDF_ESCAPES = {b"n": b"\n", b"r": b"\r", b"t": b"\t", b"b": b"\b", b"f": b"\f",
                b"(": b"(", b")": b")", b"\\": b"\\"}


def _pdf_unescape(s: bytes) -> bytes:
    out = bytearray()
    i = 0
    while i < len(s):
        c = s[i : i + 1]
        if c == b"\\" and i + 1 < len(s):
            nxt = s[i + 1 : i + 2]
            if nxt in _PDF_ESCAPES:
                out += _PDF_ESCAPES[nxt]
                i += 2
                continue
            m = re.match(rb"[0-7]{1,3}", s[i + 1 : i + 4])
            if m:
                out.append(int(m.group(), 8) & 0xFF)
                i += 1 + len(m.group())
                continue
            i += 1
            continue
        out += c
        i += 1
    return bytes(out)


def _pdf_decode(raw: bytes) -> str:
    if raw.startswith(b"\xfe\xff"):
        return raw[2:].decode("utf-16-be", errors="replace")
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return raw.decode("cp1252", errors="replace")


def _open_pdf(data: bytes) -> FileDocument:
    if not data.startswith(b"%PDF"):
        raise UnreadableFile("Это не PDF")
    lines: list[str] = []
    current = ""
    for m in _STREAM.finditer(data):
        header, body = m.group(1), m.group(2)
        if b"FlateDecode" in header:
            try:
                body = zlib.decompress(body)
            except zlib.error:
                continue
        elif b"/Filter" in header:
            continue  # картинки и прочие фильтры текста не содержат
        for op in _TEXT_OP.finditer(body):
            token = op.group()
            if token in (b"T*", b"ET", b"Td", b"TD"):
                if current.strip():
                    lines.append(current)
                current = ""
                continue
            for s in _PDF_STRING.finditer(token):
                current += _pdf_decode(_pdf_unescape(s.group(1)))
    if current.strip():
        lines.append(current)

    text = "\n".join(lines)
    letters = sum(ch.isalpha() for ch in text)
    garbage = sum(ch == "�" or (ord(ch) < 32 and ch not in "\n\t") for ch in text)
    if letters < 20 or garbage > letters * 0.2:
        raise UnreadableFile(
            "В PDF нет читаемого текстового слоя (скан или шрифт без таблицы Unicode). "
            "Такой файл не проверить — сохраните его в DOCX или распознайте текст."
        )
    return FileDocument(
        kind="pdf",
        segments=lines,
        _rebuild=lambda masked: "\n".join(masked).encode("utf-8"),
        out_suffix=".txt",
    )
