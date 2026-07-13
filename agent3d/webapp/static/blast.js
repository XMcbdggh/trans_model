// blast.js — lightweight, fully client-side "Minecraft-TNT-style" blast/bombing
// simulation for the agent3d voxel viewer. No Python backend, no server round
// trips. Operates on the voxel (InstancedMesh) representation loaded from
// voxels.json.
//
// What it does:
//   * builds an occupancy grid + per-voxel blast resistance (from voxels.json's
//     palette_resistance, baked by the pipeline from materials.py)
//   * TNT-lite detonation: energy vs per-voxel resistance with light occlusion,
//     so tougher materials survive and the fireball only reaches the farthest
//     block it actually destroyed
//   * penetrator (侵彻弹): punches holes through floors, drives deep, then blasts
//   * blast-frag (杀爆弹): detonates on contact
//   * capped debris + collapse particle pool (one InstancedMesh)
//   * floating-block collapse via a single flood-fill from the ground per strike
//   * modest fire/flash FX + descending missile flight
//
// All blast visuals live under `voxGroup` (block space; 1 block = pitch_m m).
import * as THREE from 'three';
import { MUNITIONS, DEFAULT_MUNITION, buildMissile } from './missiles.js';

// Fallback block->kPa (only used if voxels.json predates palette_resistance).
// Mirrors stand_trans/shared/materials.py; prefer the baked array.
const BLOCK_RES = {
  'minecraft:light_gray_concrete': 130, 'minecraft:smooth_sandstone': 55,
  'minecraft:bricks': 35, 'minecraft:iron_block': 240, 'minecraft:dark_oak_planks': 20,
  'minecraft:light_gray_stained_glass': 7, 'minecraft:orange_terracotta': 25,
  'minecraft:gray_concrete': 28, 'minecraft:cut_copper': 30, 'minecraft:oak_leaves': 20,
  'minecraft:sand': 10, 'minecraft:dirt': 12, 'minecraft:black_concrete': 45, 'minecraft:air': 0,
};

// --- tunables (block units, seconds) ---
const DEBRIS_CAP = 20000;    // combined flying-debris + collapse pool
const GRAV = 32;             // blocks / s^2
const DEBRIS_DRAG = 1.4;     // horizontal velocity damping coefficient
const OCC_MU = 0.3;          // occlusion attenuation per shielding block
const PEN_STEP = 0.5;        // penetrator march step (blocks)
const PEN_DRAIN = 1.0;       // energy drained per (resistance * step) solid cell
const PEN_CAP_BLOCKS = 48;   // hard cap on penetration travel
const FUZE_DELAY_MS = 180;   // penetrator: entry -> deep detonation
// Missile descent: the duration is derived from the actual travel distance so the
// on-screen speed stays consistent across scenes (a big compound no longer gets a
// blur-fast missile). Lower MISSILE_SPEED = slower / calmer descent.
const MISSILE_SPEED = 220;    // blocks / second (average over the flight)
const MISSILE_MIN_MS = 1500;  // floor so tiny scenes aren't instant
const MISSILE_MAX_MS = 3600;  // ceiling so huge scenes aren't tedious
const FIRE_MS = 640;
const FLASH_MS = 170;
const GROUND_BAND = 2;         // bottom layers treated as "ground" when seeding the collapse flood
const COLLAPSE_MAX_FRAC = 0.5; // skip a collapse that would drop >this fraction of live voxels (connectivity artifact)

