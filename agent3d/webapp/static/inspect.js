// inspect.js — client-side "剖析" (inspection) view modes for the agent3d voxel
// viewer. Three mutually-exclusive modes, all operating on the SAME voxel
// InstancedMesh that blast.js uses (single shared MeshLambertMaterial, per-voxel
// integer y). No backend, no extra geometry, no material cloning.
//
//   * 半透明透视 X-Ray      — drop the shared material to a low opacity, see inside
//   * 分层展开 Exploded     — lift each floor-band by a per-level gap (accordion)
//   * 剖切     Section Cut  — one world-space clipping plane you slide along X/Y/Z
//
// Coordinate mapping MUST match viewer.html / blast.js:
//   Plocal(x,y,z) = (x - cx + 0.5, y + 0.5, z - cz + 0.5),  cx = W/2, cz = L/2
// The whole voxGroup is scaled by pitch_m, so the section plane (world space) is
// computed from the model's block-space bounds times pitch_m.
import * as THREE from 'three';

const EXPLODE_TWEEN_MS = 500;        // accordion open/close duration
const EXPLODE_SNAP_COUNT = 150000;   // above this many voxels: snap (skip the per-frame tween) on low-end PCs

export function createInspectSystem(ctx) {
  const { voxGroup, mesh, voxData, blast } = ctx;
  const [W, , L] = voxData.dims;
  const blocks = voxData.blocks;              // [x,y,z,idx, ...]
  const count = voxData.count;
  const cx = W / 2, cz = L / 2;               // MUST match viewer.html / blast.js placement
  const pitch = voxData.pitch_m || 1;

  // Model extent in block space (recomputed locally — blast doesn't expose these).
  let minX = Infinity, maxX = -Infinity, minY = Infinity, maxY = -Infinity, minZ = Infinity, maxZ = -Infinity;
  for (let i = 0; i < count; i++) {
    const x = blocks[i * 4], y = blocks[i * 4 + 1], z = blocks[i * 4 + 2];
    if (x < minX) minX = x; if (x > maxX) maxX = x;
    if (y < minY) minY = y; if (y > maxY) maxY = y;
    if (z < minZ) minZ = z; if (z > maxZ) maxZ = z;
  }
  if (!isFinite(minY)) { minX = maxX = minY = maxY = minZ = maxZ = 0; }
  const spanY = maxY - minY + 1;

  const mat = mesh.material;                  // shared voxel MeshLambertMaterial
  const scratch = new THREE.Matrix4();

  // ---- state ----
  let mode = 'none';                          // 'none' | 'xray' | 'explode' | 'section'
  let opacity = 0.55;                         // 叠加 translucency (blade-coa both mode = 0.55)
  // Default floor height ≈ a real 3 m storey, converted to voxels by pitch (固定层高).
  let floorH = Math.min(Math.max(1, Math.round(3.0 / pitch)), Math.max(1, spanY));
  let gap = floorH;                           // vertical spacing added per floor level (voxels)
  const section = { axis: 'y', t: 0.5, flip: false };

  // explode tween: `sep` in [0,1] scales the per-floor gap; expApplied = last value written.
  let expCur = 0, expFrom = 0, expTarget = 0, expT0 = 0, expAnimating = false, expApplied = 0;

  // clipping plane (world space); mutated in place so slider drags need no shader recompile.
  const clipPlane = new THREE.Plane(new THREE.Vector3(0, -1, 0), 0);
  const AXIS = { x: new THREE.Vector3(1, 0, 0), y: new THREE.Vector3(0, 1, 0), z: new THREE.Vector3(0, 0, 1) };

  const easeInOut = (t) => (t < 0.5 ? 2 * t * t : 1 - Math.pow(-2 * t + 2, 2) / 2);
  const floorIndex = (y) => Math.floor((y - minY) / Math.max(1, floorH));

  function aliveArr() { return blast && blast.aliveMask ? blast.aliveMask() : null; }

  // ---- Exploded Floors: rewrite live instance matrices with a per-floor y offset ----
  function writeExplode(sep) {
    const alive = aliveArr();
    for (let i = 0; i < count; i++) {
      if (alive && !alive[i]) continue;       // leave killed voxels hidden (blast set a ZERO matrix)
      const x = blocks[i * 4], y = blocks[i * 4 + 1], z = blocks[i * 4 + 2];
      const yOff = y + floorIndex(y) * gap * sep;
      mesh.setMatrixAt(i, scratch.makeTranslation(x - cx + 0.5, yOff + 0.5, z - cz + 0.5));
    }
    mesh.instanceMatrix.needsUpdate = true;
    expApplied = sep;
  }

  function startExplode(target) {
    if (count > EXPLODE_SNAP_COUNT) {         // big scene → snap, don't tween per frame
      writeExplode(target); expCur = expTarget = target; expAnimating = false; return;
    }
    expFrom = expCur; expTarget = target; expT0 = performance.now(); expAnimating = true;
  }

  // ---- X-Ray (半透明透视) — blade-coa F2 BuildingViewer「叠加」(both) recipe -----------
  // Copied from BuildingViewer.tsx (~L3385 voxel / L2722 GLB, "real" view): DON'T swap the
  // material or recolour — keep the real per-voxel colours on the shared Lambert and just
  //   transparent = true;  opacity = 0.55;  depthWrite = TRUE;
  // The one thing that separates this from the old milky wash is depthWrite staying TRUE:
  // the z-buffer keeps proper front-to-back ordering so the translucent shell reads as a
  // clean tinted-glass solid instead of every overlapping voxel blending into mush.
  // (real view adds no emissive tint — hex 0x000000, intensity 0 — so colours stay true.)
  function applyXray() {
    mat.transparent = true;
    mat.opacity = opacity;                      // 叠加 default 0.55
    mat.depthWrite = true;                      // ← key vs. the old depthWrite:false milky look
    mat.needsUpdate = true;
  }
  function clearXray() {
    if (!mat.transparent && mat.opacity === 1) return;
    mat.transparent = false;
    mat.opacity = 1;
    mat.depthWrite = true;
    mat.needsUpdate = true;
  }

  // ---- Section Cut: one world-space plane on the shared material ----
  function updateClipPlane() {
    const a = section.axis, dir = AXIS[a];
    // block-space AABB (matches Plocal), then × pitch since voxGroup has only a uniform scale.
    let lo, hi;
    if (a === 'x') { lo = (minX - cx) * pitch; hi = (maxX - cx + 1) * pitch; }
    else if (a === 'y') { lo = minY * pitch; hi = (maxY + 1) * pitch; }
    else { lo = (minZ - cz) * pitch; hi = (maxZ - cz + 1) * pitch; }
    const pos = lo + (hi - lo) * section.t;
    const sign = section.flip ? 1 : -1;       // flip=false keeps the lower/near side (slide down to cut the top away)
    clipPlane.normal.copy(dir).multiplyScalar(sign);
    clipPlane.constant = -sign * pos;         // kept half-space: sign*comp + constant >= 0  ⇒  boundary at `pos`
  }
  function applySection() { updateClipPlane(); mat.clippingPlanes = [clipPlane]; mat.needsUpdate = true; }
  function clearSection() {
    if (!mat.clippingPlanes || mat.clippingPlanes.length === 0) return;
    mat.clippingPlanes = []; mat.needsUpdate = true;
  }

  // ---- public API ----
  function setMode(m) {
    if (m === mode) return;
    if (mode === 'xray') clearXray();
    if (mode === 'section') clearSection();
    if (mode === 'explode' && m !== 'explode') startExplode(0);   // fold the accordion back
    mode = m;
    if (m === 'xray') applyXray();
    if (m === 'section') applySection();
    if (m === 'explode') startExplode(1);                          // open the accordion
  }

  function setOpacity(v) { opacity = v; if (mode === 'xray') { mat.opacity = v; mat.needsUpdate = true; } }
  function setFloorHeight(v) { floorH = Math.max(1, Math.round(v)); if (mode === 'explode' && !expAnimating) writeExplode(expCur); }
  function setGap(v) { gap = Math.max(0, Math.round(v)); if (mode === 'explode' && !expAnimating) writeExplode(expCur); }
  function setSection(partial) {
    Object.assign(section, partial);
    if (mode === 'section') updateClipPlane();   // in-place; no recompile needed for a move
  }

  function update() {                            // per-frame; only the explode tween needs it
    if (!expAnimating) return;
    const t = Math.min(1, (performance.now() - expT0) / EXPLODE_TWEEN_MS);
    const sep = expFrom + (expTarget - expFrom) * easeInOut(t);
    writeExplode(sep);
    expCur = sep;
    if (t >= 1) { expCur = expTarget; expAnimating = false; }
  }

  function reset() {                             // restore everything to 正常 (called by viewer when leaving 剖析)
    clearXray();
    clearSection();
    if (expApplied !== 0) writeExplode(0);       // snap live voxels back to original y (killed ones stay hidden)
    expCur = expTarget = 0; expAnimating = false;
    mode = 'none';
  }

  return {
    setMode, setOpacity, setFloorHeight, setGap, setSection, update, reset,
    defaults: () => ({
      opacity, floorH, gap, axis: section.axis, t: section.t, flip: section.flip,
      floorHMax: Math.max(2, spanY), gapMax: Math.max(8, spanY),
    }),
  };
}
