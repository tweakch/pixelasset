# Pasture Run

A small demo game that renders the `samples/Full_Pack` horse sprites. Its job is
to prove the sheet metadata is correct — every animation it claims is on screen
and driven by real game state, so a wrong row index or cell size is immediately
visible.

## Run

The game loads **generated** assets, not the raw pack, so ingest first. ES
modules also need a real origin, so `file://` will not work:

```bash
./build.sh                 # from the repo root: venv, assets, tests
./build.sh serve           # http://localhost:8000/game/
```

`build.sh` handles the venv for you. Running the tools directly needs it
activated — system `python3` will not work, because the package is installed
into the venv and Ubuntu 22.04 ships Pillow 9.0.1 where the pipeline needs 10+:

```bash
source .venv/bin/activate
python -m pixelasset.ingest
pixelasset build horse_fox
```

## Controls

| Key | Action |
|---|---|
| `WASD` / arrows | move (4-direction facing) |
| `F` | call the horse |
| `L` | take the rope / let go |
| `M` | mount / dismount |
| `Shift` | run on foot; gallop mounted, drains stamina |
| `Ctrl` | walk — the slow gait, unreachable without it (see below) |
| | on foot, `Ctrl` is also the only pace a horse follows on a slack rope |
| `Space` | jump — mounted only, needs trot pace, costs stamina |
| `G` | graze — refills stamina (stand still); a led horse grazes on its own |
| `P` | pet — on foot only, refills faster, hearts |
| `T` | saddle / unsaddle |
| `C` | cycle coat variant |
| `R` | restart after the round ends |

## The game

You play the farmhand, never the horse. Three states, and the whole thing is
built around the trade between them:

- **Loose** the horse is nobody's. It roams, grazes and — once it is happy
  enough — frolics. You are a separate sprite on your own two feet. Walk to it,
  or press `F` and it comes to you.
- **On foot** you have the rope — an actual rope, not a pose. Slow, cannot jump
  the rail, but you are the only one who can pick an apple up off the ground.
- **Riding** is fast and the only way over the rail — but you cannot reach the
  ground from the saddle.

Riding is one asset with both of you drawn in. Loose and on foot are two actors
apiece — the riderless sheet plus the farmhand — which is only possible because
ingest already put every sheet on its own single cell and origin.

### The rope

On foot used to be one asset too: the horse-and-handler sheet, with the rope
painted in as four black pixels. It reads well standing still and badly the
moment you turn, because a pair sprite has no relative position — the whole
assembly pivots and the horse jumps to its new side. The pack even draws
`turn_walk` and `turn_trot` to cover the worst of it.

So the horse and the farmhand are separate sprites now, and the rope between
them is an object: a chain of points with both ends pinned, one to the hand and
one to the halter. It only ever pulls — below its rest length it does nothing at
all — and it cannot stretch past a hard limit, which is what keeps it a rope
rather than a bungee cord.

The horse then follows on tension rather than on your input, and how carefully
you move decides what that feels like:

| pace | peak tension | gap | what it looks like |
|---|---|---|---|
| `Ctrl` walk | 0.00 | 23–28px | the rope hangs slack; it comes because it wants to |
| plain | 0.34 | 25–36px | the rope loads and unloads as you go |
| `Shift` run | 1.00 | 37–42px | taut at the limit; you are dragging it |

A horse on a slack rope is following you, and one on a taut rope is being hauled.
Mood decides whether it will do the first at all — an unhappy horse waits to be
pulled, so petting buys you a horse that walks with you. And a led horse never
gallops: its cap is 92 against a gallop threshold of 98, deliberately.

Turning needs no transition any more. The horse has a position of its own, so it
swings round you and arrives late, which is what the authored pivot was
imitating. The rope is drawn as individual pixels rather than a stroked curve —
§5 forbids subpixel placement, and an antialiased line reads as a foreign object
laid over pixel art.

