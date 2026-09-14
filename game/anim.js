// Animation debugger — one clip at a time, at a size you can argue about.
//
// `viewer.html` answers "does every asset render", which is the conformance
// claim and deliberately shows everything at once. This answers the question
// that comes next and that the viewer is the wrong shape for: *this frame is
// wrong — which one is it, and where did it come from?*
//
// Three things make that answerable rather than a matter of squinting:
//
//   FRAMES ARE ADDRESSED, not described. Every frame carries its clip index
//   and its source row and column, and the report line at the bottom is a
//   single string naming exactly one frame. Pasting it is a complete bug
//   report; so is the URL, which carries the same selection in its hash.
//
//   THE SOURCE IS SHOWN BESIDE THE OUTPUT. The built frame and the frame it
//   was cut from are drawn side by side, and the whole source row under them.
//   That splits "the pipeline mangled it" from "the config points at the wrong
//   row" from "the artwork is like that", which are three different fixes.
//   Source sheets are gitignored, so the panels hide themselves when the sheet
//   is not reachable rather than erroring.
//
//   MOTION IS EXERCISED, not asserted. A clip that reads fine as a strip can
//   still be the wrong clip, and the tell is usually a direction change. The
//   figure-8 drives a sprite through every facing and both crossings using the
//   game's own facing rule, so what happens here happens in the pasture.
//
// It reads nothing but the manifest and the metadata, like every other
// consumer in this repo.

import { loadIndex, loadAsset, facingFrom } from './sprites.js';

const FACINGS = ['north', 'east', 'south', 'west'];
const GROUND = '#4d8446';

const $ = (id) => document.getElementById(id);
const el = (tag, cls, text) => {
  const n = document.createElement(tag);
  if (cls) n.className = cls;
  if (text !== undefined) n.textContent = text;
  return n;
};

// --- state -----------------------------------------------------------------

const ui = {
  index: null,
  entry: null,
  asset: null,
  facing: null,
  clip: null,        // clip name
  frame: 0,
  playing: true,
  clock: 0,
  sourceSheet: null, // HTMLImageElement, or null when it is not reachable
  sourceFor: null,   // the sheet path `sourceSheet` holds
};

function clipData() {
  if (!ui.asset) return null;
  return ui.asset.clips[ui.facing]?.[ui.clip] ?? null;
}

// --- url state -------------------------------------------------------------
//
// The selection lives in the hash so a link is a bug report: paste it and the
// other side is looking at the same frame of the same clip, not at a
// description of one.

// A `frame` in the hash is deliberately distinguished from no `frame` at all.
// A link that names one is somebody saying "this frame is wrong", so arriving
// on it pauses there; a link that only names a clip is somebody saying "look
// at this animation", so it plays. Defaulting the missing case to 0 would make
// every link the first kind, and a paused first frame is the least useful
// thing either of them could have meant.
function readHash() {
  const p = new URLSearchParams(location.hash.slice(1));
  return {
    id: p.get('id'), facing: p.get('facing'), clip: p.get('clip'),
    frame: p.has('frame') ? Number(p.get('frame')) : null,
  };
}

let writingHash = false;
function writeHash() {
  if (!ui.entry) return;
  const p = new URLSearchParams({
    id: ui.entry.id, facing: ui.facing, clip: ui.clip, frame: String(ui.frame),
  });
  writingHash = true;
  history.replaceState(null, '', `#${p}`);
  writingHash = false;
}

// --- asset list ------------------------------------------------------------

function buildAssetList(filter = '') {
  const list = $('assetlist');
  list.textContent = '';
  const needle = filter.trim().toLowerCase();
  const groups = new Map();
  for (const entry of ui.index.assets) {
    if (needle && !entry.id.toLowerCase().includes(needle)
        && !(entry.label ?? '').toLowerCase().includes(needle)) continue;
    const key = entry.family ?? 'other';
    if (!groups.has(key)) groups.set(key, []);
    groups.get(key).push(entry);
  }
  for (const [family, entries] of [...groups].sort()) {
    const g = el('div', 'assetgroup');
    g.append(el('span', null, `${family} · ${entries.length}`));
    for (const entry of entries) {
      const b = el('button', 'assetbtn', entry.id);
      b.setAttribute('aria-pressed', String(entry === ui.entry));
      b.title = `${entry.label ?? entry.id} — ${entry.frame_size.join('x')}, `
        + `${entry.animations} clips, ${entry.frame_count} frames`;
      b.onclick = () => selectAsset(entry);
      g.append(b);
    }
    list.append(g);
  }
}

