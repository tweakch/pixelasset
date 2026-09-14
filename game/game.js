// Pasture Run — a small demo that exercises the sample pack's animations.
//
// You play the farmhand, never the horse. Three states, and the whole game is
// the trade between them:
//
//   LOOSE    the horse is nobody's. It roams, grazes and — when it is happy
//            enough — frolics. You walk on your own two feet, and the only way
//            to reach it is to go to it or to call it.
//   ON FOOT  you have the rope. Slow, cannot jump the rail, but you are the
//            only one who can pick an apple off the ground.
//   RIDING   mounted. Fast, and the only way over the rail — but you cannot
//            reach the ground from the saddle.
//
// So you ride to reach the apples and dismount to collect them.
//
// Two of the three states are one sprite each: loose is the riderless sheet,
// riding is the horse-and-rider sheet, both already normalised onto one cell and
// one origin. On foot used to be the third — the horse-and-handler sheet, with
// the rope painted in — and it is now two actors and a rope object instead.
//
// That is the one place the artwork had to be given up, and it was costing the
// thing the mode is named for. A pair sprite has no relative position, so
// turning pivoted a rigid assembly and the horse jumped to its new side; the
// `turn_walk`/`turn_trot` clips below exist to hide that. Split, the horse has a
// position of its own and swings round you, and the rope carries the difference
// between a horse that is following and one that is being dragged. See lead.js.
//
// The pair sheet is still used, for the pet gesture. It draws the two of you
// leaning together with no rope and no halter, and nothing in the split sheets
// can express that, so the gesture borrows the sprite for as long as it lasts.
//
// Tack is a second axis, orthogonal to both: every coat ships each mode twice,
// bare and saddled, and T swaps between them. It is appearance only — the
// pipeline renders the same clips on sheets that differ in what the horse is
// wearing — so nothing in the movement or stamina model reads it. The saddle
// stays on across a mount or dismount, which is the whole reason the leading
// sheets are built saddled too.
//
// Three of the clips are not gaits and are driven from here rather than from
// speed:
//
//   TURNING   on foot, leaving the side-on view plays a transition in which the
//             handler pivots first and the horse follows. Snapping straight to
//             the north block put the horse through the handler for a frame.
//   PETTING   authored as two rows that meet end to end — lean in, lean out —
//             chained through `next` in the metadata rather than looping half
//             the gesture.
//   GRAZING   one row cut into three clips: the head goes down, stays down for
//             as long as the horse is grazing, and comes back up when it
//             stops. Looping the whole row instead made it bob its head at
//             7fps and never eat, which is the tell that an arc was being
//             played as a cycle.

import {
  loadCoats, loadCharacter, drawSprite, assetFor, clipFor, clipOf, hasClip,
  facingFrom,
  hasSaddle, isAirborne,
} from './sprites.js';
import { buildField, drawApple, drawRail, fillEllipse, trackY } from './field.js';
import { createRope, drawRope, maxSpan, placeRope, REST, stepRope } from './rope.js';
import {
  clampToRope, drawHalter, followLead, halterAt, handAt, leadFacing, leadStart,
  LEAD_HORSE,
} from './lead.js';

const NATIVE_W = 480;
const NATIVE_H = 320;
const ROUND_SECONDS = 60;

const BOUNDS = { x0: 30, x1: NATIVE_W - 30, y0: 46, y1: NATIVE_H - 12 };

const FRICTION = 520;
const PICKUP_RADIUS = 20;
const APPLE_STAMINA = 8;

const STAMINA_MAX = 100;
const DRAIN_GALLOP = 26;
const REGEN_GRAZE = 46;
const REGEN_PET = 62;
const REGEN_IDLE = 9;
const REGEN_MOVE = 3;

// Jumping. It needs trot pace or better, which is what ties the rail to the
// stamina economy rather than making it a free button.
const JUMP_MIN_SPEED = 46;
const JUMP_COST = 14;
const JUMP_SPEED = 150;
const CLEAR_BONUS = 2;

// Per-mode movement, gaits and contact shadow. `gaits` is read in order: the
// first threshold the current speed falls under wins. `shadow` is [dx, rx] —
// the leading sprite's mass sits off to one side of its anchor, because the
// handler walks beside the horse, so the contact shadow is offset to match.
const MODES = {
  // The horse on its own, driven by the AI below rather than by input. It gets
  // the same gait table as everything else so its animation still comes from
  // its speed — a loose horse that trotted at walking pace would read as wrong
  // even though nobody is steering it.
  loose: {
    label: 'LOOSE',
    accel: 260, top: 52, gallopTop: 130,
    gaits: [[4, 'idle'], [34, 'walk'], [78, 'trot'], [Infinity, 'gallop']],
    shadow: { east: [0, 17], west: [0, 17], south: [0, 9], north: [0, 9] },
    canJump: false, canGallop: true, canPickUp: false, canPet: false,
  },
  ride: {
    label: 'RIDING',
    accel: 420, top: 74, walkTop: 32, gallopTop: 152,
    gaits: [[4, 'idle'], [40, 'walk'], [82, 'trot'], [Infinity, 'gallop']],
    shadow: { east: [0, 17], west: [0, 17], south: [0, 9], north: [0, 9] },
    canJump: true, canGallop: true, canPickUp: false, canPet: false,
  },
  lead: {
    label: 'ON FOOT',
    accel: 300, top: 56, gallopTop: null,
    gaits: [[4, 'idle'], [26, 'walk'], [Infinity, 'trot']],
    shadow: { east: [8, 30], west: [-8, 30], south: [0, 12], north: [-2, 11] },
    canJump: false, canGallop: false, canPickUp: true, canPet: true,
  },
};

// Which turn transition covers which gait. Only the pair sheets carry them, and
// nothing asks for them any more: they existed so a rigid horse-and-handler
// could leave profile without the horse passing through the handler, and a led
// horse now turns by moving. Kept because `hasClip` is what gates them, so the
// lookup simply stops matching rather than needing a special case — and the
// riderless sheets have no turn rows at all.
const TURN_FOR = { walk: 'turn_walk', trot: 'turn_trot' };
const PROFILE = new Set(['east', 'west']);

// Which sheet the horse is drawn from, which is no longer the same thing as
// which state the game is in. On foot the horse is the riderless sheet and you
// are the character sheet; the pair sheet comes back only for the pet gesture.
//
// Everything that looks up a clip, a shadow or an asset goes through this rather
// than through `horse.mode`, so the split is one function and not a condition
// scattered across the draw path.
const spriteMode = () => (
  horse.mode === 'lead' && !horse.petting ? 'loose' : horse.mode
);

// The farmhand on foot. Separate from MODES because this is not a mode of the
// horse: it is the actor you control while the horse is loose.
//
// The walk band has to reach past `top`, not stop short of it. Shift is what
// picks the gait here — unlike the horse, whose speed is continuous — so with
// the band ending at 46 the farmhand crossed into `run` the moment it got up
// to its own un-shifted top speed of 58 and only ever walked while
// accelerating out of a standstill. Nobody noticed for as long as the profile
// `run` row was pointing at a led walk, which animates almost identically;
// fixing the row map is what made it visible.
const PLAYER = {
  accel: 460, top: 58, walkTop: 26, runTop: 104,
  gaits: [[3, 'idle'], [62, 'walk'], [Infinity, 'run']],
};

