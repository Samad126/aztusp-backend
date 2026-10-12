"""Streams a file from the university site through the API, so the frontend never needs the site session."""

from fastapi.responses import StreamingResponse
from starlette.background import BackgroundTask

from ..scraping import notices


def file_response(upstream, filename: str) -> StreamingResponse:
    """The site's file as a download: the name, size and type are kept, and the site connection closes afterwards."""
    headers = {"Content-Disposition": notices.content_disposition(filename)}
    if "Content-Length" in upstream.headers:
        headers["Content-Length"] = upstream.headers["Content-Length"]
    return StreamingResponse(
        upstream.iter_content(chunk_size=65536),
        media_type=upstream.headers.get("Content-Type", "application/octet-stream"),
        headers=headers,
        background=BackgroundTask(upstream.close),
    )
