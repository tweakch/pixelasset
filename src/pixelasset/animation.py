"""Idle animation from a Path C key pose. Do not redraw frames."""

from __future__ import annotations

from typing import Any

from PIL import Image

from pixelasset.pixelops import copy_shift, is_opaque, opaque_pixels, rgb_tuple


WALK_NAMES = {"walk"}


def idle_clip(spec: dict[str, Any]) -> dict[str, Any] | None:
    clips = spec.get("animation", {}).get("animations") or []
    for clip in clips:
        if str(clip.get("name", "")).lower() == "idle":
            return clip
    return None


def reject_walk(spec: dict[str, Any]) -> str | None:
    clips = spec.get("animation", {}).get("animations") or []
    for clip in clips:
        name = str(clip.get("name", "")).lower()
        if name in WALK_NAMES:
            return f"Walk is out of scope (clip {clip.get('name')!r})"
    forbidden = spec.get("constraints", {}).get("forbidden_elements") or []
    if any(str(item).lower() == "walk" for item in forbidden):
        return None
    return None


def idle_frame_count(spec: dict[str, Any], style: dict[str, Any]) -> int:
    clip = idle_clip(spec)
    if clip is None:
        raise ValueError("animation.enabled requires an idle clip")
    count = int(clip["frames"])
    rules = (style.get("animation") or {}).get("idle") or {}
    minimum = int(rules.get("min_frames") or 2)
    maximum = int(rules.get("max_frames") or 4)
    if count < minimum or count > maximum:
        raise ValueError(f"idle frames must be {minimum}–{maximum}, got {count}")
    return count


def _top_cells(image: Image.Image, *, cluster: int) -> list[tuple[int, int]]:
    pts = [(x, y) for x, y, _ in opaque_pixels(image)]
    if not pts:
        return []
    min_y = min(y for _, y in pts)
    cells = [(x, y) for x, y in pts if y == min_y]
    if len(cells) < cluster:
        cells = [(x, y) for x, y in pts if y <= min_y + 1]
    return cells


def _tail_cells(image: Image.Image, *, cluster: int) -> list[tuple[int, int]]:
    """Left-edge pixels in the mid band (tail), never hooves."""
    pts = [(x, y) for x, y, _ in opaque_pixels(image)]
    if not pts:
        return []
    height = image.size[1]
    mid = [(x, y) for x, y in pts if height // 4 <= y <= (height * 3) // 4]
    if not mid:
        mid = pts
    min_x = min(x for x, _ in mid)
    cells = [(x, y) for x, y in mid if x == min_x]
    if len(cells) < cluster:
        cells = [(x, y) for x, y in mid if x <= min_x + 1]
    # Keep a contiguous vertical run of at least `cluster`.
    cells.sort(key=lambda p: p[1])
    if len(cells) >= cluster:
        return cells[: max(cluster, min(4, len(cells)))]
    return cells


def _breath_cells(
    image: Image.Image,
    palette: dict[str, Any],
    *,
    cluster: int,
) -> list[tuple[int, int]]:
    """LIGHT pixels on the upper barrel — lighting stays top-left."""
    light = tuple(int(c) for c in palette["roles"]["LIGHT"])
    pts = [
        (x, y)
        for x, y, px in opaque_pixels(image)
        if rgb_tuple(px) == light
    ]
    if len(pts) < cluster:
        return []
    pts.sort(key=lambda p: (p[1], p[0]))
    return pts[:cluster]


def apply_idle_motions(
    key: Image.Image,
    spec: dict[str, Any],
    palette: dict[str, Any],
    style: dict[str, Any],
) -> list[Image.Image]:
    """Key pose + 1px shifts. Frame 0 is pixel-identical to the key."""
    count = idle_frame_count(spec, style)
    frames = [key.copy() for _ in range(count)]
    rules = (style.get("animation") or {}).get("idle") or {}
    motions = list(rules.get("motions") or [])
    if not motions:
        motions = [
            {"name": "ear", "frame": 1, "dy": -1},
            {"name": "tail", "frame": 2, "dx": -1},
        ]
    highlight = tuple(int(c) for c in palette["roles"]["HIGHLIGHT"]) + (255,)
    for motion in motions:
        frame_index = int(motion.get("frame") or 0)
        if frame_index < 1 or frame_index >= count:
            continue
        name = str(motion.get("name") or "")
        cluster = int(motion.get("cluster") or 2)
        current = frames[frame_index]
        if name == "ear":
            dy = int(motion.get("dy") or -1)
            frames[frame_index] = copy_shift(
                current, _top_cells(current, cluster=cluster), 0, dy
            )
        elif name == "tail":
            dx = int(motion.get("dx") or -1)
            frames[frame_index] = copy_shift(
                current, _tail_cells(current, cluster=cluster), dx, 0
            )
        elif name == "breath":
            cells = _breath_cells(current, palette, cluster=cluster)
            pixels = current.load()
            assert pixels is not None
            for x, y in cells:
                if is_opaque(pixels[x, y]):
                    pixels[x, y] = highlight
        else:
            raise ValueError(f"unknown idle motion {name!r}")
    return frames
