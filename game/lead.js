// Leading, as two actors and a rope between them.
//
// `lead` used to be one sprite with the horse, the handler and the rope all
// painted into it. That reads well standing still and badly the moment you turn:
// the whole assembly pivots and the horse jumps to its new side. So on foot now
// draws the riderless sheets for the horse and the character sheet for you, and
// the rope is an object (see rope.js).
//
// What that buys, beyond the turn: the horse has a position of its own, so it
// can lag, swing round you, and be somewhere you did not put it. What it costs
// is the pair artwork — which is why the pet gesture still uses it. game.js
// swaps back to the pair sprite for the length of that gesture.
//
// ANCHORS. The rope needs to know where the hand and the halter are, and the
// metadata cannot say: `anchors.points.feet` is bottom-centre of the cell, which
// on a pair sheet is neither actor — it lands at x=50 of 100 while the handler
// stands at x≈91. A per-facing subject anchor is the missing schema piece. Until
// it exists these are constants, measured off the sheets rather than guessed:
//
//   * The leading sheets put the halter 33px above the feet line in profile;
//     the riderless sheets put the head at 32px. Two independently composed
//     sheets agreeing to a pixel is what makes these numbers trustworthy.
//   * North is the odd one. Facing away the head is the top of the silhouette,
//     61px up rather than 44, so the rope leaves from much higher — and no
//     noseband is visible at all from behind, which is why nothing is drawn.
//
// They are constant per facing, not per frame, so the rope's end floats a pixel
// or two through a walk cycle. At native scale, under the rope's own sag, that
// is not visible; a per-frame anchor would mean new pipeline work for it.
import { facingFrom } from './sprites.js';
import { maxSpan, REST, ropeTension } from './rope.js';

export const HALTER_AT = {
  east: [24, -33],
  west: [-25, -33],
  south: [0, -29],
  north: [0, -52],
};

export const HAND_AT = {
  east: [3, -15],
  west: [-4, -15],
  south: [1, -11],
  north: [-1, -12],
};

// The riderless sheets are `Horses_no_equipment` — the horse is wearing nothing,
// because the only haltered sheets in the pack are the pair ones. So the halter
// is drawn: four pixels in the two colours the pack's own halter uses, #ebebeb
// and #515151, which are in the leading sheets and in neither the riderless nor
// the character ones. North draws none — you cannot see a noseband from behind.
const HALTER_PIXELS = {
  east: [[21, -34, 0], [24, -33, 1], [21, -31, 0], [24, -30, 1]],
  west: [[-22, -34, 0], [-25, -33, 1], [-22, -31, 0], [-25, -30, 1]],
  south: [[-2, -29, 0], [1, -29, 0], [-2, -27, 1], [1, -27, 1]],
  north: [],
};
const HALTER_COLOURS = ['#ebebeb', '#515151'];

export function halterAt(horse) {
  const [dx, dy] = HALTER_AT[horse.facing] ?? HALTER_AT.east;
  return { x: horse.x + dx, y: horse.y + dy };
}

export function handAt(player) {
  const [dx, dy] = HAND_AT[player.facing] ?? HAND_AT.east;
  return { x: player.x + dx, y: player.y + dy };
}

export function drawHalter(ctx, x, y, facing) {
  for (const [dx, dy, tone] of HALTER_PIXELS[facing] ?? []) {
    ctx.fillStyle = HALTER_COLOURS[tone];
    ctx.fillRect(Math.round(x) + dx, Math.round(y) + dy, 1, 1);
  }
}

// Where the handler stands when you first take the rope: ahead of the head, the
// side the pack's own artwork puts them on, and near enough that the rope starts
// slack. Taking the rope should not jerk the horse.
const START_AHEAD = 26;
const START_ASIDE = 6;

export function leadStart(horse) {
  const ahead = {
    east: [1, 0], west: [-1, 0], south: [0, 1], north: [0, -1],
  }[horse.facing] ?? [1, 0];
  const profile = horse.facing === 'east' || horse.facing === 'west';
  return {
    x: horse.x + ahead[0] * START_AHEAD,
    y: horse.y + ahead[1] * (profile ? 0 : START_AHEAD * 0.6) +
       (profile ? START_ASIDE : 0),
  };
}

// The horse follows at less than the rope's rest length when it is willing, so
// there is a band where it moves and the rope is still slack. That band is the
// whole feel: walk carefully and the horse comes with you on a loose rope.
const COMFORT = REST * 0.72;

// Below this the horse has no opinion about closing the gap; a deadzone, so a
// following horse settles instead of creeping.
const SETTLE = 4;

// A horse that does not want to come has to be pulled. Mood is the existing
// willingness scalar, so petting it is what buys you a horse that follows on
// slack rather than one you drag.
const WILLING_MOOD = 34;

const WALK_TOP = 40;    // top speed on a slack rope
const TROT_TOP = 92;    // top speed at full tension

// How hard the horse works to close a gap, in px/s of extra pace per px of gap.
const CLOSE_GAIN = 3;

