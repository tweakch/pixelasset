// Headless smoke test for the sample game.
//
//   npm i playwright-core && npx playwright install chromium
//   python3 -m http.server 8765          # from the repo root
//   node game/smoke-test.js [screenshot-dir]
//
// Drives a homing bot at the nearest apple, then asserts the mode trade, the
// rail, the one-shot jump clip and every standing action actually work.
// Catches a wrong row index in sprites.js far faster than looking at it does.
const { chromium } = require('playwright-core');
const EXE = process.env.CHROME_PATH || undefined;
const OUT = process.argv[2] || '.';
const BASE = process.env.BASE_URL || 'http://localhost:8765';

const assert = (c, m) => { if (!c) { console.log('FAIL:', m); process.exitCode = 1; } else console.log('pass:', m); };

(async () => {
  const browser = await chromium.launch({
    ...(EXE ? { executablePath: EXE } : {}),
    args: ['--no-sandbox', '--disable-gpu', '--hide-scrollbars'],
  });
  const page = await browser.newPage({ viewport: { width: 1100, height: 760 } });
  const errors = [];
  page.on('console', (m) => { if (m.type() === 'error') errors.push(m.text()); });
  page.on('pageerror', (e) => errors.push('PAGEERROR: ' + e.message));
  page.on('requestfailed', (r) => errors.push('REQFAIL: ' + decodeURIComponent(r.url())));

  await page.goto(`${BASE}/game/?debug=1`, { waitUntil: 'networkidle' });
  await page.waitForFunction(() => window.pasture, null, { timeout: 20000 });

  const held = new Set();
  const setKeys = async (want) => {
    for (const k of [...held]) if (!want.has(k)) { await page.keyboard.up(k); held.delete(k); }
    for (const k of want) if (!held.has(k)) { await page.keyboard.down(k); held.add(k); }
  };
  const peek = () => page.evaluate(() => {
    const p = window.pasture, h = p.horse;
    return { mode: h.mode, action: h.action, facing: h.facing, x: h.x, y: h.y,
             stamina: h.stamina, jumping: h.jumping, airborne: p.airborne,
             score: p.score, clears: p.clears, rail: p.rail };
  });

  // --- starts on foot ---
  assert((await peek()).mode === 'lead', 'round starts on foot, leading');

  // --- homing bot, on foot, collects apples ---
  const t0 = Date.now();
  const seen = new Set();
  while (Date.now() - t0 < 22000) {
    const s = await page.evaluate(() => {
      const p = window.pasture;
      if (!p.apples.length) return null;
      const h = p.horse;
      const a = p.apples.slice().sort((u, v) =>
        Math.hypot(u.x - h.x, u.y - h.y) - Math.hypot(v.x - h.x, v.y - h.y))[0];
      return { hx: h.x, hy: h.y, ax: a.x, ay: a.y, action: h.action, facing: h.facing, rail: p.rail };
    });
    if (!s) break;
    seen.add(s.action + '/' + s.facing);

    // The bot has no pathfinding, and the rail is a real barrier: on foot it
    // cannot be jumped at all, so steering straight at an apple on the far side
    // pins the handler against it forever. Ride around the nearer end first.
    let tx = s.ax, ty = s.ay;
    const r = s.rail;
    if (Math.sign(s.hy - r.y) !== Math.sign(s.ay - r.y) &&
        s.hx > r.x0 - 14 && s.hx < r.x1 + 14) {
      tx = (s.hx - r.x0 < r.x1 - s.hx) ? r.x0 - 22 : r.x1 + 22;
      ty = s.hy;
    }
    const want = new Set();
    if (tx - s.hx > 6) want.add('ArrowRight'); else if (s.hx - tx > 6) want.add('ArrowLeft');
    if (ty - s.hy > 6) want.add('ArrowDown'); else if (s.hy - ty > 6) want.add('ArrowUp');
    await setKeys(want);
    await page.waitForTimeout(60);
  }
  await setKeys(new Set());

  let st = await peek();
  console.log('score on foot:', st.score);
  assert(st.score >= 5, 'apples collected on contact while on foot');
  await page.screenshot({ path: `${OUT}/t_lead.png` });

  // --- mounting toggles, and blocks pickup ---
  await page.keyboard.press('m');
  await page.waitForTimeout(120);
  assert((await peek()).mode === 'ride', 'M mounts');

  const before = (await peek()).score;
  await page.evaluate(() => {
    const p = window.pasture;
    p.apples.length = 0;
    p.apples.push({ x: p.horse.x, y: p.horse.y });   // dead on top of the rider
  });
  await page.waitForTimeout(600);
  assert((await peek()).score === before, 'mounted rider cannot pick an apple off the ground');

  await page.keyboard.press('m');
  await page.waitForTimeout(700);
  assert((await peek()).score > before, 'dismounting picks up the same apple');

  // --- on foot you cannot jump ---
  await page.keyboard.down('ArrowRight');
  await page.waitForTimeout(500);
  await page.keyboard.press('Space');
  await page.waitForTimeout(120);
  assert((await peek()).jumping === false, 'no jumping on foot');
  await page.keyboard.up('ArrowRight');
  await page.waitForTimeout(300);

  // --- mounted: the rail blocks a grounded horse ---
  await page.keyboard.press('m');
  await page.waitForTimeout(120);
  await page.evaluate(() => {
    const p = window.pasture;
    p.horse.x = (p.rail.x0 + p.rail.x1) / 2;
    p.horse.y = p.rail.y - 40;
    p.horse.vx = 0; p.horse.vy = 0; p.horse.jumping = false;
  });
  await page.keyboard.down('ArrowDown');
  await page.waitForTimeout(1400);
  await page.keyboard.up('ArrowDown');
  let blocked = await peek();
  assert(blocked.y < blocked.rail.y, `rail blocks a grounded horse (y ${Math.round(blocked.y)} < rail ${blocked.rail.y})`);
  await page.screenshot({ path: `${OUT}/t_rail_blocked.png` });

  // --- mounted: jumping clears it ---
  const clearsBefore = (await peek()).clears;
  await page.evaluate(() => {
    const p = window.pasture;
    p.horse.x = (p.rail.x0 + p.rail.x1) / 2;
    p.horse.y = p.rail.y - 95;
    p.horse.stamina = 100;
  });
  await page.keyboard.down('ArrowDown');
  await page.keyboard.down('ShiftLeft');
  let sawJumpClip = false, shot = false, launched = false;
  for (let i = 0; i < 120; i++) {
    const s = await peek();
    if (!launched && !s.jumping && s.y > s.rail.y - 34 && s.y < s.rail.y) {
      await page.keyboard.press('Space'); launched = true;
    }
    if (s.action === 'jump') sawJumpClip = true;
    if (s.airborne && Math.abs(s.y - s.rail.y) < 9 && !shot) {
      await page.screenshot({ path: `${OUT}/t_rail_jump.png` }); shot = true;
    }
    await page.waitForTimeout(14);
  }
  await page.keyboard.up('ArrowDown'); await page.keyboard.up('ShiftLeft');
  const jumped = await peek();
  assert(sawJumpClip, 'jump clip played');
  assert(jumped.clears > clearsBefore, `rail cleared while airborne (${clearsBefore} -> ${jumped.clears})`);
  assert(jumped.y > jumped.rail.y, 'horse ended up past the rail');

  // --- cannot jump from a standstill ---
  await page.evaluate(() => { const h = window.pasture.horse; h.vx = 0; h.vy = 0; h.jumping = false; });
  await page.keyboard.press('Space');
  await page.waitForTimeout(150);
  assert((await peek()).jumping === false, 'no jump from a standstill');

  // --- graze refills, mounted ---
  await page.evaluate(() => { window.pasture.horse.stamina = 20; });
  await page.keyboard.down('KeyG');
  await page.waitForTimeout(800);
  const grazing = await peek();
  await page.keyboard.up('KeyG');
  assert(grazing.action === 'graze', `graze clip active mounted (got ${grazing.action})`);
  assert(grazing.stamina > 20, `graze refilled stamina (20 -> ${Math.round(grazing.stamina)})`);

  // --- pet is on foot only, and refills faster ---
  await page.keyboard.press('m');
  await page.waitForTimeout(120);
  await page.evaluate(() => { window.pasture.horse.stamina = 20; });
  await page.keyboard.down('KeyP');
  await page.waitForTimeout(800);
  const petting = await peek();
  await page.keyboard.up('KeyP');
  assert(petting.mode === 'lead', 'M dismounts');
  assert(petting.action === 'pet', `pet clip active on foot (got ${petting.action})`);
  assert(petting.stamina > grazing.stamina, 'pet refills faster than graze');
  await page.screenshot({ path: `${OUT}/t_pet.png` });

  await page.keyboard.press('m');
  await page.waitForTimeout(120);
  await page.keyboard.down('KeyP');
  await page.waitForTimeout(300);
  const petMounted = await peek();
  await page.keyboard.up('KeyP');
  assert(petMounted.action !== 'pet', 'cannot pet from the saddle');
  await page.keyboard.press('m');

  // --- respawn hygiene ---
  const tooClose = await page.evaluate(() => {
    const p = window.pasture, h = p.horse;
    return p.apples.filter((a) => Math.hypot(a.x - h.x, a.y - h.y) < 20).length;
  });
  assert(tooClose === 0, 'no apple inside pickup radius after respawn');

  console.log('anim states :', [...seen].sort().join(', '));
  assert(new Set([...seen].map((s) => s.split('/')[1])).size >= 3, 'multiple facings used');
  assert(errors.length === 0, `no console errors (${errors.slice(0, 3).join(' | ')})`);
  await browser.close();
})();
