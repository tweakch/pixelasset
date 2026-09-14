// Sprite loading.
//
// The game is the conformance test for the asset contract: if it can load and
// animate an asset, the metadata carried enough. That is a stronger claim than
// "it looks right", and it is what makes a hardcoded `frame_count: 1` visible.
//
// This reads the project's own metadata schema — the same document
// `stage_metadata` emits — rather than a bespoke shape. Three fields it relies
// on are the extension for directional animated assets, all optional in the
// schema so a static prop stays valid without them:
//
//   spritesheet {columns, rows, layout}   how to index the packed sheet
//   animations[].facing                   which way the clip faces
//   animations[].off_ground               inclusive frames off the ground
//
// Everything else — frame_size, anchors.points.feet, animations[].fps/loop —
// is the contract as it already stood.

const PRODUCTION = '../assets/production/';

function loadImage(src) {
  return new Promise((resolve, reject) => {
    const img = new Image();
    img.onload = () => resolve(img);
    img.onerror = () => reject(new Error(`could not load ${src}`));
    img.src = src;
  });
}

async function loadJson(src) {
  const res = await fetch(src);
  if (!res.ok) throw new Error(`${res.status} ${src}`);
  return res.json();
}

// Index `animations` by facing and name, resolving each to a concrete position
// in the packed sheet. The list is flat in the schema, which keeps a
// non-directional prop expressible; a renderer wants it two-level.
//
// Two layouts exist and both are real output:
//   row_per_animation  one clip per row, frame 0 at the left     (ingest)
//   single_strip       every frame in `order` sequence on row 0  (stage graph)
// Resolving to {row, col0} here means the draw path does not care which.
function indexClips(meta) {
  const layout = meta.spritesheet?.layout ?? 'single_strip';
  const byFacing = {};
  let cursor = 0;
  for (const anim of meta.animations) {
    const facing = anim.facing ?? 'none';
    const row = layout === 'row_per_animation' ? anim.row : 0;
    const col0 = layout === 'row_per_animation' ? 0 : cursor;
    cursor += anim.frames;
    (byFacing[facing] ??= {})[anim.name] = { ...anim, row, col0 };
  }
  return byFacing;
}

async function loadAsset(id) {
  const dir = `${PRODUCTION}${id}/`;
  const meta = await loadJson(`${dir}metadata.json`);
  const [w, h] = meta.frame_size;
  const [ox, oy] = meta.anchors.points.feet;

  return {
    id: meta.id,
    label: meta.name ?? meta.id,
    image: await loadImage(dir + (meta.spritesheet?.file ?? 'spritesheet.png')),
    cell: { w, h },
    origin: { x: ox, y: oy },
    clips: indexClips(meta),
    meta,
  };
}

export async function loadIndex() {
  return loadJson(`${PRODUCTION}index.json`);
}

export { loadAsset };

// Key for an asset whose family has only one tack, so the second level of
// `assets` is never empty.
const NO_TACK = 'none';

// Playable coats only. An asset without a `mode` has no riding or leading
// sheet — hay_bale and the single-facing horse_fox are props as far as the
// pasture is concerned, and cycling one in with C would break the draw path.
// The viewer shows everything; this deliberately does not.
//
// `assets` is two levels deep, not one: mode, then tack. Several assets of one
// coat now share a mode and differ only in what the horse is wearing, so keying
// on mode alone would quietly keep whichever the index listed last — the
// saddled sheets landing and the bareback ones vanishing. Both fields come off
// the manifest.
export async function loadCoats() {
  const index = await loadJson(`${PRODUCTION}index.json`);
  const byVariant = new Map();
  // Horses only. The manifest now carries a second family — the character is
  // the same producer and the same schema — and without this filter it grouped
  // in as a one-mode "coat" and the C cycle drew a farmhand as a horse.
  for (const entry of index.assets.filter((a) => a.mode && a.family === 'horse')) {
    if (!byVariant.has(entry.variant)) {
      byVariant.set(entry.variant, { id: entry.variant, label: entry.label, assets: {} });
    }
    const modes = byVariant.get(entry.variant).assets;
    (modes[entry.mode] ??= {})[entry.tack ?? NO_TACK] = entry.id;
  }

  return Promise.all([...byVariant.values()].map(async (coat) => {
    const assets = {};
    await Promise.all(Object.entries(coat.assets).flatMap(([mode, byTack]) => {
      assets[mode] = {};
      return Object.entries(byTack).map(async ([tack, id]) => {
        assets[mode][tack] = await loadAsset(id);
      });
    }));
    // The index labels each asset per mode ("Bay (Riding)"); the coat wants the
    // shared part, so take it from the variant id.
    const label = coat.label.replace(/\s*\(.*\)$/, '');
    return { ...coat, label, assets };
  }));
}

