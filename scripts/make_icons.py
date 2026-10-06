#!/usr/bin/env python3
"""Render the router's Windows icons from the project mark in website/favicon.svg.

Maintainers only: the .ico files it writes are committed, so installing never
needs this script (or any imaging library). The mark is a dark rounded tile with
a white route that splits into two: a vertical stem and a fork. It is drawn here
from the same coordinates as the SVG with a small anti-aliased rasteriser, so the
script has no dependencies.

    python scripts/make_icons.py            # rewrite assets/windows/*.ico
    python scripts/make_icons.py --check    # fail if the committed files differ

Outputs (assets/windows/):
    codex-router.ico             tile icon: Start menu, desktop, taskbar, Alt-Tab
    codex-router-tray-dark.ico   white glyph for a dark taskbar
    codex-router-tray-light.ico  dark glyph for a light taskbar
"""

from __future__ import annotations

import argparse
import struct
import sys
import zlib
from pathlib import Path

OUTPUT_DIRECTORY = Path(__file__).resolve().parent.parent / "assets" / "windows"

# website/favicon.svg, viewBox 0 0 40 40.
TILE_SIZE = 40.0
TILE_RADIUS = 12.0
STROKE_RADIUS = 3.5 / 2
SEGMENTS = (
    ((11.0, 12.0), (11.0, 28.0)),  # stem
    ((11.0, 20.0), (22.0, 20.0)),  # run to the fork
    ((22.0, 20.0), (29.0, 12.0)),  # upper branch
    ((22.0, 20.0), (29.0, 28.0)),  # lower branch
)
TILE_COLOR = (23, 23, 23)
DARK_GLYPH = (23, 23, 23)
LIGHT_GLYPH = (255, 255, 255)

TILE_SIZES = (16, 20, 24, 32, 40, 48, 64, 96, 128, 256)
TRAY_SIZES = (16, 20, 24, 32, 40, 48, 64)
SUPERSAMPLE = 4


def inside_capsule(x: float, y: float, a: tuple[float, float], b: tuple[float, float]) -> bool:
    dx, dy = b[0] - a[0], b[1] - a[1]
    t = ((x - a[0]) * dx + (y - a[1]) * dy) / (dx * dx + dy * dy)
    t = max(0.0, min(1.0, t))
    px, py = a[0] + t * dx, a[1] + t * dy
    return (x - px) ** 2 + (y - py) ** 2 <= STROKE_RADIUS**2


def inside_glyph(x: float, y: float) -> bool:
    return any(inside_capsule(x, y, a, b) for a, b in SEGMENTS)


def inside_tile(x: float, y: float) -> bool:
    # Rounded square: distance from the nearest corner centre once past it.
    cx = min(max(x, TILE_RADIUS), TILE_SIZE - TILE_RADIUS)
    cy = min(max(y, TILE_RADIUS), TILE_SIZE - TILE_RADIUS)
    return 0.0 <= x <= TILE_SIZE and 0.0 <= y <= TILE_SIZE and (x - cx) ** 2 + (y - cy) ** 2 <= TILE_RADIUS**2


def render(size: int, kind: str) -> bytes:
    """RGBA pixels, row-major. kind: 'tile', 'glyph-light' (white) or 'glyph-dark'."""
    if kind == "tile":
        origin, extent = 0.0, TILE_SIZE
    else:
        # Crop to the glyph, with a small margin, so it fills a tray slot.
        origin, extent = 8.0, 24.0
    glyph_color = DARK_GLYPH if kind == "glyph-dark" else LIGHT_GLYPH
    samples = SUPERSAMPLE * SUPERSAMPLE
    pixels = bytearray()
    for row in range(size):
        for column in range(size):
            tile_hits = glyph_hits = 0
            for sub_row in range(SUPERSAMPLE):
                for sub_column in range(SUPERSAMPLE):
                    x = origin + (column + (sub_column + 0.5) / SUPERSAMPLE) * extent / size
                    y = origin + (row + (sub_row + 0.5) / SUPERSAMPLE) * extent / size
                    if kind == "tile":
                        if inside_tile(x, y):
                            tile_hits += 1
                            if inside_glyph(x, y):
                                glyph_hits += 1
                    elif inside_glyph(x, y):
                        glyph_hits += 1
            if kind == "tile":
                alpha = tile_hits / samples
                mix = glyph_hits / tile_hits if tile_hits else 0.0
                color = tuple(round(TILE_COLOR[i] * (1 - mix) + glyph_color[i] * mix) for i in range(3))
            else:
                alpha = glyph_hits / samples
                color = glyph_color
            pixels += bytes((*color, round(alpha * 255)))
    return bytes(pixels)