export function createBlastSystem(ctx) {
  const { scene, camera, renderer, controls, voxGroup, mesh, voxData } = ctx;
  // Blast/damage sizes are authored in blocks at REF_BPM blocks/metre; scale them by the
  // scene's actual resolution so a given munition clears the SAME real-world volume at any
  // voxel resolution chosen on the blast page (only granularity changes, not blast reach).
  const REF_BPM = 4.0;
  const rScale = (voxData.pitch_m ? (1 / voxData.pitch_m) : REF_BPM) / REF_BPM;
  const [W, H, L] = voxData.dims;
  const blocks = voxData.blocks;          // [x,y,z,idx, ...]
  const count = voxData.count;
  const cx = W / 2, cz = L / 2;           // MUST match viewer.html instance placement
  const gi = (x, y, z) => x + W * (z + L * y);
  const inB = (x, y, z) => x >= 0 && x < W && y >= 0 && y < H && z >= 0 && z < L;
  const Plocal = (x, y, z) => new THREE.Vector3(x - cx + 0.5, y + 0.5, z - cz + 0.5);

  // resistance per palette index (prefer baked, else fallback table)
  const palRes = voxData.palette_resistance
    ? Float32Array.from(voxData.palette_resistance)
    : Float32Array.from((voxData.palette_ids || []).map(id => (id in BLOCK_RES ? BLOCK_RES[id] : 100)));
  const palColors = (voxData.palette || []).map(h => new THREE.Color(h));

  // occupancy grid: 0 = air/removed, else paletteIdx+1
  const occ = new Uint16Array(W * L * H);
  const giToInst = new Map();
  const alive = new Uint8Array(count);
  let minX = Infinity, maxX = -Infinity, minY = Infinity, maxY = -Infinity, minZ = Infinity, maxZ = -Infinity;
  for (let i = 0; i < count; i++) {
    const x = blocks[i * 4], y = blocks[i * 4 + 1], z = blocks[i * 4 + 2], idx = blocks[i * 4 + 3];
    const g = gi(x, y, z);
    occ[g] = idx + 1;
    giToInst.set(g, i);
    alive[i] = 1;
    if (x < minX) minX = x; if (x > maxX) maxX = x;
    if (y < minY) minY = y; if (y > maxY) maxY = y;
    if (z < minZ) minZ = z; if (z > maxZ) maxZ = z;
  }
  const groundY = minY;
  const restY = groundY + 0.5;
  const maxDim = Math.max(maxX - minX, maxY - minY, maxZ - minZ) || 20;

  // --- ground connectivity (for the floating-block collapse) ---
  // Collapse must only drop voxels that (a) rested on the ground in the intact
  // model AND (b) got cut off by a strike -- never "everything not currently
  // connected to the lowest layer", which wrongly purges pre-existing detached
  // decorations (or the whole model when connectivity across independently
  // voxelised elements is fragile). floodGround() flood-fills the CURRENT
  // occupancy from a BAND of bottom layers (so a 1-voxel gap or an outlier-low
  // voxel doesn't starve the seed) and marks reached cells with `stamp === gen`.
  const stamp = new Int32Array(W * L * H);
  const stack = new Int32Array(W * L * H > 1 << 20 ? 1 << 20 : W * L * H);
  let gen = 0;
  function floodGround() {
    gen++;
    let sp = 0;
    const yTop = Math.min(maxY, groundY + GROUND_BAND);
    for (let y = groundY; y <= yTop; y++)
      for (let z = minZ; z <= maxZ; z++)
        for (let x = minX; x <= maxX; x++) {
          const g = gi(x, y, z);
          if (occ[g] > 0 && stamp[g] !== gen) { stamp[g] = gen; if (sp < stack.length) stack[sp++] = g; }
        }
    while (sp > 0) {
      const g = stack[--sp];
      const x = g % W, y = Math.floor(g / (W * L)), z = Math.floor(g / W) % L;
      const nb = [[x + 1, y, z], [x - 1, y, z], [x, y + 1, z], [x, y - 1, z], [x, y, z + 1], [x, y, z - 1]];
      for (const [nx, ny, nz] of nb) {
        if (!inB(nx, ny, nz)) continue;
        const ng = gi(nx, ny, nz);
        if (occ[ng] > 0 && stamp[ng] !== gen) { stamp[ng] = gen; if (sp < stack.length) stack[sp++] = ng; }
      }
    }
    return gen;
  }
  // baseline: which voxels were genuinely ground-supported in the intact model.
  // Anything never connected to the ground band (loose decorations, or an entire
  // model with broken connectivity) is supported0=0 and can never be collapsed.
  const supported0 = new Uint8Array(count);
  {
    const g0 = floodGround();
    for (let i = 0; i < count; i++) {
      const g = gi(blocks[i * 4], blocks[i * 4 + 1], blocks[i * 4 + 2]);
      if (stamp[g] === g0) supported0[i] = 1;
    }
  }

  const resAt = (g) => palRes[occ[g] - 1] || 1;
  const topSolidAt = (x, z) => {
    if (!inB(x, groundY, z)) return -1;
    for (let y = maxY; y >= minY; y--) if (occ[gi(x, y, z)] > 0) return y;
    return -1;
  };

  // --- main mesh helpers ---
  const ZERO = new THREE.Matrix4().makeScale(0, 0, 0);
  const scratch = new THREE.Matrix4();
  function hideInstance(inst) { mesh.setMatrixAt(inst, ZERO); }
  function showInstance(inst, x, y, z) { mesh.setMatrixAt(inst, scratch.makeTranslation(x - cx + 0.5, y + 0.5, z - cz + 0.5)); }

  // --- debris / collapse particle pool ---
  const dgeo = new THREE.BoxGeometry(1, 1, 1);
  const dmat = new THREE.MeshLambertMaterial();
  const debris = new THREE.InstancedMesh(dgeo, dmat, DEBRIS_CAP);
  debris.count = 0;
  debris.frustumCulled = false;
  voxGroup.add(debris);
  const dpx = new Float32Array(DEBRIS_CAP), dpy = new Float32Array(DEBRIS_CAP), dpz = new Float32Array(DEBRIS_CAP);
  const dvx = new Float32Array(DEBRIS_CAP), dvy = new Float32Array(DEBRIS_CAP), dvz = new Float32Array(DEBRIS_CAP);
  const dstate = new Uint8Array(DEBRIS_CAP);   // 0 unused, 1 flying, 2 settled
  let dring = 0, dcount = 0;
  const _c = new THREE.Color();

  function spawnParticle(x, y, z, color, burstLocal, kind) {
    const s = dring; dring = (dring + 1) % DEBRIS_CAP;
    if (s >= dcount) dcount = s + 1;
    const p = Plocal(x, y, z);
    dpx[s] = p.x; dpy[s] = p.y; dpz[s] = p.z;
    if (kind === 'collapse') {                       // floating block: mostly straight down
      dvx[s] = (Math.random() - 0.5) * 2.4;
      dvy[s] = -(1.5 + Math.random() * 2.5);
      dvz[s] = (Math.random() - 0.5) * 2.4;
    } else {                                          // blasted chunk: outward + up
      const dir = p.clone().sub(burstLocal);
      if (dir.lengthSq() < 1e-4) dir.set(Math.random() - 0.5, Math.random(), Math.random() - 0.5);
      dir.normalize();
      const sp = 6 + Math.random() * 14;
      dvx[s] = dir.x * sp + (Math.random() - 0.5) * 3;
      dvy[s] = Math.abs(dir.y) * sp + 3 + Math.random() * 7;
      dvz[s] = dir.z * sp + (Math.random() - 0.5) * 3;
    }
    dstate[s] = 1;
    debris.setMatrixAt(s, scratch.makeTranslation(dpx[s], dpy[s], dpz[s]));
    debris.setColorAt(s, _c.copy(color || palColors[0] || _c.setHex(0x888888)));
    if (s + 1 > debris.count) debris.count = s + 1;
  }

  function updateDebris(dt) {
    let moved = false;
    for (let s = 0; s < dcount; s++) {
      if (dstate[s] !== 1) continue;
      moved = true;
      dvy[s] -= GRAV * dt;
      const damp = Math.max(0, 1 - DEBRIS_DRAG * dt);
      dvx[s] *= damp; dvz[s] *= damp;
      dpx[s] += dvx[s] * dt; dpy[s] += dvy[s] * dt; dpz[s] += dvz[s] * dt;
      if (dpy[s] <= restY) { dpy[s] = restY; dstate[s] = 2; dvx[s] = dvy[s] = dvz[s] = 0; }
      debris.setMatrixAt(s, scratch.makeTranslation(dpx[s], dpy[s], dpz[s]));
    }
    if (moved) { debris.instanceMatrix.needsUpdate = true; if (debris.instanceColor) debris.instanceColor.needsUpdate = true; }
  }

  // --- fire / flash FX ---
  const fx = [];
  function addFireball(bx, by, bz, radius) {
    const g = Plocal(bx, by, bz);
    const ball = new THREE.Mesh(
      new THREE.SphereGeometry(1, 16, 12),
      new THREE.MeshBasicMaterial({ color: 0xff7a24, transparent: true, opacity: 0.9, blending: THREE.AdditiveBlending, depthWrite: false }));
    ball.position.copy(g); ball.scale.setScalar(0.1); voxGroup.add(ball);
    const light = new THREE.PointLight(0xffb060, 6, Math.max(6, radius * 4), 2);
    light.position.copy(g); voxGroup.add(light);
    fx.push({ mesh: ball, light, t0: performance.now(), dur: FIRE_MS, r1: Math.max(1, radius) });
    // quick white core flash
    const core = new THREE.Mesh(
      new THREE.SphereGeometry(1, 12, 10),
      new THREE.MeshBasicMaterial({ color: 0xfff2d0, transparent: true, opacity: 0.95, blending: THREE.AdditiveBlending, depthWrite: false }));
    core.position.copy(g); core.scale.setScalar(Math.max(0.6, radius * 0.35)); voxGroup.add(core);
    fx.push({ mesh: core, light: null, t0: performance.now(), dur: FLASH_MS, r1: Math.max(0.6, radius * 0.5) });
  }
  function updateFx(now) {
    for (let i = fx.length - 1; i >= 0; i--) {
      const f = fx[i];
      const k = (now - f.t0) / f.dur;
      if (k >= 1) {
        voxGroup.remove(f.mesh); f.mesh.geometry.dispose(); f.mesh.material.dispose();
        if (f.light) voxGroup.remove(f.light);
        fx.splice(i, 1); continue;
      }
      const r = f.r1 * (0.2 + 0.8 * k);
      f.mesh.scale.setScalar(r);
      f.mesh.material.opacity = (1 - k) * 0.9;
      if (f.light) f.light.intensity = 6 * (1 - k);
    }
  }

  // --- missiles ---
  const missiles = [];
  function launchMissile(munitionId, aim, v, onImpact) {
    const { group } = buildMissile(munitionId);
    const target = Plocal(aim.x, aim.y, aim.z);
    const dist = maxDim * 1.4 + 34;
    const dur = Math.min(MISSILE_MAX_MS, Math.max(MISSILE_MIN_MS, dist / MISSILE_SPEED * 1000));
    const start = target.clone().addScaledVector(v, -dist);   // up-range along -v
    group.position.copy(start);
    group.quaternion.setFromUnitVectors(new THREE.Vector3(0, 1, 0), v);
    voxGroup.add(group);
    missiles.push({ group, start, target, t0: performance.now(), dur, onImpact });
  }
  function updateMissiles(now) {
    for (let i = missiles.length - 1; i >= 0; i--) {
      const m = missiles[i];
      let k = (now - m.t0) / m.dur;
      if (k >= 1) {
        voxGroup.remove(m.group);
        missiles.splice(i, 1);
        m.onImpact();
        continue;
      }
      const e = k * k;   // ease-in: accelerate as it falls
      m.group.position.copy(m.start).lerp(m.target, e);
    }
  }

  // --- scheduled callbacks (driven by the render clock) ---
  const timers = [];
  const schedule = (delayMs, fn) => timers.push({ due: performance.now() + delayMs, fn });
  function runTimers(now) {
    for (let i = timers.length - 1; i >= 0; i--) {
      if (now >= timers[i].due) { const t = timers[i]; timers.splice(i, 1); t.fn(); }
    }
  }

  // --- core: detonation ---
  function incident(diveDeg, azDeg) {
    const dr = diveDeg * Math.PI / 180, ar = azDeg * Math.PI / 180, h = Math.cos(dr);
    return new THREE.Vector3(h * Math.cos(ar), -Math.sin(dr), h * Math.sin(ar)).normalize();
  }

  // count solid blocks strictly between burst and cell (occlusion shielding)
  function occlusionBetween(bx, by, bz, x, y, z) {
    const dx = x - bx, dy = y - by, dz = z - bz;
    const d = Math.hypot(dx, dy, dz);
    const n = Math.floor(d);
    let occl = 0;
    for (let s = 1; s < n; s++) {
      const t = s / d;
      const gx = Math.floor(bx + dx * t + 0.5), gy = Math.floor(by + dy * t + 0.5), gz = Math.floor(bz + dz * t + 0.5);
      if (inB(gx, gy, gz) && occ[gi(gx, gy, gz)] > 0) occl++;
    }
    return occl;
  }

  function detonate(bx, by, bz, spec) {
    const R = spec.blastR * rScale, E0 = spec.E0;   // real-world blast radius kept constant across resolutions
    const x0 = Math.max(minX, Math.floor(bx - R)), x1 = Math.min(maxX, Math.ceil(bx + R));
    const y0 = Math.max(minY, Math.floor(by - R)), y1 = Math.min(maxY, Math.ceil(by + R));
    const z0 = Math.max(minZ, Math.floor(bz - R)), z1 = Math.min(maxZ, Math.ceil(bz + R));
    const burstLocal = Plocal(bx, by, bz);
    const kill = [];
    let maxHit = 0;
    for (let y = y0; y <= y1; y++)
      for (let z = z0; z <= z1; z++)
        for (let x = x0; x <= x1; x++) {
          const g = gi(x, y, z);
          if (occ[g] === 0) continue;
          const d = Math.hypot(x - bx, y - by, z - bz);
          if (d > R) continue;
          let energy = E0 * (1 - d / R);
          const occl = occlusionBetween(bx, by, bz, x, y, z);
          if (occl > 0) energy *= Math.exp(-OCC_MU * occl);
          if (energy >= resAt(g)) { kill.push(g); if (d > maxHit) maxHit = d; }
        }
    applyKills(kill, burstLocal, 'blast');
    addFireball(bx, by, bz, Math.max(1.2, maxHit));   // fire reaches only the farthest destroyed block
    collapsePass();
  }

  function applyKills(giList, burstLocal, kind) {
    if (!giList.length) return;
    for (const g of giList) {
      const inst = giToInst.get(g);
      const idx = occ[g] - 1;
      occ[g] = 0;
      if (inst !== undefined && alive[inst]) {
        alive[inst] = 0;
        hideInstance(inst);
        spawnParticle(g % W, Math.floor(g / (W * L)), Math.floor(g / W) % L, palColors[idx], burstLocal, kind);
      }
    }
    mesh.instanceMatrix.needsUpdate = true;
  }

  // --- penetrator: punch through floors then detonate deep ---
  function penetrate(aim, v, spec) {
    const pos = new THREE.Vector3(aim.x + 0.5, aim.y + 0.5, aim.z + 0.5);
    let E = spec.E0 * 1.5;
    let layers = 0, prevSolid = true, travel = 0;
    const punch = [];
    const burst = pos.clone();
    const penCap = PEN_CAP_BLOCKS * rScale;   // keep real-world penetration depth constant across resolutions
    while (travel < penCap) {
      pos.addScaledVector(v, PEN_STEP); travel += PEN_STEP;
      const cx2 = Math.floor(pos.x), cy2 = Math.floor(pos.y), cz2 = Math.floor(pos.z);
      if (!inB(cx2, cy2, cz2)) break;                       // punched clear through
      burst.copy(pos);
      const g = gi(cx2, cy2, cz2);
      const solid = occ[g] > 0;
      if (solid) {
        E -= resAt(g) * PEN_STEP * PEN_DRAIN;
        // punch a hole around the path so each floor gets a visible breach
        for (let ax = -1; ax <= 1; ax++) for (let ay = -1; ay <= 1; ay++) for (let az = -1; az <= 1; az++) {
          const hx = cx2 + ax, hy = cy2 + ay, hz = cz2 + az;
          if (inB(hx, hy, hz) && occ[gi(hx, hy, hz)] > 0) punch.push(gi(hx, hy, hz));
        }
        if (!prevSolid) layers++;
        prevSolid = true;
        if (layers >= (spec.penLayers || 3) || E <= 0) break;
      } else {
        prevSolid = false;
      }
    }
    applyKills(punch, Plocal(aim.x, aim.y, aim.z), 'blast');   // entry tunnel
    addFireball(aim.x, aim.y, aim.z, 1.4);                     // small entry flash
    const b = { x: burst.x - 0.5, y: burst.y - 0.5, z: burst.z - 0.5 };
    schedule(FUZE_DELAY_MS, () => detonate(b.x, b.y, b.z, spec));
  }

  // --- one-shot collapse: drop voxels that WERE ground-supported but got cut
  // off by this strike (NOT everything currently disconnected -- see floodGround) ---
  function collapsePass() {
    const g0 = floodGround();                               // re-flood current occupancy from the ground band
    const floating = [];
    for (let i = 0; i < count; i++) {
      if (!alive[i] || !supported0[i]) continue;            // only originally-supported voxels can ever fall
      const g = gi(blocks[i * 4], blocks[i * 4 + 1], blocks[i * 4 + 2]);
      if (stamp[g] !== g0) floating.push(g);                // was supported, now severed -> floating
    }
    if (!floating.length) return;
    // safety valve: one strike dropping most of the model is a connectivity
    // artifact (or the main support was severed), not a local overhang -> skip.
    let aliveNow = 0;
    for (let i = 0; i < count; i++) if (alive[i]) aliveNow++;
    if (floating.length > COLLAPSE_MAX_FRAC * aliveNow) {
      console.warn(`[blast] collapse skipped: ${floating.length}/${aliveNow} voxels would drop ` +
        `(>${COLLAPSE_MAX_FRAC * 100}%, likely a connectivity artifact, not a local overhang)`);
      return;
    }
    applyKills(floating, null, 'collapse');
  }

  // ---------- public strike API ----------
  let dive = 80, azimuth = 0, munition = DEFAULT_MUNITION;

  function strikeAt(aim, munitionId, diveDeg, azDeg) {
    const spec = MUNITIONS[munitionId] || MUNITIONS[DEFAULT_MUNITION];
    const v = incident(diveDeg, azDeg);
    launchMissile(munitionId, aim, v, () => {
      if (spec.type === 'pen') penetrate(aim, v, spec);
      else detonate(aim.x + v.x * 0.5, aim.y + v.y * 0.5, aim.z + v.z * 0.5, spec);
    });
  }

  // ---------- strike-point marker + picking ----------
  const marker = new THREE.Mesh(
    new THREE.SphereGeometry(0.9, 16, 12),
    new THREE.MeshBasicMaterial({ color: 0x3a7afe, transparent: true, opacity: 0.85, depthTest: false }));
  marker.visible = false; marker.renderOrder = 999; voxGroup.add(marker);

  // arrow showing the incoming trajectory / incident angle at the strike point:
  // the head sits at the strike point pointing along the incident vector, the
  // shaft trails up-range into the sky. Updates on pick and on angle change.
  const UP = new THREE.Vector3(0, 1, 0);
  const arrow = new THREE.Group();
  {
    // modest indicator sized to the building's HEIGHT (not its footprint) so a
    // wide/flat compound doesn't get a giant arrow; clamped to a sane range.
    const aLen = Math.min(40, Math.max(8, (maxY - minY) * 0.5));
    const headLen = aLen * 0.26, headR = Math.max(0.6, aLen * 0.06), shaftR = headR * 0.4, shaftLen = aLen - headLen;
    const amat = new THREE.MeshBasicMaterial({ color: 0xffb020 });
    const head = new THREE.Mesh(new THREE.ConeGeometry(headR, headLen, 16), amat);
    head.position.y = -headLen / 2;                       // cone tip at y=0
    const shaft = new THREE.Mesh(new THREE.CylinderGeometry(shaftR, shaftR, shaftLen, 12), amat);
    shaft.position.y = -headLen - shaftLen / 2;
    arrow.add(head); arrow.add(shaft);
  }
  arrow.visible = false; voxGroup.add(arrow);
  function updateIncidenceArrow() {
    if (!strike) { arrow.visible = false; return; }
    arrow.position.copy(Plocal(strike.x, strike.y, strike.z));
    arrow.quaternion.setFromUnitVectors(UP, incident(dive, azimuth));   // local +Y (tip) -> incident dir
    arrow.visible = true;
  }

  let strike = null;
  const raycaster = new THREE.Raycaster();
  const ndc = new THREE.Vector2();
  function pick(clientX, clientY) {
    const rect = renderer.domElement.getBoundingClientRect();
    ndc.x = ((clientX - rect.left) / rect.width) * 2 - 1;
    ndc.y = -((clientY - rect.top) / rect.height) * 2 + 1;
    raycaster.setFromCamera(ndc, camera);
    const hit = raycaster.intersectObject(mesh, false)[0];
    if (!hit || hit.instanceId == null) return false;
    const i = hit.instanceId;
    strike = { x: blocks[i * 4], y: blocks[i * 4 + 1], z: blocks[i * 4 + 2] };
    marker.position.copy(Plocal(strike.x, strike.y, strike.z));
    marker.visible = true;
    updateIncidenceArrow();
    return true;
  }

  // ---------- presets (aim points derived from the loaded building) ----------
  function buildingAims() {
    const mid = (a, b) => Math.round((a + b) / 2);
    const midX = mid(minX, maxX), midZ = mid(minZ, maxZ);
    const roof = (x, z) => ({ x, y: Math.max(topSolidAt(x, z), groundY), z });
    return {
      roof: roof(midX, midZ),
      west: roof(mid(minX, midX), midZ),
      east: roof(mid(midX, maxX), midZ),
      north: roof(midX, mid(minZ, midZ)),
      south: roof(midX, mid(midZ, maxZ)),
      door: { x: midX, y: Math.min(groundY + 3, maxY), z: minZ },
    };
  }
  const PRESETS = {
    surface: () => { const a = buildingAims(); return [
      { id: 'MK84', aim: a.roof, dive: 82, az: 0, delay: 0 },
      { id: 'MK82', aim: a.door, dive: 55, az: 90, delay: 900 }]; },
    pen_pair: () => { const a = buildingAims(); return [
      { id: 'BLU109', aim: a.roof, dive: 86, az: 20, delay: 0 },
      { id: 'GBU57', aim: a.roof, dive: 86, az: 200, delay: 1300 }]; },
    carpet: () => { const a = buildingAims(); const list = []; const pts = [a.roof, a.west, a.east, a.north, a.south];
      for (let i = 0; i < 8; i++) { const p = pts[i % pts.length];
        list.push({ id: i % 2 ? 'GBU57' : 'RAMPAGE',
          aim: { x: p.x + (Math.random() * 8 - 4) | 0, y: p.y, z: p.z + (Math.random() * 8 - 4) | 0 },
          dive: 84, az: (i * 45) % 360, delay: i * 650 }); }
      return list; },
    breach_pen: () => { const a = buildingAims(); return [
      { id: 'MK84', aim: a.roof, dive: 82, az: 0, delay: 0 },
      { id: 'MK84', aim: a.roof, dive: 82, az: 180, delay: 700 },
      { id: 'GBU57', aim: a.roof, dive: 86, az: 40, delay: 1800 },
      { id: 'BLU109', aim: a.west, dive: 84, az: 90, delay: 2600 },
      { id: 'BLU109', aim: a.east, dive: 84, az: 270, delay: 3200 }]; },
  };

  // ---------- per-frame ----------
  let lastNow = performance.now();
  function update() {
    const now = performance.now();
    const dt = Math.min(0.05, (now - lastNow) / 1000);
    lastNow = now;
    runTimers(now);
    updateMissiles(now);
    updateDebris(dt);
    updateFx(now);
  }

  function reset() {
    for (let i = 0; i < count; i++) {
      const x = blocks[i * 4], y = blocks[i * 4 + 1], z = blocks[i * 4 + 2], idx = blocks[i * 4 + 3];
      occ[gi(x, y, z)] = idx + 1; alive[i] = 1; showInstance(i, x, y, z);
    }
    mesh.instanceMatrix.needsUpdate = true;
    dring = 0; dcount = 0; debris.count = 0; dstate.fill(0);
    for (let i = fx.length - 1; i >= 0; i--) { voxGroup.remove(fx[i].mesh); if (fx[i].light) voxGroup.remove(fx[i].light); }
    fx.length = 0;
    for (const m of missiles) voxGroup.remove(m.group); missiles.length = 0;
    timers.length = 0;
  }

  return {
    update, pick, reset,
    aliveMask: () => alive,          // read-only live/killed instance flags (used by inspect.js 分层展开)
    setPickMarkerVisible: (v) => { const on = v && !!strike; marker.visible = on; arrow.visible = on; },
    setAim: (d, a) => { dive = d; azimuth = a; updateIncidenceArrow(); },
    hasStrike: () => !!strike,
    munitions: () => Object.entries(MUNITIONS).map(([id, m]) => ({ id, label: m.label, type: m.type })),
    presetKeys: () => Object.keys(PRESETS),
    // single shot: aims straight at the picked strike point
    fireSingle: ({ munitionId = munition, dive: d = dive, azimuth: a = azimuth } = {}) => {
      if (!strike) return false;
      strikeAt(strike, munitionId, d, a);
      return true;
    },
    // salvo (连投): scatter `count` shots randomly around the picked point
    fireSalvo: ({ munitionId = munition, dive: d = dive, azimuth: a = azimuth, count: n = 6, spread = 8 } = {}) => {
      if (!strike) return false;
      for (let i = 0; i < n; i++) {
        const ang = Math.random() * Math.PI * 2, rad = Math.sqrt(Math.random()) * spread;
        const ox = Math.round(Math.cos(ang) * rad), oz = Math.round(Math.sin(ang) * rad);
        const nx = strike.x + ox, nz = strike.z + oz;
        const ty = topSolidAt(nx, nz); const y = ty >= 0 ? ty : strike.y;
        const aim = { x: nx, y, z: nz };
        const dd = d + (Math.random() * 10 - 5), aa = a + (Math.random() * 40 - 20);
        schedule(i * 240, () => strikeAt(aim, munitionId, dd, aa));
      }
      return true;
    },
    runPreset: (key) => {
      const build = PRESETS[key]; if (!build) return false;
      for (const s of build()) schedule(s.delay, () => strikeAt(s.aim, s.id, s.dive, s.az));
      return true;
    },
  };
}
