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

What this is NOT: a way to derive a coat from *every* one of the pack's other
coats. Measured against bay, black and white correspond one-to-one — thirteen
colours each, worst minority branch 137 pixels of 135823 — so they are
recolourings of bay and nothing more. Paint and socks are not: the same bay
colour becomes a white patch in one place and stays brown in another, which no
colour-keyed map can express at any size. Bay's geometry is the canonical one
here, a rendered coat is a variant of *it*, and a colour whose correspondence
splits is a spatial marking rather than a coat colour.
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


def render(
    indexed: IndexedSheet,
    palette: list[RGB] | None = None,
    *,
    overlay: tuple[np.ndarray, list[RGB]] | None = None,
) -> np.ndarray:
    """Rebuild an RGBA sheet from an index map and a palette.

    With `palette` omitted this is the exact inverse of `decompose`. With a
    substituted palette of the same length it is the same artwork in another
    coat — every pixel keeps its index, so shading, outline and dithering
    structure are untouched by construction.

    `overlay` is a per-PIXEL override applied on top of the palette result, as
    (index_map, colours) where index 0 means "no override" and index i means
    colours[i-1]. It is the one thing a palette cannot express: a marking is a
    region of the artwork rather than a colour of it, because the same source
    colour becomes a white patch in one place and stays brown in another.

    It deliberately does not live in `IndexedSheet`, which is the lossless
    decomposition of one sheet — a marking is not part of that sheet, and
    folding it in would give `render(decompose(x))` byte-exactness an exception
    clause. Alpha is never read from an overlay and never written by one, and
    overrides are clipped to pixels the sheet already draws, so a stale mask
    cannot resurrect a transparent pixel.
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
    if overlay is not None:
        ov_index, ov_colours = overlay
        if ov_index.shape != indexed.shape:
            raise ValueError(
                f"overlay is {ov_index.shape}, sheet is {indexed.shape}"
            )
        if len(ov_colours) != int(ov_index.max()):
            raise ValueError(
                f"overlay has {len(ov_colours)} colours but indexes up to "
                f"{int(ov_index.max())}"
            )
        selected = (ov_index > 0) & ~transparent
        olut = np.array([(0, 0, 0)] + list(ov_colours), dtype=np.uint8)
        out[:, :, :3][selected] = olut[ov_index[selected]]
    # Last on purpose: the hidden restore is what keeps colour under alpha==0
    # byte-exact, so nothing above may write into those pixels.
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

    Unmapped colours pass through untouched, and that now means one specific
    thing: a colour outside the base coat's key space. The rider's skin, the
    halter, the rope and the tack are not in bay's thirteen colours, and they
    are identical across bay and black on all three sheet families, so passing
    them through is measured rather than hoped for. The eye is *in* the key
    space and is protected by an explicit identity row instead — on socks_brown
    the eye colour itself splits 92/7, so "the eye is invariant" was true of
    three sheets, not of the colour.
    """
    return [mapping.get(c, c) for c in indexed.palette]


# --- palette files ---------------------------------------------------------

def to_hex(c: RGB) -> str:
    return "#%02x%02x%02x" % c


def from_hex(s: str) -> RGB:
    s = s.lstrip("#")
    return (int(s[0:2], 16), int(s[2:4], 16), int(s[4:6], 16))


# --- markings --------------------------------------------------------------

@dataclass(frozen=True)
class Marking:
    """A region of the artwork, derived from two sheets of one geometry.

    The pack ships the same horse with and without each marking, so a marking
    *is* the set of pixels where those two sheets differ. Each override colour
    is then classified by one test — is it a colour the base coat has? — and
    that single test is what makes a marking compose over any body:

        ink      a colour the marking introduced. Body-independent, and by
                 derivation never a key of a base-coat mapping, so it passes
                 through `substitute_marking` untouched exactly like the eye.
        reshade  a colour the base coat already has, i.e. the artist
                 re-shading the body at a marking edge. Substituted through
                 the body mapping exactly like the body is.

    Nothing has to be tagged and nothing has to be remembered, which is the
    whole reason to classify by palette membership rather than by hue. A
    hue rule (achromatic = ink) misclassifies 2964 genuine ink pixels on the
    jump sheets, where the artist tinted the marking's dark end warm.
    """

    index: np.ndarray        # uint16; 0 = no override, i = colours[i - 1]
    colours: list[RGB]       # override colour per index, in derivation order
    counts: list[int]        # pixel count per colour
    ink: list[RGB]           # colours the base coat does not have
    reshade: list[RGB]       # colours it does

    @property
    def pixels(self) -> int:
        return sum(self.counts)

    @property
    def ink_pixels(self) -> int:
        ink = set(self.ink)
        return sum(n for c, n in zip(self.colours, self.counts) if c in ink)

    @property
    def reshade_pixels(self) -> int:
        return self.pixels - self.ink_pixels

    @property
    def shape(self) -> tuple[int, int]:
        return self.index.shape

    def ink_ladder(self) -> list[RGB]:
        """The ink colours, lightest first — how a marking reads to a human."""
        return sorted(self.ink, key=_luminance, reverse=True)