// --- selection -------------------------------------------------------------

async function selectAsset(entry, want = {}) {
  ui.entry = entry;
  ui.asset = await loadAsset(entry.id);
  buildAssetList($('filter').value);

  const facings = Object.keys(ui.asset.clips);
  ui.facing = facings.includes(want.facing) ? want.facing
    : (facings.includes('east') ? 'east' : facings[0]);
  if (Number.isFinite(want.frame)) {
    ui.playing = false;
    syncPlayButton();
  }
  selectClip(want.clip, Number.isFinite(want.frame) ? want.frame : 0);
}

function selectClip(name, frame = 0) {
  const block = ui.asset.clips[ui.facing] ?? {};
  const names = Object.keys(block).sort();
  ui.clip = name && block[name] ? name : (block.idle ? 'idle' : names[0]);
  ui.frame = 0;
  ui.clock = 0;
  renderPills();
  renderStrip();
  loadSourceSheet();
  setFrame(frame, { scrub: true });
}

// `scrub` separates the two ways a frame changes. Playback advancing is not a
// selection: it must not reset the clock (that would stall playback at exactly
// one frame per call) and it must not rewrite the hash (twelve
// history.replaceState a second, and the link you copied would be whatever
// frame happened to be up when you reached for the mouse). Clicking a thumb,
// stepping, or changing clip is a selection and does both.
function setFrame(i, { scrub = false } = {}) {
  const clip = clipData();
  if (!clip) return;
  ui.frame = ((i % clip.frames) + clip.frames) % clip.frames;
  if (scrub) ui.clock = 0;
  for (const t of $('strip').children) {
    t.setAttribute('aria-pressed', String(Number(t.dataset.i) === ui.frame));
  }
  renderReport();
  drawSourceFrame();
  if (scrub) writeHash();
}

// --- pills -----------------------------------------------------------------

function renderPills() {
  const fw = $('facings');
  fw.textContent = '';
  const drawn = Object.keys(ui.asset.clips);
  const order = drawn.includes('none') ? ['none'] : FACINGS;
  for (const f of order) {
    const has = drawn.includes(f);
    const b = el('button', has ? 'pill' : 'pill missing', f);
    b.setAttribute('aria-pressed', String(f === ui.facing));
    b.disabled = !has;
    // Keep the clip when switching facing: comparing the same action across
    // facings is the whole point, and resetting to `idle` every time would
    // make the comparison three clicks instead of one.
    b.onclick = () => { ui.facing = f; selectClip(ui.clip, 0); };
    fw.append(b);
  }

  const cw = $('clips');
  cw.textContent = '';
  for (const name of Object.keys(ui.asset.clips[ui.facing] ?? {}).sort()) {
    const b = el('button', 'pill', name);
    b.setAttribute('aria-pressed', String(name === ui.clip));
    b.onclick = () => selectClip(name, 0);
    cw.append(b);
  }
  renderClipMeta();
}

function renderClipMeta() {
  const clip = clipData();
  const box = $('clipmeta');
  box.textContent = '';
  if (!clip) { box.append('no clip'); return; }
  const rows = [
    ['frames', clip.frames],
    ['fps', clip.fps],
    ['loop', String(clip.loop)],
    ['sheet row', clip.row],
    ['cell', `${ui.asset.cell.w}x${ui.asset.cell.h}`],
    ['feet', `${ui.asset.origin.x},${ui.asset.origin.y}`],
  ];
  if (clip.next) rows.push(['next', clip.next]);
  if (clip.off_ground) rows.push(['off ground', clip.off_ground.join('–')]);
  if (clip.source) {
    const cols = clip.source.columns;
    // Spelled out rather than summarised as a range, because a clip whose
    // columns have a gap in them is a clip that skips a defective source frame
    // — which is precisely the thing somebody reading this panel wants to see.
    const span = cols[cols.length - 1] - cols[0] + 1 === cols.length
      ? `${cols[0]}–${cols[cols.length - 1]}`
      : cols.join(',');
    rows.push(['source', clip.source.sheet.split('/').pop()]);
    rows.push(['src row', clip.source.row]);
    rows.push(['src cols', span]);
    if (clip.source.patched) rows.push(['patched', clip.source.patched.join(',')]);
  }
  for (const [k, v] of rows) {
    const line = el('div');
    line.append(`${k} `);
    line.append(el('b', null, String(v)));
    box.append(line);
  }
}

