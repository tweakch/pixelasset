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


def blob_sizes(mask: np.ndarray, *, min_size: int = 1) -> list[int]:
    """Sizes of the 8-connected components of a boolean mask, largest first.

    A different question from `_axis_splits`, which asks whether a straight
    empty band cuts a cell in two and is a heuristic for grid search. This is
    exact connectivity, and on the leading sheets it measures something
    specific: the handler and the horse are two bodies joined by a lead rope, so
    a led frame is one component. Two components means the rope is not drawn —
    either because the handler let go, which the petting rows do on purpose, or
    because a frame is missing it, which one frame of the pack does by mistake.

    Run-length union-find: each row becomes a handful of horizontal runs, and
    runs in adjacent rows are joined when their columns touch or overlap by one.
    A sprite cell holds a few hundred runs against ten thousand pixels, which is
    what makes this cheap enough to sweep every frame of every sheet in the
    suite — per-pixel union-find and label propagation both measured about
    seventy times slower on the leading sheets, for the same answer.
    """
    m = np.ascontiguousarray(mask, dtype=bool)
    if not m.any():
        return []
    h, w = m.shape

    runs: list[list[tuple[int, int, int]]] = []      # per row: (start, end, id)
    starts: list[int] = []
    parent: list[int] = []
    for y in range(h):
        row = m[y]
        if not row.any():
            runs.append([])
            continue
        edges = np.flatnonzero(np.diff(np.concatenate(([0], row.view(np.int8), [0]))))
        here = []
        for a, b in zip(edges[0::2], edges[1::2]):
            here.append((int(a), int(b) - 1, len(parent)))
            starts.append(int(b) - int(a))
            parent.append(len(parent))
        runs.append(here)

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for y in range(1, h):
        for a0, a1, ai in runs[y]:
            for b0, b1, bi in runs[y - 1]:
                if b0 > a1 + 1:
                    break
                if b1 + 1 >= a0:                     # 8-connected: diagonals count
                    ra, rb = find(ai), find(bi)
                    if ra != rb:
                        parent[max(ra, rb)] = min(ra, rb)

    counts: dict[int, int] = {}
    for y in range(h):
        for a0, a1, ai in runs[y]:
            root = find(ai)
            counts[root] = counts.get(root, 0) + (a1 - a0 + 1)
    return sorted((n for n in counts.values() if n >= min_size), reverse=True)


def straddling_cells(a: np.ndarray, cw: int, ch: int) -> tuple[int, int]:
    """(cells touching both their left and right edge, occupied cells).

    The phase check `gutter_bleed` cannot make. Bleed counts pixels sitting on a
    boundary, which a correctly-cut sprite does all the time — it says nothing
    about whether the cut is in the right place. A cell that reaches both of its
    own side edges is different: a sprite narrower than its cell cannot do that,
    so it means the cut lands mid-sprite and the cell holds the tail of one
    frame beside the head of the next.

    Measured on the character sheet: 0 of 132 occupied cells at the true 16px
    pitch, 88 of 127 at an 18px one that divides the same 144px sheet.
    """
    h, w = a.shape
    straddle = occupied = 0
    for r in range(h // ch):
        for c in range(w // cw):
            cell = a[r * ch:(r + 1) * ch, c * cw:(c + 1) * cw] > 0
            if not cell.any():
                continue
            occupied += 1
            columns = cell.any(axis=0)
            straddle += bool(columns[0] and columns[-1])
    return straddle, occupied


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


def confirm_cell(a: np.ndarray, cell: tuple[int, int], *, source: str = "sheet",
                 max_straddle: float = 0.1) -> tuple[int, int]:
    """Validate a cell the config pinned, instead of searching for one.

    `detect_cell` is a search with guards tuned for this pack's horse sheets,
    and a guard that is right about horses can be wrong about something else:
    the character sheet is 16px wide per cell and `min_side` is 24, so the true
    grid is never even a candidate and the search reports no plausible grid at
    all. A pin is the answer to that, and it still has to be checked — a pin
    that does not divide the sheet, or that cuts sprites in half, is worth more
    than a wrong grid quietly rendering at 2.4x scale.

    So a pin is confirmed against what can still be measured: it divides the
    sheet, it yields frames, no cell of it straddles a sprite, and — where the
    free search does succeed — the pin has to agree with it. That last check is
    what still protects the horse sheets from the 160x164 grid that renders at
    2.4x scale.

    The straddle check is here because the other three all passed an 18px pin on
    this sheet for weeks. 144 has more than one divisor: 8 x 18 cuts the grid as
    cleanly as 9 x 16 does, yields frames in every row, and measures the same
    baseline of 33, because a pitch that is wrong by two pixels per column is
    still a pitch. What it is not is *in phase* — every frame after the first
    carried a slice of its neighbour, and the game drew a row of farmhands where
    it wanted one walking. Divisibility is not alignment, and this is the check
    that tells them apart.

    `holds_multiple_sprites` is deliberately NOT re-applied. It is a heuristic
    for ranking candidates the search invented, and on the character sheet it is
    simply wrong: 35 of 152 cells "split into two substantial parts" because a
    small character's head detaches from its body at the neck, which is one
    sprite drawn with a gap rather than two sprites in a cell. A pin is a
    reviewed line in config (PIPELINE.md §22), and overruling a heuristic is
    what it is for.
    """
    cw, ch = cell
    h, w = a.shape
    if cw <= 0 or ch <= 0 or w % cw or h % ch:
        raise ValueError(
            f"{source}: pinned cell {cw}x{ch} does not divide a {w}x{h} sheet"
        )
    if not any(row_frames(a, cw, ch, r) for r in range(h // ch)):
        raise ValueError(f"{source}: pinned cell {cw}x{ch} finds no frames")
    straddle, occupied = straddling_cells(a, cw, ch)
    if occupied and straddle > occupied * max_straddle:
        raise ValueError(
            f"{source}: pinned cell {cw}x{ch} cuts sprites — {straddle} of "
            f"{occupied} occupied cells reach both side edges, which a frame "
            f"narrower than its own cell cannot do. The pitch is out of phase."
        )
    try:
        found = detect_cell(a)
    except ValueError:
        return cw, ch          # nothing to disagree with: the case pins are for
    if found != (cw, ch):
        raise ValueError(
            f"{source}: config pins cell {cw}x{ch} but the artwork measures "
            f"{found[0]}x{found[1]}"
        )
    return cw, ch


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
