# ruff: noqa: E501
"""Lightweight document generators for coach-created exports.

These builders intentionally avoid heavyweight external dependencies so the
feature works in constrained self-hosted environments.
"""

from __future__ import annotations

import textwrap
from collections.abc import Iterable
from datetime import UTC, datetime
from io import BytesIO
from typing import Literal
from xml.sax.saxutils import escape
from zipfile import ZIP_DEFLATED, ZipFile

DocumentFormat = Literal["pdf", "docx", "xlsx", "pptx"]

_MIME_BY_FORMAT: dict[DocumentFormat, str] = {
    "pdf": "application/pdf",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
}


class GeneratedDocument:
    def __init__(self, data: bytes, mime_type: str, extension: str) -> None:
        self.data = data
        self.mime_type = mime_type
        self.extension = extension


def generate_document(
    *,
    file_format: DocumentFormat,
    title: str,
    content: str,
    table_rows: list[list[str]] | None = None,
) -> GeneratedDocument:
    if file_format == "pdf":
        data = _build_pdf(title, content, table_rows or [])
    elif file_format == "docx":
        data = _build_docx(title, content, table_rows or [])
    elif file_format == "xlsx":
        data = _build_xlsx(title, content, table_rows or [])
    elif file_format == "pptx":
        data = _build_pptx(title, content, table_rows or [])
    else:
        raise ValueError(f"Unsupported document format: {file_format}")
    return GeneratedDocument(
        data=data,
        mime_type=_MIME_BY_FORMAT[file_format],
        extension=file_format,
    )


def sanitize_filename_stem(value: str) -> str:
    cleaned = "".join(ch.lower() if ch.isalnum() else "-" for ch in value.strip())
    cleaned = "-".join(part for part in cleaned.split("-") if part)
    return cleaned[:80] or "coach-export"


def _paragraphs(title: str, content: str, table_rows: list[list[str]]) -> list[str]:
    parts = [title.strip(), ""]
    parts.extend(line.rstrip() for line in content.strip().splitlines())
    if table_rows:
        widths = [
            max(len(row[idx]) if idx < len(row) else 0 for row in table_rows)
            for idx in range(max(len(row) for row in table_rows))
        ]
        if parts and parts[-1] != "":
            parts.append("")
        for row in table_rows:
            cells = [
                (row[idx] if idx < len(row) else "").ljust(widths[idx])
                for idx in range(len(widths))
            ]
            parts.append(" | ".join(cells).rstrip())
    return parts


def _wrap_lines(title: str, content: str, table_rows: list[list[str]]) -> list[str]:
    wrapped: list[str] = []
    for idx, paragraph in enumerate(_paragraphs(title, content, table_rows)):
        if idx > 0:
            wrapped.append("")
        if not paragraph:
            continue
        wrapped.extend(textwrap.wrap(paragraph, width=92) or [""])
    return wrapped or [title]