// How close you have to be to touch the horse, and how close it stops when it
// comes to you. REACH is generous on purpose — the horse sprite is 80px wide
// and its anchor is its feet, so a tight radius means standing inside it.
const REACH = 34;
const COME_STOP = 26;

// Mood. Petting is the fast way up, grazing the slow one, and it decays, so a
// horse you ignore goes back to merely content. Frolicking needs a happy horse,
// which is the only thing mood actually gates.
const MOOD_MAX = 100;
const MOOD_PET = 34;
const MOOD_GRAZE = 4;
const MOOD_DECAY = 1.1;
const MOOD_FROLIC = 72;

// Planted on the worn track, so the run-up line is visually obvious.
const RAIL = {
  x0: NATIVE_W / 2 - 76,
  x1: NATIVE_W / 2 + 76,
  y: Math.round(trackY(NATIVE_W / 2, NATIVE_H)),
};
const RAIL_BLOCK = 5;   // half-thickness of the barrier the horse collides with

const view = document.getElementById('view');
const vctx = view.getContext('2d');
const screen = document.createElement('canvas');
screen.width = NATIVE_W;
screen.height = NATIVE_H;
const ctx = screen.getContext('2d');
ctx.imageSmoothingEnabled = false;
vctx.imageSmoothingEnabled = false;

const keys = new Set();
const pressed = new Set();
addEventListener('keydown', (e) => {
  if (!e.repeat) pressed.add(e.code);
  keys.add(e.code);
  if (['ArrowUp', 'ArrowDown', 'ArrowLeft', 'ArrowRight', 'Space'].includes(e.code)) e.preventDefault();
});
addEventListener('keyup', (e) => keys.delete(e.code));

const field = buildField(NATIVE_W, NATIVE_H);
let coats = [];
let coatIndex = 0;
let character = null;
const coat = () => coats[coatIndex];

const horse = {
  x: NATIVE_W / 2,
  y: NATIVE_H / 2,
  vx: 0,
  vy: 0,
  // 'loose' | 'lead' | 'ride' — which asset is on screen, and whether you are
  // steering it at all. Loose is the only one you are not.
  mode: 'loose',
  saddled: false,
  mood: 52,
  ai: { state: 'wander', timer: 3, tx: 0, ty: 0 },
  facing: 'east',
  lastHorizontal: 'east',
  action: 'idle',
  frame: 0,
  clock: 0,
  stamina: STAMINA_MAX,
  jumping: false,
  jumpDir: { x: 1, y: 0 },
  petHold: false,   // holding P on a loose horse borrows the rope for the gesture
  petting: false,   // the pet gesture is on screen, so the pair sprite is too
  turning: null,    // {to} while a turn transition plays; facing commits at the end
  chain: null,      // the clip a finished one-shot handed off to, via `next`
  lifting: false,   // graze_up is playing: the head is on its way back up
};

// You. On screen whenever the horse is not carrying you: loose, and on foot,
// where you are the actor being steered and the horse is the one following.
const player = {
  x: NATIVE_W / 2 - 70,
  y: NATIVE_H / 2 + 20,
  vx: 0,
  vy: 0,
  facing: 'south',
  action: 'idle',
  frame: 0,
  clock: 0,
};

// The rope. One object for the whole game — there is only ever one horse to
// lead — reset whenever either end teleports rather than recreated.
const rope = createRope();
const lead = { taut: 0, dist: 0 };

let apples = [];
let score = 0;
let clears = 0;
let best = 0;
let timeLeft = ROUND_SECONDS;
let state = 'playing';
let elapsed = 0;

// Apple placement uses Math.random deliberately — this is gameplay variety,
// not a pipeline artifact, so it is outside the determinism rule in §8.
function spawnApple() {
  // Rejection-sample away from the horse and off the rail, otherwise a fresh
  // apple can land inside the pickup radius and chain-collect on the next frame.
  for (let tries = 0; tries < 24; tries++) {
    const x = BOUNDS.x0 + 12 + Math.random() * (BOUNDS.x1 - BOUNDS.x0 - 24);
    const y = BOUNDS.y0 + 12 + Math.random() * (BOUNDS.y1 - BOUNDS.y0 - 24);
    const onRail = x > RAIL.x0 - 10 && x < RAIL.x1 + 10 &&
                   Math.abs(y - RAIL.y) < RAIL_BLOCK + 12;
    if (!onRail && Math.hypot(x - horse.x, y - horse.y) > PICKUP_RADIUS * 3) {
      apples.push({ x, y });
      return;
    }
  }
  apples.push({ x: BOUNDS.x0 + 16, y: BOUNDS.y0 + 16 });
}

function resetRound() {
  score = 0;
  clears = 0;
  timeLeft = ROUND_SECONDS;
  apples = [];
  // `saddled` is deliberately not in here: tack is a preference the player set,
  // not round state, so a restart keeps the horse dressed the way they left it.
  Object.assign(horse, {
    x: NATIVE_W / 2, y: NATIVE_H / 2 - 34, vx: 0, vy: 0,
    mode: 'loose', facing: 'east', lastHorizontal: 'east',
    action: 'idle', frame: 0, clock: 0, mood: 52,
    stamina: STAMINA_MAX, jumping: false, turning: null, chain: null,
  });
  // Opens on the move. Grazing is a 3-7 second hold and picking it first meant
  // a round could start with several seconds of a motionless horse.
  horse.ai = { state: 'wander', timer: 3, tx: horse.x - 60, ty: horse.y + 24 };
  Object.assign(player, {
    x: NATIVE_W / 2 - 70, y: NATIVE_H / 2 + 20, vx: 0, vy: 0,
    facing: 'south', action: 'idle', frame: 0, clock: 0,
  });
  for (let i = 0; i < 6; i++) spawnApple();
  state = 'playing';
}

// Three speeds, two modifiers. An actor's `top` is what it does with nothing
// held; Shift releases a faster cap and Ctrl imposes a slower one.
//
// Ctrl exists because a gait below the default one is unreachable otherwise. A
// ridden horse tops out at 74 against a walk band that ends at 40, so it only
// ever walked during the second it took to accelerate — the same shape of bug
// as the farmhand's walk band once ending below its own top speed, and the same
// fix from the other side.
//
// On foot the cap that matters is the FARMHAND's. Nothing steers a led horse:
// it is towed at whatever pace you set, and its gait comes from the speed the
// rope drags it to against `LEAD_HORSE`'s own bands (walk under 34). So
// `MODES.lead` carries no `walkTop` — its movement numbers are only reached on
// the petting path, where both actors are pinned — and `PLAYER.walkTop` is what
// makes a led horse walk.
//
// Each `walkTop` lands inside its band rather than on the edge, so a diagonal
// (1/√2 per axis, not 1) and the rope's slack still read as a walk.
//
// Ctrl+W closes the tab in Chrome and a page cannot prevent it, so walking on
// WASD means walking on `A`, `S` and `D` only. The arrow keys are the pairing
// that works; `preventDefault` covers those.
const WALK_KEYS = ['ControlLeft', 'ControlRight'];
const FAST_KEYS = ['ShiftLeft', 'ShiftRight'];
const holding = (codes) => codes.some((code) => keys.has(code));

