"""Documents on demand (PLAN.md 10.3.4), and the one place artifacts are written.

``docs.create`` turns text the agent composed into a file you can open — Markdown,
plain text, CSV, or a PDF rendered by the same headless Chromium the browser worker
uses. Every result is an artifact row minted here, so the evidence check
``artifact_uploaded`` is the server's own record, never the tool's claim.
"""

from __future__ import annotations

import hashlib
import html
import uuid
from pathlib import Path

from jarvis.core.config import get_settings
from jarvis.core.ids import uuid7
from jarvis.db.models.ops import Artifact
from sqlalchemy.ext.asyncio import AsyncSession

FORMATS = {
    "md": ("text/markdown", ".md"),
    "txt": ("text/plain", ".txt"),
    "csv": ("text/csv", ".csv"),
    "pdf": ("application/pdf", ".pdf"),
}
EXTENSIONS = {"image/png": ".png", "image/jpeg": ".jpg", "application/pdf": ".pdf"} | {
    ct: ext for ct, ext in FORMATS.values()
}


async def store_artifact(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    kind: str,
    filename: str,
    content_type: str,
    data: bytes,
    action_id: uuid.UUID | None = None,
    device_id: uuid.UUID | None = None,
) -> Artifact:
    """Write bytes under the artifact directory and mint the row that proves it."""
    settings = get_settings()
    if len(data) > settings.artifact_max_bytes:
        raise ValueError("artifact exceeds the size limit")
    artifact_id = uuid7()
    # The stored name is ours, never the caller's: a filename is untrusted input.
    ext = EXTENSIONS.get(content_type) or Path(filename).suffix[:8] or ".bin"
    directory = Path(settings.artifact_dir) / str(user_id)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{artifact_id}{ext}"
    path.write_bytes(data)
    artifact = Artifact(
        id=artifact_id,
        user_id=user_id,
        device_id=device_id,
        action_id=action_id,
        kind=kind,
        filename=(filename or f"{kind}{ext}")[:255],
        content_type=content_type,
        size_bytes=len(data),
        sha256=hashlib.sha256(data).hexdigest(),
        path=str(path),
    )
    session.add(artifact)
    await session.flush()
    return artifact


def safe_filename(title: str, ext: str) -> str:
    stem = "".join(c if c.isalnum() or c in " -_" else "" for c in title).strip()[:80]
    return f"{stem or 'document'}{ext}"


def markdown_to_html(title: str, content: str) -> str:
    from markdown_it import MarkdownIt

    body = MarkdownIt("commonmark", {"html": False}).enable("table").render(content)
    return (
        "<!doctype html><html><head><meta charset='utf-8'>"
        f"<title>{html.escape(title)}</title>"
        "<style>body{font:14px/1.5 -apple-system,Helvetica,Arial,sans-serif;max-width:720px;"
        "margin:40px auto;color:#111}h1,h2,h3{line-height:1.2}code,pre{font:12px/1.4 Menlo,"
        "monospace;background:#f4f4f5;padding:2px 4px;border-radius:4px}pre{padding:10px}"
        "table{border-collapse:collapse}td,th{border:1px solid #ddd;padding:4px 8px}</style>"
        f"</head><body><h1>{html.escape(title)}</h1>{body}</body></html>"
    )


async def html_to_pdf(page_html: str) -> bytes:
    from playwright.async_api import async_playwright

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        try:
            page = await browser.new_page()
            await page.set_content(page_html, wait_until="load")
            return await page.pdf(format="A4", print_background=True)
        finally:
            await browser.close()


async def render_document(title: str, content: str, fmt: str) -> tuple[bytes, str, str]:
    """(bytes, content_type, filename) for one of ``FORMATS``."""
    if fmt not in FORMATS:
        raise ValueError(f"format must be one of {', '.join(FORMATS)}")
    content_type, ext = FORMATS[fmt]
    if fmt == "pdf":
        data = await html_to_pdf(markdown_to_html(title, content))
    else:
        data = content.encode("utf-8")
    return data, content_type, safe_filename(title, ext)
