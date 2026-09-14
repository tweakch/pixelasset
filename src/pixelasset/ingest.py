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
    cell: tuple[int, int] = (0, 0)
    origin: tuple[int, int] = (0, 0)
    palette: list[tuple[int, int, int]] = field(default_factory=list)
    semi_alpha: tuple[int, list[int]] = (0, [])


def _anchor_for(sheets, cells, src, facing, clips_spec) -> tuple[int, int]:
    """Bottom-centre anchor for one (source sheet, facing).

    The baseline is the lowest occupied row across every clip that this facing
    draws from this sheet — the ground the sprite stands on. It is measured per
    (source, facing) rather than globally because the pack genuinely varies:
    assuming one number silently floats the leading sprite 17px off the ground
    in three of its four facings.
    """
    cw, ch = cells[src]
    a = detect.alpha(sheets[src])
    lowest = 0
    for spec in clips_spec.values():
        if spec["src"] != src:
            continue
        boxes = detect.row_frames(a, cw, ch, spec["row"])
        if boxes:
            lowest = max(lowest, detect.baseline(boxes))
    return cw // 2, lowest


def build_asset(
    pack: dict, root: Path, coat_id: str, mode_id: str, *, recolour=None
) -> Asset:
    """Build one (coat, mode) asset.

    `recolour` is a colour->colour mapping applied to every source sheet before
    anything is measured. A rendered coat therefore goes through exactly the
    same detection, normalization and validation as a shipped one — it is the
    same geometry seen through another palette, not a second code path.
    """
    coat = pack["coats"][coat_id]
    mode = pack["modes"][mode_id]

    used = {s["src"] for facing in mode["clips"].values() for s in facing.values()}
    sources, sheets, cells = {}, {}, {}
    for src in sorted(used):
        spec = pack["sources"][src]
        path = root / spec["dir"] / coat["files"][src]
        if not path.exists():
            raise FileNotFoundError(f"{coat_id}/{src}: {path}")
        sources[src] = path
        sheet = detect.load_rgba(path)
        if recolour:
            from .coat import decompose, render, substitute
            indexed = decompose(sheet)
            sheet = render(indexed, substitute(indexed, recolour))
        sheets[src] = sheet
        a = detect.alpha(sheets[src])
        declared = spec.get("cell", "auto")
        found = detect.detect_cell(a)
        if declared != "auto" and tuple(declared) != found:
            raise ValueError(
                f"{path.name}: config pins cell {tuple(declared)} but the artwork "
                f"measures {found}"
            )
        cells[src] = found

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
                declared_frames=declared,
                accept=dict(spec.get("accept", {})),
                frames=[
                    Frame(src, spec["row"], first + i, b, anchors[src])
                    for i, b in enumerate(window)
                ],
            )
            clips.append(clip)

    asset = Asset(
        id=f"horse_{coat_id}_{mode_id}", coat=coat_id, coat_label=coat["label"],
        mode=mode_id, mode_label=mode["label"], clips=clips,
        sources=sources, source_cells=cells,
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
        air = clip.airborne
        if air:
            entry["off_ground"] = air
        animations.append(entry)

    return {
        "id": asset.id,
        "name": f"{asset.coat_label} ({asset.mode_label})",
        "family": "horse",
        "variant": asset.coat,
        "category": "character",
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
    """Colour substitution for a rendered coat, from its two palette files."""
    import yaml

    from .coat import mapping_between

    def load(name):
        return yaml.safe_load(
            (paths.palettes_dir / f"{name}.yaml").read_text(encoding="utf-8")
        )

    return mapping_between(load(spec["base_palette"]), load(spec["palette"]))


def ingest_pack(pack_path: Path, paths, *, only: list[str] | None = None) -> list[dict]:
    """Build every (coat, mode) asset described by a pack file.

    Writes into the §16 layout the stage graph already uses, and promotes to
    production/ only once metadata validates — the same gate as §20, applied to
    the one check this producer can make on its own.
    """
    import json
    import shutil

    import yaml

    from .config import load_schema, validate_schema

    pack = yaml.safe_load(pack_path.read_text(encoding="utf-8"))
    root = paths.root / pack["pack"]["root"]
    schema = load_schema(paths, "metadata")

    # (coat_id, label, source_coat, recolour-or-None)
    jobs: list[tuple[str, str, str, dict | None]] = [
        (c, pack["coats"][c]["label"], c, None) for c in pack["coats"]
    ]
    for name, spec in (pack.get("rendered_coats") or {}).items():
        jobs.append((name, spec.get("label", name.title()), spec["from"],
                     _recolour_for(paths, spec)))

    results = []
    for coat, label, source_coat, recolour in jobs:
        for mode in pack["modes"]:
            asset_id = f"horse_{coat}_{mode}"
            if only and asset_id not in only and coat not in only and mode not in only:
                continue
            asset = build_asset(pack, root, source_coat, mode, recolour=recolour)
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
    ap.add_argument("assets", nargs="*", help="asset id, coat, or mode")
    ap.add_argument("--pack", type=Path, default=Path("packs/full_pack.yaml"))
    ap.add_argument("--root", type=Path, default=Path.cwd())
    args = ap.parse_args(argv)

    paths = ProjectPaths(args.root)
    metas = ingest_pack(args.pack, paths, only=args.assets or None)
    for meta in metas:
        print(f"ok   {meta['id']:26s} {meta['frame_size'][0]}x{meta['frame_size'][1]}"
              f"  {len(meta['animations']):2d} clips  {meta['frame_count']:3d} frames"
              f"  sheet {meta['spritesheet']['columns']}x{meta['spritesheet']['rows']}")

    index = {
        "pipeline_version": __version__,
        "assets": [{"id": m["id"], "variant": m["variant"],
                    "label": m["name"], "mode": m["id"].rsplit("_", 1)[-1]}
                   for m in metas],
    }
    if not args.assets:
        out = paths.assets_dir / "production" / "index.json"
        out.write_text(json.dumps(index, indent=2) + "\n", encoding="utf-8")
    print(f"\n{len(metas)} assets ingested")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
