// missiles.js — parametric munition 3D models + combat parameters for the
// lightweight browser blast module. Shapes are inspired by the roster in
// wide-sim/docs/BLAST_REPRODUCTION_SPEC.md but rebuilt from cheap Three.js
// primitives. Shape is PURELY visual; the physics reads only `type` / `E0` /
// `blastR` / `penLayers`.
//
// Local model frame: the nose TIP sits at the origin (0,0,0) and the body
// extends toward -Y, so the "forward" direction is +Y. The blast module aligns
// +Y to the incident vector, so the tip leads along the flight path.
//
// Units are VOXELS (blocks). 1 block = pitch_m metres; the models live under the
// voxel group, so they scale with the building.
import * as THREE from 'three';

// type: 'pen' = 侵彻弹 (penetrator, punches floors then detonates deep)
//       'blast' = 杀爆弹 (blast-frag, detonates on contact)
// E0     : detonation energy budget (compared against per-voxel resistance kPa)
// blastR : max detonation radius in blocks (fireball never exceeds actual damage)
// penLayers: floors a penetrator punches through before detonating (pen only)
export const MUNITIONS = {
  MK82:   { label: 'MK82 · 杀爆弹',   type: 'blast', color: 0x3e4a30, L: 5.0, R: 0.52,
            nose: 'ogive', tail: 'finCan', bands: 1, E0: 250, blastR: 6 },
  MK84:   { label: 'MK84 · 杀爆弹',   type: 'blast', color: 0x3e4a30, L: 8.0, R: 0.5,
            nose: 'sharp', tail: 'finCan', bands: 2, E0: 460, blastR: 9 },
  JASSM:  { label: 'JASSM · 巡航杀爆', type: 'blast', color: 0x6b6f63, L: 9.0, R: 0.42,
            nose: 'blunt', tail: 'midWing', bands: 0, E0: 360, blastR: 8 },
  TLAM:   { label: 'TLAM · 巡航杀爆',  type: 'blast', color: 0x60655a, L: 9.5, R: 0.4,
            nose: 'blunt', tail: 'midWing', bands: 0, E0: 360, blastR: 8 },
  GBU31:  { label: 'GBU-31 · 侵彻弹',  type: 'pen', color: 0x4a5040, L: 6.0, R: 0.5,
            nose: 'ogive', tail: 'finCan', bands: 0, E0: 430, blastR: 7, penLayers: 3 },
  BLU109: { label: 'BLU-109 · 侵彻弹', type: 'pen', color: 0x5e656f, L: 5.5, R: 0.5,
            nose: 'sharp', tail: 'jdam', bands: 0, E0: 400, blastR: 6, penLayers: 4 },
  GBU57:  { label: 'GBU-57 · 钻地弹',  type: 'pen', color: 0x32363c, L: 10.0, R: 0.34,
            nose: 'sharp', tail: 'grid', bands: 0, E0: 660, blastR: 8, penLayers: 7 },
  RAMPAGE:{ label: 'Rampage · 侵彻弹', type: 'pen', color: 0x9aa0a8, L: 8.0, R: 0.45,
            nose: 'sharp', tail: 'rocket', bands: 0, E0: 500, blastR: 8, penLayers: 5 },
  SDB:    { label: 'GBU-39 SDB · 侵彻', type: 'pen', color: 0x8a8f96, L: 4.0, R: 0.3,
            nose: 'sharp', tail: 'midWing', bands: 0, E0: 210, blastR: 4, penLayers: 2 },
};

export const DEFAULT_MUNITION = 'MK84';

export function munitionList() {
  return Object.entries(MUNITIONS).map(([id, m]) => ({ id, label: m.label, type: m.type }));
}

const _mat = (color, opts = {}) => new THREE.MeshStandardMaterial({
  color, metalness: opts.metalness ?? 0.45, roughness: opts.roughness ?? 0.5,
  emissive: opts.emissive ?? 0x0a0a0a,
});
const BAND = 0xe2cb4e;   // warning band yellow

// A fin = thin box extending outward (+X) and along the body (Y). `count` fins
// evenly around the axis, centred at y=cy.
function addFins(group, { count = 4, cy, len, span, thick = 0.06, color, tilt = 0 }) {
  const geo = new THREE.BoxGeometry(span, len, thick);
  const m = _mat(color, { metalness: 0.35 });
  for (let i = 0; i < count; i++) {
    const fin = new THREE.Mesh(geo, m);
    fin.position.set(span / 2, cy, 0);
    const pivot = new THREE.Group();
    pivot.add(fin);
    pivot.rotation.y = (i / count) * Math.PI * 2 + tilt;
    group.add(pivot);
  }
}