// `fastTop` is passed rather than read off the spec because whether the fast
// cap is available is the caller's business — a gallop needs stamina and a mode
// that draws one, and the farmhand just needs to be moving.
function paceTop(spec, fastTop) {
  if (holding(WALK_KEYS) && spec.walkTop) return spec.walkTop;
  return fastTop ?? spec.top;
}

function readInput() {
  let dx = 0;
  let dy = 0;
  if (keys.has('ArrowLeft') || keys.has('KeyA')) dx -= 1;
  if (keys.has('ArrowRight') || keys.has('KeyD')) dx += 1;
  if (keys.has('ArrowUp') || keys.has('KeyW')) dy -= 1;
  if (keys.has('ArrowDown') || keys.has('KeyS')) dy += 1;
  if (dx && dy) {
    const inv = Math.SQRT1_2;
    dx *= inv;
    dy *= inv;
  }
  return { dx, dy };
}

// --- shared actor helpers --------------------------------------------------
//
// Both actors accelerate, clamp, coast and pick a gait from their speed the
// same way; only the numbers differ. Factored out when the farmhand arrived,
// because a second copy of it would have been a second place for the gait
// thresholds to drift away from the clip table.

function steer(actor, spec, dx, dy, dt, maxSpeed) {
  if (dx || dy) {
    actor.vx += dx * spec.accel * dt;
    actor.vy += dy * spec.accel * dt;
    const sp = Math.hypot(actor.vx, actor.vy);
    if (sp > maxSpeed) {
      actor.vx = (actor.vx / sp) * maxSpeed;
      actor.vy = (actor.vy / sp) * maxSpeed;
    }
  } else {
    const sp = Math.hypot(actor.vx, actor.vy);
    const drop = Math.min(sp, FRICTION * dt);
    if (sp > 0) {
      actor.vx -= (actor.vx / sp) * drop;
      actor.vy -= (actor.vy / sp) * drop;
    }
  }
}

function moveWithin(actor, dt) {
  actor.x = Math.max(BOUNDS.x0, Math.min(BOUNDS.x1, actor.x + actor.vx * dt));
  actor.y = Math.max(BOUNDS.y0, Math.min(BOUNDS.y1, actor.y + actor.vy * dt));
}

// Facing from the dominant axis, with the hysteresis the mounted pair uses so
// diagonals do not flicker. The rule itself is in sprites.js because the
// animation debugger drives a sprite round a figure-8 to inspect it; what
// stays here is the bookkeeping only an actor has — `lastHorizontal`, which is
// the facing a side-on-only clip falls back to. No turn transitions here: the
// farmhand and the loose horse are each one sprite, and the artwork only draws
// a pivot for the pair, where the handler and the horse have to disagree for a
// moment.
function faceFromVelocity(actor, speed) {
  const want = facingFrom(actor.vx, actor.vy, speed);
  if (!want) return;
  actor.facing = want;
  if (PROFILE.has(want)) actor.lastHorizontal = want;
}

// Advance one clip. Returns true on the tick a non-looping clip finished, which
// is all the callers need to know.
function advance(actor, clip, dt) {
  actor.clock += dt;
  const step = 1 / clip.fps;
  let ended = false;
  while (actor.clock >= step) {
    actor.clock -= step;
    if (actor.frame + 1 < clip.frames) {
      actor.frame += 1;
    } else if (clip.loop) {
      actor.frame = 0;
    } else {
      actor.frame = 0;
      actor.clock = 0;
      ended = true;
      break;
    }
  }
  return ended;
}

// The horse's frame clock, plus what a finished one-shot means. Shared by both
// paths, led and steered, so a clip that chains through `next` behaves the same
// whether the horse is following you or carrying you — the pet gesture reaches
// it from one and grazing from both.
function advanceHorse(clip, dt) {
  if (!advance(horse, clip, dt)) return;
  // A one-shot finished. What that means is the clip's business, not this
  // function's: a jump lands, a turn commits the facing it was heading for, and
  // a clip with `next` hands off to the one the artwork says follows it.
  horse.jumping = false;
  if (horse.turning) horse.turning.done = true;
  if (horse.action === 'graze_up') horse.lifting = false;
  horse.chain = clip.next ?? null;
}

// The rail only blocks north/south crossing, never travel along it, so the ends
// stay open: jumping is the fast line, going round is the safe one. It applies
// to whoever is walking into it — the farmhand cannot duck under it either, and
// cannot jump it, which is what makes the horse worth having.
//
// Scoring a clear is mounted-only by construction: `airborne` is only ever true
// for the pair, since the loose horse never jumps and the farmhand has no jump
// clip at all.
function blockRail(actor, prevY, airborne) {
  if (!(actor.x > RAIL.x0 - 4 && actor.x < RAIL.x1 + 4)) return;
  const crossed = Math.sign(prevY - RAIL.y) !== Math.sign(actor.y - RAIL.y);
  if (crossed && airborne) {
    score += CLEAR_BONUS;
    clears += 1;
    dispatchEvent(new CustomEvent('pasture:clear', { detail: { clears, score } }));
  } else if (!airborne && Math.abs(actor.y - RAIL.y) < RAIL_BLOCK) {
    // Refuse the fence: push back out to the side we came from.
    //
    // This is a band the actor is ejected from, not a line it is stopped at.
    // Stopping at the line clamped y to exactly RAIL.y - RAIL_BLOCK, and on the
    // next frame the "did we cross it" test read `178 < 178` and let the horse
    // straight through on the second push.
    actor.y = prevY <= RAIL.y ? RAIL.y - RAIL_BLOCK : RAIL.y + RAIL_BLOCK;
    actor.vy = 0;
  }
}

// Apples are collected on contact by whoever has hands free: the farmhand on
// their own, or the handler leading the horse. Never from the saddle.
function collectApples(actor) {
  for (let i = apples.length - 1; i >= 0; i--) {
    const a = apples[i];
    if (Math.hypot(a.x - actor.x, a.y - actor.y) < PICKUP_RADIUS) {
      apples.splice(i, 1);
      score += 1;
      horse.stamina = Math.min(STAMINA_MAX, horse.stamina + APPLE_STAMINA);
      spawnApple();
      dispatchEvent(new CustomEvent('pasture:apple', { detail: { score } }));
    }
  }
}

const gaitFor = (spec, speed) => spec.gaits.find(([limit]) => speed < limit)[1];
const near = (a, b, r) => Math.hypot(a.x - b.x, a.y - b.y) < r;

function setMode(next) {
  if (horse.mode === next) return;
  horse.mode = next;
  horse.jumping = false;
  horse.turning = null;
  horse.chain = null;
  horse.frame = 0;
  horse.clock = 0;
  dispatchEvent(new CustomEvent('pasture:mode', { detail: { mode: next } }));
}