// --- frame strip -----------------------------------------------------------

function renderStrip() {
  const strip = $('strip');
  strip.textContent = '';
  const clip = clipData();
  if (!clip) return;
  const { w, h } = ui.asset.cell;
  const scale = Math.max(1, Math.min(3, Math.round(120 / h)));
  for (let i = 0; i < clip.frames; i++) {
    const t = el('button', 'thumb');
    t.dataset.i = String(i);
    const cv = el('canvas');
    cv.width = w * scale;
    cv.height = h * scale;
    const c = cv.getContext('2d');
    c.imageSmoothingEnabled = false;
    c.fillStyle = GROUND;
    c.fillRect(0, 0, cv.width, cv.height);
    c.drawImage(ui.asset.image, (clip.col0 + i) * w, clip.row * h, w, h,
      0, 0, cv.width, cv.height);
    t.append(cv);
    t.append(el('span', 'n', String(i)));
    // The source column, not just the index in the clip. "Frame 3" is what you
    // see; "column 5 of row 9" is what somebody has to go and fix.
    if (clip.source) {
      const fixed = clip.source.patched?.includes(i);
      // A patched frame does not match the source cell it names, on purpose.
      // Saying so here is the difference between "the pack draws this wrong and
      // we corrected it" and "the viewer is lying to you".
      t.append(el('span', fixed ? 'src fixed' : 'src',
        `r${clip.source.row}c${clip.source.columns[i]}${fixed ? ' ✎' : ''}`));
    }
    t.onclick = () => { ui.playing = false; syncPlayButton(); setFrame(i, { scrub: true }); };
    strip.append(t);
  }
}

// --- the report line -------------------------------------------------------

function renderReport() {
  const clip = clipData();
  if (!clip) { $('reportline').textContent = '—'; return; }
  const bits = [
    ui.entry.id,
    `${ui.clip}/${ui.facing}`,
    `frame ${ui.frame} of ${clip.frames}`,
    `packed row ${clip.row} col ${clip.col0 + ui.frame}`,
  ];
  if (clip.source) {
    bits.push(`${clip.source.sheet} row ${clip.source.row} col ${clip.source.columns[ui.frame]}`
      + (clip.source.patched?.includes(ui.frame) ? ' (patched)' : ''));
  }
  $('reportline').textContent = bits.join(' · ');
}

$('copy').onclick = async () => {
  const text = `${$('reportline').textContent}\n${location.href}`;
  try {
    await navigator.clipboard.writeText(text);
    $('copy').textContent = 'copied';
  } catch {
    $('copy').textContent = 'select it';
  }
  setTimeout(() => { $('copy').textContent = 'copy'; }, 1200);
};

// --- the source sheet ------------------------------------------------------
//
// Gitignored third-party art. It is usually there when somebody is debugging
// and never there on a hosted runner, so a miss hides the panels instead of
// reporting an error: the debugger still works without it, just with one fewer
// way to tell a config bug from an artwork one.

function loadSourceSheet() {
  const clip = clipData();
  const path = clip?.source?.sheet;
  $('srcbox').hidden = true;
  $('srcrowpanel').hidden = true;
  if (!path) return;
  if (ui.sourceFor === path) { showSource(); return; }
  const img = new Image();
  img.onload = () => {
    if (clipData()?.source?.sheet !== path) return;   // selection moved on
    ui.sourceSheet = img;
    ui.sourceFor = path;
    showSource();
  };
  img.onerror = () => {
    if (ui.sourceFor === path) { ui.sourceSheet = null; ui.sourceFor = null; }
  };
  img.src = new URL(`../${path}`, location.href).href;
}

function showSource() {
  $('srcbox').hidden = false;
  $('srcrowpanel').hidden = false;
  drawSourceFrame();
  drawSourceRow();
}

