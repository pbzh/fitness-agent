"""Convert uploaded files into agent input parts (text or BinaryContent).

Images stay binary so the model can actually look at them (multimodal). PDFs,
DOCX and text files are extracted to text and inlined as a fenced block so any
provider — including the local llama.cpp endpoint — can ingest them.
"""

from __future__ import annotations

from pathlib import Path

from pydantic_ai import BinaryContent

from app.db.models import File as DBFile

# Cap inlined-text length per attachment so a 200-page PDF doesn't blow the
# context window. The agent gets a clear truncation marker when this trips.
_MAX_TEXT_CHARS = 20_000

_DOCX_MIMES = {
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "application/msword",
}
_TEXTLIKE_PREFIXES = ("text/",)
_TEXTLIKE_EXACT = {
    "application/json",
    "application/xml",
    "application/x-yaml",
    "application/yaml",
}


def _truncate(text: str) -> str:
    if len(text) <= _MAX_TEXT_CHARS:
        return text
    head = text[:_MAX_TEXT_CHARS]
    return head + f"\n\n[…truncated, {len(text) - _MAX_TEXT_CHARS} more chars not shown]"


def has_image(parts: list) -> bool:
    return any(
        isinstance(p, BinaryContent) and (p.media_type or "").startswith("image/")
        for p in parts
    )


def build_part(f: DBFile, abs_path: Path) -> str | BinaryContent:
    """Turn one stored file into either a text block or a BinaryContent part."""
    mime = (f.mime_type or "").lower()
    name = f.filename
    suffix = Path(name).suffix.lower()

    # ── Images: send as binary so the model can see them. ──
    if mime.startswith("image/"):
        try:
            return BinaryContent(data=abs_path.read_bytes(), media_type=mime)
        except Exception as exc:
            return f"[Image attachment: {name} — could not be read: {exc}]"

    # ── PDF: extract text via pypdf. ──
    if mime == "application/pdf" or suffix == ".pdf":
        try:
            from pypdf import PdfReader

            reader = PdfReader(str(abs_path))
            text = "\n\n".join((p.extract_text() or "") for p in reader.pages).strip()
            text = text or "(no extractable text — possibly a scanned PDF)"
            return f"[PDF attachment: {name}]\n```pdf\n{_truncate(text)}\n```"
        except Exception as exc:
            return f"[PDF attachment: {name} — extraction failed: {exc}]"

    # ── DOCX / DOC: extract via docx2txt. ──
    if mime in _DOCX_MIMES or suffix in {".docx", ".doc"}:
        try:
            import docx2txt

            text = (docx2txt.process(str(abs_path)) or "").strip()
            text = text or "(empty document)"
            return f"[DOCX attachment: {name}]\n```docx\n{_truncate(text)}\n```"
        except Exception as exc:
            return f"[DOCX attachment: {name} — extraction failed: {exc}]"

    # ── Plain text-ish: decode as UTF-8. ──
    if mime.startswith(_TEXTLIKE_PREFIXES) or mime in _TEXTLIKE_EXACT or suffix in {
        ".txt",
        ".md",
        ".csv",
        ".tsv",
        ".json",
        ".yaml",
        ".yml",
        ".log",
    }:
        try:
            text = abs_path.read_text(encoding="utf-8", errors="replace").strip()
            text = text or "(empty file)"
            lang = suffix.lstrip(".") or "text"
            return f"[Text attachment: {name}]\n```{lang}\n{_truncate(text)}\n```"
        except Exception as exc:
            return f"[Text attachment: {name} — read failed: {exc}]"

    return (
        f"[Attachment {name} ({mime or 'unknown type'}) — unsupported format, "
        "no inline content. Ask the user to convert it to PDF, DOCX, or plain text "
        "if you need to read it.]"
    )
