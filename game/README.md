# Pasture Run

A small demo game that renders the `samples/Full_Pack` horse sprites. Its job is
to prove the sheet metadata is correct — every animation it claims is on screen
and driven by real game state, so a wrong row index or cell size is immediately
visible.

## Run

The game loads **generated** assets, not the raw pack, so ingest first. ES
modules also need a real origin, so `file://` will not work:

```bash
pip install -e ".[dev]"
python -m pixelasset.ingest        # samples/Full_Pack -> assets/production/
python -m http.server 8000         # http://localhost:8000/game/
```

## Controls

| Key | Action |
|---|---|
| `WASD` / arrows | move (4-direction facing) |
| `M` | mount / dismount |
| `Shift` | gallop — mounted only, drains stamina |
| `Space` | jump — mounted only, needs trot pace, costs stamina |
| `G` | graze — refills stamina (stand still) |
| `P` | pet — on foot only, refills faster, hearts |
| `C` | cycle coat variant |
| `R` | restart after the round ends |

## The game

Two modes, and the whole thing is built around the trade between them:

- **On foot** you lead the horse on a rope. Slow, cannot jump the rail, but you
  are the only one who can pick an apple up off the ground.
- **Riding** is fast and the only way over the rail — but you cannot reach the
  ground from the saddle.

So you ride to cover distance and dismount to collect. A striped jump rail sits
across the worn track; it blocks north/south travel but never travel along it,
so the ends stay open — riding around is the safe line, jumping it is the fast
one and pays `+2`. You cannot jump from a standstill, which ties the rail back
to the stamina economy instead of making it a free button.

Two earlier designs were cut because a bot round exposed them:

- Apples originally needed a 0.9s graze while standing on them. A full round
  under that rule scored **zero**.
- The first rail collision stopped the horse *at* a line instead of ejecting it
  from a band, so the second push walked straight through (`178 < 178`).

## What it demonstrates

- **Speed-driven animation** — idle → walk → trot → gallop comes from velocity,
  not from a keypress, so timing errors in the clip table show up as popping.
- **Three cell geometries in one sprite pipeline** — `80x64` mounted, `80x82`
  mounted jump, `100x96` leading. Cell size and origin are per-sheet.
- **Per-facing origins** — the leading sheet is the one whose baseline is not
  uniform: facing south the handler walks in front of the horse, so the pair's
  ground contact is the handler's feet at `y=95`; every other facing bottoms out
  on the horse's feet at `y=78`. Measured from alpha bboxes, not guessed.
- **One-shot clips** — the jump plays once and lands, rather than looping.
- **Collision driven by the artwork** — the frames where the horse clears the
  rail are the frames where the sprite has actually left the ground (`air` in
  `sprites.js`), not a guessed timer.
- **Asset families** (§23) — `C` swaps seven coats that share geometry, scale,
  lighting and outline rules, across all five sheets at once.
- **No subpixel placement** (§5) — all draw positions are rounded and the frame
  is composited at native 480×320, then integer-scaled with
  `imageSmoothingEnabled = false`.
- **Deterministic background** (§8) — the pasture comes from a fixed seed and a
  fixed palette in `field.js`, so it is byte-identical every run.

## Smoke test

`smoke-test.js` drives a homing bot at the nearest apple, then asserts the mode
trade (mounted cannot pick up, on foot cannot jump), the rail (blocks when
grounded, clears when airborne), the one-shot jump clip, graze, pet and all four
facings. A wrong row index in `sprites.js` fails it in seconds.

```bash
npm i playwright-core && npx playwright install chromium
python3 -m http.server 8765            # from the repo root, in another shell
node game/smoke-test.js
```

Set `CHROME_PATH` to reuse an existing Chromium, `BASE_URL` to change the port.
The test needs `?debug=1`, which exposes `window.pasture`; it is off on a normal
load.

## Two pages

`index.html` is the pasture — playable coats only. It needs four facings, gaits
and a riding or leading sheet, so it draws the ingested pack assets and filters
the manifest on `mode`. A single-facing idle has no mode and is deliberately
excluded; cycling one in with `C` would break the draw path.

`viewer.html` draws **everything** in `assets/production/`, whatever shape it
is — a 24x16 static prop, a 48x32 three-frame idle, a 100x79 leading sheet with
eighteen clips across four facings. That is where Path C output shows up:
before it existed, `hay_bale`, `horse_bay` and `horse_fox` were invisible to
the game entirely, because the pasture could only draw rideable coats.

## The game is the conformance test

This is a sandbox for seeing how assets look, but it earns its keep as the
consumer that proves the metadata contract is sufficient. If the game can load
and animate an asset, the metadata carried enough — a stronger claim than "it
looks right", and the thing that makes a hardcoded `frame_count: 1` visible.

Both pages read the project's own schema (`schemas/metadata.schema.json`), the
same document `stage_metadata` emits. Neither has a format of its own, and the
viewer covers both producers — so "it rendered" means "its metadata was
sufficient" no matter which half of the pipeline made it.

Two spritesheet layouts are real output and both are supported:

```
row_per_animation   one clip per row, frame 0 at the left     (ingest)
single_strip        every frame in `order` sequence on row 0  (stage graph)
```

```
frame_size            [w, h]
anchors.points.feet   [x, y]        origin, bottom_center
spritesheet           {file, columns, rows, layout}
animations[]          {name, facing, row, frames, fps, loop, order, off_ground}
```

`spritesheet`, `facing` and `off_ground` are the extension for directional
animated assets — all optional, so a static prop like `hay_bale` validates
without them. The reasons each is needed are in
`tests/test_metadata_contract.py`.

`off_ground` is an inclusive pair of frame indices measured at ingest from each
frame's alpha bounding box against the clip baseline, so rail collision tracks
the artwork rather than a hand-tuned timer, and stays correct if fps changes.

## Where the assets come from

Today: `python -m pixelasset.ingest`, which normalizes the third-party pack in
`samples/`. That is a second producer alongside the Path C stage graph, and it
exists to exercise this contract end to end while the graph is still
single-animation. Once `stage_spritesheet` packs a grid and `stage_metadata`
emits per-facing clips, it collapses into a construction path.

### One caveat about the artwork

**The rider and handler are unclothed base characters.** The sample pack ships
no clothing layer to composite on, so any figure on screen is a nude base
sprite. Worth knowing before this ends up in a screenshot.