def _luminance(c: RGB) -> float:
    return 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2]


def derive_marking(base: np.ndarray, marking: np.ndarray) -> Marking:
    """The marking that turns `base` into `marking`, as a per-pixel override.

    Restricted to pixels fully opaque in both sheets. That is not a shortcut:
    the marking coats carry a handful of anti-aliased edge pixels that bay does
    not, and admitting them would put semi-transparent colour into a coat whose
    alpha is bay's. Excluding them means a rendered marked coat carries bay's
    alpha exactly, and bay has no semi-alpha at all — so §5's no-anti-aliasing
    rule comes out better satisfied than the shipped sheet's.

    The cost is that a rendered marked coat is not a byte reproduction of the
    shipped one: in a few frames the shipped marking bleeds a pixel outside
    bay's silhouette. Worst case measured is 393 px of 2.75M on the leading
    sheets, 0.014%.
    """
    if base.shape != marking.shape:
        raise ValueError(f"base is {base.shape}, marking is {marking.shape}")

    both = (base[:, :, 3] == 255) & (marking[:, :, 3] == 255)
    differs = np.any(base[:, :, :3] != marking[:, :, :3], axis=2)
    mask = both & differs

    index = np.zeros(base.shape[:2], dtype=np.uint16)
    if not mask.any():
        return Marking(index=index, colours=[], counts=[], ink=[], reshade=[])

    px = marking[:, :, :3][mask].astype(np.uint32)
    codes = (px[:, 0] << 16) | (px[:, 1] << 8) | px[:, 2]
    uniq, inverse, counts = np.unique(
        codes, return_inverse=True, return_counts=True
    )
    order = np.argsort(-counts, kind="stable")
    rank = np.empty_like(order)
    rank[order] = np.arange(len(order))
    colours = [((int(c) >> 16) & 255, (int(c) >> 8) & 255, int(c) & 255)
               for c in uniq[order]]
    # +1 so that 0 stays free to mean "no override"
    index[mask] = (rank[inverse] + 1).astype(np.uint16)

    base_palette = set(decompose(base).palette)
    return Marking(
        index=index,
        colours=colours,
        counts=[int(counts[i]) for i in order],
        ink=[c for c in colours if c not in base_palette],
        reshade=[c for c in colours if c in base_palette],
    )


def substitute_marking(
    marking: Marking, mapping: dict[RGB, RGB]
) -> list[RGB]:
    """Resolve a marking's override colours against a body mapping.

    The same one-liner as `substitute`, for the same reason and with no second
    code path. An ink colour is by derivation a colour the base coat does not
    have, so it can never be a key of a mapping whose keys are the base coat's
    palette — it passes through untouched. A reshade colour *is* a base-coat
    colour, so it is substituted. That is the whole of how a marking composes
    over an arbitrary coat.
    """
    return [mapping.get(c, c) for c in marking.colours]


# --- correspondence --------------------------------------------------------

@dataclass(frozen=True)
class Correspondence:
    """Where one base colour's pixels land in another sheet.

    `branches` is every colour the target sheet draws at those pixels, ordered
    by descending count, so `branches[0]` is the dominant one. A coat colour
    has one branch and a little noise; a marking has two real branches, which
    is the whole difference between them and is why this is measured per
    colour rather than assumed.
    """

    source: RGB
    total: int                          # pixels opaque in both sheets
    branches: list[tuple[RGB, int]]     # descending by count

    @property
    def dominant(self) -> RGB:
        return self.branches[0][0]

    @property
    def minority(self) -> int:
        return self.total - self.branches[0][1]

    @property
    def minority_fraction(self) -> float:
        return self.minority / self.total if self.total else 0.0


