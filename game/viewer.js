// Asset viewer — every promoted asset, animated from its own metadata.
//
// The game proper only draws playable coats: it needs four facings, gaits and
// a riding or leading sheet. That made everything the Path C stage graph
// produces invisible, which is precisely the output most worth looking at.
//
// This draws whatever is in the manifest, whatever shape it is: a 24x16 static
// prop, a 48x32 three-frame idle, a 100x79 leading sheet with eighteen clips.
// It reads nothing but metadata, so if an asset renders here its metadata was
// sufficient — the conformance claim, generalised to both producers.

import { loadIndex, loadAsset, clipOf } from './sprites.js';

const GROUND = '#4d8446';
const grid = document.getElementById('grid');
const summary = document.getElementById('summary');

function el(tag, cls, text) {
  const n = document.createElement(tag);
  if (cls) n.className = cls;
  if (text !== undefined) n.textContent = text;
  return n;
}

// One canvas per clip, scaled up by an integer factor and never smoothed (§7).
function clipCanvas(asset, clip, scale) {
  const { w, h } = asset.cell;
  const cv = el('canvas', 'frame');
  cv.width = w * scale;
  cv.height = h * scale;
  const ctx = cv.getContext('2d');
  ctx.imageSmoothingEnabled = false;

  let frame = 0;
  let clock = 0;
  let last = performance.now();

  function draw(now) {
    if (!cv.isConnected) return;
    const dt = Math.min(0.1, (now - last) / 1000);
    last = now;
    clock += dt;
    const step = 1 / Math.max(0.25, clip.fps);
    while (clock >= step) {
      clock -= step;
      frame = clip.loop
        ? (frame + 1) % clip.frames
        : Math.min(frame + 1, clip.frames - 1);
    }
    ctx.fillStyle = GROUND;
    ctx.fillRect(0, 0, cv.width, cv.height);
    // The baseline the asset declares, so a floating anchor is visible here
    // rather than only showing up once something stands on the ground.
    ctx.fillStyle = 'rgba(20,30,18,0.28)';
    ctx.fillRect(0, asset.origin.y * scale, cv.width, scale);
    const col = clip.col0 + frame;
    ctx.drawImage(asset.image, col * w, clip.row * h, w, h,
      0, 0, cv.width, cv.height);
    requestAnimationFrame(draw);
  }
  requestAnimationFrame(draw);
  return cv;
}

function card(entry, asset) {
  const c = el('article', 'card');
  const head = el('header');
  head.append(el('h2', null, entry.id));
  const tags = el('div', 'tags');
  const bits = [
    `${entry.frame_size[0]}x${entry.frame_size[1]}`,
    `${entry.frame_count} frames`,
    `${entry.animations} clip${entry.animations === 1 ? '' : 's'}`,
    entry.layout,
  ];
  if (entry.mode) bits.push(entry.mode);
  if (entry.facings.length) bits.push(`${entry.facings.length} facings`);
  if (entry.category) bits.push(entry.category);
  for (const b of bits) tags.append(el('span', 'tag', b));
  head.append(tags);
  c.append(head);

  // Scale so small assets are still legible without giant sheets dominating.
  const scale = Math.max(2, Math.min(6, Math.round(220 / entry.frame_size[0])));

  for (const facing of Object.keys(asset.clips).sort()) {
    const row = el('div', 'facing');
    if (facing !== 'none') row.append(el('span', 'facing-label', facing));
    const strip = el('div', 'strip');
    for (const name of Object.keys(asset.clips[facing]).sort()) {
      const clip = clipOf(asset, facing, name);
      const cell = el('div', 'clipcell');
      cell.append(clipCanvas(asset, clip, scale));
      const cap = `${name} · ${clip.frames}f · ${clip.fps}fps`;
      cell.append(el('span', 'caption', clip.off_ground ? `${cap} · air` : cap));
      strip.append(cell);
    }
    row.append(strip);
    c.append(row);
  }
  return c;
}

async function main() {
  let index;
  try {
    index = await loadIndex();
  } catch (err) {
    summary.textContent = `Could not read the manifest: ${err.message}`;
    summary.classList.add('error');
    return;
  }

  const ok = [];
  const failed = [];
  for (const entry of index.assets) {
    try {
      // Shaped for the viewer: loadAsset wants an id and returns clips keyed by
      // facing, with 'none' for a non-directional asset.
      const asset = await loadAsset(entry.id);
      grid.append(card(entry, asset));
      ok.push(entry.id);
    } catch (err) {
      failed.push(`${entry.id}: ${err.message}`);
    }
  }

  summary.textContent =
    `${ok.length} of ${index.assets.length} assets rendered` +
    (failed.length ? ` — ${failed.length} failed` : '') +
    ` · pipeline ${index.pipeline_version}`;
  if (failed.length) {
    summary.classList.add('error');
    const list = el('ul', 'failures');
    for (const f of failed) list.append(el('li', null, f));
    summary.after(list);
  }
}

main();
