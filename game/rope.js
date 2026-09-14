// A lead rope, as an object in the world rather than pixels in a sprite.
//
// The pack draws the rope *into* the leading sheets: horse, handler and a short
// black line between them, baked at one fixed offset. That is why leading used
// to snap — there was no relative position to change, so turning rotated a
// rigid assembly and the horse teleported to its new side. The pair sheets even
// needed authored `turn_walk`/`turn_trot` clips to hide the worst of it.
//
// So the rope becomes a chain of points with both ends pinned: one to the
// handler's hand, one to the horse's halter. Nothing else. It carries no
// gameplay state of its own — the game reads the straight-line distance between
// the two anchors, not the chain — because a chain that had authority over the
// horse's position would let a visual artefact move a character.
//
// Two things it must get right to read as rope rather than elastic:
//
//   It only pulls.     Below its rest length a rope does nothing at all. That
//                      is the whole feel being chased here: move carefully and
//                      the rope hangs slack and the horse follows because it
//                      wants to, not because it is being dragged.
//   It cannot stretch. Past its rest length it is a hard constraint, not a
//                      spring. A spring lets a sprinting player stretch the
//                      rope to twice its length and then catapults the horse,
//                      which reads as a bungee cord.
//
// Position-based dynamics rather than implicit Verlet, so the sag does not
// change with frame rate: integrate, satisfy the constraints, then derive the
// velocity from where the points actually ended up. That last step is what
// makes the chain lose energy when it goes taut instead of ringing.

// Enough for visible sag at this scale and no more; the chain is redrawn as
// individual pixels, so segments finer than a couple of pixels are wasted.
export const SEGMENTS = 8;

// Rest length in world pixels. Measured, not guessed: on the pack's own leading
// sheets the handler stands about 41px from the cell centre in profile with the
// halter at about 20px, so the painted rope spans roughly 20px with the horse's
// head up. Doubling that gives the player somewhere to walk before the rope
// starts pulling, which is where the slack/taut distinction lives.
export const REST = 34;

// How far past rest the rope may be stretched before it is simply a hard limit.
// Small on purpose — this is the give in a rope's fibres, not travel.
export const STRETCH = 1.18;

const GRAVITY = 460;
const DRAG = 0.86;      // per 1/60s; deliberately lossy
const RELAX = 5;

export function createRope(rest = REST) {
  return {
    rest,
    segment: (rest * 1.06) / SEGMENTS,   // a touch longer than straight, so a
                                         // rope at rest still hangs
    points: Array.from({ length: SEGMENTS + 1 }, () => (
      { x: 0, y: 0, vx: 0, vy: 0 }
    )),
  };
}

// Lay the chain straight between the anchors and kill its motion. Used when the
// rope is first picked up, and after anything that teleports either end —
// without it the chain whips across the screen from wherever it used to be.
export function placeRope(rope, ax, ay, bx, by) {
  const last = rope.points.length - 1;
  rope.points.forEach((p, i) => {
    const t = i / last;
    p.x = ax + (bx - ax) * t;
    p.y = ay + (by - ay) * t;
    p.vx = 0;
    p.vy = 0;
  });
}

export function stepRope(rope, ax, ay, bx, by, dt) {
  const pts = rope.points;
  const last = pts.length - 1;
  const drag = DRAG ** (dt * 60);

  for (const p of pts) {
    p.ox = p.x;
    p.oy = p.y;
    p.vx *= drag;
    p.vy = p.vy * drag + GRAVITY * dt;
    p.x += p.vx * dt;
    p.y += p.vy * dt;
  }

  // Pinning inside the relaxation loop rather than once at the end: the ends
  // are the only points with authority, so every pass has to start from them or
  // the correction spreads outward from stale anchors and the chain lags a fast
  // hand by a visible margin.
  for (let k = 0; k < RELAX; k++) {
    pts[0].x = ax; pts[0].y = ay;
    pts[last].x = bx; pts[last].y = by;
    for (let i = 0; i < last; i++) {
      const a = pts[i];
      const b = pts[i + 1];
      const dx = b.x - a.x;
      const dy = b.y - a.y;
      const d = Math.hypot(dx, dy) || 1e-6;
      // Only ever pulls together. A segment shorter than its rest length is
      // slack, and slack is the point.
      if (d <= rope.segment) continue;
      const shift = ((d - rope.segment) / d) * 0.5;
      const ox = dx * shift;
      const oy = dy * shift;
      if (i > 0) { a.x += ox; a.y += oy; }
      if (i + 1 < last) { b.x -= ox; b.y -= oy; }
    }
  }
  pts[0].x = ax; pts[0].y = ay;
  pts[last].x = bx; pts[last].y = by;

  // Velocity from the actual displacement, constraints included. Integrating it
  // independently would let the chain keep the speed a constraint just removed,
  // which is what makes a verlet rope hum.
  for (const p of pts) {
    p.vx = (p.x - p.ox) / dt;
    p.vy = (p.y - p.oy) / dt;
  }
}

// 0 while there is slack, ramping to 1 as the rope comes up hard. The game
// reads this, never the chain: the horse follows the distance between hand and
// halter, so a wild frame in the cosmetic rope can never shove a character.
export function ropeTension(dist, rest = REST) {
  if (dist <= rest) return 0;
  return Math.min(1, (dist - rest) / (rest * (STRETCH - 1)));
}

// The furthest the two anchors may ever be. Applied as a position clamp on the
// horse, which is what stops the rope being elastic.
export function maxSpan(rest = REST) {
  return rest * STRETCH;
}

// PIPELINE.md §5: no subpixel placement. A stroked path would antialias and read
// as a foreign object laid over pixel art, so the chain is sampled and plotted
// as individual pixels — the same colour the pack's own painted rope uses.
export function drawRope(ctx, rope, colour = '#000000') {
  ctx.fillStyle = colour;
  const pts = rope.points;
  for (let i = 0; i < pts.length - 1; i++) {
    plotLine(ctx, pts[i], pts[i + 1]);
  }
}

function plotLine(ctx, a, b) {
  let x = Math.round(a.x);
  let y = Math.round(a.y);
  const x1 = Math.round(b.x);
  const y1 = Math.round(b.y);
  const dx = Math.abs(x1 - x);
  const dy = Math.abs(y1 - y);
  const sx = x < x1 ? 1 : -1;
  const sy = y < y1 ? 1 : -1;
  let err = dx - dy;
  // Bounded: a NaN anchor would otherwise spin here forever rather than drawing
  // a wrong rope, and a wrong rope is much easier to notice than a hang.
  for (let guard = 0; guard < 512; guard++) {
    ctx.fillRect(x, y, 1, 1);
    if (x === x1 && y === y1) return;
    const e2 = 2 * err;
    if (e2 > -dy) { err -= dy; x += sx; }
    if (e2 < dx) { err += dx; y += sy; }
  }
}