def correspond(
    base: np.ndarray, target: np.ndarray
) -> tuple[list[Correspondence], int]:
    """Pixel-wise colour correspondence from `base` to `target`.

    Returns one `Correspondence` per base palette colour that appears where
    both sheets are visible, in `decompose` order — descending pixel count — so
    the row order of an extracted coat file is fixed by the artwork rather than
    by whoever wrote it down.

    The second return value is the number of base-visible pixels excluded
    because the target is transparent there. It is returned rather than
    swallowed: it is 2 for black and 194 for the socks lead sheet, and a number
    that starts growing means the two sheets are no longer the same geometry.
    """
    if base.shape != target.shape:
        raise ValueError(f"base is {base.shape}, target is {target.shape}")

    indexed = decompose(base)
    both = (base[:, :, 3] > 0) & (target[:, :, 3] > 0)
    excluded = int(((base[:, :, 3] > 0) & ~both).sum())
    if not both.any():
        return [], excluded

    # One uint64 key per pixel: base palette index in the high bits, the
    # target's packed RGB in the low 24. np.unique then does the whole
    # cross-tabulation in one pass — the obvious nested loop is a Python loop
    # over ~800k pixels and was the slowest thing in the extractor.
    tp = target[:, :, :3][both].astype(np.uint64)
    codes = (tp[:, 0] << 16) | (tp[:, 1] << 8) | tp[:, 2]
    keys = (indexed.index[both].astype(np.uint64) << np.uint64(32)) | codes
    uniq, counts = np.unique(keys, return_counts=True)

    grouped: dict[int, list[tuple[RGB, int]]] = {}
    totals: dict[int, int] = {}
    for key, count in zip(uniq.tolist(), counts.tolist()):
        idx = key >> 32
        code = key & 0xFFFFFF
        rgb = ((code >> 16) & 255, (code >> 8) & 255, code & 255)
        grouped.setdefault(idx, []).append((rgb, count))
        totals[idx] = totals.get(idx, 0) + count

    out = []
    for idx, colour in enumerate(indexed.palette):
        if idx not in grouped:
            continue
        branches = sorted(grouped[idx], key=lambda b: -b[1])
        out.append(Correspondence(colour, totals[idx], branches))
    return out, excluded


# --- extraction ------------------------------------------------------------

class AmbiguousCoat(ValueError):
    """A coat whose colours split one-to-many, i.e. not a palette at all."""


# A source colour is ambiguous when it has BOTH a large enough minority share
# and enough pixels in it to be artwork rather than stray. Measured on this
# pack: legitimate coats top out at 1.252% (black and white, 137 px on
# #150a05), and genuine one-to-many bottoms out at 8.60% (socks, #80472c). 5%
# sits inside that gap with room either side.
#
# The pixel floor exists for exactly one colour: bay's #0b0b0b eye pupil is two
# pixels, and one of them differs on socks — 50%, which a fraction-only rule
# would reject, taking bay's own identity extraction down with it. Do not drop
# either condition; each is load-bearing for a different failure.
MAX_MINORITY_FRACTION = 0.05
MIN_MINORITY_PIXELS = 16


def extract_coat(
    base: np.ndarray,
    target: np.ndarray,
    *,
    name: str,
    base_name: str,
    source: dict,
    family: str = "horse",
    max_minority_fraction: float = MAX_MINORITY_FRACTION,
    min_minority_pixels: int = MIN_MINORITY_PIXELS,
) -> dict:
    """A coat palette read off two sheets of the same geometry.

    Mechanical, not a judgement call: for each colour of the base sheet, the
    colour the target sheet draws at most of those pixels. Nothing is inferred
    from hue, luminance or position.

    Raises `AmbiguousCoat` when any source colour splits one-to-many, naming
    every offender. That is the honest failure for paint and socks: a colour
    that becomes white in one place and stays brown in another is a region of
    the artwork, and picking its dominant branch would render a coat that is
    quietly wrong on tens of thousands of pixels.
    """
    rows, excluded = correspond(base, target)
    if not rows:
        raise ValueError(f"{name}: the two sheets share no visible pixels")

    ambiguous = [
        r for r in rows
        if r.minority >= min_minority_pixels
        and r.minority_fraction >= max_minority_fraction
    ]
    if ambiguous:
        detail = ", ".join(
            f"{to_hex(r.source)} {r.minority_fraction * 100:.1f}% "
            f"({r.minority} of {r.total} px, {to_hex(r.dominant)} vs "
            f"{to_hex(r.branches[1][0])})"
            for r in ambiguous
        )
        raise AmbiguousCoat(
            f"{name}: {len(ambiguous)} source colour(s) split one-to-many and "
            f"cannot be a palette substitution: {detail}. A source colour that "
            f"splits is a spatial marking, not a coat colour — it needs a mask "
            f"rather than a dominant branch."
        )

    return {
        "name": name,
        "version": f"{name}_v1",
        "family": family,
        "base": base_name,
        "source": dict(source),
        "map": [
            {"from": to_hex(r.source), "to": to_hex(r.dominant)} for r in rows
        ],
        # Not part of the file format — reported so the caller can write the
        # measured cost into the description instead of guessing at it.
        "_stats": {
            "excluded_pixels": excluded,
            "worst_minority_fraction": max(r.minority_fraction for r in rows),
            "minority_pixels": sum(r.minority for r in rows),
            "distinct_targets": len({r.dominant for r in rows}),
        },
    }