Mood is the loose horse's only stat and it gates exactly one thing: a happy
horse frolics. Petting is the fast way up, grazing the slow one, and it decays.
Holding `P` next to a horse plays the gesture from the pair sheet and gives the
state back when you let go — the pack draws no version of petting with the
farmhand separate, so the game cannot offer one. It is the one thing the pair
sheet still does, and the rope and the drawn halter both disappear for it,
because that artwork has neither.

Tack is a third axis, orthogonal to both: `T` saddles the horse and unsaddles
it again, in either mode. It is appearance only — nothing in the movement or
stamina model reads it — and the saddle stays on across a mount or dismount,
which is why the pipeline builds the leading sheets saddled as well as the
riding ones.

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

## Three pages, three questions

| page | question it answers |
|---|---|
| `index.html` | does the pasture play — the metadata is enough to drive a game |
| `viewer.html` | does **every** asset in the manifest render at all |
| `anim.html` | **this frame is wrong — which one is it and where did it come from** |

`anim.html` is the one to open when something looks off. It shows one clip at a
time, large: the built frame beside the source frame it was cut from, the whole
source row under both with the cell grid drawn on it, a numbered strip of every
frame labelled with its source row and column, and a figure-8 that walks the
subject through all four facings using the game's own `facingFrom` rule.

The selection lives in the URL hash, so a link *is* a bug report — and a link
that names a frame opens paused on it:

```
game/anim.html#id=character_base_afoot&facing=west&clip=run&frame=3
```

The line at the bottom of the clip panel is the same thing as text:

```
character_base_afoot · run/west · frame 3 of 6 · packed row 18 col 3 ·
samples/Full_Pack/Character_base.png row 15 col 3
```

Which is the point. "The walk looks wrong" is a description; "row 15 column 3"
is something a producer can act on. Keys: space plays and pauses, `←`/`→` step
frames, `↑`/`↓` change facing, `[`/`]` change clip.

The source panels need `samples/`, which is gitignored third-party art. They
hide themselves when it is not there; everything else works without it.

A frame marked **✎** in the strip was corrected on the way in — the pack draws
it incompletely and `sources.<src>.patch` restores it by copying from another
frame of the same sheet. For those frames the source panel is labelled
UNCORRECTED, because it shows what the pack draws and the left panel shows what
shipped. There is exactly one in this pack: `horse_bay_lead · idle/west ·
frame 3`, which is missing its lead rope.

The figure-8 draws the anchor as a cross on the path, which is worth knowing
before reading a leading sheet on it. `anchors.points.feet` is bottom-*centre*
of the cell and a leading cell holds a horse *and* a handler, so the centre is
neither one's feet — the pair's middle rides the path and the handler orbits it.
That is the artwork, not the tool: the two are one sprite at a fixed per-facing
offset and no consumer can move them apart. The **anchor** slider nudges the
hang point (mirrored in profile, ignored front and back, which is how the real
offset behaves) so you can put the subject you are judging onto the path and
read back the anchor x that would take — ±41 puts the handler on it.

## What it demonstrates

- **Speed-driven animation** — idle → walk → trot → gallop comes from velocity,
  not from a keypress, so timing errors in the clip table show up as popping.
  The band a gait covers has to reach past the top speed that can produce it:
  the farmhand's walk band ended at 46 against a top speed of 58, so it only
  ever walked while accelerating out of a standstill.
  
  The same arithmetic makes a *slower* gait unreachable from the other side. A
  ridden horse tops out at 74 against a walk band ending at 40, so it trotted
  the moment it got going. That is what `Ctrl` is for: a third speed cap
  (`walkTop`) below the default one, the mirror of `Shift`'s cap above it.
  Ctrl+W closes the tab in Chrome and a page cannot prevent that, so walking on
  WASD means `A`, `S`, `D` only — the arrow keys are the pairing that works.

  On foot the cap that matters is the farmhand's, because nothing steers a led
  horse: it is towed, and its gait comes out of the rope. Ctrl slows you to a
  walking pace and the horse follows at one.
