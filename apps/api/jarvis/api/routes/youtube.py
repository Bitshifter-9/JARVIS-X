"""Trigger and observe the YouTube pipeline.

The trigger only enqueues — rendering happens in the video worker, and the upload
still has to clear an R2 approval. Nothing here publishes anything.
"""

from __future__ import annotations

import uuid as uuid_module
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Annotated, Any

import jwt
from fastapi import APIRouter, File, Form, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy import text

from jarvis.api.deps import CurrentUser, SessionDep
from jarvis.core.config import get_settings
from jarvis.core.errors import Forbidden, NotFound, ProblemError
from jarvis.db.models.agent import Action
from jarvis.db.queue import JobQueue
from jarvis.services.youtube.pipeline import asset_dir, f5_available, find_assets

router = APIRouter(prefix="/v1/youtube", tags=["youtube"])

# suffixes and byte cap per asset kind. The voice sample should be ~15s of clean
# speech; the avatar is a short silent "hero" video of the user facing the camera;
# the music track is royalty-free BGM, looped and mixed quietly under the voice.
ASSET_RULES: dict[str, tuple[set[str], int]] = {
    "voice": ({".wav", ".mp3", ".m4a"}, 25 * 2**20),
    "avatar": ({".mp4", ".mov"}, 300 * 2**20),
    "music": ({".mp3", ".wav", ".m4a"}, 20 * 2**20),
}


class GenerateRequest(BaseModel):
    topic: str = Field(min_length=3, max_length=300)


@router.post("/videos", status_code=202)
async def generate(body: GenerateRequest, user: CurrentUser, session: SessionDep) -> dict:
    job = await JobQueue(session).enqueue(
        "youtube.generate",
        {"topic": body.topic, "user_id": str(user.id)},
        user_id=user.id,
        max_attempts=2,
    )
    return {"job_id": str(job.id), "status": "queued", "topic": body.topic}


@router.post("/assets", status_code=201)
async def upload_asset(
    user: CurrentUser,
    kind: Annotated[str, Form()],
    file: Annotated[UploadFile, File()],
) -> dict[str, Any]:
    """Store the user's voice sample or hero video. One of each; re-upload replaces."""
    if kind not in ASSET_RULES:
        raise ProblemError(
            status=400, title="Unknown asset kind", type_="bad-request",
            detail=f"kind must be one of: {', '.join(ASSET_RULES)}",
        )
    suffixes, cap = ASSET_RULES[kind]
    suffix = Path(file.filename or "").suffix.lower()
    if suffix not in suffixes:
        raise ProblemError(
            status=400, title="Unsupported file type", type_="bad-request",
            detail=f"{kind} accepts: {', '.join(sorted(suffixes))}",
        )

    dest_dir = asset_dir(user.id)
    dest_dir.mkdir(parents=True, exist_ok=True)
    for old in dest_dir.glob(f"{kind}.*"):
        old.unlink()

    dest = dest_dir / f"{kind}{suffix}"
    size = 0
    try:
        with dest.open("wb") as f:
            while chunk := await file.read(1 << 20):
                size += len(chunk)
                if size > cap:
                    raise ProblemError(
                        status=413, title="File too large", type_="too-large",
                        detail=f"{kind} uploads are capped at {cap >> 20} MB",
                    )
                f.write(chunk)
    except ProblemError:
        dest.unlink(missing_ok=True)
        raise
    return {"kind": kind, "filename": dest.name, "bytes": size}


@router.get("/assets")
async def list_assets(user: CurrentUser) -> dict[str, Any]:
    """What is uploaded, and whether avatar mode can actually engage."""
    found = find_assets(user.id)
    return {
        "assets": {
            kind: (
                {"filename": path.name, "bytes": path.stat().st_size}
                if path is not None
                else None
            )
            for kind, path in found.items()
        },
        "voice_clone_ready": found["voice"] is not None and f5_available(),
        "lipsync_configured": bool(get_settings().youtube_lipsync_cmd),
    }


@router.post("/videos/{job_id}/publish", status_code=202)
async def publish(
    job_id: uuid_module.UUID, user: CurrentUser, session: SessionDep
) -> dict[str, Any]:
    """Propose the upload for a finished render again — the recovery path when an
    approval window lapsed or an earlier attempt was skipped."""
    from jarvis.workers.video import propose_upload

    row = (
        await session.execute(
            text("""
                SELECT result FROM jobs
                WHERE id = :id AND user_id = :user_id
                  AND kind = 'youtube.generate' AND status = 'succeeded'
            """),
            {"id": job_id, "user_id": str(user.id)},
        )
    ).scalar()
    if not row or not row.get("video_path"):
        raise NotFound("Finished render")

    proposal = await propose_upload(
        session, user.id, row, rationale=f"Re-publish: {row.get('title', 'video')}"
    )
    return {"action_id": str(proposal.action.id), "status": "awaiting approval"}