function drawSourceFrame() {
  const clip = clipData();
  if (!ui.sourceSheet || !clip?.source) return;
  const [cw, ch] = clip.source.cell ?? [ui.asset.cell.w, ui.asset.cell.h];
  const cv = $('src');
  const scale = stageScale();
  cv.width = cw * scale;
  cv.height = ch * scale;
  // Said out loud because the two panels are routinely different shapes and
  // that is correct, not a bug: ingest re-blits every frame of an asset onto
  // one common cell, and the source sheets disagree about theirs. The leading
  // sheet is 100x96 at source and 100x84 built; the riding sheet 80x64 and
  // 80x82.
  $('srcdims').textContent = `${cw}x${ch} · ${scale}x`
    + (clip.source.patched?.includes(ui.frame) ? ' · UNCORRECTED' : '');
  const c = cv.getContext('2d');
  c.imageSmoothingEnabled = false;
  c.fillStyle = GROUND;
  c.fillRect(0, 0, cv.width, cv.height);
  c.drawImage(ui.sourceSheet,
    clip.source.columns[ui.frame] * cw, clip.source.row * ch, cw, ch,
    0, 0, cv.width, cv.height);
}

function drawSourceRow() {
  const clip = clipData();
  if (!ui.sourceSheet || !clip?.source) return;
  const [cw, ch] = clip.source.cell ?? [ui.asset.cell.w, ui.asset.cell.h];
  const cols = Math.floor(ui.sourceSheet.naturalWidth / cw);
  const scale = Math.max(1, Math.min(4, Math.round(640 / (cols * cw))));
  const cv = $('srcrow');
  cv.width = cols * cw * scale;
  cv.height = ch * scale;
  const c = cv.getContext('2d');
  c.imageSmoothingEnabled = false;
  c.fillStyle = GROUND;
  c.fillRect(0, 0, cv.width, cv.height);
  c.drawImage(ui.sourceSheet, 0, clip.source.row * ch, cols * cw, ch,
    0, 0, cv.width, cv.height);
  // The cell grid, so a pitch that is out of phase is visible as sprites
  // straddling the lines rather than as a vague feeling that it looks wrong.
  c.strokeStyle = 'rgba(224,161,58,0.35)';
  c.lineWidth = 1;
  for (let i = 0; i <= cols; i++) {
    c.beginPath();
    c.moveTo(i * cw * scale + 0.5, 0);
    c.lineTo(i * cw * scale + 0.5, cv.height);
    c.stroke();
  }
  // What this clip actually takes out of the row — cell by cell, so a column
  // the clip skips stays unshaded and is visible as a gap in the band.
  c.fillStyle = 'rgba(224,161,58,0.16)';
  for (const col of clip.source.columns) {
    c.fillRect(col * cw * scale, 0, cw * scale, cv.height);
  }
  c.strokeStyle = '#e0a13a';
  c.lineWidth = 2;
  c.strokeRect(clip.source.columns[ui.frame] * cw * scale + 1, 1,
    cw * scale - 2, cv.height - 2);
  const used = clip.source.columns;
  const skipped = [];
  for (let i = used[0]; i <= used[used.length - 1]; i++) {
    if (!used.includes(i)) skipped.push(i);
  }
  $('srcrowlabel').textContent =
    `${clip.source.sheet} · row ${clip.source.row} · ${cols} cells of `
    + `${cw}x${ch} · this clip takes ${clip.frames}`
    + (skipped.length ? ` · skips ${skipped.join(',')}` : '');
}

// --- the stage -------------------------------------------------------------

function stageScale() {
  const h = ui.asset?.cell.h ?? 32;
  return Math.max(2, Math.min(10, Math.floor(300 / h)));
}

