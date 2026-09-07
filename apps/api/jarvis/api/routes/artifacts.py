"""Artifacts: bytes a device produced on request, kept here, served only to the owner.

The device uploads with the user's bearer token *and* the device id it paired as; the
row is minted server-side. The id then travels in the device's signed job result as
``artifact_id``, which is what the ``artifact_uploaded`` evidence kind checks — so the
bytes must actually have arrived for the action to verify.
"""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, File, Form, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy import select

from jarvis.api.deps import CurrentUser, SessionDep
from jarvis.core.config import get_settings
from jarvis.core.errors import Forbidden, NotFound
from jarvis.core.logging import get_logger
from jarvis.db.models.ops import Artifact, Device
from jarvis.services.documents import store_artifact

log = get_logger(__name__)
router = APIRouter(prefix="/v1", tags=["artifacts"])

_SAFE_KINDS = {"screenshot", "file", "capture"}
_EXTENSIONS = {"image/png": ".png", "image/jpeg": ".jpg", "application/pdf": ".pdf"}


def _out(artifact: Artifact) -> dict[str, Any]:
    return {
        "id": str(artifact.id),
        "kind": artifact.kind,
        "filename": artifact.filename,
        "content_type": artifact.content_type,
        "size_bytes": artifact.size_bytes,
        "sha256": artifact.sha256,
        "action_id": str(artifact.action_id) if artifact.action_id else None,
        "created_at": artifact.created_at.isoformat(),
        "url": f"/v1/artifacts/{artifact.id}",
    }


@router.post("/devices/{device_id}/artifacts", status_code=201)
async def upload_artifact(
    device_id: uuid.UUID,
    user: CurrentUser,
    session: SessionDep,
    file: Annotated[UploadFile, File()],
    kind: Annotated[str, Form()] = "file",
    action_id: Annotated[str | None, Form()] = None,
) -> dict[str, Any]:
    """A paired device hands over bytes. Bounded, hashed, stored outside the web root."""
    device = await session.get(Device, device_id)
    if device is None or device.user_id != user.id or not device.is_active:
        raise Forbidden("That device is not paired to this account")
    if kind not in _SAFE_KINDS:
        raise Forbidden(f"unknown artifact kind {kind}")

    settings = get_settings()
    data = await file.read(settings.artifact_max_bytes + 1)
    if len(data) > settings.artifact_max_bytes:
        raise Forbidden("artifact exceeds the size limit")

    artifact = await store_artifact(
        session,
        user_id=user.id,
        kind=kind,
        filename=file.filename or kind,
        content_type=file.content_type or "application/octet-stream",
        data=data,
        action_id=uuid.UUID(action_id) if action_id else None,
        device_id=device.id,
    )
    artifact_id = artifact.id
    log.info("artifact_stored", artifact_id=str(artifact_id), kind=kind, bytes=len(data))
    return _out(artifact)


@router.get("/artifacts")
async def list_artifacts(
    user: CurrentUser, session: SessionDep, limit: int = 50
) -> list[dict[str, Any]]:
    rows = (
        await session.scalars(
            select(Artifact)
            .where(Artifact.user_id == user.id)
            .order_by(Artifact.id.desc())
            .limit(min(limit, 200))
        )
    ).all()
    return [_out(a) for a in rows]


@router.get("/artifacts/{artifact_id}")
async def get_artifact(artifact_id: uuid.UUID, user: CurrentUser, session: SessionDep):  # noqa: ANN201
    artifact = await session.get(Artifact, artifact_id)
    if artifact is None or artifact.user_id != user.id:
        raise NotFound("Artifact")
    path = Path(artifact.path)
    if not path.exists():  # noqa: ASYNC240 — one stat on local disk
        raise NotFound("Artifact bytes")
    return FileResponse(path, media_type=artifact.content_type, filename=artifact.filename)