// --- the loose horse -------------------------------------------------------
//
// Four behaviours and a timer. It is deliberately not a planner: the horse has
// to read as a horse for thirty seconds at a time, and what sells that is the
// pauses between moves, not the moves.

function wanderTarget() {
  return {
    tx: BOUNDS.x0 + 20 + Math.random() * (BOUNDS.x1 - BOUNDS.x0 - 40),
    ty: BOUNDS.y0 + 20 + Math.random() * (BOUNDS.y1 - BOUNDS.y0 - 40),
  };
}

function pickBehaviour() {
  const ai = horse.ai;
  // A happy horse frolics; that is the only thing mood gates, and it is what
  // makes petting worth doing beyond the number on the HUD.
  if (horse.mood >= MOOD_FROLIC && Math.random() < 0.45) {
    Object.assign(ai, { state: 'frolic', timer: 2.2 + Math.random() * 2.2,
                        ...wanderTarget() });
    return;
  }
  const roll = Math.random();
  if (roll < 0.42) {
    // Grazing is authored on the side-on blocks only, so the horse turns side
    // on to do it rather than the clip silently falling back to idle.
    Object.assign(ai, { state: 'graze', timer: 3 + Math.random() * 4 });
  } else if (roll < 0.62) {
    Object.assign(ai, { state: 'idle', timer: 1.5 + Math.random() * 3 });
  } else {
    Object.assign(ai, { state: 'wander', timer: 3 + Math.random() * 3,
                        ...wanderTarget() });
  }
}

function updateHorseAi(dt) {
  const ai = horse.ai;
  const spec = MODES.loose;
  ai.timer -= dt;

  let dx = 0;
  let dy = 0;
  let maxSpeed = spec.top;

  if (ai.state === 'come') {
    // Called. Head for the player and stop at arm's length rather than walking
    // into them, then hold still so the prompt stays reachable.
    //
    // The rail has to be steered around rather than walked into: a loose horse
    // never jumps, so calling it from the far side otherwise pins it against
    // the fence until the timer runs out and it wanders off. Aim at the nearer
    // open end first, then at the player.
    let tx = player.x;
    let ty = player.y;
    const split = Math.sign(horse.y - RAIL.y) !== Math.sign(player.y - RAIL.y);
    if (split && horse.x > RAIL.x0 - 16 && horse.x < RAIL.x1 + 16) {
      tx = (horse.x - RAIL.x0 < RAIL.x1 - horse.x) ? RAIL.x0 - 24 : RAIL.x1 + 24;
      ty = horse.y;
    }
    const d = Math.hypot(tx - horse.x, ty - horse.y);
    const reached = Math.hypot(player.x - horse.x, player.y - horse.y);
    if (reached > COME_STOP && d > 1) {
      dx = (tx - horse.x) / d;
      dy = (ty - horse.y) / d;
      maxSpeed = horse.mood >= MOOD_FROLIC ? spec.gallopTop * 0.7 : spec.top;
      ai.timer = Math.max(ai.timer, 0.4);
    } else if (ai.timer <= 0) {
      Object.assign(ai, { state: 'idle', timer: 2.5 });
    }
  } else if (ai.state === 'wander' || ai.state === 'frolic') {
    const d = Math.hypot(ai.tx - horse.x, ai.ty - horse.y);
    if (d > 8) {
      dx = (ai.tx - horse.x) / d;
      dy = (ai.ty - horse.y) / d;
      if (ai.state === 'frolic') maxSpeed = spec.gallopTop;
    } else if (ai.state === 'frolic' && ai.timer > 0) {
      Object.assign(ai, wanderTarget());        // keep running, new heading
    } else {
      ai.timer = Math.min(ai.timer, 0);
    }
  }

  if (ai.timer <= 0 && ai.state !== 'come') pickBehaviour();

  steer(horse, spec, dx, dy, dt, maxSpeed);
  const prevY = horse.y;
  moveWithin(horse, dt);
  blockRail(horse, prevY, false);

  const speed = Math.hypot(horse.vx, horse.vy);
  faceFromVelocity(horse, speed);

  const grazing = ai.state === 'graze' && speed < 4;
  if (grazing) {
    horse.vx = 0;
    horse.vy = 0;
    horse.facing = horse.lastHorizontal;
    horse.mood = Math.min(MOOD_MAX, horse.mood + MOOD_GRAZE * dt);
  }
  horse.mood = Math.max(0, horse.mood - MOOD_DECAY * dt);

  // Grazing enters on the head going down and the metadata chains it from
  // there; stopping is its own one-shot, so the head comes back up instead of
  // snapping. `lifting` is set the moment the horse stops grazing and cleared
  // when graze_up finishes, which is what keeps the exit from being cut off by
  // whatever the AI decided to do next.
  if (grazing) horse.lifting = false;
  else if (horse.action === 'graze_down' || horse.action === 'graze') {
    horse.lifting = true;
  }

  const previous = horse.action;
  if (grazing) horse.action = horse.chain ?? 'graze_down';
  else if (horse.lifting) horse.action = 'graze_up';
  else horse.action = gaitFor(spec, speed);
  if (!grazing && !horse.lifting) horse.chain = null;
  if (!hasClip(coat(), 'loose', horse.saddled, horse.facing, horse.action)) {
    horse.action = 'idle';
    horse.lifting = false;
    horse.chain = null;
  }
  if (horse.action !== previous) {
    horse.frame = 0;
    horse.clock = 0;
  }
  const clip = clipFor(coat(), 'loose', horse.saddled, horse.facing,
                       horse.action);
  if (advance(horse, clip, dt)) {
    if (horse.action === 'graze_up') horse.lifting = false;
    horse.chain = clip.next ?? null;
  }
}

function updatePlayer(dt) {
  const { dx, dy } = readInput();
  const running = holding(FAST_KEYS) && (dx || dy);
  steer(player, PLAYER, dx, dy, dt, paceTop(PLAYER, running ? PLAYER.runTop : null));
  const prevY = player.y;
  moveWithin(player, dt);
  blockRail(player, prevY, false);

  const speed = Math.hypot(player.vx, player.vy);
  faceFromVelocity(player, speed);

  const previous = player.action;
  const gait = gaitFor(PLAYER, speed);
  // The character sheet draws walking and running twice, once free and once
  // with the arm out holding a rope. Which pair applies is the whole difference
  // between wandering over to a horse and leading one, and it is the only thing
  // the farmhand's own animation needs to know about the mode.
  player.action = horse.mode === 'lead' && gait !== 'idle' ? `lead_${gait}` : gait;
  if (player.action !== previous) {
    player.frame = 0;
    player.clock = 0;
  }
  if (character) advance(player, clipOf(character, player.facing, player.action), dt);
}

