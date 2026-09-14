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

Tack rides alongside the coat: every coat is built bare and saddled in all three
modes, and the game toggles between them with `T`. The pack's riderless sheets
and its character sheet are ingested too, which is what makes the game's loose
state — you play the farmhand, the horse is its own agent — possible at all.

**The workflow itself is under active development.** If you notice something that
would make the pipeline better, faster or more versatile, say so — surface the idea
in your response even when it is outside what you were asked to do. Concrete open
items are listed at the bottom; add to them.

## Commands

```bash
./build.sh                  # venv, install, ingest, build every asset, test, summarise
./build.sh serve            # game at /game/, asset viewer at /game/viewer.html
./build.sh clean            # generated output only
./build.sh smoke            # headless browser test — SKIPS (exit 0) without playwright-core
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

`smoke` warns and returns 0 when `playwright-core` is absent, so a green `./build.sh`
does **not** mean the game was exercised. To actually run it: `npm i playwright-core`
anywhere, then
`NODE_PATH=<dir>/node_modules CHROME_PATH=~/.cache/ms-playwright/chromium-*/chrome-linux64/chrome \
BASE_URL=http://localhost:8765 node game/smoke-test.js <outdir>` against a running
`python -m http.server 8765`. A browser is usually already cached there even when the
npm package is not.

Visual spot-check without a browser: crop one frame per asset out of
`assets/production/<id>/spritesheet.png` using its metadata `row` and `frame_size`,
scale with `Image.NEAREST`, paste into one strip and read the PNG. Faster than `serve`
and it compares coats side by side, which the viewer does not.

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

`animations[].source` carries provenance — `{sheet, row, columns, cell}`, plus
`patched` when a frame's pixels deliberately do not match the sheet it names. It is
the field `anim.html` is built on, and it exists so a defect can be reported against
the artwork rather than against the packed output. `columns` is a list, not a first
plus a count: corrections and gaps make it non-consecutive, and a contract that
assumed otherwise would point every frame after a gap at the picture next door.

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

### Families, modes and tack

Three independent axes, three metadata fields, and the asset id is the only
place they are concatenated:

| field | horse | character |
|---|---|---|
| `family` | `horse` | `character` |
| `mode` | `ride`, `lead`, `loose` | `afoot` |
| `tack` | `bareback`, `halter`, `saddled`, `none` | absent |

`tack: none` is a value (a loose horse wears nothing); a family with no tack
axis omits the field entirely. Fifteen coats x three modes x two tacks is the
ninety horse assets a full ingest produces, and a consumer pairs them off
`mode` + `tack` rather than by parsing a suffix off the id. **Filter on
`family`** — `game/sprites.js` did not, and the farmhand joined the coat cycle.

A saddled mode is the bareback mode drawn on other sheets — same rows, same
frame counts, same jump windows. `packs/full_pack.yaml` says exactly that with
`like: ride` plus a `sheets:` remap of the source keys, resolved by
`ingest.resolve_modes`; copying the clip block instead would be thirty-four
lines whose only future is drift. Inheritance is one level, a remap that matches
no source is an error, and so is inheriting without remapping anything (it would
build byte-identical artwork under a second id).

Inheritance is one level, so `loose_saddled` says `like: ride` rather than
`like: loose` even though `loose` borrows the same rows. Saying it twice is the
price of refusing to chain.

**Always load a pack through `ingest.load_pack`, never bare `yaml.safe_load`** —
an unresolved inheriting mode has no `clips` at all. And **always read a pack sheet
through `ingest.load_source_sheet`, never bare `load_rgba`** — a source may declare
corrections, and a sheet read raw on one path and corrected on another puts the
correction into whatever the two are differenced for (`_marking_for` is the live
case).

### Correcting the artwork

A `patch:` under a source restores a frame the pack draws incompletely, by copying a
rectangle out of another frame of the same sheet. It is not a repaint: every pixel it
writes was already in the file, and the entry names the frame it came from, so the
claim is checkable by looking at two cells. It is allowed where dropping the frame
would cost more than the defect — the halter leading sheets omit the lead rope on one
frame of the west idle, and the rope, halter and gripping hand are byte-identical in
nine of the row's other ten frames, so there is nothing to interpolate.