@router.get("/actions/{action_id}/preview_url")
async def preview_url(
    action_id: uuid_module.UUID, user: CurrentUser, session: SessionDep
) -> dict[str, Any]:
    """A short-lived signed link to the rendered file, so 'approve' can mean
    'approve what I just watched'. Signed because video players and browsers
    cannot send an Authorization header."""
    action = await session.get(Action, action_id)
    if action is None or action.user_id != user.id:
        raise NotFound("Action")
    token = jwt.encode(
        {
            "sub": str(user.id),
            "purpose": "video_preview",
            "action_id": str(action_id),
            "exp": datetime.now(UTC) + timedelta(hours=1),
        },
        get_settings().jwt_secret,
        algorithm=get_settings().jwt_algorithm,
    )
    return {"url": f"{get_settings().base_url}/v1/youtube/actions/{action_id}/video?st={token}"}


@router.get("/actions/{action_id}/video")
async def preview_video(action_id: uuid_module.UUID, st: str, session: SessionDep):
    """Streams the rendered mp4. Auth is the signed ``st`` token from preview_url."""
    s = get_settings()
    try:
        claims = jwt.decode(st, s.jwt_secret, algorithms=[s.jwt_algorithm])
    except jwt.PyJWTError as exc:
        raise Forbidden("Invalid or expired preview link") from exc
    if claims.get("purpose") != "video_preview" or claims.get("action_id") != str(action_id):
        raise Forbidden("This link was issued for something else")

    action = await session.get(Action, action_id)
    if action is None or str(action.user_id) != claims.get("sub"):
        raise NotFound("Action")

    import asyncio

    def _resolve() -> Path | None:
        path = Path(str(action.args.get("file_path", ""))).resolve()
        workdir = Path(s.youtube_workdir).resolve()
        return path if path.is_relative_to(workdir) and path.is_file() else None

    path = await asyncio.to_thread(_resolve)
    if path is None:
        raise NotFound("Rendered file")
    return FileResponse(path, media_type="video/mp4", filename="preview.mp4")


@router.get("/analytics")
async def analytics(user: CurrentUser, session: SessionDep) -> dict[str, Any]:
    """The latest channel report; the same rows feed the script prompt's memory."""
    row = (
        await session.execute(
            text("""
                SELECT result FROM jobs
                WHERE user_id = :u AND kind = 'youtube.analytics'
                  AND status = 'succeeded' AND result ? 'videos'
                ORDER BY id DESC LIMIT 1
            """),
            {"u": str(user.id)},
        )
    ).scalar()
    return {"available": row is not None, **(row or {})}


@router.post("/analytics/refresh", status_code=202)
async def refresh_analytics(user: CurrentUser, session: SessionDep) -> dict[str, Any]:
    job = await JobQueue(session).enqueue(
        "youtube.analytics", {"user_id": str(user.id)}, user_id=user.id
    )
    return {"job_id": str(job.id), "status": "queued"}


@router.post("/comments/check", status_code=202)
async def check_comments(user: CurrentUser, session: SessionDep) -> dict[str, Any]:
    """Sweep comments on uploaded videos; each draft reply becomes an approval."""
    job = await JobQueue(session).enqueue(
        "youtube.comments", {"user_id": str(user.id)}, user_id=user.id
    )
    return {"job_id": str(job.id), "status": "queued"}


@router.get("/videos")
async def list_videos(user: CurrentUser, session: SessionDep) -> list[dict[str, Any]]:
    """Pipeline runs, newest first — render jobs and their upload jobs alike."""
    rows = (
        await session.execute(
            text("""
                SELECT id, kind, status, payload, result, last_error, created_at, updated_at
                FROM jobs
                WHERE user_id = :user_id AND kind LIKE 'youtube.%'
                ORDER BY id DESC
                LIMIT 50
            """),
            {"user_id": str(user.id)},
        )
    ).mappings().all()
    return [
        {
            "id": str(r["id"]),
            "kind": r["kind"],
            "status": r["status"],
            "topic": (r["payload"] or {}).get("topic"),
            "result": r["result"],
            "error": r["last_error"],
            "created_at": r["created_at"].isoformat(),
            "updated_at": r["updated_at"].isoformat() if r["updated_at"] else None,
        }
        for r in rows
    ]