// Taking the rope or the reins. Mounting puts you inside the pair sprite, so
// the horse stays where it stood and you vanish into it. Taking the rope no
// longer does that: you keep your own position and step round to the head, the
// side the pack's own leading artwork puts the handler on, near enough that the
// rope starts slack. Picking up a rope should not jerk the horse.
function attach(mode) {
  horse.vx = 0;
  horse.vy = 0;
  horse.ai.state = 'idle';
  setMode(mode);
  if (mode === 'lead' && !horse.petHold) {
    const at = leadStart(horse);
    player.x = Math.max(BOUNDS.x0, Math.min(BOUNDS.x1, at.x));
    player.y = Math.max(BOUNDS.y0, Math.min(BOUNDS.y1, at.y));
    player.vx = 0;
    player.vy = 0;
    restRope();
  }
  // Petting a loose horse borrows the pair sprite, so it is the one case where
  // taking the rope does put you inside it.
  horse.petting = mode === 'lead' && horse.petHold;
}

// Lay the chain straight between the two anchors. Called whenever either end
// teleports — taking the rope, and leaving the pet gesture, which swaps the
// sprite under it — because a chain integrated from where it used to be whips
// across the screen.
function restRope() {
  const h = halterAt(horse);
  const d = handAt(player);
  placeRope(rope, h.x, h.y, d.x, d.y);
  lead.taut = 0;
  lead.dist = Math.hypot(d.x - h.x, d.y - h.y);
}

// Letting go. From the saddle, or from the pet gesture, you are inside the pair
// sprite and have to be put somewhere — out on the side it faces away from, so
// the released horse is not standing on top of you. Dropping the rope while
// leading needs none of that: you are already a separate actor standing where
// you stood.
function release() {
  if (horse.mode !== 'lead' || horse.petting) {
    const offset = { east: [-22, 6], west: [22, 6], south: [20, -6], north: [20, 6] };
    const [ox, oy] = offset[horse.facing] ?? [22, 6];
    player.x = Math.max(BOUNDS.x0, Math.min(BOUNDS.x1, horse.x + ox));
    player.y = Math.max(BOUNDS.y0, Math.min(BOUNDS.y1, horse.y + oy));
    player.vx = 0;
    player.vy = 0;
    player.facing = horse.facing === 'east' ? 'east' : 'west';
  }
  horse.petting = false;
  Object.assign(horse.ai, { state: 'idle', timer: 1.4 });
  setMode('loose');
}

function updateLoose(dt) {
  const petting = keys.has('KeyP') && near(player, horse, REACH);

  if (petting) {
    // Petting a loose horse is the pair sprite: the artwork draws the two of
    // you together and there is no version of it with the farmhand separate.
    // So it takes the rope for as long as you hold P, and gives it back after.
    horse.petHold = true;
    attach('lead');
    return;
  }
  if (pressed.has('KeyF')) {
    Object.assign(horse.ai, { state: 'come', timer: 6 });
    dispatchEvent(new CustomEvent('pasture:call'));
  }
  if (near(player, horse, REACH)) {
    if (pressed.has('KeyL')) attach('lead');
    else if (pressed.has('KeyM')) attach('ride');
  }

  updatePlayer(dt);
  updateHorseAi(dt);
  collectApples(player);
}

// On foot: you are steered, the horse is not. Nothing here reads the input for
// the horse at all, which is the point — it moves because of where you are and
// how much rope is left, and that is what stops it snapping into place behind
// you when you turn.
function updateLead(dt) {
  if (pressed.has('KeyM') && !horse.jumping) {
    setMode('ride');
    return;
  }
  if (pressed.has('KeyL')) {
    release();
    return;
  }

  updatePlayer(dt);
  collectApples(player);

  // The rope does the work. `followLead` only ever accelerates the horse — the
  // hard limit is applied after it has been integrated, so being inside the
  // fence wins over the rope rather than the other way round.
  const state = followLead(horse, player, dt);
  lead.taut = state.taut;
  lead.dist = state.dist;

  // No friction step. `followLead` steers the horse's velocity toward a target
  // pace — zero included — so a second force taking speed away would be the two
  // of them fighting, which is exactly what made it stutter.

  const prevY = horse.y;
  horse.x = Math.max(BOUNDS.x0, Math.min(BOUNDS.x1, horse.x + horse.vx * dt));
  horse.y = Math.max(BOUNDS.y0, Math.min(BOUNDS.y1, horse.y + horse.vy * dt));
  blockRail(horse, prevY, false);
  if (clampToRope(horse, player, BOUNDS)) {
    // The rope ran out. Kill the component of the horse's velocity that was
    // taking it further away, or it spends every frame being clamped back and
    // reads as juddering against an invisible wall.
    const along = horse.vx * state.ux + horse.vy * state.uy;
    if (along < 0) {
      horse.vx -= state.ux * along;
      horse.vy -= state.uy * along;
    }
  }

  const want = leadFacing(horse, lead);
  if (want && want !== horse.facing) {
    horse.facing = want;
    if (PROFILE.has(want)) horse.lastHorizontal = want;
  }

  // Grazing is the horse's own idea now: left alone on a slack rope it puts its
  // head down, and it lifts it the moment the rope asks it to move. Holding G
  // is gone from this mode — you are not steering it, so telling it to eat is
  // not yours to do either.
  const settled = Math.hypot(horse.vx, horse.vy) < 4 && lead.taut === 0;
  if (settled && PROFILE.has(horse.facing)) {
    horse.grazeWish = Math.min(2.2, (horse.grazeWish ?? 0) + dt);
  } else {
    horse.grazeWish = 0;
  }
  const grazing = horse.grazeWish >= 2.2;
  if (grazing) horse.lifting = false;
  else if (horse.action === 'graze_down' || horse.action === 'graze') {
    horse.lifting = true;
  }

  if (grazing) {
    horse.stamina = Math.min(STAMINA_MAX, horse.stamina + REGEN_GRAZE * dt);
    horse.mood = Math.min(MOOD_MAX, horse.mood + MOOD_GRAZE * dt);
  }

  const speed = Math.hypot(horse.vx, horse.vy);
  horse.stamina = Math.min(
    STAMINA_MAX, horse.stamina + (speed < 4 ? REGEN_IDLE : REGEN_MOVE) * dt,
  );

  const previous = horse.action;
  if (grazing) horse.action = horse.chain ?? 'graze_down';
  else if (horse.lifting) horse.action = 'graze_up';
  else horse.action = gaitFor(LEAD_HORSE, speed);
  if (!grazing && !horse.lifting) horse.chain = null;

  if (!hasClip(coat(), spriteMode(), horse.saddled, horse.facing, horse.action)) {
    horse.action = 'idle';
    horse.lifting = false;
  }
  const clip = clipFor(coat(), spriteMode(), horse.saddled, horse.facing, horse.action);
  if (horse.action !== previous) {
    horse.frame = 0;
    horse.clock = 0;
  }
  advanceHorse(clip, dt);

  const h = halterAt(horse);
  const d = handAt(player);
  stepRope(rope, h.x, h.y, d.x, d.y, dt);
}