def agreement(coat: dict, pairs: list[tuple[np.ndarray, np.ndarray]]) -> list[str]:
    """Where other sheet families disagree with this coat. Never raises.

    Advisory on purpose. The pack is not self-consistent — its black jump sheet
    draws bay's #000000 as #101010 on 3515 pixels, and its white jump sheet
    disagrees with its own no-equipment sheet on two more colours. So a coat
    that matched every sheet cannot exist, and enforcing agreement would make
    extraction impossible rather than correct. Extraction is pinned to one
    named source sheet and the disagreements are reported here, which is also
    the argument for rendering a coat at all: the rendered one *is* consistent
    across every sheet, and the shipped one is not.
    """
    expected = {from_hex(r["from"]): from_hex(r["to"]) for r in coat["map"]}
    lines = []
    for base, target in pairs:
        try:
            rows, _ = correspond(base, target)
        except ValueError as exc:
            lines.append(f"skipped: {exc}")
            continue
        for r in rows:
            want = expected.get(r.source)
            if want is not None and want != r.dominant:
                lines.append(
                    f"{to_hex(r.source)}: this sheet says {to_hex(r.dominant)} "
                    f"({r.branches[0][1]} px), {coat['name']} says "
                    f"{to_hex(want)}"
                )
    return lines


def verify_source(coat: dict, root) -> None:
    """Check a coat file still describes the sheet it was measured from.

    Deliberately not called from ingest, and the omission is the design. A coat
    map is self-contained — `substitute` never opens the source sheet, and
    ingest applies the map to the ride, lead and jump sheets, none of which
    *is* `source.sheet`. Hashing a 1152x720 PNG the build never reads would
    make `pixelasset.ingest` hard-depend on a gitignored third-party file for
    nothing. This runs in the tests instead, where the guarantee is wanted and
    the file being absent is already a skip.
    """
    import hashlib
    from pathlib import Path

    path = Path(root) / coat["source"]["sheet"]
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    if digest != coat["source"]["sha256"]:
        raise ValueError(
            f"{coat['name']}: {coat['source']['sheet']} hashes to {digest}, "
            f"but the coat was measured against {coat['source']['sha256']}"
        )


# --- mappings --------------------------------------------------------------

def mapping_for(coat: dict) -> dict[RGB, RGB]:
    """A coat's rows as a colour->colour mapping.

    `from` is the key space and must be distinct; a repeat would silently
    collapse the map and lose a step, which is exactly why the file stores rows
    in a list rather than a YAML mapping — `yaml.safe_load` keeps the last of
    duplicate keys without telling anyone. `to` may repeat freely, and does:
    black uses eleven colours where bay uses thirteen.
    """
    seen: dict[str, int] = {}
    for row in coat["map"]:
        seen[row["from"]] = seen.get(row["from"], 0) + 1
    repeats = sorted(c for c, n in seen.items() if n > 1)
    if repeats:
        raise ValueError(
            f"{coat.get('name', '<coat>')}: duplicate `from` colour(s) "
            f"{', '.join(repeats)}. `from` is the key space and must be "
            f"distinct; only `to` may repeat."
        )
    return {from_hex(r["from"]): from_hex(r["to"]) for r in coat["map"]}


