// voxel_material.js — textured voxel material for the agent3d viewer.
//
// Goal: make the voxel (Litematic) view read as "real" as the GLB WITHOUT raising the
// voxel resolution (which would blow up the blast simulation's occupancy grid + collapse
// flood-fill and drop frames). Realism is added purely in the DISPLAY layer, at ~zero
// per-frame cost:
//
//   * a per-material TEXTURE ATLAS (mortar lines, wood grain, glass frame, leaf noise …)
//     built procedurally on a <canvas> — no PNG asset, works fully offline. The tile is a
//     near-grayscale *luminance detail* map; the real colour still comes from each voxel's
//     GLB face colour (instanceColor), so we get "material surface + real colour".
//   * baked ambient occlusion is applied by the caller (viewer.html) straight into
//     instanceColor, so it costs nothing per frame either.
//
// Everything stays a SINGLE InstancedMesh / one draw call, so blast.js + inspect.js keep
// operating on the exact same mesh unchanged.
import * as THREE from 'three';

const TILE = 32;   // px per tile on the atlas canvas
const COLS = 4;    // 4x4 grid

// material key -> atlas tile index. MUST stay in sync with textures/make_atlas.py.
export const MATERIAL_TILE = {
  reinforced_concrete: 0,
  stone_masonry: 1,
  brick_masonry: 2,
  steel: 3,
  timber: 4,
  glass: 5,
  tile: 6,
  concrete_light: 7,
  copper: 8,
  foliage: 9,
  sand: 10,
  soil: 11,
  vehicle_body: 12,
};
const DEFAULT_TILE = 13;

export function tileForMaterial(key) {
  return key != null && key in MATERIAL_TILE ? MATERIAL_TILE[key] : DEFAULT_TILE;
}

// ---- procedural grayscale detail tiles (mean ~0.85, range ~[0.6,1.0]) ----
function drawTile(ctx, ox, oy, kind) {
  // deterministic PRNG so the atlas is stable across loads
  let s = 1000 + ox * 31 + oy * 17;
  const rnd = () => { s = (s * 1103515245 + 12345) & 0x7fffffff; return s / 0x7fffffff; };
  const put = (x, y, v) => {
    const g = Math.max(0, Math.min(255, Math.round(v * 255)));
    ctx.fillStyle = `rgb(${g},${g},${g})`;
    ctx.fillRect(ox + x, oy + y, 1, 1);
  };
  const speckle = (base, amp, pores) => {
    for (let y = 0; y < TILE; y++) for (let x = 0; x < TILE; x++) put(x, y, base + (rnd() - 0.5) * amp);
    for (let i = 0; i < (pores || 0); i++) put((rnd() * TILE) | 0, (rnd() * TILE) | 0, 0.72);
  };

  switch (kind) {
    case 'reinforced_concrete':
    case 'concrete_light':
    case 'default':
      speckle(0.88, 0.10, 6); break;

    case 'stone_masonry':                       // sandstone strata
      for (let y = 0; y < TILE; y++) {
        const band = 0.9 + 0.06 * Math.sin(y * Math.PI / 5);
        for (let x = 0; x < TILE; x++) put(x, y, band + (rnd() - 0.5) * 0.05);
      }
      for (let y = 0; y < TILE; y += 8) for (let x = 0; x < TILE; x++) put(x, y, 0.70);
      break;

    case 'brick_masonry': {                     // running-bond brick
      const bh = 8, bw = 16;
      for (let y = 0; y < TILE; y++) {
        const off = ((y / bh) | 0) % 2 ? bw / 2 : 0;
        for (let x = 0; x < TILE; x++) {
          const mortar = (y % bh === 0) || ((x + off) % bw === 0);
          put(x, y, mortar ? 0.66 : 0.9 + (rnd() - 0.5) * 0.06);
        }
      }
      break;
    }

    case 'timber':                              // vertical wood grain
      for (let x = 0; x < TILE; x++) {
        const base = 0.88 + 0.06 * Math.sin(x * 0.9);
        for (let y = 0; y < TILE; y++) put(x, y, base + 0.05 * Math.sin(y * 0.35 + x * 0.6) + (rnd() - 0.5) * 0.04);
      }
      for (let x = 0; x < TILE; x += 11) for (let y = 0; y < TILE; y++) put(x, y, 0.72);
      break;

    case 'glass':                               // frame border + faint panes
      for (let y = 0; y < TILE; y++) for (let x = 0; x < TILE; x++) {
        const edge = x < 2 || y < 2 || x >= TILE - 2 || y >= TILE - 2;
        put(x, y, (edge ? 0.70 : 0.97) + 0.04 * Math.sin((x + y) * 0.5));
      }
      break;

    case 'tile':                                // overlapping roof tiles
      for (let y = 0; y < TILE; y++) for (let x = 0; x < TILE; x++) {
        let v = 0.78 + 0.16 * Math.sin((x % 8) / 8 * Math.PI);
        if (y % 8 === 0) v = 0.66;
        put(x, y, v + (rnd() - 0.5) * 0.04);
      }
      break;

    case 'steel':                               // brushed vertical + rivets
      for (let x = 0; x < TILE; x++) {
        const base = 0.9 + 0.05 * Math.sin(x * 2.3);
        for (let y = 0; y < TILE; y++) put(x, y, base + (rnd() - 0.5) * 0.03);
      }
      for (const cx of [4, TILE - 5]) for (const cy of [4, TILE - 5]) put(cx, cy, 0.68);
      break;

    case 'copper':                              // mottled patina
      speckle(0.86, 0.12, 0);
      for (let i = 0; i < 10; i++) put((rnd() * TILE) | 0, (rnd() * TILE) | 0, 0.75 + rnd() * 0.15);
      break;

    case 'foliage':                             // leafy clumps
      for (let y = 0; y < TILE; y++) for (let x = 0; x < TILE; x++) put(x, y, 0.78 + (rnd() - 0.5) * 0.22);
      for (let i = 0; i < 18; i++) put((rnd() * TILE) | 0, (rnd() * TILE) | 0, 0.6);
      break;

    case 'sand':
      speckle(0.9, 0.08, 0); break;

    case 'soil':
      speckle(0.82, 0.16, 12); break;

    case 'vehicle_body':                        // smooth panel + seam
      for (let y = 0; y < TILE; y++) for (let x = 0; x < TILE; x++) put(x, y, 0.92 + (rnd() - 0.5) * 0.03);
      for (let y = 0; y < TILE; y++) put((TILE / 2) | 0, y, 0.74);
      break;

    default:
      speckle(0.88, 0.10, 0);
  }
}