- **A gesture that is not a loop** — `graze` is one eight-frame row cut into
  three clips (`graze_down` → `graze` → `graze_up`) so the head goes down,
  stays down for as long as the horse is eating, and comes back up. Looping
  the row instead played the whole arc at 7fps, which reads as nodding.
  `first:`/`frames:` take the windows and `next:` chains them; the game only
  has to hold on the middle one.
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
- **Two families in one manifest** — the farmhand is the same producer, the same
  schema and the same loader as the horse, and the only thing that separates
  them is the `family` field. The pasture filters on it; the viewer does not,
  which is why the viewer shows both.
- **A grid that cannot be detected** — `Character_base.png` is an 18px cell and
  detection searches from 24, so it is pinned in config and confirmed against
  the artwork instead. Its row map was then read off the character's own iris:
  two eyes face the camera, one is profile, none faces away.
- **Two independent axes on one asset** — `mode` (riding, on foot, loose) and `tack`
  (bareback or haltered, saddled) are separate fields in the metadata, so the
  saddle toggle is a lookup rather than a guess at an id suffix. Ninety horse
  assets: fifteen coats × three modes × two tacks. The saddled sheets are the same clips,
  rows and jump windows as the bareback ones — the pack description says so with
  `like:` instead of a second copy of the clip block.
- **Tack that survives a coat swap** — the brown saddle is drawn entirely
  outside bay's thirteen-colour key space (10 px of overlap on the riding sheet,
  none on the jump sheet), so palette substitution cannot reach it. Every
  rendered coat gets the same saddle for free, with no mask and nothing to
  maintain. The black saddle would not: 4226 px of it are bay's own `#000000`.
- **Asset families** (§23) — `C` swaps fifteen horse coats that share geometry,
  scale, lighting and outline rules, across all six source sheets at once. Seven
  are the pack's own; the other eight are rendered from bay's geometry through
  another palette. Three (`black_render`, `white_render`, `bay_socks`) sit next
  to the shipped coat they reproduce, so the difference can be seen rather than
  argued about; three (`fox`, `dun`, `flaxen`) are invented, with no shipped
  counterpart; and two (`fox_socks`, `dun_paint`) combine an invented body coat
  with a marking derived from the pack's own art. `flaxen` is `fox`'s body with
  only its three mane rows changed — worth putting next to `fox` in the cycle,
  since fox's mane deliberately disappears into its coat and flaxen's does not.
- **No subpixel placement** (§5) — all draw positions are rounded and the frame
  is composited at native 480×320, then integer-scaled with
  `imageSmoothingEnabled = false`.
- **Deterministic background** (§8) — the pasture comes from a fixed seed and a
  fixed palette in `field.js`, so it is byte-identical every run.

## Smoke test

`smoke-test.js` drives a homing bot at the nearest apple, then asserts the mode
trade (mounted cannot pick up, on foot cannot jump), the rail (blocks when
grounded, clears when airborne), the one-shot jump clip, graze, pet, the saddle
toggle (asserted on the asset id it resolves to, in both modes), the loose state
(the horse behaving on its own, `F` bringing it over, `L` taking the rope and
giving it back) and all four facings. A wrong row index in `sprites.js` fails it in seconds.

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

Today: `python -m pixelasset.ingest` (inside the venv), which normalizes the pack in
`samples/`. That is a second producer alongside the Path C stage graph, and it
exists to exercise this contract end to end while the graph is still
single-animation. Once `stage_spritesheet` packs a grid and `stage_metadata`
emits per-facing clips, it collapses into a construction path.

### One caveat about the artwork

**The rider and handler are unclothed base characters.** The sample pack ships
no clothing layer to composite on, so any figure on screen is a nude base
sprite. Worth knowing before this ends up in a screenshot.