function update(dt) {
  if (pressed.has('KeyC') && coats.length) {
    coatIndex = (coatIndex + 1) % coats.length;
  }
  if (state === 'over') {
    if (pressed.has('KeyR')) resetRound();
    pressed.clear();
    return;
  }

  timeLeft -= dt;
  if (timeLeft <= 0) {
    timeLeft = 0;
    state = 'over';
    best = Math.max(best, score);
    dispatchEvent(new CustomEvent('pasture:roundover', { detail: { score, clears, best } }));
  }

  // Saddle up. Safe at any moment, mid-jump included: the saddled sheets are
  // the same clips on different artwork, so the frame the horse is on stays
  // valid and the arc is not interrupted. Refused only for a coat that has no
  // saddled sheet, rather than silently drawing a bare horse.
  if (pressed.has('KeyT') && coat() && hasSaddle(coat())) {
    horse.saddled = !horse.saddled;
    dispatchEvent(new CustomEvent('pasture:tack', {
      detail: { saddled: horse.saddled },
    }));
  }

  // Loose is a different game: two actors, neither of them a pair sprite, and
  // nothing below this line applies to it.
  if (horse.mode === 'loose') {
    updateLoose(dt);
    pressed.clear();
    return;
  }

  // On foot is two actors too, and takes the same exit — except while petting,
  // which is the pair sprite and therefore the single-actor path below.
  //
  // The gesture wants you standing at the head, which is also what it draws, so
  // the condition and the artwork agree rather than the condition being a rule
  // you have to learn. Leaving it re-lays the rope: the sprite swapped
  // underneath, so both anchors moved.
  if (horse.mode === 'lead') {
    // Petting a loose horse borrowed the rope for the gesture, so releasing P
    // gives it back rather than leaving you silently leading it. This has to be
    // handled here: the steered path below used to own it, and a led horse no
    // longer goes through the steered path at all.
    if (horse.petHold && !keys.has('KeyP')) {
      horse.petHold = false;
      release();
      pressed.clear();
      return;
    }
    const still = Math.hypot(player.vx, player.vy) < 12;
    const wants = keys.has('KeyP') && still &&
      lead.dist <= REACH && !horse.jumping;
    if (wants !== horse.petting) {
      horse.petting = wants;
      horse.chain = null;
      horse.frame = 0;
      horse.clock = 0;
      if (!wants) restRope();
    }
    if (!horse.petting) {
      updateLead(dt);
      pressed.clear();
      return;
    }
    horse.vx = 0;
    horse.vy = 0;
    player.vx = 0;
    player.vy = 0;
  }

  // Mount / dismount. Blocked mid-jump: the arc is committed.
  if (pressed.has('KeyM') && !horse.jumping) {
    setMode(horse.mode === 'ride' ? 'lead' : 'ride');
  }
  // Let go of the rope. Only from the ground and only on foot — you cannot drop
  // the reins mid-jump, and stepping off a moving horse is not an animation the
  // pack draws.
  if (pressed.has('KeyL') && horse.mode === 'lead' && !horse.jumping) {
    release();
    pressed.clear();
    return;
  }

  const mode = MODES[horse.mode];

  const { dx, dy } = readInput();
  const moving = dx !== 0 || dy !== 0;

  // Standing actions. Graze exists only on the side-on blocks of both sheets,
  // so facing snaps to the last horizontal direction; pet is authored in all
  // four directions, so it uses whatever way you are already facing.
  const wantsPet = mode.canPet && keys.has('KeyP') && !moving && !horse.jumping;
  // Holding P on a loose horse is what took the rope in the first place, so
  // letting go gives it back rather than leaving you silently leading it.
  if (horse.petHold && !keys.has('KeyP')) {
    horse.petHold = false;
    release();
    pressed.clear();
    return;
  }
  const wantsGraze = !wantsPet && keys.has('KeyG') && !moving && !horse.jumping;
  const standing = wantsPet || wantsGraze;
  if (standing) {
    horse.vx = 0;
    horse.vy = 0;
    if (wantsGraze) horse.facing = horse.lastHorizontal;
    horse.stamina = Math.min(
      STAMINA_MAX, horse.stamina + (wantsPet ? REGEN_PET : REGEN_GRAZE) * dt,
    );
    if (wantsPet) horse.mood = Math.min(MOOD_MAX, horse.mood + MOOD_PET * dt);
  }

  // Take-off. Direction and speed are locked for the whole clip: the jump
  // sprite is a committed arc, so letting the player steer mid-air would slide
  // the horse sideways against its own animation.
  const launchSpeed = Math.hypot(horse.vx, horse.vy);
  if (
    mode.canJump && pressed.has('Space') && !horse.jumping &&
    launchSpeed >= JUMP_MIN_SPEED && horse.stamina >= JUMP_COST
  ) {
    horse.jumping = true;
    horse.frame = 0;
    horse.clock = 0;
    horse.stamina -= JUMP_COST;
    horse.jumpDir = { x: horse.vx / launchSpeed, y: horse.vy / launchSpeed };
  }

  const galloping = mode.canGallop && moving && horse.stamina > 0 &&
    holding(FAST_KEYS) && !holding(WALK_KEYS);
  const maxSpeed = paceTop(mode, galloping ? mode.gallopTop : null);

  if (horse.jumping) {
    horse.vx = horse.jumpDir.x * JUMP_SPEED;
    horse.vy = horse.jumpDir.y * JUMP_SPEED;
  } else if (moving && !standing) {
    horse.vx += dx * mode.accel * dt;
    horse.vy += dy * mode.accel * dt;
    const s = Math.hypot(horse.vx, horse.vy);
    if (s > maxSpeed) {
      horse.vx = (horse.vx / s) * maxSpeed;
      horse.vy = (horse.vy / s) * maxSpeed;
    }
  } else if (!standing) {
    const s = Math.hypot(horse.vx, horse.vy);
    const drop = Math.min(s, FRICTION * dt);
    if (s > 0) {
      horse.vx -= (horse.vx / s) * drop;
      horse.vy -= (horse.vy / s) * drop;
    }
  }

  const prevY = horse.y;
  horse.x = Math.max(BOUNDS.x0, Math.min(BOUNDS.x1, horse.x + horse.vx * dt));
  horse.y = Math.max(BOUNDS.y0, Math.min(BOUNDS.y1, horse.y + horse.vy * dt));

  const airborne = horse.jumping &&
    isAirborne(clipFor(coat(), spriteMode(), horse.saddled, horse.facing, 'jump'),
               horse.frame);
  blockRail(horse, prevY, airborne);

  const speed = Math.hypot(horse.vx, horse.vy);

  if (galloping) {
    horse.stamina = Math.max(0, horse.stamina - DRAIN_GALLOP * dt);
  } else if (!standing) {
    horse.stamina = Math.min(
      STAMINA_MAX, horse.stamina + (speed < 4 ? REGEN_IDLE : REGEN_MOVE) * dt,
    );
  }

  // Facing from the dominant axis, with hysteresis so diagonals don't flicker.
  //
  // Leaving the side-on view does not take effect at once: the leading sheets
  // draw the handler pivoting while the horse is still in profile and holding
  // its stride, so the new facing is held in `turning` and committed when that
  // clip ends. Every other change — into profile, or east<->west — is immediate,
  // because the artwork draws no transition for it.
  // A turn that reached its last frame commits here, before anything reads the
  // facing, so `facing` and `action` are never inconsistent inside one frame.
  if (horse.turning && horse.turning.done) {
    horse.facing = horse.turning.to;
    horse.turning = null;
  }
  if (speed > 4 && !standing && !horse.turning) {
    let want = null;
    if (Math.abs(horse.vx) > Math.abs(horse.vy) * 1.15) {
      want = horse.vx > 0 ? 'east' : 'west';
    } else if (Math.abs(horse.vy) > Math.abs(horse.vx) * 1.15) {
      want = horse.vy > 0 ? 'south' : 'north';
    }
    if (want && want !== horse.facing) {
      const gait = mode.gaits.find(([limit]) => speed < limit)[1];
      const turn = TURN_FOR[gait];
      const leavingProfile = PROFILE.has(horse.facing) && !PROFILE.has(want);
      if (leavingProfile && turn &&
          hasClip(coat(), spriteMode(), horse.saddled, horse.facing, turn)) {
        horse.turning = { to: want, clip: turn };
      } else {
        horse.facing = want;
        if (PROFILE.has(want)) horse.lastHorizontal = want;
      }
    }
  }
  // A standing action or a jump abandons a turn in progress: both re-pose the
  // pair, so finishing a pivot afterwards would play against the wrong frame.
  if ((standing || horse.jumping) && horse.turning) {
    horse.facing = horse.turning.to;
    horse.turning = null;
  }

  // Same shape as the loose horse's: holding G keeps the head down, releasing
  // it plays the lift rather than cutting to a gait mid-arc.
  if (wantsGraze) horse.lifting = false;
  else if (horse.action === 'graze_down' || horse.action === 'graze') {
    horse.lifting = true;
  }

  const previous = horse.action;
  if (horse.jumping) horse.action = 'jump';
  else if (horse.turning) horse.action = horse.turning.clip;
  // Petting enters on the lean-in and the metadata chains it from there, so
  // holding P plays nuzzle -> pet -> nuzzle instead of replaying the lean-out.
  else if (wantsPet) horse.action = horse.chain ?? 'nuzzle';
  else if (wantsGraze) horse.action = horse.chain ?? 'graze_down';
  else if (horse.lifting) horse.action = 'graze_up';
  else horse.action = mode.gaits.find(([limit]) => speed < limit)[1];
  if (!wantsPet && !wantsGraze && !horse.lifting) horse.chain = null;

  // A facing may not carry the requested clip (no graze north/south); fall back
  // rather than silently drawing the wrong row.
  if (!hasClip(coat(), spriteMode(), horse.saddled, horse.facing, horse.action)) {
    // Committing first matters: falling back to a looping idle while `turning`
    // is still set would strand the facing, since only a one-shot ending
    // commits it.
    if (horse.turning) {
      horse.facing = horse.turning.to;
      horse.turning = null;
    }
    horse.action = 'idle';
    horse.lifting = false;
  }

  const clip = clipFor(coat(), spriteMode(), horse.saddled, horse.facing, horse.action);
  if (horse.action !== previous && !horse.jumping) {
    horse.frame = 0;
    horse.clock = 0;
  }
  advanceHorse(clip, dt);

  // You cannot reach the ground from the saddle. That is the whole reason to
  // dismount.
  if (mode.canPickUp) collectApples(horse);

  pressed.clear();
}

