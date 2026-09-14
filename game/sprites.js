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

// Playable coats only. An asset without a `mode` has no riding or leading
// sheet — hay_bale and the single-facing horse_fox are props as far as the
// pasture is concerned, and cycling one in with C would break the draw path.
// The viewer shows everything; this deliberately does not.
export async function loadCoats() {
  const index = await loadJson(`${PRODUCTION}index.json`);
  const byVariant = new Map();
  for (const entry of index.assets.filter((a) => a.mode)) {
    if (!byVariant.has(entry.variant)) {
      byVariant.set(entry.variant, { id: entry.variant, label: entry.label, assets: {} });
    }
    byVariant.get(entry.variant).assets[entry.mode] = entry.id;
  }

  return Promise.all([...byVariant.values()].map(async (coat) => {
    const assets = {};
    await Promise.all(Object.entries(coat.assets).map(async ([mode, id]) => {
      assets[mode] = await loadAsset(id);
    }));
    // The index labels each asset per mode ("Bay (Riding)"); the coat wants the
    // shared part, so take it from the variant id.
    const label = coat.label.replace(/\s*\(.*\)$/, '');
    return { ...coat, label, assets };
  }));
}

export function clipFor(coat, mode, facing, action) {
  const block = coat.assets[mode].clips[facing];
  return block[action] ?? block.idle;
}

// Same lookup against a bare asset, for consumers that are not mode-based.
export function clipOf(asset, facing, action) {
  const block = asset.clips[facing];
  return block[action] ?? Object.values(block)[0];
}

export function hasClip(coat, mode, facing, action) {
  return Boolean(coat.assets[mode].clips[facing][action]);
}

// `off_ground` is an inclusive pair of frame indices, measured at ingest from
// each frame's alpha bbox against the clip baseline. Collision therefore tracks
// the artwork and stays correct when fps changes.
export function isAirborne(clip, frame) {
  if (!clip.off_ground) return false;
  return frame >= clip.off_ground[0] && frame <= clip.off_ground[1];
}

// PIPELINE.md §5: no subpixel placement. Positions are rounded at draw time.
export function drawSprite(ctx, coat, mode, facing, action, frame, x, y) {
  const asset = coat.assets[mode];
  const clip = clipFor(coat, mode, facing, action);
  const { w, h } = asset.cell;
  const col = clip.col0 + (frame % clip.frames);
  ctx.drawImage(
    asset.image,
    col * w, clip.row * h, w, h,
    Math.round(x) - asset.origin.x, Math.round(y) - asset.origin.y, w, h,
  );
}