// The saddle toggle is a boolean, and the sheet it wants is not: mounted, bare
// means `bareback`; on foot it means `halter`, because a led horse still wears
// something. So this asks for the saddled tack by name and otherwise takes the
// one that is not it, rather than carrying the pack's vocabulary over here.
export function assetFor(coat, mode, saddled) {
  const byTack = coat.assets[mode];
  const tacks = Object.keys(byTack);
  const want = saddled ? 'saddled' : tacks.find((t) => t !== 'saddled');
  return byTack[want] ?? byTack[tacks[0]];
}

// Whether this coat has a saddled sheet for every mode. A coat built before the
// saddled sources existed has none, and the toggle skips it rather than drawing
// a bareback horse while the HUD claims a saddle.
export function hasSaddle(coat) {
  return Object.values(coat.assets).every((byTack) => 'saddled' in byTack);
}

// The character, as a bare asset rather than a coat: one subject, one mode, no
// tack and no variants, so none of the coat machinery applies to it.
export async function loadCharacter() {
  const index = await loadJson(`${PRODUCTION}index.json`);
  const entry = index.assets.find((a) => a.family === 'character');
  return entry ? loadAsset(entry.id) : null;
}

export function clipFor(coat, mode, saddled, facing, action) {
  const block = assetFor(coat, mode, saddled).clips[facing];
  return block[action] ?? block.idle;
}

// Same lookup against a bare asset, for consumers that are not mode-based.
export function clipOf(asset, facing, action) {
  const block = asset.clips[facing];
  return block[action] ?? Object.values(block)[0];
}

export function hasClip(coat, mode, saddled, facing, action) {
  return Boolean(assetFor(coat, mode, saddled).clips[facing][action]);
}

// `off_ground` is an inclusive pair of frame indices, measured at ingest from
// each frame's alpha bbox against the clip baseline. Collision therefore tracks
// the artwork and stays correct when fps changes.
export function isAirborne(clip, frame) {
  if (!clip.off_ground) return false;
  return frame >= clip.off_ground[0] && frame <= clip.off_ground[1];
}

// PIPELINE.md §5: no subpixel placement. Positions are rounded at draw time.
export function drawSprite(ctx, coat, mode, saddled, facing, action, frame, x, y) {
  const asset = assetFor(coat, mode, saddled);
  const clip = clipFor(coat, mode, saddled, facing, action);
  const { w, h } = asset.cell;
  const col = clip.col0 + (frame % clip.frames);
  ctx.drawImage(
    asset.image,
    col * w, clip.row * h, w, h,
    Math.round(x) - asset.origin.x, Math.round(y) - asset.origin.y, w, h,
  );
}

// Facing from a velocity, with hysteresis so a diagonal does not flicker
// between two facings frame by frame. Returns the facing to adopt, or null
// when the actor is too slow to have an opinion — a stopping sprite keeps the
// way it was pointing rather than snapping to whatever the last dying
// component of its velocity happened to be.
//
// It lives here rather than in game.js because the animation debugger drives a
// sprite round a figure-8 to look at exactly this: if the two had their own
// copies, the preview would eventually stop showing what the game does.
export const FACE_MIN_SPEED = 4;

export function facingFrom(vx, vy, speed = Math.hypot(vx, vy)) {
  if (speed <= FACE_MIN_SPEED) return null;
  if (Math.abs(vx) > Math.abs(vy) * 1.15) return vx > 0 ? 'east' : 'west';
  if (Math.abs(vy) > Math.abs(vx) * 1.15) return vy > 0 ? 'south' : 'north';
  return null;
}
