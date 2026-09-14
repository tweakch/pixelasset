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
.venv/bin/python -m pixelasset.coat extract --base A.png --target B.png \
    --name coat_grey --base-name coat_bay                        # prints YAML to stdout
```

The extractor writes nothing on purpose: `config/` is the hand-reviewed tree, and a
tool that overwrites a coat file in place makes the review step skippable. It also
preserves the comments a dumper would erase. A test asserts each checked-in coat equals
the extractor's output, so the values stay mechanical while committing stays human.

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
  renders them back. `config/palettes/coat_*.yaml` holds one `{from, to}` row per
  colour of the base coat (`coat_bay`), `rendered_coats:` in `packs/full_pack.yaml`
  declares what to render, and `markings:` declares regions a palette cannot express.

`from` is the key space and must be distinct; `to` may repeat, and must be allowed to —
black's art uses eleven colours where bay uses thirteen, so two of bay's steps collapse
onto a shared step. Rows live in a list rather than a YAML mapping because `safe_load`
keeps the last of duplicate keys silently. Key distinctness is a loader check, not a
schema one: JSON Schema has no unique-by-property keyword.

Which colours *are* the coat is measured, not guessed: for each colour of the base
sheet, the colour the target sheet draws at most of those pixels. `extract_coat`
refuses a one-to-many correspondence by name rather than picking a branch, which is
what separates a coat from a marking. The eye is four explicit identity rows — an
earlier model derived "invariant" by intersecting sibling palettes, which held for the
three coats it ran on and is not a property of the colour (on socks_brown the iris
itself splits 92/7). Pass-through still protects the rider and the tack, but for a
category that is a fact about the artwork: colours outside the key space entirely.

**Roles.** `coat_bay` names all thirteen — `BODY_*`, `MANE_*`, `OUTLINE`/`CONTOUR`/
`HOOF`, `EYE_*` — in four groups, so a coat can be authored by intent rather than by
hex. A derived coat may address rows by `role` instead of `from` and the loader
resolves it, so `coat_flaxen.yaml` reads as "fox's body, three mane rows changed".
`python -m pixelasset.coat propose --group body=#c89a4e --group mane=#f0e4c8` writes a
whole coat from one colour per group, holding each row's luminance ratio so the ladder
survives. The names are derived, not assumed — the measurement for each is in
`coat_bay.yaml`, and the one that mattered is the marking test: socks and paint
override 66-75% of the body group and 0.0-0.1% of `MANE_*`, which is what proves those
three colours are the mane and tail rather than body shading.

`propose_coat` and `collapses` report role pairs that bay separates and a coat does
not — the mane's hatched strands stop reading against the neck. Not automatically a
defect: a chestnut is self-coloured and a black horse's mane is not lighter than its
body, so each is signed off in `accept_collapse:` with its reason (§27), and a test
fails on both an unsigned collapse and a stale sign-off.

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
- **The pack's coats mostly *are* recolours of one geometry** — measured, against the
  earlier claim in this file. Alpha differs from bay by 4 px of 135823 on the
  no-equipment sheets, 39 riding, 606 leading, and bay's thirteen colours correspond
  one-to-one to black's and white's (worst minority branch 137 px). "Bay and black do
  not share an alpha channel" was true and meant nothing. Paint and socks are the real
  exception, and not because of alpha: the same bay colour has two destinations there,
  which is a marking. Bay's geometry is still canonical for rendered coats.
- **The pack is not self-consistent across its own sheet families.** Its black jump
  sheet draws bay's `#000000` as `#101010` on 3457 px; its white jump sheet shades
  midtones a step darker than its own body sheets. So a rendered coat that matched
  every shipped sheet cannot exist — extraction is pinned to one named sheet and
  `coat.agreement` reports the rest without enforcing it. Rendered white differs from
  shipped white by 0.211% riding and 22.1% on the jump rows, and `_ride` packs both.
- **The rider and handler are unclothed base sprites.** The pack ships no clothing
  layer. Worth remembering before anything ends up in a screenshot.

## Open items — extend this list

- The two coat mechanisms (Path C role ramps, pack colour maps) should become one, and
  they are now much closer: both are role-keyed. The remaining differences are real
  though — the pack has thirteen roles in four groups against Path C's seven flat ones,
  a three-value outline against one, and a separate mane group where Path C folds mane,
  tail and legs into `SHADOW_DARK`. A shared vocabulary is the next step; `roles:`
  groups in `coat_bay.yaml` are the shape to converge on, since they are the ones
  derived from artwork rather than chosen.
- `stage_spritesheet` packs a single horizontal strip and `stage_metadata` emits one
  non-directional clip. A grid packer plus per-facing clips would let Path C assets be
  playable coats instead of viewer-only.
- Socks and paint markings are expressible on the **pack** side, derived from the
  pack's own art (`config/markings/`), and so are flaxen manes — `coat_flaxen` ships.
  An earlier note here said manes were impossible because bay's thirteen colours are a
  shading ladder rather than a body-part map. That was measured wrong: the per-colour
  mean y over the whole sheet (0.48-0.79) mixes all four facings and flattens the
  signal. Per frame and relative to each silhouette, `MANE_*` sits at 0.31 against the
  body's 0.46, and socks and paint override 0.0-0.1% of it. Still open on the **Path C**
  side, where `SHADOW_DARK` genuinely does cover mane, tail and legs in one template
  token — that one needs a new token, not a palette.
- Bay is a dark palette and its dark end is three values of outline, so with a body at
  bay's own luminance there is almost nowhere to darken a mane into. Lightening has the
  whole range above. That is headroom, not a rule about manes — `coat_white`'s mane is
  darker than its body and reads fine at luminance 238. Worth knowing before authoring
  a black-pointed coat.
- Rendered coats derive from bay's geometry only; nothing generalises the choice. The
  blocker is real for the marked coats — the ink/reshade classifier needs a base coat
  that does not already contain the marking's colours, so it collapses against black.
- A marking mask is recomputed at ingest from two full sheets per family. Correct and
  cheap next to grid detection, but it means the *marking* artwork must be present even
  to build a coat that only borrows its mask.
- `coat.correspond` is the general tool and only extraction uses it. The same
  cross-tabulation would answer "which colours moved between these two builds", which
  is a better regression signal than a spritesheet hash that only says "something".
- No CI workflow exists yet, though the suite is now hosted-runner-safe.
