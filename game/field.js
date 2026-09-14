// Procedural pasture background.
//
// Drawn once into an offscreen canvas at native resolution, from a fixed seed,
// using a fixed palette. Same seed + same palette -> same pixels (§8).

const PALETTE = {
  GRASS_SHADOW: '#3c6b3a',
  GRASS_BASE:   '#4d8446',
  GRASS_LIGHT:  '#5d9a52',
  GRASS_TUFT:   '#356030',
  DIRT_BASE:    '#7a6142',
  DIRT_LIGHT:   '#8f7452',
  DIRT_DARK:    '#5f4a31',
  FENCE_DARK:   '#4a3524',
  FENCE_BASE:   '#6b4d33',
  FENCE_LIGHT:  '#8a6743',
};

// The worn track's centre line. Exported so the jump rail can be planted on
// the path rather than floating on grass next to it.
export function trackY(x, h) {
  return h * 0.62 + Math.sin(x / 46) * 16 + Math.sin(x / 13) * 3;
}

// Deterministic 32-bit LCG — no Math.random, so the field is reproducible.
function rng(seed) {
  let s = seed >>> 0;
  return () => {
    s = (Math.imul(s, 1664525) + 1013904223) >>> 0;
    return s / 4294967296;
  };
}

// Scanline ellipse — the only shape primitive here. Canvas `arc()` would
// anti-alias, which §5 forbids, so spans are computed per row instead.
export function fillEllipse(g, cx, cy, rx, ry, style) {
  g.fillStyle = style;
  const x0 = Math.round(cx);
  const y0 = Math.round(cy);
  for (let dy = -ry; dy <= ry; dy++) {
    const span = Math.round(rx * Math.sqrt(Math.max(0, 1 - (dy / ry) ** 2)));
    if (span > 0) g.fillRect(x0 - span, y0 + dy, span * 2, 1);
  }
}

export function buildField(w, h, seed = 0x5eed) {
  const cv = document.createElement('canvas');
  cv.width = w;
  cv.height = h;
  const g = cv.getContext('2d');
  const rand = rng(seed);

  g.fillStyle = PALETTE.GRASS_BASE;
  g.fillRect(0, 0, w, h);

  // Mottling: 2x2 clusters, never single-pixel noise (§5 "prefer clusters").
  for (let i = 0; i < 1400; i++) {
    const x = Math.floor(rand() * (w / 2)) * 2;
    const y = Math.floor(rand() * (h / 2)) * 2;
    g.fillStyle = rand() < 0.5 ? PALETTE.GRASS_SHADOW : PALETTE.GRASS_LIGHT;
    g.fillRect(x, y, 2, 2);
  }

  // Grass tufts: three stalks, all integer coords.
  for (let i = 0; i < 220; i++) {
    const x = Math.floor(rand() * w);
    const y = Math.floor(rand() * h);
    g.fillStyle = PALETTE.GRASS_TUFT;
    g.fillRect(x, y, 1, 3);
    g.fillRect(x - 2, y + 1, 1, 2);
    g.fillRect(x + 2, y + 1, 1, 2);
  }

  // A worn dirt track. Solid fill with ragged edges reads as a path; the
  // earlier per-pixel random fill just looked like noise.
  const edge = () => (rand() < 0.35 ? 1 : 0);
  for (let x = 0; x < w; x++) {
    const cy = trackY(x, h);
    const half = 8 + Math.sin(x / 27) * 3;
    const top = Math.round(cy - half) - edge();
    const bot = Math.round(cy + half) + edge();
    g.fillStyle = PALETTE.DIRT_BASE;
    g.fillRect(x, top, 1, bot - top);
    g.fillStyle = PALETTE.DIRT_LIGHT;
    g.fillRect(x, top + 3, 1, Math.max(1, bot - top - 6));
  }
  // Pebbles and stray tufts so the path is not a flat slab.
  for (let i = 0; i < 90; i++) {
    const x = Math.floor(rand() * w);
    const cy = trackY(x, h);
    const y = Math.round(cy + (rand() - 0.5) * 14);
    g.fillStyle = rand() < 0.5 ? PALETTE.DIRT_DARK : PALETTE.GRASS_TUFT;
    g.fillRect(x, y, 2, 1);
  }

  drawFence(g, w, h);
  return cv;
}