def _pdf_escape(text: str) -> str:
    return text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def _build_pdf(title: str, content: str, table_rows: list[list[str]]) -> bytes:
    lines = _wrap_lines(title, content, table_rows)
    page_height = 792
    start_y = 756
    line_height = 16
    bottom_margin = 54
    max_lines = max(1, (start_y - bottom_margin) // line_height)
    pages = [lines[i : i + max_lines] for i in range(0, len(lines), max_lines)] or [[]]

    objects: list[bytes] = []

    def add_object(body: str | bytes) -> int:
        data = body.encode("latin-1") if isinstance(body, str) else body
        objects.append(data)
        return len(objects)

    font_id = add_object("<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
    page_ids: list[int] = []
    pages_placeholder = add_object(b"")
    for page_num, page_lines in enumerate(pages, start=1):
        ops = ["BT", "/F1 12 Tf", "72 756 Td"]
        for idx, line in enumerate(page_lines):
            if idx == 0:
                ops.append(f"({_pdf_escape(line)}) Tj")
            else:
                ops.append(f"0 -{line_height} Td")
                ops.append(f"({_pdf_escape(line)}) Tj")
        ops.extend(
            [
                "ET",
                "BT",
                "/F1 10 Tf",
                "72 28 Td",
                (
                    f"(Generated {datetime.now(UTC).strftime('%Y-%m-%d %H:%M UTC')}  "
                    f"Page {page_num}/{len(pages)}) Tj"
                ),
                "ET",
            ]
        )
        stream = "\n".join(ops).encode("latin-1")
        content_id = add_object(
            b"<< /Length "
            + str(len(stream)).encode("ascii")
            + b" >>\nstream\n"
            + stream
            + b"\nendstream"
        )
        page_ids.append(
            add_object(
                f"<< /Type /Page /Parent {pages_placeholder} 0 R /MediaBox [0 0 612 {page_height}] "
                f"/Resources << /Font << /F1 {font_id} 0 R >> >> /Contents {content_id} 0 R >>"
            )
        )

    objects[pages_placeholder - 1] = (
        f"<< /Type /Pages /Count {len(page_ids)} /Kids [{' '.join(f'{pid} 0 R' for pid in page_ids)}] >>"
    ).encode("latin-1")
    catalog_id = add_object(f"<< /Type /Catalog /Pages {pages_placeholder} 0 R >>")

    buf = BytesIO()
    buf.write(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets = [0]
    for idx, obj in enumerate(objects, start=1):
        offsets.append(buf.tell())
        buf.write(f"{idx} 0 obj\n".encode("ascii"))
        buf.write(obj)
        buf.write(b"\nendobj\n")
    xref_start = buf.tell()
    buf.write(f"xref\n0 {len(objects) + 1}\n".encode("ascii"))
    buf.write(b"0000000000 65535 f \n")
    for off in offsets[1:]:
        buf.write(f"{off:010d} 00000 n \n".encode("ascii"))
    buf.write(
        (
            f"trailer\n<< /Size {len(objects) + 1} /Root {catalog_id} 0 R >>\n"
            f"startxref\n{xref_start}\n%%EOF"
        ).encode("ascii")
    )
    return buf.getvalue()


def _docx_paragraph(text: str) -> str:
    return (
        "<w:p><w:r><w:t xml:space=\"preserve\">"
        f"{escape(text)}"
        "</w:t></w:r></w:p>"
    )


def _build_docx(title: str, content: str, table_rows: list[list[str]]) -> bytes:
    paragraphs = [
        "<w:p><w:r><w:rPr><w:b/><w:sz w:val=\"32\"/></w:rPr>"
        f"<w:t>{escape(title)}</w:t></w:r></w:p>"
    ]
    for line in content.strip().splitlines():
        paragraphs.append(_docx_paragraph(line or " "))
    for row in table_rows:
        paragraphs.append(_docx_paragraph(" | ".join(row)))
    document_xml = (
        "<?xml version=\"1.0\" encoding=\"UTF-8\" standalone=\"yes\"?>"
        "<w:document xmlns:w=\"http://schemas.openxmlformats.org/wordprocessingml/2006/main\">"
        f"<w:body>{''.join(paragraphs)}<w:sectPr/></w:body></w:document>"
    )
    content_types = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
  <Default Extension="xml" ContentType="application/xml"/>
  <Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>
  <Override PartName="/docProps/core.xml" ContentType="application/vnd.openxmlformats-package.core-properties+xml"/>
  <Override PartName="/docProps/app.xml" ContentType="application/vnd.openxmlformats-officedocument.extended-properties+xml"/>
</Types>"""
    rels = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>
  <Relationship Id="rId2" Type="http://schemas.openxmlformats.org/package/2006/relationships/metadata/core-properties" Target="docProps/core.xml"/>
  <Relationship Id="rId3" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/extended-properties" Target="docProps/app.xml"/>
</Relationships>"""
    app_xml = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Properties xmlns="http://schemas.openxmlformats.org/officeDocument/2006/extended-properties"
 xmlns:vt="http://schemas.openxmlformats.org/officeDocument/2006/docPropsVTypes">
  <Application>coacher</Application>
</Properties>"""
    now = datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    core_xml = f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties"
 xmlns:dc="http://purl.org/dc/elements/1.1/"
 xmlns:dcterms="http://purl.org/dc/terms/"
 xmlns:dcmitype="http://purl.org/dc/dcmitype/"
 xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">
  <dc:title>{escape(title)}</dc:title>
  <dc:creator>coacher</dc:creator>
  <cp:lastModifiedBy>coacher</cp:lastModifiedBy>
  <dcterms:created xsi:type="dcterms:W3CDTF">{now}</dcterms:created>
  <dcterms:modified xsi:type="dcterms:W3CDTF">{now}</dcterms:modified>
</cp:coreProperties>"""
    buf = BytesIO()
    with ZipFile(buf, "w", ZIP_DEFLATED) as zf:
        zf.writestr("[Content_Types].xml", content_types)
        zf.writestr("_rels/.rels", rels)
        zf.writestr("docProps/app.xml", app_xml)
        zf.writestr("docProps/core.xml", core_xml)
        zf.writestr("word/document.xml", document_xml)
    return buf.getvalue()


def _xlsx_cell(value: str, cell_ref: str, style_id: int | None = None) -> str:
    style = f' s="{style_id}"' if style_id is not None else ""
    return f'<c r="{cell_ref}" t="inlineStr"{style}><is><t>{escape(value)}</t></is></c>'


def _col_name(index: int) -> str:
    name = ""
    while index > 0:
        index, rem = divmod(index - 1, 26)
        name = chr(65 + rem) + name
    return name


def _iter_sheet_rows(
    title: str, content: str, table_rows: list[list[str]]
) -> Iterable[list[str]]:
    yield [title]
    yield []
    for line in content.strip().splitlines():
        yield [line]
    if table_rows:
        yield []
        yield from table_rows


def _build_xlsx(title: str, content: str, table_rows: list[list[str]]) -> bytes:
    rows_xml: list[str] = []
    for row_idx, row in enumerate(_iter_sheet_rows(title, content, table_rows), start=1):
        cells = []
        for col_idx, value in enumerate(row, start=1):
            if value == "":
                continue
            style_id = 1 if row_idx == 1 else None
            cells.append(_xlsx_cell(value, f"{_col_name(col_idx)}{row_idx}", style_id))
        rows_xml.append(f'<row r="{row_idx}">{"".join(cells)}</row>')
    sheet_xml = (
        "<?xml version=\"1.0\" encoding=\"UTF-8\" standalone=\"yes\"?>"
        "<worksheet xmlns=\"http://schemas.openxmlformats.org/spreadsheetml/2006/main\">"
        "<sheetViews><sheetView workbookViewId=\"0\"/></sheetViews>"
        "<sheetFormatPr defaultRowHeight=\"15\"/>"
        f"<sheetData>{''.join(rows_xml)}</sheetData>"
        "</worksheet>"
    )
    workbook_xml = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"
 xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
  <sheets>
    <sheet name="Export" sheetId="1" r:id="rId1"/>
  </sheets>
</workbook>"""
    workbook_rels = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>
  <Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>
</Relationships>"""
    styles_xml = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
  <fonts count="2">
    <font><sz val="11"/><name val="Calibri"/></font>
    <font><b/><sz val="14"/><name val="Calibri"/></font>
  </fonts>
  <fills count="1"><fill><patternFill patternType="none"/></fill></fills>
  <borders count="1"><border/></borders>
  <cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>
  <cellXfs count="2">
    <xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/>
    <xf numFmtId="0" fontId="1" fillId="0" borderId="0" xfId="0" applyFont="1"/>
  </cellXfs>
</styleSheet>"""
    content_types = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
  <Default Extension="xml" ContentType="application/xml"/>
  <Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>
  <Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>
  <Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>
  <Override PartName="/docProps/core.xml" ContentType="application/vnd.openxmlformats-package.core-properties+xml"/>
  <Override PartName="/docProps/app.xml" ContentType="application/vnd.openxmlformats-officedocument.extended-properties+xml"/>
</Types>"""
    rels = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>
  <Relationship Id="rId2" Type="http://schemas.openxmlformats.org/package/2006/relationships/metadata/core-properties" Target="docProps/core.xml"/>
  <Relationship Id="rId3" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/extended-properties" Target="docProps/app.xml"/>
</Relationships>"""
    app_xml = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Properties xmlns="http://schemas.openxmlformats.org/officeDocument/2006/extended-properties"
 xmlns:vt="http://schemas.openxmlformats.org/officeDocument/2006/docPropsVTypes">
  <Application>coacher</Application>
</Properties>"""
    now = datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    core_xml = f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties"
 xmlns:dc="http://purl.org/dc/elements/1.1/"
 xmlns:dcterms="http://purl.org/dc/terms/"
 xmlns:dcmitype="http://purl.org/dc/dcmitype/"
 xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">
  <dc:title>{escape(title)}</dc:title>
  <dc:creator>coacher</dc:creator>
  <dcterms:created xsi:type="dcterms:W3CDTF">{now}</dcterms:created>
  <dcterms:modified xsi:type="dcterms:W3CDTF">{now}</dcterms:modified>
</cp:coreProperties>"""
    buf = BytesIO()
    with ZipFile(buf, "w", ZIP_DEFLATED) as zf:
        zf.writestr("[Content_Types].xml", content_types)
        zf.writestr("_rels/.rels", rels)
        zf.writestr("docProps/app.xml", app_xml)
        zf.writestr("docProps/core.xml", core_xml)
        zf.writestr("xl/workbook.xml", workbook_xml)
        zf.writestr("xl/_rels/workbook.xml.rels", workbook_rels)
        zf.writestr("xl/worksheets/sheet1.xml", sheet_xml)
        zf.writestr("xl/styles.xml", styles_xml)
    return buf.getvalue()


def _pptx_slide_xml(title: str, bullets: list[str]) -> str:
    bullet_runs = "".join(
        "<a:p><a:pPr lvl=\"0\"/><a:r><a:t>"
        f"{escape(b)}"
        "</a:t></a:r></a:p>"
        for b in bullets
    ) or "<a:p/>"
    return f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<p:sld xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"
 xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"
 xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main">
  <p:cSld>
    <p:spTree>
      <p:nvGrpSpPr><p:cNvPr id="1" name=""/><p:cNvGrpSpPr/><p:nvPr/></p:nvGrpSpPr>
      <p:grpSpPr/>
      <p:sp>
        <p:nvSpPr><p:cNvPr id="2" name="Title"/><p:cNvSpPr/><p:nvPr/></p:nvSpPr>
        <p:spPr/>
        <p:txBody>
          <a:bodyPr/><a:lstStyle/>
          <a:p><a:r><a:rPr lang="en-US" sz="2400" b="1"/><a:t>{escape(title)}</a:t></a:r></a:p>
        </p:txBody>
      </p:sp>
      <p:sp>
        <p:nvSpPr><p:cNvPr id="3" name="Body"/><p:cNvSpPr txBox="1"/><p:nvPr/></p:nvSpPr>
        <p:spPr/>
        <p:txBody>
          <a:bodyPr wrap="square"/><a:lstStyle/>
          {bullet_runs}
        </p:txBody>
      </p:sp>
    </p:spTree>
  </p:cSld>
  <p:clrMapOvr><a:masterClrMapping/></p:clrMapOvr>
</p:sld>"""


def _build_pptx(title: str, content: str, table_rows: list[list[str]]) -> bytes:
    bullets = [line.strip() for line in content.strip().splitlines() if line.strip()]
    bullets.extend(" | ".join(row) for row in table_rows if row)
    chunks = [bullets[i : i + 8] for i in range(0, len(bullets), 8)] or [["Generated by coacher"]]
    content_type_overrides = [
        '<Override PartName="/ppt/presentation.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.presentation.main+xml"/>',
        '<Override PartName="/docProps/core.xml" ContentType="application/vnd.openxmlformats-package.core-properties+xml"/>',
        '<Override PartName="/docProps/app.xml" ContentType="application/vnd.openxmlformats-officedocument.extended-properties+xml"/>',
    ]
    presentation_slides = []
    presentation_rels = []
    slide_files: list[tuple[str, str]] = []
    slide_rels: list[tuple[str, str]] = []
    for idx, chunk in enumerate(chunks, start=1):
        content_type_overrides.append(
            f'<Override PartName="/ppt/slides/slide{idx}.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.slide+xml"/>'
        )
        presentation_slides.append(f'<p:sldId id="{255 + idx}" r:id="rId{idx}"/>')
        presentation_rels.append(
            f'<Relationship Id="rId{idx}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/slide" Target="slides/slide{idx}.xml"/>'
        )
        slide_files.append(
            (
                f"ppt/slides/slide{idx}.xml",
                _pptx_slide_xml(title if idx == 1 else f"{title} ({idx})", chunk),
            )
        )
        slide_rels.append(
            (
                f"ppt/slides/_rels/slide{idx}.xml.rels",
                """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"/>""",
            )
        )
    presentation_xml = f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<p:presentation xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"
 xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"
 xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main">
  <p:sldIdLst>{''.join(presentation_slides)}</p:sldIdLst>
  <p:sldSz cx="9144000" cy="6858000"/>
  <p:notesSz cx="6858000" cy="9144000"/>
</p:presentation>"""
    presentation_rels_xml = (
        """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">"""
        + "".join(presentation_rels)
        + "</Relationships>"
    )
    content_types = (
        """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
  <Default Extension="xml" ContentType="application/xml"/>"""
        + "".join(content_type_overrides)
        + "</Types>"
    )
    rels = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="ppt/presentation.xml"/>
  <Relationship Id="rId2" Type="http://schemas.openxmlformats.org/package/2006/relationships/metadata/core-properties" Target="docProps/core.xml"/>
  <Relationship Id="rId3" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/extended-properties" Target="docProps/app.xml"/>
</Relationships>"""
    app_xml = f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Properties xmlns="http://schemas.openxmlformats.org/officeDocument/2006/extended-properties"
 xmlns:vt="http://schemas.openxmlformats.org/officeDocument/2006/docPropsVTypes">
  <Application>coacher</Application>
  <Slides>{len(chunks)}</Slides>
</Properties>"""
    now = datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    core_xml = f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties"
 xmlns:dc="http://purl.org/dc/elements/1.1/"
 xmlns:dcterms="http://purl.org/dc/terms/"
 xmlns:dcmitype="http://purl.org/dc/dcmitype/"
 xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">
  <dc:title>{escape(title)}</dc:title>
  <dc:creator>coacher</dc:creator>
  <dcterms:created xsi:type="dcterms:W3CDTF">{now}</dcterms:created>
  <dcterms:modified xsi:type="dcterms:W3CDTF">{now}</dcterms:modified>
</cp:coreProperties>"""
    buf = BytesIO()
    with ZipFile(buf, "w", ZIP_DEFLATED) as zf:
        zf.writestr("[Content_Types].xml", content_types)
        zf.writestr("_rels/.rels", rels)
        zf.writestr("docProps/app.xml", app_xml)
        zf.writestr("docProps/core.xml", core_xml)
        zf.writestr("ppt/presentation.xml", presentation_xml)
        zf.writestr("ppt/_rels/presentation.xml.rels", presentation_rels_xml)
        for path, xml in slide_files:
            zf.writestr(path, xml)
        for path, xml in slide_rels:
            zf.writestr(path, xml)
    return buf.getvalue()
