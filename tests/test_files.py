"""Файлы (этап 1): DOCX, XLSX, PDF и текст — извлечение, обезличенная
копия в том же формате, политика блокировки, fail-closed на нечитаемом PDF,
HTTP-эндпоинты /v1/file и /try/api/file."""

import base64
import io
import json
import re
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
import zipfile
import zlib
from pathlib import Path

from gateway.config import Settings
from gateway.core import files
from gateway.core.audit import AuditLog
from gateway.core.mapping_store import MappingStore
from gateway.core.pipeline import Pipeline
from gateway.core.policy import PolicyEngine
from gateway.server import make_server

from .helpers import VALID_IIN, FakeForwarder

W_NS = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'


def make_docx(paragraphs: list[list[str]]) -> bytes:
    """Каждый абзац — список прогонов (<w:r>), как Word режет текст по форматированию."""
    body = "".join(
        "<w:p><w:pPr><w:jc w:val=\"left\"/></w:pPr>"
        + "".join(f"<w:r><w:rPr><w:b/></w:rPr><w:t xml:space=\"preserve\">{r}</w:t></w:r>" for r in runs)
        + "</w:p>"
        for runs in paragraphs
    )
    document = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        f'<w:document {W_NS} xmlns:w14="http://schemas.microsoft.com/office/word/2010/wordml" '
        'xmlns:mc="http://schemas.openxmlformats.org/markup-compatibility/2006" mc:Ignorable="w14">'
        f"<w:body>{body}</w:body></w:document>"
    )
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("[Content_Types].xml", "<Types/>")
        zf.writestr("word/document.xml", document)
    return buf.getvalue()


def make_xlsx(shared: list[str], numbers: list[str]) -> bytes:
    sst = "".join(f"<si><t>{s}</t></si>" for s in shared)
    cells = "".join(f'<c r="A{i + 1}" t="s"><v>{i}</v></c>' for i in range(len(shared)))
    cells += "".join(f'<c r="B{i + 1}" s="1"><v>{n}</v></c>' for i, n in enumerate(numbers))
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("[Content_Types].xml", "<Types/>")
        zf.writestr("xl/sharedStrings.xml", f'<sst count="{len(shared)}">{sst}</sst>')
        zf.writestr(
            "xl/worksheets/sheet1.xml",
            f"<worksheet><sheetData><row r=\"1\">{cells}</row></sheetData></worksheet>",
        )
    return buf.getvalue()


def make_pdf(lines: list[str], compress: bool = True) -> bytes:
    ops = "BT /F1 12 Tf 72 720 Td " + " T* ".join(f"({line}) Tj" for line in lines) + " ET"
    content = ops.encode("latin-1")
    if compress:
        content = zlib.compress(content)
        header = f"<< /Length {len(content)} /Filter /FlateDecode >>".encode()
    else:
        header = f"<< /Length {len(content)} >>".encode()
    return b"%PDF-1.4\n1 0 obj\n" + header + b"\nstream\n" + content + b"\nendstream\nendobj\n%%EOF"


def docx_text(data: bytes) -> str:
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        xml = zf.read("word/document.xml").decode()
    return xml


