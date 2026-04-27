"""Disk-backed file storage.

Bytes live under ``settings.file_storage_dir`` in a 2-level shard
(``ab/cd/<uuid>.<ext>``) to keep individual directories small. The DB only
holds the relative path.
"""

from __future__ import annotations

import mimetypes
from pathlib import Path
from uuid import UUID, uuid4

from app.config import get_settings


def _root() -> Path:
    root = Path(get_settings().file_storage_dir)
    root.mkdir(parents=True, exist_ok=True)
    return root


def _ext_for(filename: str, mime_type: str | None) -> str:
    suffix = Path(filename).suffix
    if suffix:
        return suffix
    guessed = mimetypes.guess_extension(mime_type or "") or ""
    return guessed


def write_bytes(
    data: bytes,
    *,
    filename: str,
    mime_type: str | None = None,
    file_id: UUID | None = None,
) -> tuple[UUID, str]:
    """Persist bytes to disk. Returns (file_id, relative_path)."""
    fid = file_id or uuid4()
    ext = _ext_for(filename, mime_type)
    rel = f"{str(fid)[:2]}/{str(fid)[2:4]}/{fid}{ext}"
    full = _root() / rel
    full.parent.mkdir(parents=True, exist_ok=True)
    full.write_bytes(data)
    return fid, rel


def absolute_path(relative_path: str) -> Path:
    return _root() / relative_path


def delete(relative_path: str) -> None:
    full = _root() / relative_path
    if full.exists():
        full.unlink()
