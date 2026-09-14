# Pixelasset

Reproducible pixel-art asset pipeline for **Wunderhof**. Architecture: [`PIPELINE.md`](PIPELINE.md).

Slice 1 delivers one static prop (`hay_bale`) through the 17-stage graph on **construction path C** (template / procedural / hand-authored — no AI). Style Bible v0 is **Hof (Soft Equine Charm)**.

## Success

```bash
pixelasset build hay_bale
```

produces native PNG (24×16), spritesheet, `metadata.json`, and preview under `assets/`, palette-compliant and validated. Artifacts are copied to `assets/production/hay_bale/` only when gates pass and review is approved.

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

`build` runs every stage. Stages that do not apply are marked `NOT_APPLICABLE` (never silently skipped). For `hay_bale` Path C that is: Reference/Concept Generation, Animation Construction, Frame Consistency.

## Layout

```
assets/source/<id>/     spec + Path C template
assets/concepts/<id>/
assets/working/<id>/
assets/production/<id>/ # approved only
assets/previews/<id>/
assets/metadata/<id>/
assets/review/<id>/     status.json
config/                 project, style bible, palettes/palette_v0.yaml
schemas/                JSON Schema for config, asset, metadata, review, stages
```

Art rules live in `config/` and the Hof style bible, not in scripts. Palette roles only (RGB frozen in `palette_v0`, never in the asset spec): OUTLINE, SHADOW_DARK, SHADOW, BASE, LIGHT, HIGHLIGHT, ACCENT.

## Tests

```bash
pytest
```
