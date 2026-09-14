"""Deterministic Pillow helpers."""

from __future__ import annotations

from collections.abc import Iterable

from PIL import Image


def new_canvas(width: int, height: int) -> Image.Image:
    return Image.new("RGBA", (width, height), (0, 0, 0, 0))


def is_opaque(pixel: tuple[int, int, int, int], threshold: int = 255) -> bool:
    return pixel[3] >= threshold


def opaque_pixels(image: Image.Image) -> list[tuple[int, int, tuple[int, int, int, int]]]:
    pixels = image.load()
    assert pixels is not None
    width, height = image.size
    found: list[tuple[int, int, tuple[int, int, int, int]]] = []
    for y in range(height):
        for x in range(width):
            px = pixels[x, y]
            if is_opaque(px):
                found.append((x, y, px))
    return found


def rgb_tuple(pixel: tuple[int, ...]) -> tuple[int, int, int]:
    return (int(pixel[0]), int(pixel[1]), int(pixel[2]))


def palette_rgb_set(roles: dict[str, Iterable[int]]) -> dict[tuple[int, int, int], str]:
    mapping: dict[tuple[int, int, int], str] = {}
    for role, rgb in roles.items():
        r, g, b = (int(c) for c in rgb)
        mapping[(r, g, b)] = role
    return mapping


def nearest_neighbor_scale(image: Image.Image, scale: int) -> Image.Image:
    if scale < 1:
        raise ValueError("preview scale must be >= 1")
    width, height = image.size
    return image.resize((width * scale, height * scale), Image.Resampling.NEAREST)


def silhouette_mask(image: Image.Image) -> Image.Image:
    """Black opaque silhouette on transparent background."""
    out = Image.new("RGBA", image.size, (0, 0, 0, 0))
    src = image.load()
    dst = out.load()
    assert src is not None and dst is not None
    width, height = image.size
    for y in range(height):
        for x in range(width):
            if is_opaque(src[x, y]):
                dst[x, y] = (0, 0, 0, 255)
    return out


def connected_components(image: Image.Image) -> list[list[tuple[int, int]]]:
    """4-connected components of opaque pixels."""
    pixels = image.load()
    assert pixels is not None
    width, height = image.size
    seen = [[False] * width for _ in range(height)]
    components: list[list[tuple[int, int]]] = []

    def neighbors(x: int, y: int) -> list[tuple[int, int]]:
        out: list[tuple[int, int]] = []
        for nx, ny in ((x - 1, y), (x + 1, y), (x, y - 1), (x, y + 1)):
            if 0 <= nx < width and 0 <= ny < height:
                out.append((nx, ny))
        return out

    for y in range(height):
        for x in range(width):
            if seen[y][x] or not is_opaque(pixels[x, y]):
                continue
            stack = [(x, y)]
            seen[y][x] = True
            comp: list[tuple[int, int]] = []
            while stack:
                cx, cy = stack.pop()
                comp.append((cx, cy))
                for nx, ny in neighbors(cx, cy):
                    if not seen[ny][nx] and is_opaque(pixels[nx, ny]):
                        seen[ny][nx] = True
                        stack.append((nx, ny))
            components.append(comp)
    return components


def bounding_box(points: list[tuple[int, int]]) -> tuple[int, int, int, int] | None:
    if not points:
        return None
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    return (min(xs), min(ys), max(xs), max(ys))


def snap_binary_alpha(image: Image.Image) -> tuple[Image.Image, int]:
    """Force alpha to 0 or 255. Returns (image, count of repaired pixels)."""
    out = image.convert("RGBA")
    pixels = out.load()
    assert pixels is not None
    repaired = 0
    width, height = out.size
    for y in range(height):
        for x in range(width):
            r, g, b, a = pixels[x, y]
            if a == 0 or a == 255:
                continue
            repaired += 1
            pixels[x, y] = (0, 0, 0, 0) if a < 128 else (r, g, b, 255)
    return out, repaired