function drawShadow(g, x, y, mode, facing, airborne) {
  const [dx, rx] = MODES[mode].shadow[facing];
  fillEllipse(g, x + dx, y - 1, Math.round(rx * (airborne ? 0.6 : 1)),
    airborne ? 2 : 3, airborne ? 'rgba(24,40,20,0.18)' : 'rgba(24,40,20,0.26)');
}

// What you can do from where you are standing. Shown in the world rather than
// the HUD because it is about a distance, and the only honest place to say
// "close enough" is next to the thing you are close to.
function drawPrompt(g) {
  if (!near(player, horse, REACH)) {
    g.font = '8px monospace';
    g.textAlign = 'center';
    g.textBaseline = 'alphabetic';
    g.fillStyle = 'rgba(242,233,200,0.75)';
    g.fillText('F  call', player.x, player.y - 40);
    return;
  }
  const label = 'P pet   L lead   M mount';
  g.font = '8px monospace';
  g.textAlign = 'center';
  g.textBaseline = 'alphabetic';
  const w = g.measureText(label).width + 8;
  const x = Math.max(w / 2 + 2, Math.min(NATIVE_W - w / 2 - 2, horse.x));
  const y = Math.max(34, horse.y - 62);
  g.fillStyle = 'rgba(20,26,20,0.72)';
  g.fillRect(x - w / 2, y - 9, w, 12);
  g.fillStyle = '#f2e9c8';
  g.fillText(label, x, y);
}

function drawHud(g) {
  g.fillStyle = 'rgba(20,26,20,0.72)';
  g.fillRect(0, 0, NATIVE_W, 14);

  g.font = '9px monospace';
  g.textBaseline = 'middle';
  g.textAlign = 'left';
  g.fillStyle = '#f2e9c8';
  g.fillText(`SCORE ${score}`, 6, 7);
  g.fillStyle = '#b9c9a8';
  g.fillText(`CLEARS ${clears}`, 68, 7);
  g.fillStyle = { ride: '#e0a13a', lead: '#7fbf4a', loose: '#8fb8d8' }[horse.mode];
  g.fillText(MODES[horse.mode].label, 142, 7);

  g.textAlign = 'center';
  g.fillStyle = timeLeft < 10 ? '#e5675a' : '#f2e9c8';
  g.fillText(`${Math.ceil(timeLeft)}s`, NATIVE_W / 2, 7);

  // Coat and tack share the right-hand slot: both are what the horse looks
  // like, and neither changes how it plays. Tack is drawn dimmer so the coat
  // name still reads first.
  g.textAlign = 'right';
  const tack = horse.saddled ? 'SADDLED' : 'BAREBACK';
  g.fillStyle = horse.saddled ? '#e0a13a' : '#7d8c70';
  g.fillText(tack, NATIVE_W - 6, 7);
  g.fillStyle = '#b9c9a8';
  g.fillText(coat() ? coat().label.toUpperCase() : '',
    NATIVE_W - 6 - g.measureText(`${tack} `).width, 7);

  const bw = 78;
  const bx = NATIVE_W / 2 - bw / 2;
  g.fillStyle = '#2a301f';
  g.fillRect(bx - 1, 17, bw + 2, 5);
  g.fillStyle = horse.stamina > 25 ? '#7fbf4a' : '#e0a13a';
  g.fillRect(bx, 18, Math.round((horse.stamina / STAMINA_MAX) * bw), 3);

  // Mood, under the stamina bar. It only does one thing — a happy horse
  // frolics — so the bar is marked at the threshold rather than left as a
  // number with no consequence.
  g.fillStyle = '#2a301f';
  g.fillRect(bx - 1, 23, bw + 2, 4);
  g.fillStyle = horse.mood >= MOOD_FROLIC ? '#d98fc0' : '#6e6e8a';
  g.fillRect(bx, 24, Math.round((horse.mood / MOOD_MAX) * bw), 2);
  g.fillStyle = 'rgba(242,233,200,0.6)';
  g.fillRect(bx + Math.round((MOOD_FROLIC / MOOD_MAX) * bw), 23, 1, 4);
}

