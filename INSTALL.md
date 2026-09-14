# Linux install notes

Slice 1 requires Python 3.10+ and Pillow. Tested against CPython 3.12 on Linux.

## Virtualenv

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

This installs the `pixelasset` console script.

## Verify

```bash
pixelasset build hay_bale
pytest
```

Expected: `assets/production/hay_bale/` contains `hay_bale.png`, `spritesheet.png`, `metadata.json`, and `preview_4x.png`. Working files land in `assets/working/hay_bale/` including `stage_graph.json`. Review state is `assets/review/hay_bale/status.json`.

`PIXELASSET_ROOT` may be set if you invoke the CLI outside the repo:

```bash
export PIXELASSET_ROOT=/path/to/pixelasset
pixelasset build hay_bale
```

## Notes

- No extra system packages beyond Python, pip, and a venv.
- ImageMagick and Aseprite are optional and are not required to build Slice 1.
- There is no live AI model; Path C reads `assets/source/<id>/template.txt` and maps tokens through the Hof style bible onto frozen `palette_v0`.
- First game is Wunderhof. No Idle, catalog, or SVG swap in this slice.
