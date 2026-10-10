"""Checks for uploaded profile photos: a size limit, and the image format read from the file's first bytes."""

MAX_PHOTO_BYTES = 2 * 1024 * 1024


# The uploader's Content-Type header is not trusted; only the bytes decide what the photo is.
def sniff_image_type(data: bytes) -> str | None:
    """The MIME type of a JPEG, PNG or WebP file, or None for anything else."""
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return None