function addRing(group, { cy, r, h = 0.18, color }) {
  const ring = new THREE.Mesh(new THREE.CylinderGeometry(r, r, h, 14), _mat(color, { roughness: 0.4 }));
  ring.position.y = cy;
  group.add(ring);
}

// Build a munition model. Returns { group, spec }. group's nose tip is at origin,
// body along -Y, forward = +Y.
export function buildMissile(id) {
  const spec = MUNITIONS[id] || MUNITIONS[DEFAULT_MUNITION];
  const { color, L, R } = spec;
  const group = new THREE.Group();
  const bodyMat = _mat(color);

  const noseFrac = spec.nose === 'sharp' ? 0.38 : spec.nose === 'ogive' ? 0.28 : 0.16;
  const noseLen = L * noseFrac;
  const bodyLen = L - noseLen;

  // nose cone: tip at y=0, base at y=-noseLen
  const nose = new THREE.Mesh(new THREE.ConeGeometry(R, noseLen, 16), bodyMat);
  nose.position.y = -noseLen / 2;
  group.add(nose);
  if (spec.nose !== 'sharp') {   // round the tip for ogive/blunt
    const cap = new THREE.Mesh(new THREE.SphereGeometry(R * (spec.nose === 'blunt' ? 0.9 : 0.5), 12, 8), bodyMat);
    cap.position.y = -noseLen * (spec.nose === 'blunt' ? 0.12 : 0.02);
    group.add(cap);
  }

  // body: from y=-noseLen down to y=-L
  const body = new THREE.Mesh(new THREE.CylinderGeometry(R, R, bodyLen, 16), bodyMat);
  body.position.y = -noseLen - bodyLen / 2;
  group.add(body);

  // warning bands on the forward body
  for (let b = 0; b < (spec.bands || 0); b++) {
    addRing(group, { cy: -noseLen - 0.5 - b * 0.9, r: R * 1.05, color: BAND });
  }

  const tailY = -L + Math.min(1.0, L * 0.14);   // fin band near the base
  switch (spec.tail) {
    case 'finCan': {
      const boat = new THREE.Mesh(new THREE.CylinderGeometry(R, R * 0.72, L * 0.12, 16), bodyMat);
      boat.position.y = -L + L * 0.06;
      group.add(boat);
      addFins(group, { cy: tailY, len: L * 0.2, span: R * 1.7, color });
      addRing(group, { cy: -L + 0.06, r: R * 0.75, h: 0.14, color: BAND });
      break;
    }
    case 'jdam': {   // flat base + 4 small strakes
      const base = new THREE.Mesh(new THREE.CylinderGeometry(R, R, 0.14, 16), _mat(0x2a2d32));
      base.position.set(0, -L + 0.07, 0);
      group.add(base);
      addFins(group, { cy: tailY, len: L * 0.16, span: R * 1.3, color });
      break;
    }
    case 'grid':   // lattice tail fins (GBU-57 look)
      addFins(group, { cy: -L + L * 0.16, len: L * 0.24, span: R * 2.4, thick: 0.05, color: 0x5a5f66 });
      break;
    case 'rocket': {  // nozzle bell + 4 fins
      const noz = new THREE.Mesh(new THREE.CylinderGeometry(R * 0.55, R * 0.85, L * 0.12, 14),
        _mat(0x26282c, { metalness: 0.7 }));
      noz.position.y = -L - L * 0.02;
      group.add(noz);
      addFins(group, { cy: tailY, len: L * 0.22, span: R * 1.9, color });
      break;
    }
    case 'midWing':   // cruise-style wings mid-body + small tail fins
      addFins(group, { count: 2, cy: -noseLen - bodyLen * 0.45, len: L * 0.1, span: R * 3.4, thick: 0.05, color });
      addFins(group, { count: 4, cy: tailY, len: L * 0.14, span: R * 1.3, color });
      break;
    default:
      addFins(group, { cy: tailY, len: L * 0.18, span: R * 1.6, color });
  }

  if (spec.nose === 'sharp' && id === 'RAMPAGE') {   // nose canards
    addFins(group, { count: 4, cy: -noseLen - 0.3, len: 0.5, span: R * 1.4, thick: 0.05, color });
  }

  return { group, spec };
}