function drawStage() {
  const clip = clipData();
  const cv = $('stage');
  if (!clip) return;
  const { w, h } = ui.asset.cell;
  const scale = stageScale();
  // Both dimensions, not just the width. A canvas starts at the HTML default
  // 300x150, and the leading sheets are a 100px cell at scale 3 — exactly 300
  // wide — so a width-only guard decided the canvas was already the right size
  // and left the height at 150. Every other asset in the repo resized fine,
  // which is what made it look like a leading-sheet problem rather than a
  // one-line bug.
  if (cv.width !== w * scale || cv.height !== h * scale) {
    cv.width = w * scale;
    cv.height = h * scale;
  }
  $('stagedims').textContent = `${w}x${h} · ${scale}x`;
  const c = cv.getContext('2d');
  c.imageSmoothingEnabled = false;
  c.fillStyle = GROUND;
  c.fillRect(0, 0, cv.width, cv.height);

  // Guides that sit *behind* the sprite go down first. An anchor column drawn
  // over the artwork hides the column of pixels you are trying to judge it
  // against, which is the one thing it must not do.
  const guides = $('guides').checked;
  if (guides) {
    const { x, y } = ui.asset.origin;
    c.fillStyle = 'rgba(20,30,18,0.35)';          // the declared ground line
    c.fillRect(0, y * scale, cv.width, Math.max(1, scale));
    c.fillStyle = 'rgba(224,161,58,0.30)';        // the anchor column
    c.fillRect(x * scale, 0, Math.max(1, scale), cv.height);
  }

  if ($('onion').checked) {
    // The frame before and the frame after, faintly. Motion between two frames
    // is invisible in a still and obvious as a ghost.
    c.globalAlpha = 0.25;
    for (const d of [-1, 1]) {
      const i = ((ui.frame + d) % clip.frames + clip.frames) % clip.frames;
      c.drawImage(ui.asset.image, (clip.col0 + i) * w, clip.row * h, w, h,
        0, 0, cv.width, cv.height);
    }
    c.globalAlpha = 1;
  }

  c.drawImage(ui.asset.image, (clip.col0 + ui.frame) * w, clip.row * h, w, h,
    0, 0, cv.width, cv.height);

  if (guides) {
    // Only the ones that have to be readable on top: the feet mark, which is
    // where the sprite claims to touch the ground, and the cell edge.
    const { x, y } = ui.asset.origin;
    c.fillStyle = '#e0a13a';
    c.fillRect(x * scale - scale, y * scale, scale * 3, Math.max(1, scale));
    c.strokeStyle = 'rgba(224,161,58,0.30)';
    c.lineWidth = 1;
    c.strokeRect(0.5, 0.5, cv.width - 1, cv.height - 1);
  }
}

// --- transport -------------------------------------------------------------

function syncPlayButton() {
  $('playpause').textContent = ui.playing ? '⏸ pause' : '▶ play';
}

$('playpause').onclick = () => { ui.playing = !ui.playing; syncPlayButton(); };
$('prev').onclick = () => { ui.playing = false; syncPlayButton(); setFrame(ui.frame - 1, { scrub: true }); };
$('next').onclick = () => { ui.playing = false; syncPlayButton(); setFrame(ui.frame + 1, { scrub: true }); };
$('onion').onchange = drawStage;
$('guides').onchange = drawStage;
$('speed').oninput = () => { $('speedval').textContent = `${$('speed').value}%`; };
$('figsize').oninput = () => { $('figsizeval').textContent = $('figsize').value; };
$('figspeed').oninput = () => { $('figspeedval').textContent = `${$('figspeed').value}%`; };
$('fignudge').oninput = () => figureEight(0);
$('fignudgereset').onclick = () => { $('fignudge').value = '0'; figureEight(0); };
$('filter').oninput = () => buildAssetList($('filter').value);

addEventListener('keydown', (e) => {
  if (e.target.tagName === 'INPUT') return;
  const clips = Object.keys(ui.asset?.clips[ui.facing] ?? {}).sort();
  if (e.code === 'Space') { e.preventDefault(); $('playpause').click(); }
  else if (e.code === 'ArrowLeft') { e.preventDefault(); $('prev').click(); }
  else if (e.code === 'ArrowRight') { e.preventDefault(); $('next').click(); }
  else if (e.code === 'BracketLeft' || e.code === 'BracketRight') {
    const d = e.code === 'BracketRight' ? 1 : -1;
    const i = clips.indexOf(ui.clip);
    selectClip(clips[(i + d + clips.length) % clips.length], 0);
  } else if (e.code === 'ArrowUp' || e.code === 'ArrowDown') {
    e.preventDefault();
    const drawn = FACINGS.filter((f) => ui.asset?.clips[f]);
    const i = drawn.indexOf(ui.facing);
    if (i >= 0) {
      ui.facing = drawn[(i + (e.code === 'ArrowDown' ? 1 : -1) + drawn.length) % drawn.length];
      selectClip(ui.clip, 0);
    }
  }
});

addEventListener('hashchange', () => {
  if (writingHash) return;
  const want = readHash();
  const entry = ui.index.assets.find((a) => a.id === want.id);
  if (entry) selectAsset(entry, want);
});