const TILE_KINDS = [
  'reinforced_concrete', 'stone_masonry', 'brick_masonry', 'steel',
  'timber', 'glass', 'tile', 'concrete_light',
  'copper', 'foliage', 'sand', 'soil',
  'vehicle_body', 'default', 'default', 'default',
];

// Build the atlas as a THREE.CanvasTexture (Nearest, no mipmaps → crisp block look).
export function buildAtlasTexture() {
  const size = TILE * COLS;
  const canvas = document.createElement('canvas');
  canvas.width = canvas.height = size;
  const ctx = canvas.getContext('2d');
  ctx.fillStyle = '#dcdcdc'; ctx.fillRect(0, 0, size, size);
  for (let i = 0; i < TILE_KINDS.length; i++) {
    const col = i % COLS, row = (i / COLS) | 0;
    drawTile(ctx, col * TILE, row * TILE, TILE_KINDS[i]);
  }
  const tex = new THREE.CanvasTexture(canvas);
  tex.magFilter = THREE.NearestFilter;
  tex.minFilter = THREE.NearestFilter;
  tex.generateMipmaps = false;
  // Treat the atlas as already-linear luminance (a multiplier), not an sRGB colour map, so
  // it isn't gamma-decoded. Defensive: use whichever "no conversion" constant this three
  // build exports.
  const linearCS = THREE.NoColorSpace ?? THREE.LinearSRGBColorSpace;
  if (linearCS !== undefined) tex.colorSpace = linearCS;
  tex.needsUpdate = true;
  return tex;
}

// A MeshLambertMaterial patched so each instance samples its material's atlas tile
// (per-instance `tileIndex` attribute) and multiplies it into the per-instance colour.
// Falls back gracefully: instances with no tile attribute sample tile 0.
export function createVoxelMaterial(atlasTex) {
  const mat = new THREE.MeshLambertMaterial();
  mat.onBeforeCompile = (shader) => {
    shader.uniforms.uAtlas = { value: atlasTex };
    shader.uniforms.uCols = { value: COLS };
    shader.uniforms.uTile = { value: TILE };
    shader.vertexShader = `#define USE_UV\nattribute float tileIndex;\nvarying float vTileIndex;\n`
      + shader.vertexShader.replace(
        '#include <uv_vertex>',
        '#include <uv_vertex>\n\tvTileIndex = tileIndex;'
      );
    shader.fragmentShader = `#define USE_UV\nuniform sampler2D uAtlas;\nuniform float uCols;\nuniform float uTile;\nvarying float vTileIndex;\n`
      + shader.fragmentShader.replace(
        '#include <color_fragment>',
        `#include <color_fragment>
\t{
\t\tfloat col = mod(vTileIndex, uCols);
\t\tfloat row = floor(vTileIndex / uCols);
\t\tvec2 tuv = (clamp(vUv, 0.0, 1.0) * (uTile - 1.0) + 0.5) / uTile;   // inset to avoid tile bleed
\t\tvec2 auv = (vec2(col, row) + tuv) / uCols;
\t\tdiffuseColor.rgb *= texture2D(uAtlas, auv).rgb;
\t}`
      );
  };
  // marker so the caller can detect the patched material if needed
  mat.userData.isVoxelAtlasMaterial = true;
  return mat;
}
