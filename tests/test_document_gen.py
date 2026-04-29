from io import BytesIO
from zipfile import ZipFile

from app.agent.document_gen import generate_document, sanitize_filename_stem


def test_sanitize_filename_stem_normalizes_title() -> None:
    assert sanitize_filename_stem(" Weekly Plan: May / 2026 ") == "weekly-plan-may-2026"


def test_generate_pdf_export_has_pdf_header() -> None:
    doc = generate_document(
        file_format="pdf",
        title="Weekly Plan",
        content="Monday: Strength\nTuesday: Rest",
        table_rows=[["Day", "Session"], ["Wed", "Cardio"]],
    )

    assert doc.mime_type == "application/pdf"
    assert doc.data.startswith(b"%PDF-1.4")


def test_generate_office_exports_are_zip_packages() -> None:
    for file_format, expected_member in (
        ("docx", "word/document.xml"),
        ("xlsx", "xl/workbook.xml"),
        ("pptx", "ppt/presentation.xml"),
    ):
        doc = generate_document(
            file_format=file_format,
            title="Coach Export",
            content="Line one\nLine two",
            table_rows=[["A", "B"], ["1", "2"]],
        )

        assert doc.extension == file_format
        with ZipFile(BytesIO(doc.data)) as zf:
            assert expected_member in zf.namelist()
