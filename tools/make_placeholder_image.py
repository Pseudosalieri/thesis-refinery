#!/usr/bin/env python3
"""Draw the demo thesis's placeholder figure, by hand, into a PNG.

The bundled example has to contain *some* image: without one, nothing exercises the
drawing checks, the caption stage or the media inventory, and a pipeline whose
figure handling is never run in its own demo is a pipeline whose figure handling is
never run. But the only honest image for a public example is one this repository
draws itself. A real instrument trace or a reproduced figure belongs to somebody
else — see ``docs/third-party.md``.

So the picture is generated, and it is generated *here* rather than committed as an
opaque blob: a grey rectangle with a dark border is visibly not data, and the fact
that it is not data is legible in the code as well as in the file. ``TINY_PNG`` in
``tests/conftest.py`` shows how little a valid PNG needs to be; this one is larger
only because a one-pixel image would not survive ``{width=70%}`` in the rendered
document.

The PNG is written with :mod:`zlib` and :mod:`struct` rather than with an image
library. A dependency would be a poor trade for four hundred bytes of grey, and it
would put the demo's figure outside what a reader can verify by reading.

Run it with no arguments:

    python tools/make_placeholder_image.py
"""

from __future__ import annotations

import struct
import zlib
from collections.abc import Iterator
from pathlib import Path

__all__ = ["BORDER", "FILL", "HEIGHT", "WIDTH", "build", "main", "png_bytes"]

WIDTH = 480
HEIGHT = 320
"""Big enough to look like a figure once it is scaled to 70% of the text width."""

FILL = (0xC9, 0xC9, 0xC9)
BORDER = (0x3A, 0x3A, 0x3A)
BORDER_PIXELS = 4

_FILTER_NONE = b"\x00"
"""PNG filter type 0: no prediction. The only one a flat rectangle benefits from."""

REPO_ROOT = Path(__file__).resolve().parent.parent
DESTINATION = (
    REPO_ROOT
    / "examples"
    / "demo-thesis"
    / "manuscript"
    / "chapters"
    / "raw"
    / "media"
    / "placeholder.png"
)


def _chunk(kind: bytes, payload: bytes) -> bytes:
    """One PNG chunk: length, type, payload, CRC over type and payload."""
    return (
        struct.pack(">I", len(payload))
        + kind
        + payload
        + struct.pack(">I", zlib.crc32(kind + payload) & 0xFFFFFFFF)
    )


def _row(row: int) -> bytes:
    """One scanline, filter byte included, as raw RGB triples."""
    if row < BORDER_PIXELS or row >= HEIGHT - BORDER_PIXELS:
        return _FILTER_NONE + bytes(BORDER) * WIDTH

    edge = bytes(BORDER) * BORDER_PIXELS
    middle = bytes(FILL) * (WIDTH - 2 * BORDER_PIXELS)
    return _FILTER_NONE + edge + middle + edge


def _scanlines() -> Iterator[bytes]:
    for row in range(HEIGHT):
        yield _row(row)


def png_bytes() -> bytes:
    """The complete PNG file as bytes.

    Deterministic by construction: same pixels, same zlib level, same bytes — which
    is what lets the test suite re-run this and compare.
    """
    header = struct.pack(">IIBBBBB", WIDTH, HEIGHT, 8, 2, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + _chunk(b"IHDR", header)
        + _chunk(b"IDAT", zlib.compress(b"".join(_scanlines()), 9))
        + _chunk(b"IEND", b"")
    )


def build(destination: Path = DESTINATION) -> Path:
    """Write the PNG to *destination*, creating parent directories, and return it."""
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(png_bytes())
    return destination


def main() -> int:
    path = build()
    print(f"wrote {path} ({path.stat().st_size} bytes, {WIDTH}x{HEIGHT} RGB)")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