// --- the figure-8 ----------------------------------------------------------
//
// Two circles of equal radius meeting at a crossing, the right one traced
// clockwise and the left one anticlockwise. That is what a figure-8 is when
// you ride one, and the reason to build it out of circles rather than reach
// for a lemniscate is measured: on a lemniscate of Gerono
// (x = A sin t, y = B sin t cos t) the two points where the horizontal
// velocity vanishes both head *north*, so the path never faces south at all —
// 711 east, 711 west, 498 north, 0 south over a full period, for any A and B.
// Two counter-rotating loops sweep a full turn of heading each, so every
// facing gets an equal quarter of the path and both senses of rotation are
// exercised. A facing rule can only be judged against a path that visits every
// facing.

const fig = { u: 0, facing: 'south', frame: 0, clock: 0 };

// Position and velocity at path parameter u, where u runs 0..2: the first unit
// is the right-hand loop, the second the left-hand one. Velocity is analytic
// rather than a difference of two rounded positions, because the facing rule
// is at its most sensitive exactly where a difference would be noisiest.
function figurePoint(u, R) {
  const t = ((u % 2) + 2) % 2;
  if (t < 1) {
    const phi = Math.PI - 2 * Math.PI * t;            // clockwise, from the crossing
    return { x: R + R * Math.cos(phi), y: R * Math.sin(phi),
             vx: Math.sin(phi), vy: -Math.cos(phi) };
  }
  const phi = 2 * Math.PI * (t - 1);                  // anticlockwise, from the crossing
  return { x: -R + R * Math.cos(phi), y: R * Math.sin(phi),
           vx: -Math.sin(phi), vy: Math.cos(phi) };
}

