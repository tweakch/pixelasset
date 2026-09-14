// Pasture Run — a small demo that exercises the sample pack's animations.
//
// Two modes, and the whole game is built around the trade between them:
//
//   ON FOOT  you lead the horse on a rope. Slow, cannot jump the rail, but
//            you are the only one who can pick an apple off the ground.
//   RIDING   mounted. Fast, and the only way over the rail — but you cannot
//            reach the ground from the saddle.
//
// So you ride to reach the apples and dismount to collect them.

import {
  loadCoats, drawSprite, clipFor, hasClip, isAirborne,
} from './sprites.js';
import { buildField, drawApple, drawRail, fillEllipse, trackY } from './field.js';

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
  ride: {
    label: 'RIDING',
    accel: 420, top: 74, gallopTop: 152,
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
const coat = () => coats[coatIndex];

const horse = {
  x: NATIVE_W / 2,
  y: NATIVE_H / 2,
  vx: 0,
  vy: 0,
  mode: 'lead',
  facing: 'east',
  lastHorizontal: 'east',
  action: 'idle',
  frame: 0,
  clock: 0,
  stamina: STAMINA_MAX,
  jumping: false,
  jumpDir: { x: 1, y: 0 },
};

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
  Object.assign(horse, {
    x: NATIVE_W / 2, y: NATIVE_H / 2 - 34, vx: 0, vy: 0,
    mode: 'lead', facing: 'east', lastHorizontal: 'east',
    action: 'idle', frame: 0, clock: 0,
    stamina: STAMINA_MAX, jumping: false,
  });
  for (let i = 0; i < 6; i++) spawnApple();
  state = 'playing';
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

function setMode(next) {
  if (horse.mode === next) return;
  horse.mode = next;
  horse.jumping = false;
  horse.frame = 0;
  horse.clock = 0;
  dispatchEvent(new CustomEvent('pasture:mode', { detail: { mode: next } }));
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

  // Mount / dismount. Blocked mid-jump: the arc is committed.
  if (pressed.has('KeyM') && !horse.jumping) {
    setMode(horse.mode === 'ride' ? 'lead' : 'ride');
  }
  const mode = MODES[horse.mode];

  const { dx, dy } = readInput();
  const moving = dx !== 0 || dy !== 0;

  // Standing actions. Graze exists only on the side-on blocks of both sheets,
  // so facing snaps to the last horizontal direction; pet is authored in all
  // four directions, so it uses whatever way you are already facing.
  const wantsPet = mode.canPet && keys.has('KeyP') && !moving && !horse.jumping;
  const wantsGraze = !wantsPet && keys.has('KeyG') && !moving && !horse.jumping;
  const standing = wantsPet || wantsGraze;
  if (standing) {
    horse.vx = 0;
    horse.vy = 0;
    if (wantsGraze) horse.facing = horse.lastHorizontal;
    horse.stamina = Math.min(
      STAMINA_MAX, horse.stamina + (wantsPet ? REGEN_PET : REGEN_GRAZE) * dt,
    );
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
    (keys.has('ShiftLeft') || keys.has('ShiftRight'));
  const maxSpeed = galloping ? mode.gallopTop : mode.top;

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

  // The rail only blocks north/south crossing, never travel along it, so the
  // ends stay open: jumping is the fast line, riding around is the safe one.
  const overRail = horse.x > RAIL.x0 - 4 && horse.x < RAIL.x1 + 4;
  const airborne = horse.jumping &&
    isAirborne(clipFor(coat(), horse.mode, horse.facing, 'jump'), horse.frame);
  if (overRail) {
    const crossed = Math.sign(prevY - RAIL.y) !== Math.sign(horse.y - RAIL.y);
    if (crossed && airborne) {
      score += CLEAR_BONUS;
      clears += 1;
      dispatchEvent(new CustomEvent('pasture:clear', { detail: { clears, score } }));
    } else if (!airborne && Math.abs(horse.y - RAIL.y) < RAIL_BLOCK) {
      // Refuse the fence: push back out to the side we came from.
      //
      // This is a band the horse is ejected from, not a line it is stopped at.
      // Stopping at the line clamped y to exactly RAIL.y - RAIL_BLOCK, and on
      // the next frame the "did we cross it" test read `178 < 178` and let the
      // horse straight through on the second push.
      horse.y = prevY <= RAIL.y ? RAIL.y - RAIL_BLOCK : RAIL.y + RAIL_BLOCK;
      horse.vy = 0;
    }
  }

  const speed = Math.hypot(horse.vx, horse.vy);

  if (galloping) {
    horse.stamina = Math.max(0, horse.stamina - DRAIN_GALLOP * dt);
  } else if (!standing) {
    horse.stamina = Math.min(
      STAMINA_MAX, horse.stamina + (speed < 4 ? REGEN_IDLE : REGEN_MOVE) * dt,
    );
  }

  // Facing from the dominant axis, with hysteresis so diagonals don't flicker.
  if (speed > 4 && !standing) {
    if (Math.abs(horse.vx) > Math.abs(horse.vy) * 1.15) {
      horse.facing = horse.vx > 0 ? 'east' : 'west';
      horse.lastHorizontal = horse.facing;
    } else if (Math.abs(horse.vy) > Math.abs(horse.vx) * 1.15) {
      horse.facing = horse.vy > 0 ? 'south' : 'north';
    }
  }

  const previous = horse.action;
  if (horse.jumping) horse.action = 'jump';
  else if (wantsPet) horse.action = 'pet';
  else if (wantsGraze) horse.action = 'graze';
  else horse.action = mode.gaits.find(([limit]) => speed < limit)[1];

  // A facing may not carry the requested clip (no graze north/south); fall back
  // rather than silently drawing the wrong row.
  if (!hasClip(coat(), horse.mode, horse.facing, horse.action)) horse.action = 'idle';

  const clip = clipFor(coat(), horse.mode, horse.facing, horse.action);
  if (horse.action !== previous && !horse.jumping) {
    horse.frame = 0;
    horse.clock = 0;
  }
  horse.clock += dt;
  const step = 1 / clip.fps;
  while (horse.clock >= step) {
    horse.clock -= step;
    if (horse.frame + 1 < clip.frames) {
      horse.frame += 1;
    } else if (clip.loop) {
      horse.frame = 0;
    } else {
      horse.jumping = false;   // one-shot clip finished — land
      horse.frame = 0;
      horse.clock = 0;
      break;
    }
  }

  // Apples are collected on contact, but only on foot — you cannot reach the
  // ground from the saddle. That is the whole reason to dismount.
  if (mode.canPickUp) {
    for (let i = apples.length - 1; i >= 0; i--) {
      const a = apples[i];
      if (Math.hypot(a.x - horse.x, a.y - horse.y) < PICKUP_RADIUS) {
        apples.splice(i, 1);
        score += 1;
        horse.stamina = Math.min(STAMINA_MAX, horse.stamina + APPLE_STAMINA);
        spawnApple();
        dispatchEvent(new CustomEvent('pasture:apple', { detail: { score } }));
      }
    }
  }

  pressed.clear();
}

function drawShadow(g, x, y, mode, facing, airborne) {
  const [dx, rx] = MODES[mode].shadow[facing];
  fillEllipse(g, x + dx, y - 1, Math.round(rx * (airborne ? 0.6 : 1)),
    airborne ? 2 : 3, airborne ? 'rgba(24,40,20,0.18)' : 'rgba(24,40,20,0.26)');
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
  g.fillStyle = horse.mode === 'ride' ? '#e0a13a' : '#7fbf4a';
  g.fillText(MODES[horse.mode].label, 142, 7);

  g.textAlign = 'center';
  g.fillStyle = timeLeft < 10 ? '#e5675a' : '#f2e9c8';
  g.fillText(`${Math.ceil(timeLeft)}s`, NATIVE_W / 2, 7);

  g.textAlign = 'right';
  g.fillStyle = '#b9c9a8';
  g.fillText(coat() ? coat().label.toUpperCase() : '', NATIVE_W - 6, 7);

  const bw = 78;
  const bx = NATIVE_W / 2 - bw / 2;
  g.fillStyle = '#2a301f';
  g.fillRect(bx - 1, 17, bw + 2, 5);
  g.fillStyle = horse.stamina > 25 ? '#7fbf4a' : '#e0a13a';
  g.fillRect(bx, 18, Math.round((horse.stamina / STAMINA_MAX) * bw), 3);
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
    isAirborne(clipFor(coat(), horse.mode, horse.facing, 'jump'), horse.frame);

  const drawables = [
    ...apples.map((a) => ({ y: a.y, draw: () => drawApple(ctx, a.x, a.y, bob) })),
    { y: RAIL.y, draw: () => drawRail(ctx, RAIL) },
    {
      // While airborne the horse sorts in front of the rail regardless of where
      // its feet are, otherwise it vanishes behind the top rail at the apex.
      y: airborne ? RAIL.y + 1 : horse.y,
      draw: () => {
        drawShadow(ctx, horse.x, horse.y, horse.mode, horse.facing, airborne);
        if (coat()) {
          drawSprite(ctx, coat(), horse.mode, horse.facing, horse.action,
            horse.frame, horse.x, horse.y);
        }
      },
    },
  ];
  drawables.sort((a, b) => a.y - b.y).forEach((d) => d.draw());

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

loadCoats()
  .then((loaded) => {
    coats = loaded;
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
    get airborne() {
      return horse.jumping &&
        isAirborne(clipFor(coat(), horse.mode, horse.facing, 'jump'), horse.frame);
    },
    rail: RAIL,
  };
}
