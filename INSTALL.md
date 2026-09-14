# Linux install notes

Requires Python 3.10+ and Pillow. Tested against CPython 3.12 on Linux.

## Virtualenv

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

This installs the `pixelasset` console script.

If `python3 -m venv` fails on Debian/Ubuntu (`ensurepip is not available`), install `python3-venv` or use `pip install --user -e ".[dev]"` and put `~/.local/bin` on `PATH`.

## Verify

```bash
pixelasset build hay_bale
pixelasset build horse_bay
pytest
```

Expected:

- `assets/production/hay_bale/` contains `hay_bale.png` (24×16), `spritesheet.png`, `metadata.json`, and `preview_4x.png`.
- `assets/production/horse_bay/` contains `horse_bay.png` (48×32), `idle/00.png`–`idle/02.png`, `spritesheet.png`, `metadata.json`, and `preview_4x.png`.
- Working files land in `assets/working/<id>/` including `stage_graph.json`.
- Review state is `assets/review/<id>/status.json`.

`PIXELASSET_ROOT` may be set if you invoke the CLI outside the repo:

```bash
export PIXELASSET_ROOT=/path/to/pixelasset
pixelasset build hay_bale
pixelasset build horse_bay
```

## Notes

- No extra system packages beyond Python, pip, and a venv.
- ImageMagick and Aseprite are optional and are not required.
- There is no live AI model; Path C reads `assets/source/<id>/template.txt` and maps tokens through the Hof style bible onto a frozen palette.
- `hay_bale` uses frozen `palette_v0`. `horse_bay` uses frozen `palette_v1` (equine / bay). Do not mix.
- Wunderhof SVG (`horse.js`) is proportion/feel reference only — never a pixel source.
- First game is Wunderhof. No catalog, Walk, or SVG swap in this slice.
