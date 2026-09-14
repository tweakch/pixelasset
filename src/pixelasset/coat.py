"""Recolouring a sprite sheet by palette substitution.

The premise the user named: a coat is a palette, and running the pipeline with
a different palette should render every sheet in that coat — with extracting
bay's own palette and re-rendering reproducing bay byte for byte.

That round trip is only trustworthy if the decomposition is lossless, so a
sheet is split into exactly two things and nothing is inferred:

    index map   one palette index per pixel, plus the original alpha
    palette     the ordered, exact colour list

render(index_map, palette) is the identity when the palette is unchanged. Not
approximately — the same bytes, which is what makes a substituted palette
trustworthy too.

One thing this is NOT: a way to derive a coat from the pack's other coats. The
pack's bay, black and white sheets are independently drawn — their alpha
channels differ and several bay colours map to more than one black colour, so
there is no recolouring between them. Bay's geometry is the canonical one here;
a rendered coat is a variant of *it*.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from PIL import Image

RGB = tuple[int, int, int]


@dataclass
class IndexedSheet:
    """A sheet as (palette index per pixel, alpha), plus its palette.

    `hidden` exists because the pack's PNGs carry colour underneath fully
    transparent pixels — 17 distinct values over 4058 pixels on the bay sheet
    alone, left behind by an exporter that erased alpha without clearing RGB.
    It is invisible, and it is still part of the file's bytes, so discarding it
    would make "byte-identical" quietly mean "identical where you can see".
    It is kept separate from `palette` because a coat must never touch it:
    substituting a colour nobody can see would be a silent no-op at best.
    """

    index: np.ndarray        # uint16 into `palette`; TRANSPARENT where alpha==0
    alpha: np.ndarray        # uint8, preserved exactly
    palette: list[RGB]       # visible colours — what a coat substitutes
    hidden: np.ndarray       # uint16 into `hidden_palette`, where alpha==0
    hidden_palette: list[RGB]

    TRANSPARENT = 0xFFFF

    @property
    def shape(self) -> tuple[int, int]:
        return self.index.shape


def decompose(sheet: np.ndarray) -> IndexedSheet:
    """Split an RGBA sheet into an index map and its exact palette.

    Colours are ordered by descending pixel count, so index 0 is the dominant
    body colour and a palette file reads in a stable, meaningful order rather
    than by whatever numpy happened to sort first.

    Partially transparent pixels keep their own colour entry: the pack uses a
    handful of authored translucent colours for socks markings, and folding
    them into the opaque palette would lose them on the way back.
    """
    h, w = sheet.shape[:2]
    alpha = sheet[:, :, 3].copy()
    visible = alpha > 0

    def table(mask):
        """Palette and index map for the pixels under `mask`.

        RGB is packed into one integer so np.unique can return the inverse
        mapping directly. The obvious version — a dict lookup per pixel — is a
        Python loop over ~700k pixels on the leading sheet and dominated the
        whole build.
        """
        if not mask.any():
            return [], np.zeros((h, w), dtype=np.uint16)
        px = sheet[:, :, :3][mask].astype(np.uint32)
        codes = (px[:, 0] << 16) | (px[:, 1] << 8) | px[:, 2]
        uniq, inverse, counts = np.unique(
            codes, return_inverse=True, return_counts=True
        )
        # Order by descending pixel count: index 0 is the dominant body colour,
        # so a palette file reads as a meaningful ladder rather than by whatever
        # numpy sorted first.
        order = np.argsort(-counts, kind="stable")
        rank = np.empty_like(order)
        rank[order] = np.arange(len(order))
        pal = [((int(c) >> 16) & 255, (int(c) >> 8) & 255, int(c) & 255)
               for c in uniq[order]]
        arr = np.zeros((h, w), dtype=np.uint16)
        arr[mask] = rank[inverse].astype(np.uint16)
        return pal, arr

    palette, index = table(visible)
    index[~visible] = IndexedSheet.TRANSPARENT
    hidden_palette, hidden = table(~visible)
    return IndexedSheet(index=index, alpha=alpha, palette=palette,
                        hidden=hidden, hidden_palette=hidden_palette)


def render(indexed: IndexedSheet, palette: list[RGB] | None = None) -> np.ndarray:
    """Rebuild an RGBA sheet from an index map and a palette.

    With `palette` omitted this is the exact inverse of `decompose`. With a
    substituted palette of the same length it is the same artwork in another
    coat — every pixel keeps its index, so shading, outline and dithering
    structure are untouched by construction.
    """
    pal = palette if palette is not None else indexed.palette
    if len(pal) != len(indexed.palette):
        raise ValueError(
            f"palette has {len(pal)} entries, sheet was indexed against "
            f"{len(indexed.palette)}"
        )
    h, w = indexed.shape
    out = np.zeros((h, w, 4), dtype=np.uint8)
    out[:, :, 3] = indexed.alpha
    transparent = indexed.index == IndexedSheet.TRANSPARENT

    if pal:
        lut = np.array(pal + [(0, 0, 0)], dtype=np.uint8)
        idx = np.where(transparent, len(pal), indexed.index)
        out[:, :, :3] = lut[idx]
    if indexed.hidden_palette:
        hlut = np.array(indexed.hidden_palette, dtype=np.uint8)
        out[:, :, :3] = np.where(
            transparent[:, :, None], hlut[indexed.hidden], out[:, :, :3]
        )
    return out


def load_sheet(path) -> np.ndarray:
    with Image.open(path) as im:
        return np.array(im.convert("RGBA"))


def save_sheet(path, sheet: np.ndarray) -> None:
    Image.fromarray(sheet, "RGBA").save(path)


def substitute(indexed: IndexedSheet, mapping: dict[RGB, RGB]) -> list[RGB]:
    """A new palette with `mapping` applied, positions preserved.

    Keyed by colour rather than index so a ramp is written once and applies to
    every sheet of a family — the ride, lead and jump sheets order their
    palettes by pixel count, which differs between them.

    Unmapped colours pass through untouched. That is the whole reason the coat
    ramp and the invariants are separated: the eye and the rider's skin are
    simply absent from the mapping, so they cannot be recoloured by accident.
    """
    return [mapping.get(c, c) for c in indexed.palette]


def coat_ramp(palettes: list[list[RGB]]) -> tuple[list[RGB], list[RGB]]:
    """Split one coat's palette into (ramp, invariant), given sibling coats.

    Derived rather than guessed: a colour present in *every* coat of a family
    cannot be what distinguishes them, so it is invariant — for this pack that
    finds exactly the four eye colours and nothing else. What remains is the
    ramp, returned lightest first so a substitution can be written as an
    ordered ladder.
    """
    if not palettes:
        return [], []
    shared = set(palettes[0])
    for pal in palettes[1:]:
        shared &= set(pal)
    lum = lambda c: 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2]  # noqa: E731
    ramp = sorted((c for c in palettes[0] if c not in shared), key=lum, reverse=True)
    return ramp, sorted(shared, key=lum, reverse=True)


# --- palette files ---------------------------------------------------------

def to_hex(c: RGB) -> str:
    return "#%02x%02x%02x" % c


def from_hex(s: str) -> RGB:
    s = s.lstrip("#")
    return (int(s[0:2], 16), int(s[2:4], 16), int(s[4:6], 16))


def extract_coat(sheets: list, siblings: list) -> dict:
    """Propose a coat palette from one coat's sheets, given its siblings.

    `sheets` are the sheets of the coat being extracted; `siblings` are the
    other coats of the same family, used only to work out which colours are
    invariant. Nothing is inferred from hue or pixel count — a colour every
    coat shares cannot be what distinguishes them.
    """
    own = decompose(sheets[0]).palette
    sibling_palettes = [own] + [decompose(s).palette for s in siblings]
    ramp, invariant = coat_ramp(sibling_palettes)
    return {
        "ramp": [to_hex(c) for c in ramp],
        "invariant": [to_hex(c) for c in invariant],
    }


def mapping_between(base: dict, target: dict) -> dict[RGB, RGB]:
    """Colour substitution from one coat palette to another, by ramp position.

    Both ramps are ordered lightest first, so step *i* of one coat becomes step
    *i* of the other and the shading ladder is carried over intact. Invariants
    are deliberately not in the mapping, so they pass through `substitute`
    untouched rather than relying on anyone remembering to exclude them.
    """
    a, b = base["ramp"], target["ramp"]
    if len(a) != len(b):
        raise ValueError(
            f"ramp length mismatch: base has {len(a)} steps, target has {len(b)}"
        )
    return {from_hex(x): from_hex(y) for x, y in zip(a, b)}