function drawOverlay(g) {
  g.fillStyle = 'rgba(14,18,14,0.78)';
  g.fillRect(0, 0, NATIVE_W, NATIVE_H);
  g.textAlign = 'center';
  g.textBaseline = 'middle';
  g.fillStyle = '#f2e9c8';
  g.font = '16px monospace';
  g.fillText('TIME UP', NATIVE_W / 2, NATIVE_H / 2 - 30);
  g.font = '11px monospace';
  g.fillText(`SCORE  ${score}`, NATIVE_W / 2, NATIVE_H / 2 - 6);
  g.fillStyle = '#b9c9a8';
  g.fillText(`RAILS CLEARED  ${clears}`, NATIVE_W / 2, NATIVE_H / 2 + 12);
  g.fillText(`BEST  ${Math.max(best, score)}`, NATIVE_W / 2, NATIVE_H / 2 + 28);
  g.fillText('PRESS R TO RIDE AGAIN', NATIVE_W / 2, NATIVE_H / 2 + 52);
}

function render() {
  ctx.drawImage(field, 0, 0);

  const bob = Math.round(Math.sin(elapsed * 3) * 1);
  const airborne = horse.jumping &&
    isAirborne(clipFor(coat(), spriteMode(), horse.saddled, horse.facing, 'jump'),
               horse.frame);

  const drawables = [
    ...apples.map((a) => ({ y: a.y, draw: () => drawApple(ctx, a.x, a.y, bob) })),
    { y: RAIL.y, draw: () => drawRail(ctx, RAIL) },
    {
      // While airborne the horse sorts in front of the rail regardless of where
      // its feet are, otherwise it vanishes behind the top rail at the apex.
      y: airborne ? RAIL.y + 1 : horse.y,
      draw: () => {
        drawShadow(ctx, horse.x, horse.y, spriteMode(), horse.facing, airborne);
        if (coat()) {
          drawSprite(ctx, coat(), spriteMode(), horse.saddled, horse.facing,
            horse.action, horse.frame, horse.x, horse.y);
        }
        // The riderless sheets are the no-equipment ones, so a led horse is
        // wearing nothing and the halter has to be drawn. Four pixels, in the
        // two colours the pack's own halter uses. Not while petting: that
        // gesture's artwork draws no halter either.
        if (horse.mode === 'lead' && !horse.petting) {
          drawHalter(ctx, horse.x, horse.y, horse.facing);
        }
      },
    },
  ];
  // The farmhand is a separate sprite unless the horse is carrying you or you
  // are leaning in to pet it — the two cases the pack draws as one sprite with
  // both of you in it. They sort into the same list, so walking behind the
  // horse puts you behind it.
  const afoot = horse.mode === 'loose' ||
    (horse.mode === 'lead' && !horse.petting);
  if (afoot && character) {
    drawables.push({
      y: player.y,
      draw: () => {
        fillEllipse(ctx, player.x, player.y - 1, 5, 2, 'rgba(24,40,20,0.24)');
        const clip = clipOf(character, player.facing, player.action);
        const { w, h } = character.cell;
        ctx.drawImage(
          character.image,
          (clip.col0 + (player.frame % clip.frames)) * w, clip.row * h, w, h,
          Math.round(player.x) - character.origin.x,
          Math.round(player.y) - character.origin.y, w, h,
        );
      },
    });
  }
  drawables.sort((a, b) => a.y - b.y).forEach((d) => d.draw());

  // The rope goes on last rather than sorting with the actors. It spans the two
  // of them, so there is no one depth it belongs at, and at a pixel wide the
  // difference only shows facing away — where the rope leaves the top of the
  // horse's head and crosses nothing anyway.
  if (horse.mode === 'lead' && !horse.petting) drawRope(ctx, rope);

  if (horse.mode === 'loose') drawPrompt(ctx);

  drawHud(ctx);
  if (state === 'over') drawOverlay(ctx);

  vctx.clearRect(0, 0, view.width, view.height);
  vctx.drawImage(screen, 0, 0, view.width, view.height);
}

function resize() {
  const scale = Math.max(
    1,
    Math.floor(Math.min(innerWidth / NATIVE_W, (innerHeight - 96) / NATIVE_H)),
  );
  view.width = NATIVE_W * scale;
  view.height = NATIVE_H * scale;
  view.style.width = `${NATIVE_W * scale}px`;
  view.style.height = `${NATIVE_H * scale}px`;
  vctx.imageSmoothingEnabled = false;   // reset: resizing clears context state
}
addEventListener('resize', resize);

function fail(message) {
  const el = document.getElementById('error');
  el.innerHTML = `<strong>${message}</strong><br>` +
    'The game loads generated assets, not the raw pack. Run ' +
    '<code>python -m pixelasset.ingest</code> from the repo root to build ' +
    '<code>assets/production/</code>, then reload.';
  el.hidden = false;
  document.getElementById('loading').hidden = true;
}

let last = performance.now();
function loop(now) {
  const dt = Math.min(0.05, (now - last) / 1000);
  last = now;
  elapsed += dt;
  update(dt);
  render();
  requestAnimationFrame(loop);
}

Promise.all([loadCoats(), loadCharacter()])
  .then(([loaded, farmhand]) => {
    coats = loaded;
    character = farmhand;
    resize();
    resetRound();
    document.getElementById('loading').hidden = true;
    requestAnimationFrame(loop);
  })
  .catch((err) => fail(`Could not load sprites: ${err.message}`));

// Optional debug surface, enabled with ?debug=1. Used by the headless smoke
// test, and handy for tuning speeds by hand.
if (new URLSearchParams(location.search).has('debug')) {
  window.pasture = {
    horse,
    get apples() { return apples; },
    get score() { return score; },
    get clears() { return clears; },
    get state() { return state; },
    player,
    get saddled() { return horse.saddled; },
    get turning() { return horse.turning ? horse.turning.to : null; },
    get mood() { return horse.mood; },
    get ai() { return horse.ai.state; },
    get inReach() { return near(player, horse, REACH); },
    // The rope, for the smoke test. `taut` is what the horse actually reads —
    // the straight line between the anchors — not the cosmetic chain.
    get lead() {
      return {
        taut: lead.taut,
        dist: lead.dist,
        petting: horse.petting,
        rest: REST,
        span: maxSpan(REST),
      };
    },
    get assetId() {
      return coat() ? assetFor(coat(), spriteMode(), horse.saddled).id : null;
    },
    get characterId() { return character ? character.id : null; },
    get airborne() {
      return horse.jumping &&
        isAirborne(clipFor(coat(), spriteMode(), horse.saddled, horse.facing, 'jump'),
                   horse.frame);
    },
    rail: RAIL,
    // The suite runs longer than a round. When the clock runs out `update` stops
    // reading input, so every assertion after that point silently tests a game
    // that has stopped listening — which is a very hard way to fail. Debug only,
    // and the test says out loud when it uses it.
    keepRoundAlive() { timeLeft = ROUND_SECONDS; },
  };
}