def mapping_between(base: dict, target: dict) -> dict[RGB, RGB]:
    """Colour substitution from one coat palette to another.

    Keyed by colour, not by position. The ramps this replaced encoded the
    correspondence in line numbers, so a reorder silently remapped the whole
    coat and equal lengths were required for a positional zip to mean anything
    — which is what stopped a coat from having a different number of colours.
    A row carries its own key, so every coat covers the same key space and the
    only thing that varies is what each colour becomes.
    """
    if target.get("base") != base["name"]:
        raise ValueError(
            f"{target.get('name', '<coat>')} declares base "
            f"{target.get('base')!r}, but was asked to map from "
            f"{base['name']!r}"
        )
    base_map = mapping_for(base)
    target_map = mapping_for(target)
    missing = sorted(to_hex(c) for c in set(base_map) - set(target_map))
    extra = sorted(to_hex(c) for c in set(target_map) - set(base_map))
    if missing or extra:
        raise ValueError(
            f"coat key space mismatch: {target.get('name', '<coat>')} is "
            f"missing {missing or 'nothing'} and adds {extra or 'nothing'} "
            f"relative to {base['name']}"
        )
    return target_map


# --- author-time CLI -------------------------------------------------------

def as_yaml(coat: dict) -> str:
    """A coat dict as the YAML that belongs in config/palettes/.

    Hand-rolled rather than yaml.safe_dump because the checked-in file carries
    comments — which colour is the body, which rows collapse, what the measured
    delta is — and a dumper would erase all of it on every re-extraction.
    """
    src = coat["source"]
    lines = [
        f"name: {coat['name']}",
        f"version: {coat['version']}",
        f"family: {coat['family']}",
        f"base: {coat['base']}",
        "description: >-",
        "  TODO — written by hand. Numbers to quote are in the comments below.",
        "source:",
        f"  sheet: {src['sheet']}",
        f"  sha256: {src['sha256']}",
        "map:",
    ]
    lines += [
        f"  - {{from: '{r['from']}', to: '{r['to']}'}}" for r in coat["map"]
    ]
    st = coat.get("_stats")
    if st:
        lines += [
            "",
            f"# {len(coat['map'])} rows, {st['distinct_targets']} distinct targets"
            f" ({len(coat['map']) - st['distinct_targets']} collapse)",
            f"# worst minority branch {st['worst_minority_fraction'] * 100:.3f}%"
            f", {st['minority_pixels']} px total",
            f"# {st['excluded_pixels']} base-visible px excluded (target"
            " transparent there)",
        ]
    return "\n".join(lines) + "\n"


def _main(argv=None) -> int:
    import argparse
    import hashlib
    import sys
    from pathlib import Path

    ap = argparse.ArgumentParser(
        prog="python -m pixelasset.coat",
        description="Read a coat palette off two sheets of the same geometry.",
    )
    sub = ap.add_subparsers(dest="cmd", required=True)
    ex = sub.add_parser("extract", help="print a coat palette as YAML")
    ex.add_argument("--base", type=Path, required=True)
    ex.add_argument("--target", type=Path, required=True)
    ex.add_argument("--name", required=True)
    ex.add_argument("--base-name", default="coat_bay")
    ex.add_argument("--family", default="horse")
    ex.add_argument(
        "--agree-with", action="append", default=[], metavar="BASE:TARGET",
        help="another sheet pair to cross-check; reported, never enforced",
    )
    ex.add_argument("--root", type=Path, default=Path.cwd())
    args = ap.parse_args(argv)

    base = load_sheet(args.base)
    target = load_sheet(args.target)
    sheet = args.target
    try:
        sheet = args.target.resolve().relative_to(args.root.resolve())
    except ValueError:
        pass
    source = {
        "sheet": str(sheet),
        "sha256": hashlib.sha256(args.target.read_bytes()).hexdigest(),
    }
    try:
        coat = extract_coat(
            base, target, name=args.name, base_name=args.base_name,
            source=source, family=args.family,
        )
    except AmbiguousCoat as exc:
        print(str(exc), file=sys.stderr)
        return 1

    pairs = []
    for spec in args.agree_with:
        b, _, t = spec.partition(":")
        pairs.append((load_sheet(b), load_sheet(t)))
    if pairs:
        report = agreement(coat, pairs)
        print(
            f"# cross-sheet agreement: {len(report)} disagreement(s)",
            file=sys.stderr,
        )
        for line in report:
            print(f"#   {line}", file=sys.stderr)

    print(as_yaml(coat), end="")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(_main())
