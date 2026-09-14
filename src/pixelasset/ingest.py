"""Ingest: a third-party sprite pack in, canonical measured assets out.

The interesting work is normalization. The pack stores an asset across sheets
with different cell sizes and different baselines — mounted ground clips are
80x64 on a y=63 baseline, the mounted jump is 80x82 on y=81, and the leading
sheet is 100x96 whose baseline is 78 in three facings and 95 in the fourth,
because facing south the handler walks in front of the horse.

A consumer should never have to know any of that. Ingest re-blits every frame
of an asset onto one cell with one origin, aligning each frame by the anchor it
was measured against. Afterwards a renderer needs a single cell size, a single
origin, and nothing else.

This is a second producer alongside the Path C stage graph, not a replacement.
It exists to exercise the directional-animated metadata contract end to end
while the graph is still single-animation. Once `stage_spritesheet` packs a
grid and `stage_metadata` emits per-facing clips, this collapses into a
construction path and the duplicate output code here goes away.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
from PIL import Image

from . import __version__, detect
from .detect import BBox

AIRBORNE_MIN_GAP = 5


@dataclass
class Frame:
    src: str
    row: int
    col: int
    bbox: BBox
    anchor: tuple[int, int]      # anchor within the SOURCE cell

    @property
    def offsets(self) -> tuple[int, int, int, int]:
        """Content extent relative to the anchor: (left, right, up, down)."""
        ax, ay = self.anchor
        return (ax - self.bbox.x0, self.bbox.x1 - ax,
                ay - self.bbox.y0, self.bbox.y1 - ay)


@dataclass
class Clip:
    facing: str
    action: str
    fps: int
    loop: bool
    frames: list[Frame] = field(default_factory=list)
    declared_frames: int | None = None
    row_index: int = 0
    accept: dict = field(default_factory=dict)
    # The clip that follows when this one ends. Some gestures are two rows that
    # meet end to end rather than one looping row, and nothing in the frames
    # themselves says so.
    next: str | None = None

    @property
    def airborne(self) -> list[int] | None:
        """Clip-relative frame indices where the sprite has left the ground."""
        air = [
            i for i, f in enumerate(self.frames)
            if (f.anchor[1] - f.bbox.y1) >= AIRBORNE_MIN_GAP
        ]
        return [air[0], air[-1]] if air else None


@dataclass
class Asset:
    id: str
    coat: str
    coat_label: str
    mode: str
    mode_label: str
    clips: list[Clip]
    sources: dict[str, Path]
    source_cells: dict[str, tuple[int, int]]
    # (row, column) of every source cell a declared `patch:` corrected. Carried
    # so metadata can say which frames of the output do not match the sheet it
    # names — otherwise the difference reads as the pipeline corrupting pixels.
    source_patches: dict[str, set] = field(default_factory=dict)
    # The pack's subject family. Two producers write into one manifest and the
    # game groups by it, so it comes from the pack rather than being assumed:
    # `Character_base.png` is the same art pack and emphatically not a horse.
    family: str = "horse"
    # What the horse is wearing. A second axis beside `mode`: `ride` and
    # `ride_saddled` are the same configuration of rider and horse, drawn on
    # sheets that differ only in tack, so the id separates them and this says
    # why.
    tack: str | None = None
    cell: tuple[int, int] = (0, 0)
    origin: tuple[int, int] = (0, 0)
    palette: list[tuple[int, int, int]] = field(default_factory=list)
    semi_alpha: tuple[int, list[int]] = (0, [])


def load_source_sheet(path: Path, spec: dict) -> np.ndarray:
    """Load one pack sheet and apply the corrections its source declares.

    A `patch:` entry restores a frame the artwork draws incompletely, by
    copying a rectangle from another frame of the same sheet. It is not a
    repaint and it invents nothing: every pixel it writes was already in the
    file, and the entry has to say which frame it took them from, so a reviewer
    can check the claim by looking at two cells.

    It exists because the alternative was worse. The halter leading sheets draw
    the west idle without its lead rope on one frame of eleven, and the rope,
    the halter and the hand that grips it are byte-identical in the other ten —
    so the missing layer is a copy away, and leaving the frame out instead
    would have thrown away a pose to fix a colour.

    Every reader of a pack sheet goes through here. `_marking_for` derives a
    mask by differencing two sheets of the same source, so a sheet patched on
    one path and raw on the other would put the correction into the marking.
    """
    sheet = detect.load_rgba(path)
    entries = spec.get("patch") or []
    if not entries:
        return sheet
    declared = spec.get("cell", "auto")
    if declared == "auto":
        raise ValueError(
            f"{path.name}: a source that declares `patch:` must pin `cell:`. "
            f"The patch is addressed in cells, and detecting the grid from a "
            f"sheet this is about to change means the coordinates could mean "
            f"one thing before the edit and another after."
        )
    cw, ch = detect.confirm_cell(detect.alpha(sheet), tuple(declared),
                                 source=path.name)
    rows, cols = sheet.shape[0] // ch, sheet.shape[1] // cw
    for entry in entries:
        (dr, dc), (sr, sc) = entry["to"], entry["from"]
        x, y, bw, bh = entry["box"]
        for r, c, what in ((dr, dc, "to"), (sr, sc, "from")):
            if not (0 <= r < rows and 0 <= c < cols):
                raise ValueError(
                    f"{path.name}: patch `{what}` [{r}, {c}] is outside a "
                    f"{cols}x{rows} grid of {cw}x{ch} cells"
                )
            cell = detect.alpha(sheet)[r * ch:(r + 1) * ch, c * cw:(c + 1) * cw]
            if detect.cell_bbox(cell) is None:
                raise ValueError(
                    f"{path.name}: patch `{what}` [{r}, {c}] names an empty "
                    f"cell — the row it meant has moved"
                )
        if not (0 <= x and 0 <= y and bw > 0 and bh > 0
                and x + bw <= cw and y + bh <= ch):
            raise ValueError(
                f"{path.name}: patch box {entry['box']} does not fit a "
                f"{cw}x{ch} cell"
            )
        dst = (slice(dr * ch + y, dr * ch + y + bh),
               slice(dc * cw + x, dc * cw + x + bw))
        srcbox = sheet[sr * ch + y:sr * ch + y + bh,
                       sc * cw + x:sc * cw + x + bw]
        if np.array_equal(sheet[dst], srcbox):
            raise ValueError(
                f"{path.name}: patch [{sr}, {sc}] -> [{dr}, {dc}] box "
                f"{entry['box']} changes nothing. Either the sheet was fixed "
                f"upstream and the entry should go, or it is aimed at the "
                f"wrong cell."
            )
        sheet[dst] = srcbox
    return sheet


def load_pack(path: Path) -> dict:
    """Read a pack description and resolve mode inheritance.

    Every entry point that builds from a pack goes through here, so `like` is
    never a thing only `ingest_pack` understands. A caller handed the raw
    `yaml.safe_load` would see a mode with no `clips` at all.
    """
    import yaml

    return resolve_modes(yaml.safe_load(path.read_text(encoding="utf-8")))


def resolve_modes(pack: dict) -> dict:
    """Expand `like` / `sheets` into ordinary modes, in place.

    A tack variant is the same animations drawn on different sheets: same rows,
    same frame counts, same jump windows. Copying the clip block to say that
    would be thirty-four lines whose only possible future is drift, so a mode
    declares `like:` and a remap of the source keys its inherited clips use.

    Two fields come out of it that the raw file does not carry: `mode`, the
    configuration this is a variant of — `ride_saddled` is still riding, and
    that is what metadata reports — and `clips`, resolved against `sheets`.
    """
    # `subjects:` is the general spelling and `coats:` the horse pack's. They
    # are the same list — the thing a mode is built for — and a pack that says
    # `coats` is describing subjects that happen to be distinguished by coat.
    if "subjects" in pack:
        if "coats" in pack:
            raise ValueError(
                "a pack declares either `subjects:` or `coats:`, not both — "
                "they are the same list under two names"
            )
        pack["coats"] = pack.pop("subjects")
    modes = pack.get("modes") or {}
    for name, mode in modes.items():
        parent_name = mode.get("like")
        if parent_name is None:
            mode.setdefault("mode", name)
            continue
        if parent_name not in modes:
            raise ValueError(
                f"mode {name!r} inherits from {parent_name!r}, which is not a "
                f"declared mode (have: {', '.join(sorted(modes))})"
            )
        parent = modes[parent_name]
        if parent.get("like"):
            raise ValueError(
                f"mode {name!r} inherits from {parent_name!r}, which itself "
                f"inherits from {parent['like']!r}. One level only — a chain "
                f"makes the sheet a clip actually reads impossible to see."
            )
        sheets = mode.get("sheets") or {}
        used = {s["src"] for block in parent["clips"].values()
                for s in block.values()}
        unknown = sorted(set(sheets) - used)
        if unknown:
            raise ValueError(
                f"mode {name!r} remaps source(s) {', '.join(unknown)}, which "
                f"{parent_name!r} does not draw from (it uses: "
                f"{', '.join(sorted(used))}). A remap that matches nothing is a "
                f"typo that would silently build a duplicate of {parent_name!r}."
            )
        if not sheets:
            raise ValueError(
                f"mode {name!r} inherits {parent_name!r} but remaps no sheets, "
                f"so it would be a byte-identical copy under a second asset id."
            )
        # `like` is about clips, not identity. A tack variant is the same
        # configuration on different sheets and inherits `mode`; the riderless
        # horse borrows the same eighteen rows but is emphatically not riding,
        # so it declares its own and this only fills in the default.
        mode.setdefault("mode", parent.get("mode", parent_name))
        mode.setdefault("label", parent["label"])
        mode["clips"] = {
            facing: {
                action: {**spec, "src": sheets.get(spec["src"], spec["src"])}
                for action, spec in block.items()
            }
            for facing, block in parent["clips"].items()
        }
    return pack


def _anchor_for(sheets, cells, src, facing, clips_spec) -> tuple[int, int]:
    """Bottom-centre anchor for one (source sheet, facing).

    The baseline is the lowest occupied row across every clip that this facing
    draws from this sheet — the ground the sprite stands on. It is measured per
    (source, facing) rather than globally because the pack genuinely varies:
    assuming one number silently floats the leading sprite 17px off the ground
    in three of its four facings.

    A clip may opt out with `defines_baseline: false`, and exactly one kind of
    clip should: a transition that is on its way to another facing. The leading
    sheet's turn rows draw the handler already stepped toward the camera, 5px
    lower than the walking rows, because the front-facing stance they lead into
    stands lower still (south's ground is 95 against east's 78). Letting those
    rows set the ground moves east and west from 78 to 83 and floats all six
    other clips. Opting out is not the same as ignoring them — they still get
    measured against the ground everything else stands on, and the cell grows
    to fit them.
    """
    cw, ch = cells[src]
    a = detect.alpha(sheets[src])
    lowest = 0
    for spec in clips_spec.values():
        if spec["src"] != src or not spec.get("defines_baseline", True):
            continue
        boxes = detect.row_frames(a, cw, ch, spec["row"])
        if boxes:
            lowest = max(lowest, detect.baseline(boxes))
    return cw // 2, lowest


def build_asset(
    pack: dict, root: Path, coat_id: str, mode_id: str, *, recolour=None,
    marking=None,
) -> Asset:
    """Build one (coat, mode) asset.

    `recolour` is a colour->colour mapping applied to every source sheet before
    anything is measured. A rendered coat therefore goes through exactly the
    same detection, normalization and validation as a shipped one — it is the
    same geometry seen through another palette, not a second code path.

    `marking` is an optional {source: Marking} of per-pixel overrides applied in
    the same pass, for the part of a coat a palette cannot express. Both are
    applied before measurement for the same reason, and neither touches alpha,
    so a marked rendered coat is still the same geometry.
    """
    coat = pack["coats"][coat_id]
    mode = pack["modes"][mode_id]
    family = pack["pack"].get("family", "horse")

    used = {s["src"] for facing in mode["clips"].values() for s in facing.values()}
    sources, sheets, cells, patched = {}, {}, {}, {}
    for src in sorted(used):
        spec = pack["sources"][src]
        path = root / spec["dir"] / coat["files"][src]
        if not path.exists():
            raise FileNotFoundError(f"{coat_id}/{src}: {path}")
        sources[src] = path
        sheet = load_source_sheet(path, spec)
        patched[src] = {(e["to"][0], e["to"][1]) for e in spec.get("patch") or []}
        overlay = None
        if marking and src in marking:
            from .coat import substitute_marking
            overlay = (
                marking[src].index,
                substitute_marking(marking[src], recolour or {}),
            )
        if recolour or overlay:
            from .coat import decompose, render, substitute
            indexed = decompose(sheet)
            palette = substitute(indexed, recolour) if recolour else None
            sheet = render(indexed, palette, overlay=overlay)
        sheets[src] = sheet
        a = detect.alpha(sheets[src])
        declared = spec.get("cell", "auto")
        cells[src] = (detect.detect_cell(a) if declared == "auto"
                      else detect.confirm_cell(a, tuple(declared),
                                               source=path.name))

    clips: list[Clip] = []
    for facing in ("east", "west", "south", "north"):
        spec_block = mode["clips"].get(facing, {})
        anchors = {
            src: _anchor_for(sheets, cells, src, facing, spec_block)
            for src in {s["src"] for s in spec_block.values()}
        }
        for action, spec in spec_block.items():
            src = spec["src"]
            cw, ch = cells[src]
            boxes = detect.row_frames(detect.alpha(sheets[src]), cw, ch, spec["row"])
            first = spec.get("first", 0)
            declared = spec.get("frames")
            window = boxes[first:first + declared] if declared else boxes[first:]
            clip = Clip(
                facing=facing, action=action,
                fps=int(spec.get("fps", 8)), loop=bool(spec.get("loop", True)),
                declared_frames=declared, next=spec.get("next"),
                accept=dict(spec.get("accept", {})),
                frames=[Frame(src, spec["row"], first + i, b, anchors[src])
                        for i, b in enumerate(window)],
            )
            clips.append(clip)

    asset = Asset(
        id=f"{family}_{coat_id}_{mode_id}", coat=coat_id,
        coat_label=coat["label"],
        mode=mode.get("mode", mode_id), mode_label=mode["label"], clips=clips,
        sources=sources, source_cells=cells, family=family,
        source_patches=patched, tack=mode.get("tack"),
    )

    # One cell and one origin for the whole asset: the tightest box that fits
    # every frame once they are all aligned on their anchors.
    offsets = [f.offsets for c in asset.clips for f in c.frames]
    if not offsets:
        raise ValueError(f"{asset.id}: no frames")
    ox = max(o[0] for o in offsets)
    oy = max(o[2] for o in offsets)
    asset.origin = (ox, oy)
    asset.cell = (ox + max(o[1] for o in offsets) + 1,
                  oy + max(o[3] for o in offsets) + 1)

    palette, semi_n, semi_vals = set(), 0, set()
    for src in sources:
        palette |= set(detect.opaque_palette(sheets[src]))
        n, vals = detect.semi_transparent(sheets[src])
        semi_n += n
        semi_vals |= set(vals)
    asset.palette = sorted(palette)
    asset.semi_alpha = (semi_n, sorted(semi_vals))

    asset._sheets = sheets   # noqa: SLF001 - handed straight to render_sheet
    return asset


def render_sheet(asset: Asset) -> Image.Image:
    """Repack into a canonical spritesheet: one clip per row, frame 0 at left.

    §14: spritesheets are always generated, never hand-packed.
    """
    cw, ch = asset.cell
    ox, oy = asset.origin
    ordered = sorted(asset.clips, key=lambda c: (c.facing, c.action))
    for i, clip in enumerate(ordered):
        clip.row_index = i
    widest = max(len(c.frames) for c in ordered)

    out = Image.new("RGBA", (widest * cw, len(ordered) * ch), (0, 0, 0, 0))
    for clip in ordered:
        scw, sch = asset.source_cells[clip.frames[0].src] if clip.frames else (0, 0)
        for col, f in enumerate(clip.frames):
            scw, sch = asset.source_cells[f.src]
            cell = Image.fromarray(
                asset._sheets[f.src][           # noqa: SLF001
                    f.row * sch:(f.row + 1) * sch,
                    f.col * scw:(f.col + 1) * scw,
                ]
            )
            patch = cell.crop((f.bbox.x0, f.bbox.y0, f.bbox.x1 + 1, f.bbox.y1 + 1))
            left, _, up, _ = f.offsets
            out.alpha_composite(
                patch, (col * cw + ox - left, clip.row_index * ch + oy - up)
            )
    return out



def _repo_relative(path: Path) -> str:
    """A source path a browser can fetch from a server rooted at the repo.

    The viewer loads the original sheet beside the built one so a wrong frame
    can be compared against the artwork. Absolute paths would leak this
    machine into generated metadata and be unfetchable anyway; a path that
    cannot be made relative is emitted as-is rather than dropped, because
    knowing where a frame came from is still worth more than a tidy field.
    """
    try:
        return path.resolve().relative_to(Path.cwd().resolve()).as_posix()
    except ValueError:
        return path.as_posix()


def metadata(asset: Asset, style_version: str = "0.1.0",
             palette_version: str = "ingested") -> dict:
    """Engine-agnostic metadata in the project schema (§14).

    Three fields here are the contract extension for directional animated
    assets, and each exists because a consumer cannot work without it:

    - `facing` — `animations` is otherwise a flat list with nowhere to say that
      `walk` east and `walk` north are different clips of the same asset.
    - `spritesheet` + per-animation `row` — `order` names the loose frame files,
      which tells a consumer loading the packed sheet nothing about where those
      frames actually are in it.
    - `off_ground` — measured from each frame's alpha bbox against the clip
      baseline, so collision tracks the artwork rather than a hand-tuned timer.

    All three are optional in the schema, so a static prop stays valid without
    them.
    """
    ordered = sorted(asset.clips, key=lambda c: (c.facing, c.action))
    animations = []
    for clip in ordered:
        entry = {
            "name": clip.action,
            "facing": clip.facing,
            "row": clip.row_index,
            "frames": len(clip.frames),
            "fps": float(clip.fps),
            "loop": clip.loop,
            "order": [f"{clip.action}_{clip.facing}/{i:02d}.png"
                      for i in range(len(clip.frames))],
        }
        if clip.next:
            entry["next"] = clip.next
        air = clip.airborne
        if air:
            entry["off_ground"] = air
        src = clip.frames[0].src if clip.frames else None
        if src in asset.sources:
            # Provenance, not geometry. Every frame of a clip comes from one
            # row of one sheet, so a sheet, a row and a column per frame locate
            # any frame of the packed output back in the artwork it was cut
            # from. That is what makes "frame 3 of walk west is wrong" a report
            # a producer can act on rather than a description of a picture.
            #
            # A list of columns rather than a first-and-count, because `skip`
            # means they are not always consecutive — and a clip with a gap is
            # exactly the case where somebody is looking at this field.
            entry["source"] = {
                "sheet": _repo_relative(asset.sources[src]),
                "row": clip.frames[0].row,
                "columns": [f.col for f in clip.frames],
                "cell": list(asset.source_cells[src]),
            }
            corrected = [i for i, f in enumerate(clip.frames)
                         if (f.row, f.col) in asset.source_patches.get(src, ())]
            if corrected:
                entry["source"]["patched"] = corrected
        animations.append(entry)

    return {
        "id": asset.id,
        "name": f"{asset.coat_label} ({asset.mode_label})",
        "family": asset.family,
        "variant": asset.coat,
        "category": "character",
        "mode": asset.mode,
        # Two axes, two fields. `mode` is the configuration — riding or on foot
        # — and `tack` is what the horse wears in it, so a consumer can offer a
        # saddle toggle without parsing asset ids for a suffix.
        **({"tack": asset.tack} if asset.tack else {}),
        "frame_size": [asset.cell[0], asset.cell[1]],
        "frame_count": sum(len(c.frames) for c in asset.clips),
        "spritesheet": {
            "file": "spritesheet.png",
            "columns": max(len(c.frames) for c in ordered),
            "rows": len(ordered),
            "layout": "row_per_animation",
        },
        "animations": animations,
        "anchors": {
            "origin": "bottom_center",
            "points": {"feet": [asset.origin[0], asset.origin[1]]},
        },
        "palette_name": palette_version,
        "palette_version": palette_version,
        "style_version": style_version,
        "pipeline_version": __version__,
        "asset_version": __version__,
        "construction_path": "C",
        "view": "side_and_top_4dir",
    }


def _recolour_for(paths, spec: dict) -> dict:
    """Colour substitution for a rendered coat, from its two palette files.

    Loaded through `load_coat_palette` rather than bare yaml so a rendered coat
    is schema-validated on the one path that actually builds it — including the
    key-distinctness check, which is what stops a duplicated `from` colour from
    silently collapsing the map and dropping a step.
    """
    from .coat import mapping_between
    from .config import load_coat_palette

    return mapping_between(
        load_coat_palette(paths, spec["base_palette"]),
        load_coat_palette(paths, spec["palette"]),
    )


_MARKINGS: dict[tuple[str, str], dict] = {}


def _marking_for(pack: dict, root: Path, paths, spec: dict) -> dict | None:
    """Per-source overrides for a marked rendered coat, derived from the pack.

    Memoised on (marking, source coat) because the derivation reads two full
    sheets per family and every mode of every marked coat wants the same ones.
    Cheap next to grid detection, which dominates and is already memoised on
    alpha — and a marked coat never changes alpha, so those hits survive too.
    """
    name = spec.get("marking")
    if not name:
        return None

    from .coat import derive_marking
    from .config import load_marking

    declared = (pack.get("markings") or {})
    if name not in declared:
        available = ", ".join(sorted(declared)) or "none"
        raise ValueError(
            f"rendered coat asks for marking {name!r}, which the pack does not "
            f"declare (available: {available})"
        )

    key = (name, spec["from"])
    if key in _MARKINGS:
        return _MARKINGS[key]

    descriptor = load_marking(paths, declared[name].get("descriptor", name))
    if descriptor["against"] != spec["from"]:
        raise ValueError(
            f"marking {name!r} is derived against {descriptor['against']!r} but "
            f"would be applied to {spec['from']!r}. The classification relies "
            f"on the base coat not already containing the marking's colours, "
            f"and it collapses against a coat that does."
        )
    marked = pack["coats"][descriptor["derive_from"]]
    base = pack["coats"][descriptor["against"]]

    out = {}
    for src, src_spec in pack["sources"].items():
        folder = root / src_spec["dir"]
        derived = derive_marking(
            load_source_sheet(folder / base["files"][src], src_spec),
            load_source_sheet(folder / marked["files"][src], src_spec),
        )
        if not derived.pixels:
            raise ValueError(
                f"marking {name!r}/{src}: the two sheets are identical, so the "
                f"marking would be a silent no-op"
            )
        share = derived.reshade_pixels / derived.pixels
        if share >= 0.05:
            raise ValueError(
                f"marking {name!r}/{src}: {share * 100:.1f}% of the override is "
                f"re-shading rather than ink. Above a few percent that means "
                f"`against` names the wrong coat — the base coat already has "
                f"the marking's colours and the classification has collapsed."
            )
        out[src] = derived

    _MARKINGS[key] = out
    return out


def ingest_pack(pack_path: Path, paths, *, only: list[str] | None = None) -> list[dict]:
    """Build every (coat, mode) asset described by a pack file.

    Writes into the §16 layout the stage graph already uses, and promotes to
    production/ only once metadata validates — the same gate as §20, applied to
    the one check this producer can make on its own.
    """
    import json
    import shutil

    from .config import load_schema, validate_schema
    from .manifest import register

    pack = load_pack(pack_path)
    root = paths.root / pack["pack"]["root"]
    schema = load_schema(paths, "metadata")

    # (coat_id, label, source_coat, recolour-or-None, marking-or-None)
    jobs: list[tuple[str, str, str, dict | None, dict | None]] = [
        (c, pack["coats"][c]["label"], c, None, None) for c in pack["coats"]
    ]
    for name, spec in (pack.get("rendered_coats") or {}).items():
        jobs.append((name, spec.get("label", name.title()), spec["from"],
                     _recolour_for(paths, spec),
                     _marking_for(pack, root, paths, spec)))

    # Asset ids, manifest entries and the game's coat cycle are all keyed on the
    # coat id (game/sprites.js groups the production index by `variant`), so a
    # rendered coat sharing a shipped coat's name would not conflict — it would
    # quietly replace it and drop one coat from the cycle. Rendered black and
    # white are named apart for exactly this reason; fail loudly if that slips.
    seen: dict[str, str] = {}
    for coat_id, _label, source_coat, _recolour, _marking in jobs:
        if coat_id in seen:
            raise ValueError(
                f"{pack_path.name}: coat id {coat_id!r} is declared twice "
                f"(from {seen[coat_id]!r} and {source_coat!r}). Asset ids and "
                f"manifest entries are keyed on it, so one would silently "
                f"replace the other."
            )
        seen[coat_id] = source_coat

    results = []
    for coat, label, source_coat, recolour, marking in jobs:
        for mode in pack["modes"]:
            asset_id = f"{pack['pack'].get('family', 'horse')}_{coat}_{mode}"
            if only and asset_id not in only and coat not in only and mode not in only:
                continue
            asset = build_asset(pack, root, source_coat, mode,
                                recolour=recolour, marking=marking)
            asset.id = asset_id
            asset.coat = coat
            asset.coat_label = label
            sheet = render_sheet(asset)
            meta = metadata(asset)
            validate_schema(meta, schema, source=f"{asset_id} metadata")

            # Deliberately not ensure_asset_dirs(): an ingested asset has no
            # spec, so creating assets/source/<id>/ for it litters the one
            # directory that holds hand-authored, version-controlled input.
            work = paths.working_dir(asset_id)
            work.mkdir(parents=True, exist_ok=True)
            sheet.save(work / "spritesheet.png")
            blob = json.dumps(meta, indent=2) + "\n"
            (work / "metadata.json").write_text(blob, encoding="utf-8")
            meta_dir = paths.metadata_dir(asset_id)
            meta_dir.mkdir(parents=True, exist_ok=True)
            (meta_dir / "metadata.json").write_text(blob, encoding="utf-8")

            prod = paths.production_dir(asset_id)
            prod.mkdir(parents=True, exist_ok=True)
            shutil.copy2(work / "spritesheet.png", prod / "spritesheet.png")
            (prod / "metadata.json").write_text(blob, encoding="utf-8")
            register(paths, meta)
            results.append(meta)
    return results


def main(argv: list[str] | None = None) -> int:
    import argparse
    import json

    from .paths import ProjectPaths

    ap = argparse.ArgumentParser(
        prog="python -m pixelasset.ingest",
        description="Normalize a third-party sprite pack into project assets.")
    # `pack` is a flag, not a positional: with both positional, `ingest bay`
    # bound "bay" to the pack path and died looking for a file called bay.
    ap.add_argument("assets", nargs="*", help="asset id, subject, or mode")
    # Repeatable rather than a glob over packs/: the tests write throwaway pack
    # files into that directory, and a crashed test should not leave something
    # the next build silently ingests.
    ap.add_argument("--pack", type=Path, action="append", dest="packs",
                    help="pack description; repeat for more than one")
    ap.add_argument("--root", type=Path, default=Path.cwd())
    args = ap.parse_args(argv)

    paths = ProjectPaths(args.root)
    metas = []
    for pack_path in (args.packs or [Path("packs/full_pack.yaml")]):
        metas.extend(ingest_pack(pack_path, paths, only=args.assets or None))
    for meta in metas:
        print(f"ok   {meta['id']:26s} {meta['frame_size'][0]}x{meta['frame_size'][1]}"
              f"  {len(meta['animations']):2d} clips  {meta['frame_count']:3d} frames"
              f"  sheet {meta['spritesheet']['columns']}x{meta['spritesheet']['rows']}")

    print(f"\n{len(metas)} assets ingested")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
