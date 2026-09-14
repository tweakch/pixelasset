# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this repo is

A reproducible pixel-art asset pipeline for **Wunderhof**, plus a browser game that
consumes its output. [`PIPELINE.md`](PIPELINE.md) is the specification and the code
refers to it by section number (`§14`, `§20`). When you change behaviour that a `§`
comment describes, re-read that section — the numbers are load-bearing, not decoration.

## Where we are right now

We are **extracting colour palettes from the sample pack and regenerating its assets
through the pipeline.** The goal is that a coat is a *palette*: define one, run the
pipeline, and every sheet of a family comes out in that coat. The correctness bar is
byte-identity — extracting a coat's own palette and rendering through it must
reproduce the source exactly, or a substituted palette cannot be trusted either.
`tests/test_coat_render.py` pins that.

**The workflow itself is under active development.** If you notice something that
would make the pipeline better, faster or more versatile, say so — surface the idea
in your response even when it is outside what you were asked to do. Concrete open
items are listed at the bottom; add to them.

## Commands

```bash
./build.sh                  # venv, install, ingest, build every asset, test, summarise
./build.sh serve            # game at /game/, asset viewer at /game/viewer.html
./build.sh clean            # generated output only
./build.sh smoke            # headless browser test (needs node + playwright-core)
```

`./build.sh` is the entry point and exits non-zero on failure. Everything runs through
`.venv` — Ubuntu ships no `python` alias, and system `python3` fails twice over: the
package is only installed in the venv, and 22.04 carries Pillow 9.0.1 where the
pipeline needs 10+. To run tools directly, `source .venv/bin/activate` first.

```bash
.venv/bin/python -m pytest -q                                    # all tests
.venv/bin/python -m pytest tests/test_coats.py::test_fox_has_no_black_points
.venv/bin/pixelasset build horse_fox                             # one Path C asset
.venv/bin/python -m pixelasset.ingest bay                        # one coat from the pack
```

## Architecture

### Two producers, one contract

Both write `assets/production/<id>/` and upsert `assets/production/index.json` through
`manifest.py` (one entry at a time — an earlier wholesale rewrite made the other
producer's output silently vanish):

| producer | entry point | what it makes |
|---|---|---|
| Path C stage graph | `pixelasset build <id>` | hand-authored ASCII templates → small assets |
| pack ingest | `python -m pixelasset.ingest` | third-party sheets → normalized multi-facing assets |

`schemas/metadata.schema.json` is the interface between producers and consumers.
Extending *it* is how you add capability; neither the game nor a producer should grow
a private format. Two spritesheet layouts are real output and both must keep working:
`row_per_animation` (ingest) and `single_strip` (stage graph).

### The stage graph

`stages.py` implements all 17 `PIPELINE.md §2` stages in order, driven by
`STAGE_DEFINITIONS` / `HANDLERS`. A stage that does not apply is marked
`NOT_APPLICABLE` with a reason — never silently skipped. A failed gate must not
promote into `production/` (`§20`), and review state is a file, not a decision in
conversation (`§22`).

### Ingest and normalization

`detect.py` measures (grid, frame counts, anchors, airborne windows); `ingest.py`
re-blits every frame of an asset onto **one cell with one origin**, so a renderer
needs a cell size, an origin and nothing else. Source sheets disagree on both — the
leading sheet's baseline is 78 in three facings and 95 in the fourth, because facing
south the handler walks in front of the horse.

### Coats

A coat is a palette, and there are currently **two mechanisms** — converging them is
an open item:

- **Path C**: named ramps under `coats:` in `config/palettes/palette_v1.yaml`, selected
  by `palette.coat` in a spec. `template_from` shares one geometry between coats;
  copying a template lets variants drift, which `§23` forbids.
- **Pack**: `coat.py` splits a sheet into an index map plus its exact palette and
  renders them back. `config/palettes/coat_*.yaml` holds the ramps,
  `rendered_coats:` in `packs/full_pack.yaml` declares what to render.

Which colours *are* the coat is derived, not guessed: a colour present in every coat
of a family cannot be what distinguishes them, which finds the eye as invariant.
Invariants are absent from the substitution mapping, so the eye and the rider's skin
survive because they were never in it.

### The game is the conformance test

`game/` reads the project schema directly. If it can load and animate an asset, the
metadata carried enough — a stronger claim than "it looks right". `index.html` is the
playable pasture (filters the manifest on `mode`); `viewer.html` draws **everything**
in `production/`, which is where Path C output shows up.

## Things that will bite you

- **`assets/` holds committed source *and* generated output.** `rm -rf assets` deletes
  hand-authored specs. Use `./build.sh clean`.
- **`samples/` is gitignored, licensed third-party art.** Tests that need it skip
  rather than fail, so the suite runs on a hosted runner. Never commit it, and never
  commit anything derived from it.
- **Validation reports, it does not repair** (`§27`). A finding that is genuinely
  intended gets signed off with `accept:` in config, which downgrades it to `ACCEPTED`
  *with the reason attached* and leaves it in the report.
- **The pack's filenames are unreliable.** Six carry U+200B zero-width spaces, and the
  same coat is spelled five ways (`White`/`white`, `socks`/`sock`,
  `paint_black`/`paint_back`). They are listed explicitly in `packs/full_pack.yaml`,
  never globbed.
- **The pack's PNGs carry RGB underneath fully transparent pixels** — invisible, still
  part of the bytes. `coat.py` keeps it in a separate `hidden` channel; dropping it
  breaks byte-identity.
- **The jump sheet's `160x164` grid divides cleanly and bleeds *less* than the true
  `80x82` one.** Each of those cells holds a 2x2 block of sprites; taking it renders
  the horse at 2.4x scale. `detect.holds_multiple_sprites` rejects it.
- **The pack's coats are independently drawn**, not recolours of each other — bay and
  black do not share an alpha channel. Bay's geometry is canonical for rendered coats.
- **The rider and handler are unclothed base sprites.** The pack ships no clothing
  layer. Worth remembering before anything ends up in a screenshot.

## Open items — extend this list

- The two coat mechanisms (Path C ramps, pack palette substitution) should become one.
- `stage_spritesheet` packs a single horizontal strip and `stage_metadata` emits one
  non-directional clip. A grid packer plus per-facing clips would let Path C assets be
  playable coats instead of viewer-only.
- Flaxen manes, socks and stockings are not expressible: `SHADOW_DARK` covers mane,
  tail *and* legs in the Path C template, so a pale mane bleaches the legs too.
- Rendered coats derive from bay's geometry only; nothing generalises the choice.
- No CI workflow exists yet, though the suite is now hosted-runner-safe.