def png(size: int, rgba: bytes) -> bytes:
    def chunk(kind: bytes, data: bytes) -> bytes:
        body = kind + data
        return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body))

    stride = size * 4
    raw = b"".join(b"\x00" + rgba[row * stride : (row + 1) * stride] for row in range(size))
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(raw, 9))
        + chunk(b"IEND", b"")
    )


def dib(size: int, rgba: bytes) -> bytes:
    """A classic icon frame: a 32-bit BGRA bitmap, bottom-up, plus an empty AND mask.

    Every size below 256 uses this form (as the official icons do) because it is
    read everywhere, including .NET's System.Drawing, which rejects some small
    PNG-compressed frames.
    """
    stride = size * 4
    rows = [rgba[row * stride : (row + 1) * stride] for row in range(size)]
    bgra = b"".join(
        b"".join(bytes((r[i + 2], r[i + 1], r[i], r[i + 3])) for i in range(0, stride, 4))
        for r in reversed(rows)
    )
    mask = bytes(((size + 31) // 32) * 4 * size)
    header = struct.pack("<IiiHHIIiiII", 40, size, size * 2, 1, 32, 0, len(bgra) + len(mask), 0, 0, 0, 0)
    return header + bgra + mask


def ico(images: list[tuple[int, bytes]]) -> bytes:
    """An .ico from ready-made frames (size, bytes)."""
    header = struct.pack("<HHH", 0, 1, len(images))
    offset = 6 + 16 * len(images)
    directory = b""
    payload = b""
    for size, data in images:
        directory += struct.pack("<BBBBHHII", size % 256, size % 256, 0, 0, 1, 32, len(data), offset + len(payload))
        payload += data
    return header + directory + payload


def encode(size: int, kind: str) -> tuple[int, bytes]:
    pixels = render(size, kind)
    return size, (png(size, pixels) if size >= 256 else dib(size, pixels))


def build() -> dict[str, bytes]:
    return {
        "codex-router.ico": ico([encode(s, "tile") for s in TILE_SIZES]),
        "codex-router-tray-dark.ico": ico([encode(s, "glyph-light") for s in TRAY_SIZES]),
        "codex-router-tray-light.ico": ico([encode(s, "glyph-dark") for s in TRAY_SIZES]),
    }


def normalized(icon_bytes: bytes) -> list[tuple[int, bytes]]:
    """The pixels of every frame, independent of how zlib happened to compress them.

    Different zlib builds (and zlib-ng) produce different but equivalent PNG streams,
    so --check compares what the frames contain, not the compressed bytes.
    """
    count = struct.unpack("<H", icon_bytes[4:6])[0]
    frames = []
    for index in range(count):
        width, _, _, _, _, _, length, offset = struct.unpack("<BBBBHHII", icon_bytes[6 + 16 * index : 22 + 16 * index])
        frame = icon_bytes[offset : offset + length]
        if frame[:4] == b"\x89PNG":
            idat = b""
            position = 8
            while position < len(frame):
                size = struct.unpack(">I", frame[position : position + 4])[0]
                kind = frame[position + 4 : position + 8]
                if kind == b"IDAT":
                    idat += frame[position + 8 : position + 8 + size]
                position += 12 + size
            frame = b"PNG" + zlib.decompress(idat)
        frames.append((width or 256, frame))
    return frames


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--check", action="store_true", help="fail if the committed icons differ")
    args = parser.parse_args(argv)
    outputs = build()
    if args.check:
        stale = [
            name
            for name, data in outputs.items()
            if not (OUTPUT_DIRECTORY / name).is_file()
            or normalized((OUTPUT_DIRECTORY / name).read_bytes()) != normalized(data)
        ]
        if stale:
            print("out of date: " + ", ".join(stale) + " (run scripts/make_icons.py)", file=sys.stderr)
            return 1
        print("icons are up to date")
        return 0
    OUTPUT_DIRECTORY.mkdir(parents=True, exist_ok=True)
    for name, data in outputs.items():
        (OUTPUT_DIRECTORY / name).write_bytes(data)
        print(f"wrote {OUTPUT_DIRECTORY / name} ({len(data)} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
