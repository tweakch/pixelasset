// Headless smoke test for the sample game.
//
//   npm i playwright-core && npx playwright install chromium
//   python3 -m http.server 8765          # from the repo root
//   node game/smoke-test.js [screenshot-dir]
//
// Drives a homing bot at the nearest apple, then asserts the mode trade, the
// rail, the one-shot jump clip, the saddle toggle and every standing action
// actually work.
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
  // Sampling the rope from here aliases: a `peek()` round trip is tens of
  // milliseconds and the tension peaks are shorter than that, so a max taken
  // out here reads zero through a rope that visibly snapped taut. Poll inside
  // the page instead, once per frame.
  const sampleRope = (ms) => page.evaluate((duration) => new Promise((done) => {
    const peak = { taut: 0, dist: 0 };
    const t0 = performance.now();
    const tick = () => {
      const l = window.pasture.lead;
      peak.taut = Math.max(peak.taut, l.taut);
      peak.dist = Math.max(peak.dist, l.dist);
      if (performance.now() - t0 >= duration) done(peak);
      else requestAnimationFrame(tick);
    };
    requestAnimationFrame(tick);
  }), ms);

  const peek = () => page.evaluate(() => {
    const p = window.pasture, h = p.horse;
    return { mode: h.mode, action: h.action, facing: h.facing, x: h.x, y: h.y,
             stamina: h.stamina, jumping: h.jumping, airborne: p.airborne,
             saddled: p.saddled, assetId: p.assetId, turning: p.turning,
             ai: p.ai, mood: p.mood, inReach: p.inReach,
             characterId: p.characterId, pact: p.player.action,
             px: p.player.x, py: p.player.y, lead: p.lead,
             score: p.score, clears: p.clears, rail: p.rail };
  });

  // --- starts with the horse loose, and you are a separate sprite ---
  let st0 = await peek();
  assert(st0.mode === 'loose', 'round starts with the horse loose');
  assert(st0.assetId.endsWith('_loose'),
    `loose draws the riderless sheet (${st0.assetId})`);
  assert(st0.characterId === 'character_base_afoot',
    `the farmhand is its own asset (${st0.characterId})`);
  assert(!st0.inReach, 'the horse does not start in your hands');

  // The loose horse behaves on its own, with no input at all. Driven rather
  // than sampled: which behaviour it picks is random, so waiting to see two of
  // them is a coin flip dressed up as a test. Each behaviour is set directly
  // and then checked for the thing that behaviour is supposed to do.
  const drive = (state, extra = {}) => page.evaluate(([st, ex]) => {
    Object.assign(window.pasture.horse.ai, { state: st, timer: 6 }, ex);
  }, [state, extra]);

  await page.evaluate(() => {
    const h = window.pasture.horse;
    h.x = 150; h.y = 120; h.vx = 0; h.vy = 0;
  });
  await drive('wander', { tx: 360, ty: 130 });
  await page.waitForTimeout(900);
  const roaming = await peek();
  assert(roaming.x > 170, `a loose horse walks to where it decided to go (x ${Math.round(roaming.x)})`);
  assert(['walk', 'trot'].includes(roaming.action),
    `and animates from its own speed (${roaming.action})`);

  await page.evaluate(() => { window.pasture.horse.mood = 95; });
  await drive('frolic', { tx: 120, ty: 240 });
  await page.waitForTimeout(900);
  const frolic = await peek();
  assert(['trot', 'gallop'].includes(frolic.action),
    `a happy horse frolics (${frolic.action} at mood ${Math.round(frolic.mood)})`);

  await page.evaluate(() => { window.pasture.horse.vx = 0; window.pasture.horse.vy = 0; });
  await drive('graze');
  await page.waitForTimeout(700);
  const grazed = await peek();
  assert(grazed.action === 'graze', `and grazes when it settles (${grazed.action})`);
  assert(['east', 'west'].includes(grazed.facing),
    `turning side on, because graze is only drawn there (${grazed.facing})`);

  // --- walking on your own two feet ---
  await page.keyboard.down('ArrowLeft');
  await page.waitForTimeout(400);
  const walking = await peek();
  await page.keyboard.up('ArrowLeft');
  assert(['walk', 'run'].includes(walking.pact),
    `the farmhand animates from its own speed (${walking.pact})`);

  // --- call it over, then take the rope ---
  //
  // Put them on opposite sides of the rail first. That is the case the call has
  // to handle: a loose horse never jumps, so heading straight for the player
  // walks it into the fence and it has to go round the end instead.
  await page.evaluate(() => {
    const p = window.pasture;
    p.horse.x = p.rail.x0 + 30; p.horse.y = p.rail.y - 70;
    p.horse.vx = 0; p.horse.vy = 0; p.horse.mood = 50;
    p.player.x = p.rail.x0 + 40; p.player.y = p.rail.y + 60;
    p.player.vx = 0; p.player.vy = 0;
  });
  await page.waitForTimeout(120);
  assert(!(await peek()).inReach, 'the horse starts the call out of reach');
  await page.keyboard.press('f');
  let came = false;
  for (let i = 0; i < 140; i++) {
    const s = await peek();
    if (s.ai === 'come') came = true;
    if (s.inReach) break;
    await page.waitForTimeout(100);
  }
  assert(came, 'F calls the horse');
  const arrived = await peek();
  assert(arrived.inReach,
    'the called horse gets round the rail and comes within reach');
  await page.screenshot({ path: `${OUT}/t_loose.png` });

  await page.keyboard.press('l');
  await page.waitForTimeout(150);
  const roped = await peek();
  assert(roped.mode === 'lead', 'L takes the rope');
  // Leading draws the riderless sheet, not the pair one. The pair sheet has the
  // handler and the rope painted into it, which is what made turning snap; on
  // foot is two actors and a rope object now, and the pair sheet is kept for the
  // pet gesture alone.
  assert(roped.assetId.endsWith('_loose'),
    `leading draws the riderless sheet (${roped.assetId})`);
  assert(roped.lead.taut === 0,
    'taking the rope does not jerk the horse — it starts slack');

  // --- homing bot, on foot, collects apples ---
  const t0 = Date.now();
  const seen = new Set();
  while (Date.now() - t0 < 22000) {
    const s = await page.evaluate(() => {
      const p = window.pasture;
      if (!p.apples.length) return null;
      const h = p.horse;
      // Steer whoever the arrow keys actually move. Mounted that is the horse;
      // on foot it is the farmhand, and the horse trails behind on the rope —
      // homing the horse at the apple would aim the wrong actor and walk the
      // farmhand straight past it.
      const me = h.mode === 'ride' ? h : p.player;
      const a = p.apples.slice().sort((u, v) =>
        Math.hypot(u.x - me.x, u.y - me.y) - Math.hypot(v.x - me.x, v.y - me.y))[0];
      return { hx: me.x, hy: me.y, ax: a.x, ay: a.y,
               action: h.action, facing: h.facing, rail: p.rail };
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
  // On foot the farmhand picks them up, at the farmhand's feet. With a pair
  // sprite that was the same place as the horse; split, it is not, and the
  // farmhand is the one with hands.
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

  // --- Ctrl is the only way to reach a walk ---
  //
  // The gait comes from the speed, so a band the speed cannot land in is a clip
  // that never plays. A ridden horse tops out at 74 against a walk band that
  // ends at 40: without a slower cap it trots the moment it is moving, and
  // `walk` was dead on the ridden sheets for exactly that reason. Three keys,
  // three gaits, and the test is that they are three different gaits.
  const pace = async (mods) => {
    await page.evaluate(() => {
      const h = window.pasture.horse;
      h.x = 90; h.y = 180; h.vx = 0; h.vy = 0; h.stamina = 100;
    });
    for (const m of mods) await page.keyboard.down(m);
    await page.keyboard.down('ArrowRight');
    await page.waitForTimeout(900);
    const st = await peek();
    await page.keyboard.up('ArrowRight');
    for (const m of mods) await page.keyboard.up(m);
    await page.waitForTimeout(250);
    return st.action;
  };
  // Put the horse back where the rest of the run expects it. The pace samples
  // teleport it to a clear stretch of field, and the rope checks further down
  // measure a span between two anchors that has to have been laid from where
  // those anchors actually are.
  const home = await page.evaluate(() => {
    const h = window.pasture.horse;
    return { x: h.x, y: h.y };
  });
  const walked = await pace(['Control']);
  const plain = await pace([]);
  const fast = await pace(['Shift']);
  assert(walked === 'walk', `Ctrl walks a ridden horse (got ${walked})`);
  assert(plain === 'trot', `nothing held trots (got ${plain})`);
  assert(fast === 'gallop', `Shift gallops (got ${fast})`);
  assert(new Set([walked, plain, fast]).size === 3,
    `the three paces are three gaits (${walked}/${plain}/${fast})`);
  // Ctrl wins over Shift: a cap below beats a cap above, so a fumbled chord
  // slows you down rather than bolting.
  const both = await pace(['Control', 'Shift']);
  assert(both === 'walk', `Ctrl overrides Shift (got ${both})`);
  await page.evaluate((at) => {
    const h = window.pasture.horse;
    h.x = at.x; h.y = at.y; h.vx = 0; h.vy = 0;
  }, home);
  await page.waitForTimeout(200);

  // --- graze refills, mounted ---
  await page.evaluate(() => { window.pasture.horse.stamina = 20; });
  await page.keyboard.down('KeyG');
  await page.waitForTimeout(800);
  const grazing = await peek();
  assert(grazing.action === 'graze', `graze clip active mounted (got ${grazing.action})`);
  assert(grazing.stamina > 20, `graze refilled stamina (20 -> ${Math.round(grazing.stamina)})`);
  // The head stays down for as long as G is held — 800ms is five times the
  // 0.29s the whole arc used to take when the row was played as a loop — and
  // comes back up through its own clip rather than snapping to a gait.
  await page.keyboard.up('KeyG');
  await page.waitForTimeout(60);
  assert((await peek()).action === 'graze_up', 'releasing G lifts the head');
  await page.waitForTimeout(400);
  assert((await peek()).action !== 'graze_up', 'and the lift ends on its own');

  // --- pet is on foot only, and refills faster ---
  await page.keyboard.press('m');
  await page.waitForTimeout(120);
  await page.evaluate(() => { window.pasture.horse.stamina = 20; });
  await page.keyboard.down('KeyP');
  // Petting is authored as two rows that meet end to end and chain through
  // `next`, so sample across the hold: both halves have to play, and the entry
  // has to be the lean-in rather than the lean-out the game used to loop alone.
  // Long enough for both halves: nuzzle is 10 frames at 8fps and pet 9, so the
  // full cycle is ~2.4s and a shorter window only ever sees the lean-in.
  const petClips = new Set();
  let petEntry = null;
  for (let i = 0; i < 90; i++) {
    const a = (await peek()).action;
    if (a === 'nuzzle' || a === 'pet') { petEntry ??= a; petClips.add(a); }
    await page.waitForTimeout(35);
  }
  const petting = await peek();
  await page.keyboard.up('KeyP');
  assert(petting.mode === 'lead', 'M dismounts');
  assert(petEntry === 'nuzzle', `petting enters on the lean-in (got ${petEntry})`);
  assert(petClips.has('nuzzle') && petClips.has('pet'),
    `both halves of the pet gesture play (saw ${[...petClips].join(', ')})`);
  assert(petting.stamina > grazing.stamina, 'pet refills faster than graze');
  await page.screenshot({ path: `${OUT}/t_pet.png` });

  await page.keyboard.press('m');
  await page.waitForTimeout(120);
  await page.keyboard.down('KeyP');
  await page.waitForTimeout(300);
  const petMounted = await peek();
  await page.keyboard.up('KeyP');
  assert(!['pet', 'nuzzle'].includes(petMounted.action), 'cannot pet from the saddle');
  await page.keyboard.press('m');

  // --- turning out of profile plays the transition, on foot ---
  //
  // The clock. Everything from here on takes longer than one round, and a
  // finished round stops reading input entirely — so without this the rest of
  // the suite quietly tests a game that has stopped listening.
  await page.evaluate(() => window.pasture.keepRoundAlive());
  assert((await page.evaluate(() => window.pasture.state)) === 'playing',
    'the round is still live before the on-foot tests');

  // Walk east, then press up. On foot the horse is no longer part of your
  // sprite, so there is nothing to pivot: it keeps its own position and swings
  // round you on the rope. The old assertion here was that a `turn_` clip
  // played and held the facing until it finished — that artwork existed to hide
  // a rigid pair passing through itself, and a led horse has no rigid pair.
  //
  // What matters now is the thing the transition was papering over: the horse
  // must never jump. So sample it every tick through a full direction change and
  // assert it moved continuously, and that the rope never stretched past its
  // limit while doing it.
  await page.evaluate(() => {
    const p = window.pasture;
    Object.assign(p.horse, { x: 240, y: 120, vx: 0, vy: 0, jumping: false });
    Object.assign(p.player, { x: 266, y: 126, vx: 0, vy: 0 });
  });
  await page.waitForTimeout(60);
  await page.keyboard.down('ArrowRight');
  await page.waitForTimeout(600);
  assert((await peek()).facing === 'east', 'walking east on foot');
  await page.keyboard.up('ArrowRight');

  await page.keyboard.down('ArrowUp');
  let prev = await peek();
  let biggestStep = 0;
  let overStretched = 0;
  let sawNorth = false;
  for (let i = 0; i < 60; i++) {
    const t = await peek();
    biggestStep = Math.max(biggestStep, Math.hypot(t.x - prev.x, t.y - prev.y));
    if (t.lead.dist > t.lead.span + 1.5) overStretched += 1;
    if (t.facing === 'north') sawNorth = true;
    prev = t;
    await page.waitForTimeout(20);
  }
  await page.keyboard.up('ArrowUp');

  // A pair sprite turning put the horse ~40px away in a single frame. Anything
  // that size is the snap coming back.
  assert(biggestStep < 15,
    `a led horse never jumps when you change direction (worst step ${biggestStep.toFixed(1)}px)`);
  assert(overStretched === 0,
    `the rope never stretches past its limit (${overStretched} frames over)`);
  assert(sawNorth, 'the horse comes round to follow you north');
  const turned = await peek();
  assert(!String(turned.action).startsWith('turn_'),
    'and does it by moving, not by playing a pair transition');

  // The slack/taut distinction, which is the whole feel: standing still the rope
  // hangs and the horse is not being pulled at all.
  await page.evaluate(() => {
    const p = window.pasture;
    Object.assign(p.horse, { x: 240, y: 160, vx: 0, vy: 0 });
    Object.assign(p.player, { x: 258, y: 166, vx: 0, vy: 0 });
  });
  await page.waitForTimeout(200);
  assert((await peek()).lead.taut === 0, 'a rope with slack in it pulls nothing');

  await page.keyboard.down('ArrowRight');
  const pulled = await sampleRope(1600);
  await page.keyboard.up('ArrowRight');
  assert(pulled.taut > 0,
    `walking away puts the rope under tension (peak ${pulled.taut.toFixed(2)})`);
  assert(pulled.dist > pulled.taut && pulled.dist > 30,
    `and the gap opened past the rope's rest length (${pulled.dist.toFixed(0)}px)`);
  await page.waitForTimeout(600);
  assert((await peek()).lead.taut === 0, 'and it goes slack again when you stop');

  // The central claim, and the one worth a test of its own: how carefully you
  // move decides whether the horse is following you or being dragged. Three
  // speeds, one gradient, measured per frame at steady state.
  //
  // An earlier version of this tapped the key and sampled from Node, and
  // reported a flat zero — wrong twice over. Tapping is stop-start, which is the
  // opposite of careful, and a `peek()` round trip is longer than a tension
  // peak, so the max came back empty through a rope that had gone fully taut.
  // Hold the key and sample in-page.
  const ledAt = async (mods) => {
    await page.evaluate(() => {
      const p = window.pasture;
      Object.assign(p.horse, { x: 120, y: 140, vx: 0, vy: 0, facing: 'east', mood: 80 });
      Object.assign(p.player, { x: 146, y: 144, vx: 0, vy: 0, facing: 'east' });
      p.keepRoundAlive();
    });
    await page.waitForTimeout(250);
    const start = await peek();
    const from = start.x;
    const pfrom = start.px;
    for (const m of mods) await page.keyboard.down(m);
    await page.keyboard.down('ArrowRight');
    await page.waitForTimeout(450);            // settle before measuring
    const s = await sampleRope(1300);
    await page.keyboard.up('ArrowRight');
    for (const m of mods) await page.keyboard.up(m);
    await page.waitForTimeout(300);
    const end = await peek();
    return { ...s, moved: end.x - from, pmoved: end.px - pfrom };
  };

  const careful = await ledAt(['ControlLeft']);
  const normal = await ledAt([]);
  const bolting = await ledAt(['ShiftLeft']);

  // The horse covers most of the ground you do rather than a fixed distance —
  // asserting px is asserting the sample window. It lags by design, so "most"
  // is the honest bar; nothing at all would mean it never followed.
  assert(careful.moved > careful.pmoved * 0.55,
    `a carefully led horse keeps up (${careful.moved.toFixed(0)}px of your ` +
    `${careful.pmoved.toFixed(0)}px)`);
  assert(careful.taut === 0,
    `and does it on a rope that never loads (peak ${careful.taut.toFixed(2)})`);
  assert(bolting.taut > 0.9,
    `bolting drags it on a rope at its limit (peak ${bolting.taut.toFixed(2)})`);
  assert(careful.taut < normal.taut && normal.taut < bolting.taut,
    `tension tracks how hard you pull (${careful.taut.toFixed(2)} < ` +
    `${normal.taut.toFixed(2)} < ${bolting.taut.toFixed(2)})`);
  assert(bolting.dist <= 42,
    `and the rope still cannot be stretched (${bolting.dist.toFixed(0)}px, span 40)`);

  // Put the rope down. The petting section below is about borrowing it from a
  // LOOSE horse, so leaving the world on foot makes it test something else
  // entirely — and pass, because petting works from either state.
  await page.keyboard.press('l');
  await page.waitForTimeout(250);
  assert((await peek()).mode === 'loose',
    'the rope is back down before the petting tests');

  await page.evaluate(() => window.pasture.keepRoundAlive());
  assert((await page.evaluate(() => window.pasture.state)) === 'playing',
    'the round is still live before the petting tests');

  // Petting a loose horse borrows the rope for as long as P is held, because
  // the artwork only draws the two of you together — and gives it back after.
  await page.evaluate(() => {
    const p = window.pasture;
    p.player.x = p.horse.x + 18; p.player.y = p.horse.y + 4;
    p.player.vx = 0; p.player.vy = 0;
    p.horse.mood = 30;      // grazing has had a whole round to cap it out
  });
  await page.waitForTimeout(100);
  await page.keyboard.down('KeyP');
  await page.waitForTimeout(700);
  const petLoose = await peek();
  await page.keyboard.up('KeyP');
  await page.waitForTimeout(250);
  const afterPet = await peek();
  assert(petLoose.mode === 'lead' && ['nuzzle', 'pet'].includes(petLoose.action),
    `P on a loose horse pets it (${petLoose.mode}/${petLoose.action})`);
  assert(afterPet.mode === 'loose', 'letting go of P gives the horse back');
  assert(afterPet.mood > 30,
    `petting raises mood (30 -> ${Math.round(afterPet.mood)})`);

  // --- respawn hygiene ---
  const tooClose = await page.evaluate(() => {
    const p = window.pasture, h = p.horse;
    return p.apples.filter((a) => Math.hypot(a.x - h.x, a.y - h.y) < 20).length;
  });
  assert(tooClose === 0, 'no apple inside pickup radius after respawn');

  console.log('anim states :', [...seen].sort().join(', '));
  assert(new Set([...seen].map((s) => s.split('/')[1])).size >= 3, 'multiple facings used');
  assert(errors.length === 0, `no console errors (${errors.slice(0, 3).join(' | ')})`);

  // --- the animation debugger ---
  //
  // A second consumer of the same metadata, and the one that reads the newest
  // field in it. Checked here rather than in its own runner because the cost
  // is one page load and the failure it catches — a provenance field the
  // producer stopped emitting, or renamed — is otherwise silent until somebody
  // opens the page to report a bug and finds the tool broken instead.
  const dbg = await browser.newPage({ viewport: { width: 1400, height: 1000 } });
  const dbgErrors = [];
  dbg.on('console', (m) => { if (m.type() === 'error') dbgErrors.push(m.text()); });
  dbg.on('pageerror', (e) => dbgErrors.push('PAGEERROR: ' + e.message));
  await dbg.goto(`${BASE}/game/anim.html#id=${st0.characterId}&facing=west&clip=walk&frame=2`);
  await dbg.waitForFunction(() => document.querySelectorAll('#strip .thumb').length > 0,
    null, { timeout: 20000 });
  await dbg.waitForTimeout(500);
  const line = await dbg.textContent('#reportline');
  assert(line.includes(`${st0.characterId} · walk/west · frame 2`),
    `the debugger opens on the frame a link names (${line})`);
  assert(/row \d+ col 2/.test(line), `and names the source frame it came from (${line})`);

  // Every facing, because the figure-8 exists to exercise the facing rule and
  // a path that misses one is a path that cannot test it. A lemniscate misses
  // south.
  const facings = new Set();
  for (let i = 0; i < 80; i++) {
    facings.add((await dbg.textContent('#figfacing')).trim());
    await dbg.waitForTimeout(90);
  }
  console.log('figure-8     :', [...facings].sort().join(', '));
  for (const f of ['north', 'east', 'south', 'west']) {
    assert(facings.has(f), `the figure-8 faces ${f}`);
  }
  await dbg.screenshot({ path: `${OUT}/anim.png`, fullPage: true });
  assert(dbgErrors.length === 0,
    `no console errors in the debugger (${dbgErrors.slice(0, 3).join(' | ')})`);

  await browser.close();
})();