Four things keep it honest. A source that patches must pin `cell:`, because the patch
is addressed in cells and detecting a grid from a sheet about to change lets the
coordinates mean two things. A patch that changes nothing is an error — that is how a
correction outlives the defect it was for. `metadata` reports the corrected frames in
`source.patched`, so a consumer differencing output against source sees a declared
edit rather than corruption. And the defect that motivated it has its own check:
`detect.blob_sizes` counts 8-connected components, a led horse is one blob because the
rope joins the two bodies, and `tests/test_clips.py` pins the census of every frame in
the pack that splits — fourteen per sheet are the petting rows letting go on purpose.

This is narrower than it sounds and deliberately not a general repair facility.
Validation still only reports (§27); this is a declared, reviewed, measured statement
about one rectangle of one cell.

### A second pack, a second family

`packs/character.yaml` describes `Character_base.png` through the same producer:
`subjects:` is the general spelling of what `full_pack.yaml` calls `coats:`, and
`pack.family` becomes the metadata `family` and the id prefix. It is a separate
file because the subject has no coats, no markings and nothing to render by
palette substitution. `build.sh` names both packs; `--pack` is repeatable and
deliberately not a glob, because tests write throwaway packs into `packs/`.

Its grid is the one in the project that **cannot be detected**: `detect_cell`
searches from `min_side: 24` for the horse sheets and this cell is 16 wide, so
the true grid is never a candidate. `config` pins it and `detect.confirm_cell`
validates the pin — divides the sheet, yields frames, no cell of it straddles a
sprite, and it agrees with the free search wherever that search succeeds. It
deliberately does not re-apply `holds_multiple_sprites`, which on this sheet
flags 35 of 152 cells because a small character's head detaches from its body
at the neck.

The straddle check is there because the pin was **18 for weeks and wrong**.
144 = 8 x 18 as evenly as it is 9 x 16, so the wrong pitch divided the sheet,
found frames in every row and measured the same baseline of 33 — it was simply
two pixels per column out of phase, and every frame after the first carried a
slice of its neighbour. In the game that read as a row of farmhands walking
abreast. Divisibility is not alignment; `detect.straddling_cells` is what tells
them apart, and a cell reaching *both* its side edges is the signal (0 of 132
occupied cells at 16, 88 of 127 at 18).

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

**Classifying new pack artwork.** `derive_marking(a, b)` on two same-geometry sheets
returns the exact override between them, colours and counts. Intersect those colours
with the base coat's `from` set: entirely outside means pass-through will protect it, so
it can be a new source (this is why the brown saddle works and the black one does not);
inside means a coat swap will recolour it, so it needs a mask or a hold-out.
`extract_coat` refusing a one-to-many correspondence is the same question asked the
other way round.

Tack is what pass-through buys. The saddled sources name the pack's **brown**
saddle, and that is measured rather than preferred: the brown saddle falls
outside bay's key space almost entirely (10 px of overlap on the riding sheet,
0 on the jump sheet, 126 on the leading sheet), so every rendered coat gets the
same saddle for free — no mask, no marking, nothing to maintain. The black
saddle draws 4226 px of the riding sheet in bay's own `#000000` (role `HOOF`),
so on fox or dun it would be recoloured with the horse. Add it as a third tack
if you want it; do not make it the one a rendered coat is built from.

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
playable pasture (filters the manifest on `mode` **and `family`**); `viewer.html` draws
**everything** in `production/`, which is where Path C output shows up.

`anim.html` is the third page and the one to reach for when a sprite looks wrong.
It shows one clip at a time: the built frame beside the source cell it was cut
from, the whole source row under both with the cell grid drawn on, a numbered
frame strip labelled `r<row>c<col>` in *source* coordinates, and a figure-8 that
drives the subject through all four facings. The selection is in the URL hash, so
a link opens on exactly one frame — paused, when the hash names one — and the
report line under the strip says the same thing as text:

```
character_base_afoot · run/west · frame 3 of 6 · packed row 18 col 3 ·
samples/Full_Pack/Character_base.png row 15 col 3
```

That last clause is `animations[].source` in the metadata, and it exists only for
this: a defect reported against the artwork is actionable, one reported against
the packed output is a description of a picture. The figure-8 is two
counter-rotating loops rather than a lemniscate because a lemniscate of Gerono
never faces south — both of its zero-`vx` points head north (711 east, 711 west,
498 north, 0 south over a period, for any A and B), and a facing rule cannot be
judged on a path that skips a facing. `facingFrom` lives in `sprites.js` so the
page and the game cannot drift apart on it.

The pasture has three states. `ride` is a pair sheet with both of you drawn in;
`loose` and `lead` are two actors — the riderless sheet plus the farmhand — and
on `lead` there is a rope object between them (`game/rope.js`, `game/lead.js`).
You control the farmhand and never the horse — `F` calls it, `L` takes the rope,
`M` mounts, `P` pets. A loose horse roams, grazes and frolics when its mood is
high enough, and petting is the fast way to raise that.