function figureEight(dt) {
  const cv = $('fig');
  if (!ui.asset) return;
  const R = Number($('figsize').value) / 2;
  const speed = Number($('figspeed').value) / 100;
  // The sprite is deliberately smaller here than on the stage. This panel is
  // about the path and the facing changes on it, and a 4x farmhand on a 300px
  // canvas covers the crossing it is supposed to be walking through.
  const scale = Math.max(1, Math.min(3, Math.floor(72 / ui.asset.cell.h)));
  const pad = Math.max(ui.asset.cell.w, ui.asset.cell.h) * scale;
  const W = Math.round(R * 4 + pad * 1.6);
  const H = Math.round(R * 2 + pad * 1.6);
  if (cv.width !== W || cv.height !== H) { cv.width = W; cv.height = H; }

  fig.u = (fig.u + dt * speed * 0.35) % 2;
  const cx = W / 2;
  const cy = H / 2;
  const at = figurePoint(fig.u, R);
  const x = cx + at.x;
  const y = cy + at.y;

  const want = facingFrom(at.vx * 300, at.vy * 300);
  if (want) fig.facing = want;

  // Fall back the way the game does, and say so, rather than drawing a facing
  // the asset does not have.
  let facing = fig.facing;
  let fallback = false;
  if (!ui.asset.clips[facing]) {
    facing = ui.facing;
    fallback = true;
  }
  const block = ui.asset.clips[facing] ?? {};
  const wanted = block[ui.clip];
  const clip = wanted ?? block.idle ?? Object.values(block)[0];
  if (!clip) return;

  fig.clock += dt * speed;
  const step = 1 / Math.max(0.25, clip.fps);
  while (fig.clock >= step) {
    fig.clock -= step;
    fig.frame = clip.loop ? (fig.frame + 1) % clip.frames
      : Math.min(fig.frame + 1, clip.frames - 1);
  }
  const frame = fig.frame % clip.frames;

  const c = cv.getContext('2d');
  c.imageSmoothingEnabled = false;
  c.fillStyle = GROUND;
  c.fillRect(0, 0, W, H);

  if ($('figpath').checked) {
    c.strokeStyle = 'rgba(20,30,18,0.35)';
    c.lineWidth = 1;
    c.beginPath();
    for (let i = 0; i <= 400; i++) {
      const q = figurePoint((i / 400) * 2, R);
      if (i) c.lineTo(cx + q.x, cy + q.y); else c.moveTo(cx + q.x, cy + q.y);
    }
    c.stroke();
    // Where the facing changes, marked on the path. These are the moments
    // worth watching, and they are otherwise over before you notice them.
    let prev = null;
    for (let i = 0; i <= 800; i++) {
      const q = figurePoint((i / 800) * 2, R);
      const f = facingFrom(q.vx * 300, q.vy * 300);
      if (f && prev && f !== prev) {
        c.fillStyle = 'rgba(224,161,58,0.8)';
        c.fillRect(cx + q.x - 2, cy + q.y - 2, 4, 4);
      }
      if (f) prev = f;
    }
  }

  const { w, h } = ui.asset.cell;
  // The nudge shifts the anchor the sprite is hung from, in source pixels. It
  // exists because `anchors.points.feet` is bottom-*centre* of the cell, and on
  // a sheet that draws two subjects in one cell the centre is nobody's feet:
  // on horse_bay_lead the anchor is x=50 while the handler stands at x≈91
  // facing east and x≈6 facing west. So the pair's middle rides the path and
  // the handler swings around it. Nudging until the subject you care about sits
  // on the path reads off what that subject's anchor would have to be, which
  // turns "this looks wrong" into a number.
  // Mirrored on the profile facings and ignored on the front and back ones,
  // because that is how the offset it is standing in for actually behaves:
  // facing east the handler is drawn at x≈91 of a 100px cell and facing west at
  // x≈6, while facing north and south it stands on the centreline at x≈48 and
  // needs no offset at all. One number that flips sign therefore covers all
  // four, which is also the shape a per-facing anchor would take if the schema
  // grew one.
  const nudge = Number($('fignudge').value);
  const signed = facing === 'east' ? nudge : facing === 'west' ? -nudge : 0;
  const anchorX = ui.asset.origin.x + signed;
  c.fillStyle = 'rgba(24,40,20,0.22)';
  c.beginPath();
  c.ellipse(x, y, Math.max(3, w * scale * 0.22), Math.max(2, scale * 1.6), 0, 0, Math.PI * 2);
  c.fill();
  c.drawImage(ui.asset.image,
    (clip.col0 + frame) * w, clip.row * h, w, h,
    Math.round(x) - anchorX * scale,
    Math.round(y) - ui.asset.origin.y * scale,
    w * scale, h * scale);
  // The anchor itself, on the path. Without it you can see that the sprite is
  // off the path but not which point of the sprite the path is holding.
  c.strokeStyle = '#e0a13a';
  c.lineWidth = 1;
  c.beginPath();
  c.moveTo(x - 4.5, y + 0.5); c.lineTo(x + 4.5, y + 0.5);
  c.moveTo(x + 0.5, y - 4.5); c.lineTo(x + 0.5, y + 4.5);
  c.stroke();

  $('figfacing').textContent = fallback ? `${fig.facing} → ${facing} (not drawn)` : facing;
  $('figfacing').className = fallback ? 'warn' : '';
  $('fignudgeval').textContent = nudge
    ? `${signed > 0 ? '+' : ''}${signed} facing ${facing} → anchor x ${anchorX} of ${w}`
    : `declared: x ${ui.asset.origin.x} of ${w}`;
  $('figclip').textContent = wanted
    ? `${ui.clip} ${frame}/${clip.frames}`
    : `${ui.clip} not drawn → ${Object.keys(block).find((k) => block[k] === clip)} ${frame}/${clip.frames}`;
}

// --- the loop --------------------------------------------------------------

let last = performance.now();
function tick(now) {
  const dt = Math.min(0.1, (now - last) / 1000);
  last = now;
  if (ui.asset) {
    const clip = clipData();
    if (clip && ui.playing) {
      ui.clock += dt * (Number($('speed').value) / 100);
      const step = 1 / Math.max(0.25, clip.fps);
      while (ui.clock >= step) {
        ui.clock -= step;
        setFrame(clip.loop ? ui.frame + 1 : Math.min(ui.frame + 1, clip.frames - 1));
      }
    }
    drawStage();
    figureEight(dt);
  }
  requestAnimationFrame(tick);
}

// --- boot ------------------------------------------------------------------

async function main() {
  try {
    ui.index = await loadIndex();
  } catch (err) {
    $('summary').textContent = `could not read the manifest: ${err.message}`;
    $('summary').className = 'warn';
    return;
  }
  $('summary').textContent =
    `${ui.index.assets.length} assets · pipeline ${ui.index.pipeline_version}`;
  buildAssetList();
  const want = readHash();
  const entry = ui.index.assets.find((a) => a.id === want.id)
    ?? ui.index.assets.find((a) => a.family === 'character')
    ?? ui.index.assets[0];
  await selectAsset(entry, want);
  syncPlayButton();
  requestAnimationFrame(tick);
}

main();
