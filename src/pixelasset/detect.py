"""Measurement primitives.

Nothing in here knows what a horse is. Every function takes pixels and returns
numbers, so the semantic layer (which row is a walk) stays in config where
PIPELINE.md §18 wants it, and the measurable layer stays here where it can be
checked against the artwork.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

import numpy as np
from PIL import Image


@dataclass(frozen=True)
class BBox:
    x0: int
    y0: int
    x1: int
    y1: int

    @property
    def width(self) -> int:
        return self.x1 - self.x0 + 1

    @property
    def height(self) -> int:
        return self.y1 - self.y0 + 1


def load_rgba(path) -> np.ndarray:
    with Image.open(path) as im:
        return np.array(im.convert("RGBA"))


def alpha(sheet: np.ndarray) -> np.ndarray:
    return sheet[:, :, 3]


def cell_bbox(a: np.ndarray, min_pixels: int = 20) -> BBox | None:
    """Alpha bounding box of one cell, or None if the cell is effectively empty.

    `min_pixels` exists because a few sheets bleed a pixel or two of a
    neighbouring sprite across a gutter; a handful of stray pixels is not a
    frame and must not be counted as one.
    """
    ys, xs = np.nonzero(a)
    if len(ys) < min_pixels:
        return None
    return BBox(int(xs.min()), int(ys.min()), int(xs.max()), int(ys.max()))


def gutter_bleed(a: np.ndarray, cw: int, ch: int) -> int:
    """Opaque pixels sitting exactly on a cell boundary, summed.

    A correct grid scores near zero. It is rarely exactly zero: sprites in this
    pack routinely touch their cell edge, and a couple bleed a pixel over it.
    """
    h, w = a.shape
    total = 0
    for x in range(cw, w, cw):
        total += int((a[:, x] > 0).sum())
    for y in range(ch, h, ch):
        total += int((a[y, :] > 0).sum())
    return total


def _axis_splits(cell: np.ndarray, axis: int, min_share: float) -> bool:
    """True if an interior empty band cuts this cell into two substantial parts.

    This is what separates a real cell from a coarser grid whose cells each hold
    a block of sprites. A 2x2 block splits 0.5/0.5; a horse with a handler beside
    it does not split at all, because the lead rope connects them, and a floating
    heart emote is far too small to clear `min_share`. Measured, not assumed.
    """
    occupied = cell > 0
    total = int(occupied.sum())
    if total == 0:
        return False
    mass = occupied.sum(axis=axis)
    nz = np.nonzero(mass)[0]
    if len(nz) == 0:
        return False
    seg = mass[nz[0]:nz[-1] + 1]

    i = 0
    while i < len(seg):
        if seg[i] != 0:
            i += 1
            continue
        j = i
        while j < len(seg) and seg[j] == 0:
            j += 1
        left, right = int(seg[:i].sum()), int(seg[j:].sum())
        if left >= total * min_share and right >= total * min_share:
            return True
        i = j
    return False


def holds_multiple_sprites(
    a: np.ndarray, cw: int, ch: int, *, sample: int = 10, min_share: float = 0.3
) -> bool:
    """Does a candidate cell size hold more than one sprite per cell?"""
    rows, cols = a.shape[0] // ch, a.shape[1] // cw
    checked = 0
    for r in range(rows):
        for c in range(cols):
            cell = a[r * ch:(r + 1) * ch, c * cw:(c + 1) * cw]
            if (cell > 0).sum() < 40:
                continue
            if _axis_splits(cell, 0, min_share) or _axis_splits(cell, 1, min_share):
                return True
            checked += 1
            if checked >= sample:
                return False
    return False


# Grid detection brute-forces candidate cell sizes and walks cells in Python,
# which is the slowest step in a build. It depends only on the alpha channel,
# so the result is memoised on that: ingest builds sixteen assets from the same
# few sheets, and a recoloured coat never changes alpha at all.
_CELL_CACHE: dict[bytes, tuple[int, int]] = {}


def detect_cell(
    a: np.ndarray,
    *,
    min_side: int = 24,
    max_side: int = 400,
    aspect: tuple[float, float] = (0.4, 2.5),
) -> tuple[int, int]:
    """Best (cell_w, cell_h) for a sheet.

    Two traps this has to survive, both real in the sample pack:

    - Several coarser grids also divide the sheet exactly and score *lower*
      bleed than the true one, because they cut through fewer sprites. The jump
      sheet reads as 160x164 that way, and every one of those cells holds a 2x2
      block of sprites — taking it at face value renders the horse at 2.4x
      scale. So candidates whose cells hold more than one sprite are rejected
      outright, before bleed is considered at all.
    - Requiring *zero* bleed rejects the correct grid on sheets where sprites
      touch their cell edges, so survivors are ranked by bleed per cut line
      rather than filtered on it.
    """
    h, w = a.shape
    key = hashlib.blake2b(
        np.ascontiguousarray(a).tobytes(), digest_size=16,
        key=f"{min_side}:{max_side}:{aspect}".encode(),
    ).digest()
    cached = _CELL_CACHE.get(key)
    if cached is not None:
        return cached

    widths = [d for d in range(min_side, max_side + 1) if w % d == 0]
    heights = [d for d in range(min_side, max_side + 1) if h % d == 0]

    best: tuple[float, int, int, int] | None = None
    for cw in widths:
        for ch in heights:
            if not (aspect[0] <= cw / ch <= aspect[1]):
                continue
            if holds_multiple_sprites(a, cw, ch):
                continue
            cuts = (w // cw - 1) + (h // ch - 1)
            per_line = gutter_bleed(a, cw, ch) / max(1, cuts)
            score = (per_line, -(w // cw) * (h // ch), cw, ch)
            if best is None or score < best:
                best = score
    if best is None:
        raise ValueError(f"no plausible grid for a {w}x{h} sheet")
    _CELL_CACHE[key] = (best[2], best[3])
    return _CELL_CACHE[key]


def row_frames(a: np.ndarray, cw: int, ch: int, row: int) -> list[BBox]:
    """Per-column bounding boxes for one row, stopping at the first empty cell.

    Trailing cells in a row are padding, not frames. Stopping at the first gap
    (rather than skipping it) means a row that is genuinely sparse in the middle
    surfaces as a short frame count instead of silently splicing across the gap.
    """
    cols = a.shape[1] // cw
    out: list[BBox] = []
    for c in range(cols):
        box = cell_bbox(a[row * ch:(row + 1) * ch, c * cw:(c + 1) * cw])
        if box is None:
            break
        out.append(box)
    return out


def baseline(boxes: list[BBox]) -> int:
    """The ground line for a set of frames: the lowest occupied row."""
    return max(b.y1 for b in boxes)


def airborne_columns(
    boxes: list[BBox], ch: int, first_col: int, *, min_gap: int = 5
) -> tuple[int, int] | None:
    """Inclusive column range where the sprite has left the ground.

    Derived from how far each frame's content floats above the cell floor, so
    the frames a game treats as airborne are exactly the frames that *look*
    airborne. A guessed timer drifts away from the artwork the moment fps
    changes; this cannot.
    """
    air = [i for i, b in enumerate(boxes) if (ch - 1 - b.y1) >= min_gap]
    if not air:
        return None
    return first_col + air[0], first_col + air[-1]


def semi_transparent(sheet: np.ndarray) -> tuple[int, list[int]]:
    """Count of partially-opaque pixels, and the distinct alpha values used.

    A long tail of alpha values means anti-aliasing (§5 forbids it). A handful
    of discrete values means an authored translucent colour, which is a
    different thing and a decision for config, not a defect to silently repair.
    """
    a = alpha(sheet)
    mask = (a > 0) & (a < 255)
    return int(mask.sum()), sorted({int(v) for v in a[mask]})


def opaque_palette(sheet: np.ndarray) -> list[tuple[int, int, int]]:
    a = alpha(sheet)
    rgb = sheet[a == 255][:, :3]
    if len(rgb) == 0:
        return []
    return sorted(map(tuple, np.unique(rgb, axis=0)))