`lead` was a pair sheet too, with the rope painted in, and that is what made
turning snap: a rigid assembly has no relative position, so the horse jumped to
its new side and `turn_walk`/`turn_trot` existed to hide it. Split, the horse
follows on rope tension — slack and it comes because it wants to, taut and it is
being dragged — and how carefully you move decides which. Measured: Ctrl peaks at
0.00 tension, plain 0.34, Shift 1.00. The pair sheet is still used for the pet
gesture, which draws the two of you leaning together with no rope and no halter
and has no equivalent in the split sheets.

Petting a loose horse borrows the rope for as long as `P` is held, because the
artwork has no version of the gesture with the farmhand drawn separately. That
is the shape of the whole state machine: what the game can express is exactly
what the pack draws.

## Things that will bite you

- **A gait band has to reach PAST the speed it describes.** This has bitten four
  times: the farmhand's walk (band to 46, top 58), the ridden walk (band to 40,
  top 74), and a led horse's slack cap (40 against a band ending at 34) and trot
  cap (92 against 78, so it galloped). Every time the symptom is a gait that is
  never seen or is seen where it should not be, and every time it looks like an
  animation bug rather than an arithmetic one. The farmhand's went unnoticed for
  as long as the profile `run` row pointed at a led walk, which animates almost
  identically — two bugs each hiding the other. Derive the bands from the caps,
  `[WALK_TOP + 6, 'walk']`, rather than writing both by hand.

- **`assets/` holds committed source *and* generated output.** `rm -rf assets` deletes
  hand-authored specs. Use `./build.sh clean`.
- **`samples/` is gitignored, licensed third-party art.** Tests that need it skip
  rather than fail, so the suite runs on a hosted runner. Never commit it, and never
  commit anything derived from it.
- **Validation reports, it does not repair** (`§27`). A finding that is genuinely
  intended gets signed off with `accept:` in config, which downgrades it to `ACCEPTED`
  *with the reason attached* and leaves it in the report.
- **The pack draws one frame without its lead rope** — row 8 (west idle) column 3
  of every halter leading sheet, and nowhere else in the pack. The saddled
  leading sheets are clean, which is why the correction is declared on the
  source and not on the clip: `lead_saddled` inherits the whole `lead` clip
  block and would have inherited an edit to artwork that was never wrong.
  Connectivity is the way to find this class of thing — a led horse is one
  8-connected blob, and the only frames that legitimately split are the fourteen
  `nuzzle`/`pet` ones where the handler has let go.
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
- **`detect_cell`'s guards are tuned for horse sheets and are wrong elsewhere.**
  `min_side: 24` excludes the character's 16px cell; `holds_multiple_sprites`
  flags a sprite whose head detaches from its body. A pinned `cell:` plus
  `confirm_cell` is the way out, and it is a reviewed statement (§22) rather
  than a way around the checks.
- **A pinned cell that divides the sheet can still be the wrong pitch**, and
  almost every check you would reach for passes anyway — frames per row, a
  shared baseline, a plausible aspect. Ask whether any cell touches both of its
  own side edges: a sprite narrower than its cell cannot, so it means the cut
  lands mid-sprite. This is what an 18px character pin failed and nothing
  else caught.
- **A row of a pack sheet is not automatically a loop.** `graze` is an arc —
  head down, hold, head up — and looping it made the horse nod at 7fps and
  never eat. The tell is a plateau in the middle: the opaque pixel count of a
  frame's bottom fourteen rows steps 264/285/297/430/430/430/297/285, so the
  three flat frames are the hold and the other five are the way in and out.
  `first:` + `frames:` cut one row into three clips and `next:` chains them.
- **The row a config calls `run` is a claim, and on the character sheet two of
  them were wrong.** The profile block does not follow the sheet's group order:
  rows 9/10 are the led walk and 15/16 are the run, not the other way round.
  Measure it off the legs — a lead animation is its gait with the arm redrawn,
  so the knees-down half clusters {6,9} at 48 px and {15,17} at 150 against
  197-292 across — and settle which cluster is which on the stride (walk 6.50,
  lead_walk 6.56, run 9.17, lead_run 8.88). The old map gave a run the same
  stride as a walk, which is the shape of the error.
