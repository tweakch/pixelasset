# Pixelasset

Reproducible pixel-art asset pipeline for **Wunderhof**. Architecture: [`PIPELINE.md`](PIPELINE.md).

Slice 1 delivers one static prop (`hay_bale`) through the 17-stage graph on **construction path C**. Slice 2 adds one character idle (`horse_bay`, bay coat only) with Animation Construction + Frame Consistency **on**. Style Bible is **Hof (Soft Equine Charm)**.

## Success

```bash
pixelasset build hay_bale
pixelasset build horse_bay
```

`hay_bale` produces native PNG (24×16), spritesheet, `metadata.json`, and preview. `horse_bay` produces native 48×32 idle frames (2–4), spritesheet, `metadata.json`, and preview. Artifacts are copied to `assets/production/<id>/` only when gates pass and review is approved.

## Install

See [INSTALL.md](INSTALL.md).

## CLI

```
pixelasset create <id>
pixelasset concept <id>
pixelasset pixelate <id>
pixelasset palette <id>
pixelasset animate <id>
pixelasset validate <id>
pixelasset spritesheet <id>
pixelasset build <id>
pixelasset build --all
pixelasset review <id> [--approve|--reject]
```

`build` runs every stage. Stages that do not apply are marked `NOT_APPLICABLE` (never silently skipped). For Path C `hay_bale` that is: Reference/Concept Generation, Animation Construction, Frame Consistency. For `horse_bay`: Reference/Concept is `NOT_APPLICABLE`; Animation Construction and Frame Consistency **must run**.

## Layout

```
assets/source/<id>/     spec + Path C template
assets/concepts/<id>/
assets/working/<id>/
assets/production/<id>/ # approved only
assets/previews/<id>/
assets/metadata/<id>/
assets/review/<id>/     status.json
config/                 project, style bible, palettes/palette_v0.yaml + palette_v1.yaml
schemas/                JSON Schema for config, asset, metadata, review, stages
```

Art rules live in `config/` and the Hof style bible, not in scripts. Palette roles only (RGB frozen in palette files, never in the asset spec): OUTLINE, SHADOW_DARK, SHADOW, BASE, LIGHT, HIGHLIGHT, ACCENT.

- `palette_v0` — hay_bale (untouched)
- `palette_v1` — equine / horse_bay (bay)

No asset catalog, no Walk, no live SVG as a pixel source.

## Tests

```bash
pytest
```