function drawFence(g, w, h) {
  const rail = (x, y, len) => {
    g.fillStyle = PALETTE.FENCE_DARK;
    g.fillRect(x, y + 2, len, 1);
    g.fillStyle = PALETTE.FENCE_BASE;
    g.fillRect(x, y, len, 2);
    g.fillStyle = PALETTE.FENCE_LIGHT;
    g.fillRect(x, y, len, 1);
  };

  rail(0, 18, w);
  rail(0, 26, w);
  rail(0, h - 10, w);
  rail(0, h - 2, w);

  for (let y = 0; y < h; y += 1) {
    const edge = y > 14 && y < h;
    if (!edge) continue;
    g.fillStyle = PALETTE.FENCE_BASE;
    g.fillRect(4, y, 3, 1);
    g.fillRect(w - 7, y, 3, 1);
    g.fillStyle = PALETTE.FENCE_LIGHT;
    g.fillRect(4, y, 1, 1);
    g.fillRect(w - 7, y, 1, 1);
  }

  // Posts.
  for (let x = 10; x < w; x += 52) {
    g.fillStyle = PALETTE.FENCE_DARK;
    g.fillRect(x, 14, 4, 16);
    g.fillRect(x, h - 14, 4, 14);
    g.fillStyle = PALETTE.FENCE_LIGHT;
    g.fillRect(x, 14, 1, 16);
    g.fillRect(x, h - 14, 1, 14);
  }
}

export function drawApple(g, x, y, bob) {
  const px = Math.round(x);
  const py = Math.round(y);
  fillEllipse(g, px, py, 4, 2, 'rgba(0,0,0,0.20)');   // ground shadow stays put
  const fy = py + bob;                                // only the fruit bobs
  g.fillStyle = '#3f7a2e';
  g.fillRect(px, fy - 9, 1, 3);                       // stem
  g.fillStyle = '#4f8f38';
  g.fillRect(px + 1, fy - 9, 2, 1);                   // leaf
  fillEllipse(g, px, fy - 4, 4, 4, '#8e2b27');        // shadow
  fillEllipse(g, px, fy - 5, 3, 3, '#c83b32');        // base
  g.fillStyle = '#e5675a';
  g.fillRect(px - 2, fy - 6, 2, 2);                   // highlight
}

// A cross-country jump rail: two posts, two rails, a groundline shadow.
// Drawn as a depth-sorted entity rather than baked into the field, so the
// horse passes behind it approaching and in front of it after landing.
export const RAIL_H = 19;

const RAIL = {
  SHADOW: 'rgba(24,40,20,0.26)',
  DARK:   '#4a3524',
  BASE:   '#6b4d33',
  LIGHT:  '#8a6743',
  STRIPE: '#d9d2bc',
};

export function drawRail(g, rail) {
  const { x0, x1, y } = rail;
  const w = x1 - x0;

  fillEllipse(g, x0 + w / 2, y + 1, Math.round(w / 2) + 2, 3, RAIL.SHADOW);

  // Posts.
  for (const px of [x0, x1 - 3]) {
    g.fillStyle = RAIL.DARK;
    g.fillRect(px, y - RAIL_H, 3, RAIL_H + 2);
    g.fillStyle = RAIL.LIGHT;
    g.fillRect(px, y - RAIL_H, 1, RAIL_H + 2);
  }

  // Rails, lower one first so the upper reads as nearer.
  for (const [oy, striped] of [[-9, false], [-17, true]]) {
    g.fillStyle = RAIL.DARK;
    g.fillRect(x0, y + oy + 3, w, 1);
    g.fillStyle = RAIL.BASE;
    g.fillRect(x0, y + oy, w, 3);
    g.fillStyle = RAIL.LIGHT;
    g.fillRect(x0, y + oy, w, 1);
    if (!striped) continue;
    // Painted stripes so the take-off point is readable at speed.
    for (let x = x0 + 4; x < x1 - 4; x += 16) {
      g.fillStyle = RAIL.STRIPE;
      g.fillRect(x, y + oy, 6, 2);
    }
  }
}