class TestOpenDocuments(unittest.TestCase):
    def setUp(self):
        self.pipeline = Pipeline(
            policy_engine=PolicyEngine(Path(tempfile.mkdtemp()) / "p.json"),
            mapping_store=MappingStore(),
            forwarder=FakeForwarder(),
            audit=AuditLog(Path(tempfile.mkdtemp()) / "a.log"),
        )

    def test_docx_name_split_across_runs(self):
        data = make_docx([["Клиент ", "Айгуль", " Ахметова", f", ИИН {VALID_IIN}"], ["Без данных."]])
        doc = files.open_document("письмо.docx", data)
        self.assertEqual(doc.segments[0], f"Клиент Айгуль Ахметова, ИИН {VALID_IIN}")
        result = self.pipeline.sanitize_file("письмо.docx", data)
        self.assertEqual(result["verdict"], "masked")
        xml = docx_text(result["data"])
        self.assertNotIn("Ахметова", xml)
        self.assertNotIn(VALID_IIN, xml)
        self.assertIn("[PERSON_1]", xml)
        self.assertIn("Без данных.", xml)
        # пространства имён и mc:Ignorable не тронуты — Word откроет файл
        self.assertIn('mc:Ignorable="w14"', xml)
        self.assertIn("xmlns:w14=", xml)
        self.assertIn("<w:b/>", xml)

    def test_docx_restore_after_ai_answer(self):
        data = make_docx([[f"ИИН {VALID_IIN}"]])
        result = self.pipeline.sanitize_file("a.docx", data, session_id="s1")
        self.assertEqual(self.pipeline.restore("s1", "Проверил [IIN_1]"), f"Проверил {VALID_IIN}")
        self.assertEqual(result["session_id"], "s1")

    def test_xlsx_shared_strings_and_numeric_iin(self):
        data = make_xlsx(["Айгуль Ахметова", "+7 701 123 45 67", "Итого"], [VALID_IIN, "150"])
        result = self.pipeline.sanitize_file("база.xlsx", data)
        self.assertEqual(result["verdict"], "masked")
        with zipfile.ZipFile(io.BytesIO(result["data"])) as zf:
            sst = zf.read("xl/sharedStrings.xml").decode()
            sheet = zf.read("xl/worksheets/sheet1.xml").decode()
        self.assertNotIn("Ахметова", sst)
        self.assertNotIn("701 123", sst)
        self.assertIn("Итого", sst)
        self.assertNotIn(VALID_IIN, sheet)
        self.assertRegex(sheet, r'<c r="B1" s="1" t="inlineStr"><is><t[^>]*>\[IIN_1\]</t></is></c>')
        self.assertIn('<c r="B2" s="1"><v>150</v></c>', sheet)
        # ячейки общих строк не превращены в inline
        self.assertIn('<c r="A1" t="s"><v>0</v></c>', sheet)

    def test_pdf_text_layer(self):
        data = make_pdf(["Klient Aigul Akhmetova", f"IIN {VALID_IIN}", "tel. +7 701 123 45 67"])
        result = self.pipeline.sanitize_file("scan.pdf", data)
        self.assertEqual(result["verdict"], "masked")
        self.assertEqual(result["out_suffix"], ".txt")
        text = result["data"].decode()
        self.assertNotIn(VALID_IIN, text)
        self.assertNotIn("Akhmetova", text)

    def test_pdf_without_text_is_fail_closed(self):
        data = b"%PDF-1.4\n1 0 obj\n<< /Length 4 /Filter /DCTDecode >>\nstream\nabcd\nendstream\n%%EOF"
        result = self.pipeline.sanitize_file("scan.pdf", data)
        self.assertEqual(result["verdict"], "fail_closed")
        self.assertIsNone(result["data"])
        self.assertIn("текстового слоя", result["message"])

    def test_secret_in_file_blocks(self):
        data = "config\nOPENAI=sk-proj-Ab12Cd34Ef56Gh78Ij90KlMnOpQrStUv\n".encode()
        result = self.pipeline.sanitize_file("conf.txt", data)
        self.assertEqual(result["verdict"], "blocked")
        self.assertIsNone(result["data"])

    def test_cp1251_text(self):
        data = f"Сотрудник Айгуль Ахметова, ИИН {VALID_IIN}".encode("cp1251")
        result = self.pipeline.sanitize_file("list.csv", data)
        self.assertNotIn(VALID_IIN, result["data"].decode())

    def test_unsupported(self):
        with self.assertRaises(files.UnsupportedFile):
            self.pipeline.sanitize_file("photo.jpg", b"\xff\xd8")

    def test_broken_docx_fail_closed(self):
        result = self.pipeline.sanitize_file("x.docx", b"not a zip")
        self.assertEqual(result["verdict"], "fail_closed")


class TestFileEndpoints(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        tmp = Path(cls._tmp.name)
        settings = Settings(
            host="127.0.0.1",
            port=0,
            channel_api_keys="chan",
            policy_path=tmp / "policy.json",
            audit_log_path=tmp / "audit.log",
        )
        cls.pipeline = Pipeline(
            policy_engine=PolicyEngine(settings.policy_path),
            mapping_store=MappingStore(),
            forwarder=FakeForwarder(),
            audit=AuditLog(settings.audit_log_path),
        )
        cls.server = make_server(cls.pipeline, settings)
        cls.base = f"http://127.0.0.1:{cls.server.server_address[1]}"
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls._tmp.cleanup()

    def _post(self, path, body, key=None):
        headers = {"Content-Type": "application/json"}
        if key:
            headers["X-AIGate-Key"] = key
        req = urllib.request.Request(
            self.base + path, data=json.dumps(body).encode(), headers=headers, method="POST"
        )
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                return resp.status, json.loads(resp.read().decode())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read().decode())

    def test_v1_file_requires_key_and_keeps_session(self):
        payload = {
            "filename": "Анкета.docx",
            "content_base64": base64.b64encode(make_docx([[f"ИИН {VALID_IIN}"]])).decode(),
        }
        self.assertEqual(self._post("/v1/file", payload)[0], 401)
        status, data = self._post("/v1/file", payload, key="chan")
        self.assertEqual(status, 200)
        self.assertEqual(data["filename"], "Анкета-обезличен.docx")
        masked = base64.b64decode(data["content_base64"])
        self.assertNotIn(VALID_IIN, docx_text(masked))
        status, restored = self._post(
            "/v1/restore", {"session_id": data["session_id"], "text": "[IIN_1]"}, key="chan"
        )
        self.assertEqual(restored["text"], VALID_IIN)

    def test_try_file_purges_session(self):
        payload = {
            "filename": "../../etc/list.txt",
            "content_base64": base64.b64encode(f"ИИН {VALID_IIN}".encode()).decode(),
        }
        status, data = self._post("/try/api/file", payload)
        self.assertEqual(status, 200)
        self.assertNotIn("session_id", data)
        self.assertEqual(data["filename"], "list-обезличен.txt")
        self.assertEqual(self.pipeline.store._reverse, {})

    def test_unsupported_415_and_bad_base64_400(self):
        status, _ = self._post("/try/api/file", {"filename": "a.jpg", "content_base64": "AAAA"})
        self.assertEqual(status, 415)
        status, _ = self._post("/try/api/file", {"filename": "a.txt", "content_base64": "%%%"})
        self.assertEqual(status, 400)


if __name__ == "__main__":
    unittest.main()
