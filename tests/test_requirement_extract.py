"""技术文档本地提取的离线回归；不调用 Qwen。"""
from __future__ import annotations

import io
import zipfile
import unittest
from pathlib import Path

from backend.services.requirement_extract import extract_document_text, prepare_documents


def _docx_bytes(text: str) -> bytes:
    xml = (
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        f'<w:body><w:p><w:r><w:t>{text}</w:t></w:r></w:p></w:body></w:document>'
    )
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("word/document.xml", xml)
    return buffer.getvalue()


class RequirementDocumentExtractionTests(unittest.TestCase):
    def test_multiple_text_documents_are_combined_with_file_boundaries(self):
        prepared = prepare_documents([
            ("spec.txt", "产品名称：12 英寸静电吸盘".encode()),
            ("schedule.md", "首样交付：2026-08-01".encode()),
        ])
        self.assertEqual(prepared.processed_files, ["spec.txt", "schedule.md"])
        self.assertIn("【技术文档：spec.txt】", prepared.text)
        self.assertIn("首样交付", prepared.text)

    def test_docx_is_extracted_without_a_word_runtime(self):
        prepared = prepare_documents([("technical.docx", _docx_bytes("材料：氮化铝（AlN）"))])
        self.assertEqual(prepared.processed_files, ["technical.docx"])
        self.assertIn("氮化铝", prepared.text)

    def test_single_document_extractor_is_reusable_by_drawing_parse(self):
        text = extract_document_text("technical.docx", _docx_bytes("洁净度：ISO Class 5"))
        self.assertIn("ISO Class 5", text)

    def test_binary_document_is_kept_but_not_sent_to_the_text_model(self):
        prepared = prepare_documents([("legacy.doc", b"not a readable word document")])
        self.assertFalse(prepared.text)
        self.assertTrue(prepared.skipped_files)

    def test_full_requirement_example_is_a_readable_text_document(self):
        example = Path(__file__).resolve().parents[1] / "data" / "1.1工艺评估需求完整示例.txt"
        prepared = prepare_documents([(example.name, example.read_bytes())])
        self.assertEqual(prepared.processed_files, [example.name])
        self.assertIn("产品名称", prepared.text)
        self.assertIn("期望工艺评估完成日期", prepared.text)


if __name__ == "__main__":
    unittest.main()