export const LEAD_HORSE = {
  amble: 150,     // how fast it can change pace on its own
  drag: 460,      // how fast the rope can change it
  walk: WALK_TOP,
  trot: TROT_TOP,
  // Each band has to reach PAST the speed it describes, or the top of a band is
  // animated by the next one up. With walk's 40 against a band ending at 34, a
  // horse ambling on a fully slack rope animated as a trot — and at 92 it
  // animated as a gallop, which a led horse should never do at all.
  gaits: [
    [SETTLE, 'idle'],
    [WALK_TOP + 6, 'walk'],
    [TROT_TOP + 6, 'trot'],
    [Infinity, 'gallop'],
  ],
};

const clamp = (v, lo, hi) => (v < lo ? lo : v > hi ? hi : v);

// Move the horse for one frame of being led. Returns the rope's state, for the
// HUD and for whoever wants to know whether the rope is doing the work.
//
// Deliberately reads the straight line between the anchors rather than the
// chain: the chain is cosmetic, and a cosmetic object should not be able to push
// a character around.
//
// It owns the horse's velocity outright — there is no separate friction step.
// The first version added an impulse whenever the rope was loaded and let drag
// take it away whenever it was not, which is a bang-bang controller: the horse
// was yanked, overshot into the slack, stopped dead, and was yanked again. At
// walking pace that stutter was most of the motion — measured at 20 frames in 30
// spent at a standstill — and it got worse the slower you went, because a gentle
// pull spends proportionally longer in the slack. Two forces fighting over one
// velocity is the bug; one controller with a target is the fix.
export function followLead(horse, player, dt) {
  const halter = halterAt(horse);
  const hand = handAt(player);
  const dx = hand.x - halter.x;
  const dy = hand.y - halter.y;
  const dist = Math.hypot(dx, dy) || 1e-6;
  const ux = dx / dist;
  const uy = dy / dist;
  const taut = ropeTension(dist, REST);

  // A pull overrides an opinion: an unwilling horse still has to come once the
  // rope is tight. Below that, mood decides, which is what makes petting worth
  // doing — a happy horse follows on slack and a sulky one has to be dragged.
  const willing = taut > 0 || horse.mood >= WILLING_MOOD;
  const top = taut > 0
    ? WALK_TOP + (TROT_TOP - WALK_TOP) * taut
    : WALK_TOP;

  // Match the rate you are opening the gap at, and treat the gap itself as a
  // correction on top. Both halves matter:
  //
  //   * The pace is your velocity PROJECTED ON THE ROPE, not your speed. Walking
  //     away at 26 gives 26 and the horse holds station; walking across gives
  //     almost nothing, which is right — you are not leaving.
  //   * The correction is signed and the target is only floored at zero, so
  //     there is no band where the target snaps to nothing. An earlier version
  //     gated the whole target on `gap > SETTLE` and that was still bang-bang:
  //     inside the deadzone the horse braked to a standstill, the gap reopened,
  //     and it lurched. 18 frames in 30 at a dead stop, at walking pace.
  const gap = dist - COMFORT;
  const pace = player.vx * ux + player.vy * uy;
  const target = willing
    ? clamp(pace + (gap - SETTLE) * CLOSE_GAIN, 0, top)
    : 0;

  const rate = (taut > 0 ? LEAD_HORSE.drag : LEAD_HORSE.amble) * dt;
  horse.vx += clamp(ux * target - horse.vx, -rate, rate);
  horse.vy += clamp(uy * target - horse.vy, -rate, rate);

  return { taut, dist, halter, hand, ux, uy, target };
}

// The rope cannot stretch, so once the horse is as far away as the rope allows,
// it is moved — not accelerated. Applied after the horse has been integrated and
// clamped to the field, because being inside the fence wins over the rope.
//
// This is what separates a rope from elastic: without it a sprinting player
// trails the horse at whatever distance their speeds differ by, and then it
// snaps back.
export function clampToRope(horse, player, bounds) {
  const halter = halterAt(horse);
  const hand = handAt(player);
  const dx = hand.x - halter.x;
  const dy = hand.y - halter.y;
  const dist = Math.hypot(dx, dy);
  const span = maxSpan(REST);
  if (dist <= span) return false;

  const pull = dist - span;
  horse.x += (dx / dist) * pull;
  horse.y += (dy / dist) * pull;
  horse.x = Math.max(bounds.x0, Math.min(bounds.x1, horse.x));
  horse.y = Math.max(bounds.y0, Math.min(bounds.y1, horse.y));
  return true;
}

// The horse faces where it is going, with one exception: while the rope is taut
// and it is barely moving, it faces the pull instead. A horse being dragged
// looks at what is dragging it, and without this a planted horse keeps whatever
// facing it happened to stop in while the rope visibly hauls at its head.
export function leadFacing(horse, lead) {
  const speed = Math.hypot(horse.vx, horse.vy);
  return facingFrom(horse.vx, horse.vy, speed)
    ?? (lead.taut > 0.35 ? facingFrom(lead.ux, lead.uy, 100) : null);
}
