"""Profile photo format checks. No database or site needed: the format is read from the first bytes of the file."""

import pytest

from app.photos import sniff_image_type

JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 20
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 20
WEBP = b"RIFF" + b"\x00\x00\x00\x00" + b"WEBPVP8 " + b"\x00" * 20


@pytest.mark.parametrize(("data", "content_type"), [(JPEG, "image/jpeg"), (PNG, "image/png"), (WEBP, "image/webp")])
def test_jpeg_png_and_webp_are_recognised(data, content_type):
    assert sniff_image_type(data) == content_type


@pytest.mark.parametrize(
    "data",
    [
        b"",
        b"GIF89a" + b"\x00" * 20,
        b"<html><script>alert(1)</script></html>",
        b"\x89PNG",  # signature cut short
        b"RIFF\x00\x00\x00\x00WAVEfmt ",  # RIFF, but not WebP
        b"\xff\xd8",  # JPEG cut short
    ],
)
def test_other_files_are_not_photos(data):
    assert sniff_image_type(data) is None
