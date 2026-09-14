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

// Index `animations` by facing and name. The list is flat in the schema, which
// keeps a non-directional prop expressible; a renderer wants it two-level.
function indexClips(meta) {
  const byFacing = {};
  for (const anim of meta.animations) {
    const facing = anim.facing ?? 'none';
    (byFacing[facing] ??= {})[anim.name] = anim;
  }
  return byFacing;
}

async function loadAsset(id) {
  const dir = `${PRODUCTION}${id}/`;
  const meta = await loadJson(`${dir}metadata.json`);
  const [w, h] = meta.frame_size;
  const [ox, oy] = meta.anchors.points.feet;

  const layout = meta.spritesheet?.layout ?? 'single_strip';
  if (layout !== 'row_per_animation') {
    throw new Error(`${id}: unsupported spritesheet layout "${layout}"`);
  }

  return {
    id: meta.id,
    image: await loadImage(dir + (meta.spritesheet?.file ?? 'spritesheet.png')),
    cell: { w, h },
    origin: { x: ox, y: oy },
    clips: indexClips(meta),
  };
}

export async function loadCoats() {
  const index = await loadJson(`${PRODUCTION}index.json`);
  const byVariant = new Map();
  for (const entry of index.assets) {
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
  const col = frame % clip.frames;
  ctx.drawImage(
    asset.image,
    col * w, clip.row * h, w, h,
    Math.round(x) - asset.origin.x, Math.round(y) - asset.origin.y, w, h,
  );
}