- **Three of the character's five profile actions are mirrored artwork.** idle,
  walk and run are east = west flipped pixel for pixel; lead_walk and lead_run
  are drawn twice. So "west and east carry the same frame counts" is true by
  construction for three of them and only has teeth for the other two — the
  load-bearing evidence for the facing split is the iris position (0.20 or
  0.80 of head width, exactly, on every profile frame).
- **Silhouette IoU cannot tell you which way a sprite faces.** On the character
  it scored 0.237 direct against 0.237 mirrored and produced a confident wrong
  answer. Count iris pixels instead: two blobs face the camera, one is profile,
  none faces away — and read the side off an idle frame, where nothing bobs.
- **`_marking_for` derives a mask for every source the pack declares**, not just
  the ones the mode being built asks for. Adding a source means every marked
  coat's marking sheet must exist for it, and `config/markings/*.yaml` grows an
  entry a test checks against the artwork.
- **The rider and handler are unclothed base sprites.** The pack ships no clothing
  layer. Worth remembering before anything ends up in a screenshot.
- **`anchors.points.feet` is bottom-*centre* of the cell, and on a pair sheet the
  centre is nobody's feet.** On `horse_bay_lead` the anchor is x=50 of 100 while
  the handler stands at x≈91 facing east, x≈6 facing west and x≈48 facing north
  or south — so the anchor lands on the horse's shoulder. Move the asset along
  any path and the horse tracks it while the handler orbits. It does not show up
  in the pasture because the pair moves as one unit and you are inside it, but it
  is visible the moment anything drives the sprite along a curve, and `feet` is
  claiming something it does not measure. The `anchor` nudge on `anim.html`'s
  figure-8 reads off what the handler's anchor would have to be.
- **A canvas starts at 300x150 and the leading cell is 100px wide.** At the
  debugger's 3x that is exactly 300, so a resize guard that checked only the
  width decided the canvas was already correct and left the height at the
  default — one asset family out of the whole repo, which made a one-line bug
  look like a leading-sheet problem. Check both dimensions.

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
- The pack ships a **black** saddle beside the brown one, and a third tack is now
  cheap — one `sheets:` remap and seven filenames. What it needs first is a way
  to say "this colour is tack, leave it alone", because the black saddle shares
  bay's `#000000`. An explicit hold-out set on a coat file would do it and would
  also cover the halter, which is only safe today by accident of being outside
  the key space.
- The riderless sheets are in (`loose`, `loose_saddled`); **rearing is not**.
  `Horse_Sprite_Rearing_Asset` is a whole action set at an 88x66 cell in both
  tacks and all four facings — a new clip in existing modes, not a new mode, and
  the obvious thing for a frolicking horse to do. The **big saddle** is also
  unused: the author's rider update says the small one is the redraw that fits
  the rider, so the big one is tack no ridden sheet matches and would need its
  own tack value rather than replacing `saddled`.
- ~~The character sheet's `lead_walk` / `lead_run` rows are ingested and nothing
  draws them.~~ Done: `lead` is two actors and a rope object. What remains of the
  idea is leading *from the saddle*, which would want the same rows on a mounted
  handler and has no artwork.
- The led horse's rope anchors are per-facing constants in `game/lead.js`, not
  per-frame, so the rope's end floats a pixel or two through a walk cycle. Under
  the sag it does not show at native scale. A per-facing subject anchor in the
  metadata (the item below) would let the hand end track the artwork instead.
- A led horse wears no halter, because the riderless sheets are the
  no-equipment ones and the only haltered sheets in the pack are the pair ones.
  `lead.js` draws four pixels in the pack's own halter colours (`#ebebeb`,
  `#515151`) to cover it. A haltered riderless sheet would be better and the pack
  does not have one.
- A pair sheet needs a per-facing subject anchor, or `feet` should stop claiming
  to be feet on one. Two shapes would do: an explicit `anchors.points.handler`
  per facing, or a signed offset from the centre that mirrors in profile and is
  zero front and back (±41 on `horse_bay_lead`), which is the shape the artwork
  actually has. Changing `feet` itself would move every lead sprite in the
  pasture, so this wants measuring against the game before it is done.
- Path C has no tack at all — `horse_fox` is one geometry with no equipment
  layer. Converging the two coat mechanisms will have to decide whether tack is
  a mode axis (as it is here) or a compositing layer, and the pack cannot answer
  that: it ships tack baked into the artwork, never as a separate sheet.
- No CI workflow exists yet, though the suite is now hosted-runner-safe. A full
  build is now 60 ingested assets and about 1m45s, most of it grid detection.
