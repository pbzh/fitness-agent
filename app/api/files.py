"""File upload, list, download, delete."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, UploadFile, status
from fastapi import File as FastFile
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlmodel import select

from app.api.deps import get_approved_user_id
from app.config import get_settings
from app.db.models import File, FileKind
from app.db.session import AsyncSessionLocal
from app.files import storage

router = APIRouter(prefix="/files", tags=["files"])


class FileMeta(BaseModel):
    id: UUID
    kind: str
    filename: str
    mime_type: str
    size_bytes: int
    description: str | None
    prompt: str | None
    linked_workout_plan_id: UUID | None
    linked_meal_plan_id: UUID | None
    created_at: str


def _to_meta(f: File) -> FileMeta:
    return FileMeta(
        id=f.id,
        kind=f.kind.value if hasattr(f.kind, "value") else str(f.kind),
        filename=f.filename,
        mime_type=f.mime_type,
        size_bytes=f.size_bytes,
        description=f.description,
        prompt=f.prompt,
        linked_workout_plan_id=f.linked_workout_plan_id,
        linked_meal_plan_id=f.linked_meal_plan_id,
        created_at=f.created_at.isoformat(),
    )


@router.get("", response_model=list[FileMeta])
async def list_files(
    user_id: Annotated[UUID, Depends(get_approved_user_id)],
    kind: str | None = None,
) -> list[FileMeta]:
    async with AsyncSessionLocal() as session:
        stmt = select(File).where(File.user_id == user_id).order_by(File.created_at.desc())
        if kind:
            try:
                file_kind = FileKind(kind)
            except ValueError as exc:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Unknown file kind: {kind}",
                ) from exc
            stmt = stmt.where(File.kind == file_kind)
        result = await session.execute(stmt)
        return [_to_meta(f) for f in result.scalars().all()]


@router.post("", response_model=FileMeta, status_code=status.HTTP_201_CREATED)
async def upload_file(
    user_id: Annotated[UUID, Depends(get_approved_user_id)],
    upload: Annotated[UploadFile, FastFile()],
    description: str | None = None,
) -> FileMeta:
    settings = get_settings()
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = await upload.read(1024 * 1024)
        if not chunk:
            break
        total += len(chunk)
        if total > settings.max_upload_bytes:
            raise HTTPException(
                status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                detail=f"File exceeds {settings.max_upload_bytes // 1024 // 1024} MB limit",
            )
        chunks.append(chunk)
    data = b"".join(chunks)

    fid, rel = storage.write_bytes(
        data,
        filename=upload.filename or "upload",
        mime_type=upload.content_type,
    )
    async with AsyncSessionLocal() as session:
        f = File(
            id=fid,
            user_id=user_id,
            kind=FileKind.UPLOAD,
            filename=upload.filename or "upload",
            mime_type=upload.content_type or "application/octet-stream",
            size_bytes=len(data),
            storage_path=rel,
            description=description,
        )
        session.add(f)
        await session.commit()
        await session.refresh(f)
        return _to_meta(f)


@router.get("/{file_id}")
async def download_file(
    file_id: UUID,
    user_id: Annotated[UUID, Depends(get_approved_user_id)],
) -> FileResponse:
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(File).where(File.id == file_id, File.user_id == user_id)
        )
        f = result.scalar_one_or_none()
        if not f:
            raise HTTPException(status_code=404, detail="File not found")
        return FileResponse(
            path=str(storage.absolute_path(f.storage_path)),
            media_type=f.mime_type,
            filename=f.filename,
        )


@router.delete("/{file_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_file(
    file_id: UUID,
    user_id: Annotated[UUID, Depends(get_approved_user_id)],
) -> None:
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(File).where(File.id == file_id, File.user_id == user_id)
        )
        f = result.scalar_one_or_none()
        if not f:
            raise HTTPException(status_code=404, detail="File not found")
        storage.delete(f.storage_path)
        await session.delete(f)
        await session.commit()
