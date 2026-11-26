/* 3D 제안서 — plan.json에서 직접 생성하는 절차적 3D 모델.
   좌표: plan의 u(전면도로 방향)·v(대지 안쪽) → three X=u, Z=-v, Y=높이 */
(function () {
  'use strict';
  const M = DATA.model;
  const T = THREE;
  const FL = M.floors;
  const fIndex = {}; FL.forEach((f, i) => fIndex[f.id] = i);
  const SLAB = 0.25;

  /* ---------- palette ---------- */
  const ZCOL = {
    unit: '#e9d6b4', core: '#b55a84', corridor: '#d7dad3', mep: '#9aa49f', retail: '#e8ac45',
    public: '#5f9f86', lobby: '#7fb6c9', community: '#a989c7', parking: '#8f9ca6', aisle: '#c4c9cc', ramp: '#6f7f8c'
  };
  const UCOL = ['#f1dfbd', '#e5c08c', '#d49c63', '#b97a45'];
  const ZLABEL = {
    unit: '세대', core: '코어', corridor: '복도·홀', mep: '설비', retail: '근린생활시설', public: '공공시설(도서관)',
    lobby: '주거 로비', community: '주민공동시설', parking: '주차', aisle: '차로', ramp: '램프'
  };
  const UTL = M.ut;
  const col = new T.Color();

  /* ---------- batched geometry ---------- */
  class Batch {
    constructor() { this.p = []; this.n = []; this.c = []; this.items = []; }
    hexa(v, color, item) {
      // v: 8 corners [x,y,z]: 0-3 bottom (ccw seen from top), 4-7 top
      col.set(color);
      const F = [[0, 3, 2, 1], [4, 5, 6, 7], [0, 1, 5, 4], [1, 2, 6, 5], [2, 3, 7, 6], [3, 0, 4, 7]];
      for (const f of F) {
        const a = v[f[0]], b = v[f[1]], c = v[f[2]], d = v[f[3]];
        const ux = b[0] - a[0], uy = b[1] - a[1], uz = b[2] - a[2];
        const wx = d[0] - a[0], wy = d[1] - a[1], wz = d[2] - a[2];
        let nx = uy * wz - uz * wy, ny = uz * wx - ux * wz, nz = ux * wy - uy * wx;
        const l = Math.hypot(nx, ny, nz) || 1; nx /= l; ny /= l; nz /= l;
        for (const q of [a, b, c, a, c, d]) {
          this.p.push(q[0], q[1], q[2]); this.n.push(nx, ny, nz); this.c.push(col.r, col.g, col.b);
        }
      }
      this.items.push(item || null);
    }
    // oriented box from plan segment a→b (u,v), thickness t, y0..y1
    seg(ax, av, bx, bv, t, y0, y1, color, item) {
      const du = bx - ax, dv = bv - av, L = Math.hypot(du, dv) || 1;
      const nu = -dv / L * t / 2, nv = du / L * t / 2;
      const q = [[ax - nu, av - nv], [bx - nu, bv - nv], [bx + nu, bv + nv], [ax + nu, av + nv]];
      // ensure ccw in (x=u, z=-v) seen from +y
      this.hexa(this.corners(q, y0, y1), color, item);
    }
    rect(u, v, w, h, y0, y1, color, item) {
      this.hexa(this.corners([[u, v], [u + w, v], [u + w, v + h], [u, v + h]], y0, y1), color, item);
    }
    corners(q, y0, y1) {
      // orientation so that faces point outward: compute signed area in (x, z)
      let s = 0; for (let i = 0; i < 4; i++) { const a = q[i], b = q[(i + 1) % 4]; s += a[0] * (-b[1]) - b[0] * (-a[1]); }
      const Q = s > 0 ? [q[0], q[3], q[2], q[1]] : q;
      const out = [];
      for (const [u, v] of Q) out.push([u, y0, -v]);
      for (const [u, v] of Q) out.push([u, y1, -v]);
      return out;
    }
    mesh(mat) {
      if (!this.p.length) return null;
      const g = new T.BufferGeometry();
      g.setAttribute('position', new T.Float32BufferAttribute(this.p, 3));
      g.setAttribute('normal', new T.Float32BufferAttribute(this.n, 3));
      g.setAttribute('color', new T.Float32BufferAttribute(this.c, 3));
      g.computeBoundingSphere();
      const m = new T.Mesh(g, mat);
      m.userData.items = this.items;
      m.userData.base = Float32Array.from(this.c);
      m.castShadow = true; m.receiveShadow = true;
      return m;
    }
  }

  function shapeOf(pts) {
    const s = new T.Shape();
    pts.forEach((p, i) => i ? s.lineTo(p[0], p[1]) : s.moveTo(p[0], p[1]));
    return s;
  }
  function extrude(pts, depth, mat, y0) {
    const g = new T.ExtrudeGeometry(shapeOf(pts), { depth, bevelEnabled: false });
    g.rotateX(-Math.PI / 2);
    const m = new T.Mesh(g, mat); m.position.y = y0 || 0; m.castShadow = true; m.receiveShadow = true;
    return m;
  }
  function flat(pts, mat, y) {
    const g = new T.ShapeGeometry(shapeOf(pts)); g.rotateX(-Math.PI / 2);
    const m = new T.Mesh(g, mat); m.position.y = y; m.receiveShadow = true; return m;
  }
  function line(pts, mat, y, close) {
    const a = pts.map(p => new T.Vector3(p[0], y, -p[1]));
    if (close) a.push(a[0].clone());
    return new T.Line(new T.BufferGeometry().setFromPoints(a), mat);
  }

  /* ---------- materials ---------- */
  const MAT = {
    facade: new T.MeshStandardMaterial({ color: '#e2dccf', roughness: .86, metalness: 0 }),
    podium: new T.MeshStandardMaterial({ color: '#5c5f5d', roughness: .7, metalness: .05 }),
    glass: new T.MeshStandardMaterial({ color: '#8fa9b6', roughness: .08, metalness: .35, transparent: true, opacity: .62, emissive: '#000000' }),
    slab: new T.MeshStandardMaterial({ color: '#d6d3cc', roughness: .9, transparent: true, opacity: 1 }),
    inner: new T.MeshStandardMaterial({ color: '#fbfaf7', roughness: .9 }),
    vcol: new T.MeshStandardMaterial({ vertexColors: true, roughness: .82, transparent: true, opacity: 1 }),
    patch: new T.MeshStandardMaterial({ vertexColors: true, roughness: .95 }),
    core: new T.MeshStandardMaterial({ vertexColors: true, roughness: .7, transparent: true, opacity: .92 }),
    shaft: new T.MeshStandardMaterial({ color: '#3b4a52', roughness: .5, transparent: true, opacity: .55 }),
    stair: new T.MeshStandardMaterial({ color: '#c6c1b6', roughness: .8 }),
    column: new T.MeshStandardMaterial({ color: '#c86f3c', roughness: .6 }),
    bearing: new T.MeshStandardMaterial({ color: '#d99a6c', roughness: .7, transparent: true, opacity: .85 }),
    stall: new T.MeshStandardMaterial({ vertexColors: true, roughness: .9 }),
    car: new T.MeshStandardMaterial({ vertexColors: true, roughness: .35, metalness: .4 }),
    ramp: new T.MeshStandardMaterial({ color: '#7d8995', roughness: .85, side: T.DoubleSide }),
    ctx: new T.MeshStandardMaterial({ color: '#cbccc5', roughness: .95, transparent: true, opacity: 1 }),
    existing: new T.MeshBasicMaterial({ color: '#c0563b', wireframe: true, transparent: true, opacity: .55 }),
    existingFill: new T.MeshStandardMaterial({ color: '#c0563b', transparent: true, opacity: .12, depthWrite: false }),
    ground: new T.MeshStandardMaterial({ color: '#d5d7d0', roughness: 1, transparent: true, opacity: 1 }),
    site: new T.MeshStandardMaterial({ color: '#cfd8cc', roughness: 1, polygonOffset: true, polygonOffsetFactor: -2 }),
    road: new T.MeshStandardMaterial({ color: '#b9bcbc', roughness: 1, polygonOffset: true, polygonOffsetFactor: -1 }),
    rail: new T.MeshStandardMaterial({ color: '#c98a6a', roughness: 1, transparent: true, opacity: .55, polygonOffset: true, polygonOffsetFactor: -3 }),
    parcel: new T.LineBasicMaterial({ color: '#a7aea6', transparent: true, opacity: .55 }),
    zoneLine: new T.LineBasicMaterial({ color: '#3f6f5f', transparent: true, opacity: .9 }),
    bline: new T.LineBasicMaterial({ color: '#c0563b' }),
    hi: new T.MeshStandardMaterial({ color: '#3f8a72', roughness: .6, transparent: true, opacity: .55, depthWrite: false }),
  };

  /* ---------- build ---------- */
  const scene = new T.Scene();
  const root = new T.Group(); scene.add(root);
  const ctxG = new T.Group(); root.add(ctxG);
  const floorsG = [];

  // ground & context
  const ground = new T.Mesh(new T.CircleGeometry(420, 64).rotateX(-Math.PI / 2), MAT.ground);
  ground.position.set(15, -0.02, -20); ground.receiveShadow = true; ctxG.add(ground);
  const siteMesh = flat(M.site, MAT.site, 0.0); ctxG.add(siteMesh);
  const siteLine = line(M.site, new T.LineBasicMaterial({ color: '#38655f' }), 0.05, true); ctxG.add(siteLine);
  const roadsG = new T.Group(); ctxG.add(roadsG);
  M.ctx.roads.forEach(r => { if (r.p.length > 3) roadsG.add(flat(r.p, MAT.road, 0.005)); });
  const railG = new T.Group(); ctxG.add(railG);
  M.ctx.rail.forEach(r => { if (r.p.length > 3) railG.add(flat(r.p, MAT.rail, 0.012)); });
  const parcelG = new T.Group(); ctxG.add(parcelG);
  M.ctx.parcels.forEach(p => parcelG.add(line(p, MAT.parcel, 0.03, false)));
  const zoningG = new T.Group(); ctxG.add(zoningG); zoningG.visible = false;
  const ZC = { '일반상업지역': '#c0563b', '제3종일반주거지역': '#d6a64a', '제1종일반주거지역': '#7aa06b' };
  M.ctx.zoning.forEach(z => zoningG.add(line(z.p, new T.LineBasicMaterial({ color: ZC[z.n] || '#555' }), 0.4, false)));
  const blineL = line(M.bline, MAT.bline, 0.08, false); ctxG.add(blineL);
  const bldG = new T.Group(); ctxG.add(bldG);
  const existingG = new T.Group(); root.add(existingG); existingG.visible = false;
  M.ctx.bld.forEach(b => {
    if (b.on) {
      existingG.add(extrude(b.p, b.h, MAT.existing));
      existingG.add(extrude(b.p, b.h, MAT.existingFill));
    } else bldG.add(extrude(b.p, b.h, MAT.ctx));
  });
  // trees along 망우로 (simple)
  const treeG = new T.Group(); ctxG.add(treeG);
  {
    const tb = new Batch();
    for (let u = -10; u <= 34; u += 7.5) {
      tb.rect(u - .12, -1.9, .24, .24, 0, 2.2, '#6b5a48');
      tb.rect(u - 1.1, -2.9, 2.2, 2.2, 2.6, 5.0, '#6f8f69');
    }
    const tm = tb.mesh(new T.MeshStandardMaterial({ vertexColors: true, roughness: 1 })); tm && treeG.add(tm);
  }

  /* ---------- furniture & fixtures (plan.json fixtures) ---------- */
  const FXK = M.fxk || [];
  function addFixture(b, x) {
    const [u, v, w, h, h3, ki] = x, k = FXK[ki] || 'table';
    const W = '#f2f0ea', WOOD = '#b98f63', FAB = '#8796a3', DARK = '#3a3f44', TILE = '#e9eef0';
    switch (k) {
      case 'bed_s': case 'bed_d':
        b.rect(u, v, w, h, 0, .3, WOOD); b.rect(u + .03, v + .03, w - .06, h - .06, .3, .52, '#f4f1ea');
        if (h > w) b.rect(u + .12, v + h - .5, w - .24, .4, .52, .62, '#dfe7ee'); else b.rect(u + w - .5, v + .12, .4, h - .24, .52, .62, '#dfe7ee'); break;
      case 'sofa': case 'sofa_s':
        b.rect(u, v, w, h, 0, .42, FAB);
        if (w > h) b.rect(u, v + h - .2, w, .2, .42, .8, FAB); else b.rect(u + w - .2, v, .2, h, .42, .8, FAB); break;
      case 'table': case 'table_s':
        b.rect(u, v, w, h, .7, .75, WOOD);
        [[u, v], [u + w - .06, v], [u, v + h - .06], [u + w - .06, v + h - .06]].forEach(p => b.rect(p[0], p[1], .06, .06, 0, .7, WOOD)); break;
      case 'counter': b.rect(u, v, w, h, 0, .82, W); b.rect(u - .01, v - .01, w + .02, h + .02, .82, .87, '#9a9a94'); break;
      case 'fridge': b.rect(u, v, w, h, 0, 1.8, '#d9dcdf'); break;
      case 'washer': b.rect(u, v, w, h, 0, .85, W); break;
      case 'wardrobe': b.rect(u, v, w, h, 0, 2.1, '#d8cbb6'); break;
      case 'shelf': b.rect(u, v, w, h, 0, 1.8, '#cdbfa8'); break;
      case 'shoe': b.rect(u, v, w, h, 0, 1.0, '#d8cbb6'); break;
      case 'tv': b.rect(u, v, w, h, 0, .45, '#cdbfa8'); b.rect(u + w * .1, v + h * .4, w * .8, Math.max(.05, h * .2), .45, 1.1, DARK); break;
      case 'shower': b.rect(u, v, w, h, 0, .05, TILE); b.rect(u, v, w, .03, .05, 1.9, '#bcd3db'); break;
      case 'toilet': b.rect(u + w * .15, v + h * .1, w * .7, h * .8, 0, .42, W); break;
      case 'basin': b.rect(u, v, w, h, 0, .85, W); break;
      default: b.rect(u, v, w, h, 0, Math.max(.3, h3 || .8), W);
    }
  }

  const rand = (s => () => (s = (s * 16807) % 2147483647) / 2147483647)(7);
  FL.forEach((f, fi) => {
    const g = new T.Group(); g.position.y = f.el; g.userData.f = f; root.add(g);
    const H = f.h - SLAB, top = f === FL[FL.length - 1];
    const parts = {};
    const below = f.lv < 0;
    // slab (floor slab lies at local y in [-SLAB, 0])
    parts.slab = extrude(f.ol, SLAB, below ? MAT.slab : MAT.slab, -SLAB);
    g.add(parts.slab);
    if (top) { parts.roof = extrude(f.ol, SLAB, MAT.slab, f.h - SLAB); g.add(parts.roof); }
    // walls
    const ext = new Batch(), gl = new Batch(), inn = new Batch(), bear = new Batch(), extCut = new Batch(), innCut = new Batch();
    const opsBy = {}; f.op.forEach(o => (opsBy[o[0]] = opsBy[o[0]] || []).push(o));
    f.wl.forEach((w, wi) => {
      const [ax, av, bx, bv, t, k, br] = w;
      const L = Math.hypot(bx - ax, bv - av); if (L < .01) return;
      const isExt = k === 0 || k === 1;
      const B = isExt ? ext : inn, BC = isExt ? extCut : innCut;
      const cutH = Math.min(1.3, H);
      const ops = (opsBy[wi] || []).slice().sort((a, b) => a[1] - b[1]);
      const at = s => [ax + (bx - ax) * s / L, av + (bv - av) * s / L];
      let s0 = 0;
      const piece = (s1, s2, y0, y1, bb) => { if (s2 - s1 < .01 || y1 - y0 < .01) return; const p = at(s1), q = at(s2); bb.seg(p[0], p[1], q[0], q[1], t, y0, y1, '#ffffff'); };
      ops.forEach(o => {
        const a = Math.max(0, o[1] - o[2] / 2), b = Math.min(L, o[1] + o[2] / 2);
        piece(s0, a, 0, H, B); piece(s0, a, 0, cutH, BC);
        const sill = o[5] ? o[4] : 0, head = Math.min(H, sill + o[3]);
        piece(a, b, 0, sill, B); piece(a, b, head, H, B);
        piece(a, b, 0, Math.min(sill, cutH), BC);
        if (o[5]) { const p = at(a), q = at(b); gl.seg(p[0], p[1], q[0], q[1], .04, sill, head, '#ffffff'); }
        s0 = Math.max(s0, b);
      });
      piece(s0, L, 0, H, B); piece(s0, L, 0, cutH, BC);
      if (br && !isExt) bear.seg(ax, av, bx, bv, t, 0, H, '#ffffff');
      if (br && isExt) bear.seg(ax, av, bx, bv, t + .02, 0, H, '#ffffff');
    });
    if (top) f.wl.forEach(w => { if (w[5] === 0) ext.seg(w[0], w[1], w[2], w[3], w[4], f.h, f.h + 1.1, '#ffffff'); });
    const podium = f.lv === 1 || f.lv === 2;
    parts.ext = ext.mesh(podium ? MAT.podium : MAT.facade);
    parts.glass = gl.mesh(MAT.glass); if (parts.glass) parts.glass.castShadow = false;
    parts.inner = inn.mesh(MAT.inner);
    parts.extCut = extCut.mesh(podium ? MAT.podium : MAT.facade);
    parts.innCut = innCut.mesh(MAT.inner);
    parts.bear = bear.mesh(MAT.bearing);
    // rooms: floor patches + program blocks
    const patch = new Batch(), prog = new Batch(), core = new Batch();
    f.rm.forEach(r => {
      const [x, y, w, h, zt, ni, ui] = r; const z = M.zt[zt];
      const name = M.names[ni];
      const c = z === 'unit' && ui >= 0 ? UCOL[f.un[ui][1]] : ZCOL[z];
      const item = { kind: z, name, unit: ui >= 0 ? f.un[ui] : null, floor: f.id };
      patch.rect(x + .04, y + .04, w - .08, h - .08, 0.005, 0.03, c, item);
      if (z !== 'unit') {
        prog.rect(x + .08, y + .08, w - .16, h - .16, 0.04, Math.max(.5, H - .35), ZCOL[z], { kind: z, name, floor: f.id });
        if (z === 'core') core.rect(x + .05, y + .05, w - .1, h - .1, 0, f.h - .02, ZCOL.core, { kind: 'core', name, floor: f.id });
      }
    });
    f.un.forEach(u => {
      const [name, ti, area, x, y, w, h] = u;
      prog.rect(x + .12, y + .12, w - .24, h - .24, 0.04, H - .35, UCOL[ti], { kind: 'unit', unitType: ti, name: name + '호', unit: u, floor: f.id });
    });
    parts.patch = patch.mesh(MAT.patch); parts.prog = prog.mesh(MAT.vcol); parts.core = core.mesh(MAT.core);
    // stairs, elevators
    const st = new Batch();
    f.st.forEach(s => {
      const [x, y, w, h, n, z0, z1, dir, axis] = s;
      for (let i = 0; i < n; i++) {
        const top = z0 + (i + 1) * (z1 - z0) / n;
        if (axis === 'v') { const d = h / n; const yy = dir > 0 ? y + i * d : y + h - (i + 1) * d; st.rect(x, yy, w, d, Math.max(0, top - .35), top, '#c6c1b6'); }
        else { const d = w / n; const xx = dir > 0 ? x + i * d : x + w - (i + 1) * d; st.rect(xx, y, d, h, Math.max(0, top - .35), top, '#c6c1b6'); }
      }
    });
    parts.stairs = st.mesh(new T.MeshStandardMaterial({ vertexColors: true, roughness: .8 }));
    const ev = new Batch();
    f.ev.forEach(e => ev.rect(e[0] + .15, e[1] + .15, e[2] - .3, e[3] - .3, 0, f.h, e[4] ? '#c0563b' : '#3b4a52'));
    parts.ev = ev.mesh(MAT.shaft);
    // columns
    const cb = new Batch();
    f.co.forEach(c => cb.rect(c[0] - c[2] / 2, c[1] - c[2] / 2, c[2], c[2], 0, f.h - SLAB, '#ffffff'));
    parts.cols = cb.mesh(MAT.column);
    // parking
    const sb = new Batch(), car = new Batch();
    const CARC = ['#d9dcde', '#2f3437', '#7d8287', '#a33b2f', '#2d4a6b', '#e8e6e0'];
    f.sl.forEach((s, si) => {
      const [x, y, w, h, ty] = s;
      sb.rect(x + .06, y + .06, w - .12, h - .12, .01, .04, ty === 1 ? '#5c86b0' : (ty === 2 ? '#e5e3dc' : '#f2f1ec'), { kind: 'stall', name: ty === 1 ? '장애인 주차' : (ty === 2 ? '확장형 주차' : '일반 주차'), floor: f.id });
      if (rand() < .62) {
        const long = h > w; const cw = long ? Math.min(1.85, w - .5) : 4.4, ch = long ? 4.4 : Math.min(1.85, h - .5);
        const cx = x + (w - cw) / 2, cy = y + (h - ch) / 2, c = CARC[Math.floor(rand() * CARC.length)];
        car.rect(cx, cy, cw, ch, .15, .85, c);
        car.rect(cx + (long ? .1 : .9), cy + (long ? .9 : .1), cw - (long ? .2 : 1.9), ch - (long ? 1.9 : .2), .85, 1.42, '#39434a');
      }
    });
    parts.stalls = sb.mesh(MAT.stall); parts.cars = car.mesh(MAT.car);
    if (f.fx && f.fx.length) { const fb = new Batch(); f.fx.forEach(x => addFixture(fb, x)); parts.fx = fb.mesh(new T.MeshStandardMaterial({ vertexColors: true, roughness: .7 })); }
    // ramps (sloped slab)
    if (f.rp.length) {
      const rb = new Batch();
      f.rp.forEach(r => {
        const [x, y, w, h, axis, s0, s1, hi, lo] = r;
        const zAt = s => hi + (lo - hi) * Math.min(1, Math.max(0, (s - s0) / (s1 - s0)));
        if (axis === 'v') {
          const za = zAt(y), zb = zAt(y + h);
          const v = [[x, za - .3, -y], [x + w, za - .3, -y], [x + w, zb - .3, -(y + h)], [x, zb - .3, -(y + h)],
                     [x, za, -y], [x + w, za, -y], [x + w, zb, -(y + h)], [x, zb, -(y + h)]];
          rb.hexa([v[0], v[3], v[2], v[1], v[4], v[7], v[6], v[5]].map(p => p), '#7d8995');
        } else {
          const za = zAt(x), zb = zAt(x + w);
          rb.hexa([[x, za - .3, -y], [x, za - .3, -(y + h)], [x + w, zb - .3, -(y + h)], [x + w, zb - .3, -y],
                   [x, za, -y], [x, za, -(y + h)], [x + w, zb, -(y + h)], [x + w, zb, -y]], '#7d8995');
        }
      });
      parts.ramp = rb.mesh(new T.MeshStandardMaterial({ vertexColors: true, roughness: .85, side: T.DoubleSide }));
    }
    for (const k in parts) if (parts[k]) { parts[k].userData.part = k; g.add(parts[k]); }
    g.userData.parts = parts;
    floorsG.push(g);
  });

  // highlight slab overlay (per floor, created on demand)
  const hiSlab = new T.Mesh(new T.BufferGeometry(), MAT.hi); hiSlab.visible = false; root.add(hiSlab);

  /* ---------- lights ---------- */
  const hemi = new T.HemisphereLight('#ffffff', '#b9b7ad', .75); scene.add(hemi);
  const sun = new T.DirectionalLight('#ffffff', 1.15);
  sun.position.set(-60, 110, 70); sun.target.position.set(15, 0, -20);
  sun.castShadow = true; sun.shadow.mapSize.set(2048, 2048);
  Object.assign(sun.shadow.camera, { left: -90, right: 90, top: 90, bottom: -90, near: 10, far: 320 });
  sun.shadow.bias = -0.0006; sun.shadow.normalBias = .04;
  scene.add(sun, sun.target);
  const fill = new T.DirectionalLight('#dfe8ff', .25); fill.position.set(80, 40, -90); scene.add(fill);

  /* ---------- backgrounds ---------- */
  function grad(stops) {
    const c = document.createElement('canvas'); c.width = 8; c.height = 512;
    const x = c.getContext('2d'); const g = x.createLinearGradient(0, 0, 0, 512);
    stops.forEach(([o, s]) => g.addColorStop(o, s)); x.fillStyle = g; x.fillRect(0, 0, 8, 512);
    const t = new T.CanvasTexture(c); if (T.sRGBEncoding) t.encoding = T.sRGBEncoding; return t;
  }
  const BG = {
    day: new T.Color('#f4f5f3'),
    dusk: grad([[0, '#1c2b3d'], [.45, '#5f6f86'], [.72, '#d49a72'], [1, '#f1c495']]),
    night: grad([[0, '#0b1117'], [.6, '#1b2631'], [1, '#33414b']]),
  };

  /* ---------- renderer ---------- */
  let renderer = null, camera, controls, host = null, raf = 0, needs = true;
  const ray = new T.Raycaster(), mouse = new T.Vector2();
  let tip = null, pickables = [], hoverCb = null;
  const center = new T.Vector3(13, 20, -20);

  function init() {
    if (renderer) return true;
    try {
      renderer = new T.WebGLRenderer({ antialias: true, preserveDrawingBuffer: false });
    } catch (e) { return false; }
    renderer.setPixelRatio(Math.min(2, devicePixelRatio || 1));
    renderer.shadowMap.enabled = true; renderer.shadowMap.type = T.PCFSoftShadowMap;
    if ('outputEncoding' in renderer && T.sRGBEncoding) renderer.outputEncoding = T.sRGBEncoding;
    renderer.toneMapping = T.ACESFilmicToneMapping; renderer.toneMappingExposure = .82;
    renderer.domElement.className = 'scene-canvas';
    camera = new T.PerspectiveCamera(32, 16 / 9, .5, 1600);
    camera.position.set(-70, 60, 40);
    controls = new T.OrbitControls(camera, renderer.domElement);
    controls.enableDamping = true; controls.dampingFactor = .08;
    controls.maxPolarAngle = Math.PI * .495; controls.minDistance = 8; controls.maxDistance = 520;
    controls.target.copy(center);
    controls.addEventListener('change', () => needs = true);
    controls.addEventListener('start', () => { tween = null; pan = null; controls.autoRotate = false; needs = true; });
    tip = document.createElement('div'); tip.className = 'scene-tip'; tip.hidden = true;
    renderer.domElement.addEventListener('pointermove', onMove);
    renderer.domElement.addEventListener('pointerleave', () => { tip.hidden = true; });
    renderer.domElement.addEventListener('click', onClick);
    return true;
  }
  function itemAt(e) {
    if (!pickables.length) return null;
    const r = renderer.domElement.getBoundingClientRect();
    mouse.set((e.clientX - r.left) / r.width * 2 - 1, -(e.clientY - r.top) / r.height * 2 + 1);
    ray.setFromCamera(mouse, camera);
    const hit = ray.intersectObjects(pickables.filter(o => o.visible && o.parent.visible), false)[0];
    if (!hit || !hit.object.userData.items) return null;
    return { item: hit.object.userData.items[Math.floor(hit.faceIndex / 12)], r, e };
  }
  function describe(it) {
    if (!it) return '';
    if (it.unit) { const u = it.unit; return `<b>${it.floor} · ${u[0]}호</b>${UTL[u[1]]} · ${u[2].toFixed(1)}㎡${it.kind === 'unit' && it.name && !it.name.endsWith('호') ? '<br><small>' + esc(it.name) + '</small>' : ''}`; }
    return `<b>${it.floor} · ${esc(it.name || ZLABEL[it.kind] || '')}</b>${ZLABEL[it.kind] || ''}`;
  }
  const esc = s => String(s).replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
  function onMove(e) {
    const h = itemAt(e);
    if (!h || !h.item) { tip.hidden = true; renderer.domElement.style.cursor = ''; return; }
    tip.innerHTML = describe(h.item); tip.hidden = false;
    const fit = h.r.width / renderer.domElement.offsetWidth || 1;
    tip.style.left = ((e.clientX - h.r.left) / fit + 14) + 'px'; tip.style.top = ((e.clientY - h.r.top) / fit + 14) + 'px';
    renderer.domElement.style.cursor = 'help';
  }
  function onClick(e) { const h = itemAt(e); if (h && h.item && hoverCb) hoverCb(h.item); }

  /* ---------- camera presets ---------- */
  const V = (u, y, v) => new T.Vector3(u, y, -v);
  // 카메라 프리셋은 51.9m(16층) 건물 기준 — 건물 높이에 맞춰 높이·거리를 늘이거나 줄임
  const TOPH = Math.max(10, ...FL.filter(f => f.lv > 0).map(f => f.el + f.h));
  const HS = Math.max(.6, Math.min(2.4, TOPH / 51.9));
  const SCALED = ['iso', 'aerial', 'hero', 'low', 'north', 'side', 'core', 'struct', 'street'];
  function view(name, cfg) {
    const pv = baseView(name, cfg);
    if (!SCALED.includes(name)) return pv;
    const [p, t] = pv, t2 = new T.Vector3(t.x, t.y * HS, t.z), k = Math.max(.85, HS);
    const p2 = p.clone().sub(t).multiplyScalar(k).add(t2); if (name === 'street') p2.y = 1.7;
    return [p2, t2];
  }
  function baseView(name, cfg) {
    const f = cfg.floor ? FL[fIndex[cfg.floor]] : null;
    const el = f ? f.el : 0;
    const P = {
      iso: [V(-82, 74, -92), V(13, 18, 20)],
      aerial: [V(-120, 210, -200), V(15, 0, 25)],
      street: [V(-38, 1.7, -34), V(18, 15, 16)],
      hero: [V(-88, 20, -78), V(16, 24, 18)],
      low: [V(118, 30, -96), V(12, 20, 18)],
      north: [V(92, 80, 140), V(13, 18, 20)],
      side: [V(-130, 38, -10), V(15, 26, 20)],
      top: [V(14.6, 230, 12), V(14.6, 0, 20)],
      plan: [V(14.6, el + 74, 6), V(14.6, el, 20.5)],
      floor: [V(-26, el + 52, -32), V(14, el, 20)],
      basement: [V(-60, 22, -50), V(14, -5, 20)],
      core: [V(-74, 66, -66), V(14, 18, 18)],
      struct: [V(-82, 40, -66), V(14, 10, 20)],
      park: [V(-34, 34, -30), V(12, -6, 22)],
      flows: [V(-30, 30, -46), V(16, 1, 14)],
    };
    if (name === 'unit' && cfg.unitBox) {
      const [x, y, w, h, e] = cfg.unitBox, cx = x + w / 2, cy = y + h / 2, d = Math.max(w, h) * 0.95 + 3.2;
      const ang = cfg.unitAng != null ? cfg.unitAng : -2.3;
      return [V(cx + Math.cos(ang) * d * .75, e + d * 1.05, cy + Math.sin(ang) * d * .75), V(cx, e + .4, cy)];
    }
    return P[name] || P.iso;
  }
  let tween = null;
  function flyTo(name, cfg, instant) {
    const [p, t] = view(name, cfg);
    if (instant) { camera.position.copy(p); controls.target.copy(t); controls.update(); tween = null; needs = true; return; }
    tween = { p0: camera.position.clone(), t0: controls.target.clone(), p, t, s: performance.now(), d: 1100 };
  }

  /* ---------- modes ---------- */
  let cur = {};
  const allParts = ['slab', 'roof', 'ext', 'glass', 'inner', 'extCut', 'innCut', 'bear', 'patch', 'prog', 'core', 'stairs', 'ev', 'cols', 'stalls', 'cars', 'ramp', 'fx'];
  function show(g, list) { const P = g.userData.parts; allParts.forEach(k => { if (P[k]) P[k].visible = list.includes(k); }); g.visible = list.length > 0; }

  function apply(cfg, instant) {
    cfg = Object.assign({ mode: 'exterior' }, cfg || {});
    cur = cfg;
    let mode = cfg.mode;
    const sim = mode === 'parksim', unitMode = mode === 'unit', flows = mode === 'flows';
    if (sim) mode = 'basement';
    if (flows) mode = 'exterior';
    const fi = cfg.floor != null ? fIndex[cfg.floor] : -1;
    const f = fi >= 0 ? FL[fi] : null;
    const gap = cfg.explode ? 4 : 0;
    pickables = [];
    hiSlab.visible = false;
    // defaults
    MAT.slab.opacity = 1; MAT.ctx.opacity = 1; MAT.ground.opacity = 1; MAT.vcol.opacity = 1;
    MAT.glass.emissive.set('#000000'); MAT.glass.opacity = .62;
    MAT.facade.color.set('#e2dccf'); MAT.facade.transparent = false; MAT.facade.opacity = 1;
    MAT.podium.transparent = false; MAT.podium.opacity = 1;
    ctxG.visible = true; bldG.visible = true; [roadsG, siteMesh, siteLine, blineL].forEach(o => o.visible = true); zoningG.visible = false; parcelG.visible = true; railG.visible = true; treeG.visible = true;
    existingG.visible = !!cfg.existing;
    scene.background = BG.day; scene.fog = null;
    hemi.intensity = .5; hemi.color.set('#ffffff'); hemi.groundColor.set('#b9b7ad');
    sun.intensity = 1.0; sun.color.set('#fff6ea'); sun.position.set(-60, 110, 70);
    fill.intensity = .25;
    renderer && (renderer.toneMappingExposure = .82);

    floorsG.forEach((g, i) => {
      const F = FL[i]; g.position.y = F.el + (F.lv > 0 ? F.lv - 1 : F.lv) * gap;
      const above = F.lv > 0;
      let list = [];
      switch (mode) {
        case 'cinema': case 'exterior': case 'compare':
          list = above ? ['slab', 'roof', 'ext', 'glass'] : []; break;
        case 'program':
          list = (above || cfg.withBasement) ? ['slab', 'prog', 'stairs'] : []; break;
        case 'floor': {
          if (i < fi) list = (F.lv > 0 || f.lv < 0) ? ['slab', 'ext', 'glass'] : [];
          else if (i === fi) list = ['slab', 'patch', 'extCut', 'innCut', 'stairs', 'ev', 'cols', 'stalls', 'cars', 'ramp', 'fx'];
          if (f && f.lv < 0 && F.lv < 0 && i < fi) list = ['slab', 'patch', 'stalls', 'cars', 'ramp', 'cols', 'extCut'];
          break; }
        case 'core':
          list = ['slab', 'core', 'stairs', 'ev', 'cols']; break;
        case 'struct':
          list = ['slab', 'cols', 'bear', 'roof']; break;
        case 'basement':
          list = F.lv < 0 ? ['slab', 'patch', 'innCut', 'extCut', 'stalls', 'cars', 'ramp', 'cols', 'stairs'] : (F.lv === 1 ? ['slab', 'ramp', 'ext', 'glass'] : ['slab', 'ext', 'glass']); break;
        case 'context':
          list = above ? ['slab', 'roof', 'ext', 'glass'] : []; break;
      }
      if (sim) list = list.filter(k => k !== 'cars');
      if (flows) list = F.lv > 1 ? ['slab', 'roof', 'ext', 'glass'] : F.lv === 1 ? ['slab', 'patch', 'extCut', 'innCut', 'ramp'] : F.lv === -1 ? ['slab', 'ramp', 'patch'] : [];
      show(g, list);
      const P = g.userData.parts;
      if (mode === 'program' && P.prog) pickables.push(P.prog);
      if (mode === 'floor' && i === fi && P.patch) pickables.push(P.patch);
      if (mode === 'basement' && F.lv < 0 && P.stalls) pickables.push(P.stalls);
    });

    if (mode === 'floor' || mode === 'basement') { MAT.ctx.opacity = .35; treeG.visible = mode === 'floor' && f && f.lv > 0; }
    if (mode === 'basement') {
      MAT.ground.opacity = .1; MAT.slab.opacity = .3; bldG.visible = false; treeG.visible = false;
      MAT.facade.transparent = true; MAT.facade.opacity = .12; MAT.podium.transparent = true; MAT.podium.opacity = .15; MAT.glass.opacity = .08;
      floorsG.forEach((g, i) => { if (FL[i].lv < 0) { /* keep */ } });
    }
    if (mode === 'floor' && f && f.lv < 0) { MAT.ground.opacity = .1; bldG.visible = false; treeG.visible = false; }
    if (mode === 'basement' || (mode === 'floor' && f && f.lv < 0)) { [roadsG, railG, siteMesh, siteLine, parcelG, blineL].forEach(o => o.visible = false); }
    if (mode === 'core' || mode === 'struct') {
      MAT.slab.opacity = mode === 'core' ? .18 : .55; MAT.ctx.opacity = .3; treeG.visible = false;
      if (mode === 'struct') { MAT.ground.opacity = .25; }
      if (mode === 'core') { MAT.ground.opacity = .5; }
    }
    if (mode === 'program') { MAT.ctx.opacity = .45; MAT.ground.opacity = cfg.withBasement ? .2 : 1; MAT.slab.opacity = .55; }
    if (mode === 'context') { zoningG.visible = true; MAT.ctx.opacity = 1; }
    if (mode === 'compare') { existingG.visible = true; }
    if (mode === 'cinema' || cfg.light === 'dusk') {
      scene.background = BG.dusk; scene.fog = new T.Fog('#c9a58a', 320, 760);
      hemi.intensity = .55; hemi.color.set('#ffd9b8'); hemi.groundColor.set('#3a4350');
      sun.intensity = 1.25; sun.color.set('#ffb680'); sun.position.set(-120, 38, 40);
      fill.intensity = .35; MAT.glass.emissive.set('#ffbf73'); MAT.glass.emissiveIntensity = .55; MAT.glass.opacity = .85;
      renderer && (renderer.toneMappingExposure = 1.0);
    }
    if (cfg.light === 'night' && mode === 'basement') {
      scene.background = BG.night; scene.fog = null; hemi.intensity = .55; hemi.color.set('#c9d6e6'); sun.intensity = .7; sun.color.set('#cfd8ff');
    } else if (cfg.light === 'night') {
      scene.background = BG.night; scene.fog = new T.Fog('#1b2631', 120, 420);
      hemi.intensity = .35; hemi.color.set('#9fb4c8'); sun.intensity = .35; sun.color.set('#9fb4ff');
      MAT.glass.emissive.set('#ffc77d'); MAT.glass.emissiveIntensity = .9; MAT.glass.opacity = .9;
    }
    unitG.visible = false; labelLayer && (labelLayer.innerHTML = ''); labels = [];
    if (unitMode) { buildUnit(cfg); ctxG.visible = false; existingG.visible = false; scene.background = BG.unit; }
    SIM.group.visible = sim; SIM.active = sim; if (sim && !SIM.ready) simInit();
    MAT.facade.depthWrite = true; MAT.podium.depthWrite = true; MAT.slab.depthWrite = true;
    FLOW.active = flows; FLOW.group.visible = flows;
    if (flows) {
      if (!FLOW.ready) flowInit();
      MAT.facade.transparent = true; MAT.facade.opacity = .08; MAT.facade.depthWrite = false;
      MAT.podium.transparent = true; MAT.podium.opacity = .12; MAT.podium.depthWrite = false;
      MAT.glass.opacity = 0; MAT.slab.opacity = .1; MAT.slab.depthWrite = false; bldG.visible = false; treeG.visible = false;
    }
    if (cfg.highlightFloors) {
      const ids = cfg.highlightFloors.map(id => fIndex[id]).filter(i => i != null);
      floorsG.forEach((g, i) => { if (!ids.includes(i) && g.visible && (mode === 'exterior')) { /* dim */ } });
      setHighlightSlabs(ids);
    }
    if (cfg.highlight) highlight(cfg.highlight); else highlight(null);
    if (controls) {
      controls.autoRotate = !!cfg.auto; controls.autoRotateSpeed = cfg.autoSpeed || .55;
      flyTo(cfg.view || defaultView(cfg.mode === 'flows' ? 'flows' : mode), cfg, instant);
      pan = null;
      if (cfg.pan) { const [pp, tt] = view(cfg.view || defaultView(mode), cfg); pan = { p: pp.clone(), tg: tt.clone(), t: 0 }; }
    }
    needs = true;
  }
  function defaultView(mode) {
    return { flows: 'flows', unit: 'unit', parksim: 'basement', cinema: 'hero', exterior: 'iso', program: 'iso', floor: 'floor', core: 'core', struct: 'struct', basement: 'basement', context: 'aerial', compare: 'side' }[mode] || 'iso';
  }
  function setHighlightSlabs(ids) {
    hiSlab.geometry.dispose();
    const geos = [];
    const b = new Batch();
    ids.forEach(i => { const F = FL[i]; const ol = F.ol; // bbox-less: extrude outline via Batch rects of rooms would be heavy; use outline extrusion
      const g = new T.ExtrudeGeometry(shapeOf(ol), { depth: F.h - .3, bevelEnabled: false }); g.rotateX(-Math.PI / 2); g.translate(-14, 0, 20); g.scale(1.03, 1, 1.03); g.translate(14, floorsG[i].position.y + .02, -20); geos.push(g); });
    if (!geos.length) { hiSlab.visible = false; return; }
    hiSlab.geometry = mergeGeos(geos); hiSlab.visible = true;
  }
  function mergeGeos(geos) {
    const pos = [], nor = [];
    geos.forEach(g => { const ng = g.index ? g.toNonIndexed() : g; pos.push(...ng.attributes.position.array); nor.push(...ng.attributes.normal.array); });
    const g = new T.BufferGeometry(); g.setAttribute('position', new T.Float32BufferAttribute(pos, 3)); g.setAttribute('normal', new T.Float32BufferAttribute(nor, 3)); return g;
  }

  // program highlight: dim every block whose kind (or unit type) isn't selected
  const dim = new T.Color('#e6e6e2');
  function highlight(sel) {
    floorsG.forEach(g => {
      ['prog', 'patch'].forEach(k => {
        const m = g.userData.parts[k]; if (!m) return;
        const c = m.geometry.attributes.color, base = m.userData.base, items = m.userData.items;
        for (let b = 0; b < items.length; b++) {
          const it = items[b];
          const on = !sel || (it && (sel.includes(it.kind) || (it.unitType != null && sel.includes('u' + it.unitType)) || (it.unit && sel.includes('u' + it.unit[1]) && it.kind === 'unit')));
          for (let v = b * 36; v < (b + 1) * 36; v++) {
            if (on) { c.array[v * 3] = base[v * 3]; c.array[v * 3 + 1] = base[v * 3 + 1]; c.array[v * 3 + 2] = base[v * 3 + 2]; }
            else { c.array[v * 3] = dim.r; c.array[v * 3 + 1] = dim.g; c.array[v * 3 + 2] = dim.b; }
          }
        }
        c.needsUpdate = true;
      });
    });
    needs = true;
  }

  /* ---------- single-unit view ---------- */
  const unitG = new T.Group(); root.add(unitG); unitG.visible = false;
  BG.unit = new T.Color('#eceee9');
  let labels = [], labelLayer = null;
  const RCOL = n => /욕실/.test(n) ? '#bcd3db' : /침실/.test(n) ? '#dcc39c' : /거실/.test(n) ? '#e6d2ae' : /주방|식당/.test(n) ? '#d9dbd6' : /현관/.test(n) ? '#c9c3b8' : /수납|다용도/.test(n) ? '#d2cbbd' : '#e0dbd0';
  function findUnit(fid, name) { const f = FL[fIndex[fid]]; return f && f.un.find(u => u[0] === name); }
  function buildUnit(cfg) {
    while (unitG.children.length) { const c = unitG.children.pop(); c.geometry && c.geometry.dispose(); }
    const f = FL[fIndex[cfg.floor]]; const u = f && findUnit(cfg.floor, cfg.unit); if (!u) return;
    const [name, ti, area, x, y, w, h] = u, pad = .25, H = f.h - SLAB;
    cfg.unitBox = [x, y, w, h, f.el];
    const inBox = (px, py, m = pad) => px >= x - m && px <= x + w + m && py >= y - m && py <= y + h + m;
    const slab = new Batch(); slab.rect(x - .3, y - .3, w + .6, h + .6, -SLAB, 0, '#cfcbc2');
    const pat = new Batch(), wall = new Batch(), gl = new Batch(), fx = new Batch();
    const rooms = f.rm.filter(r => inBox(r[0] + r[2] / 2, r[1] + r[3] / 2, -.05) && r[6] >= 0 && f.un[r[6]][0] === name);
    rooms.forEach(r => { const nm = M.names[r[5]]; pat.rect(r[0] + .03, r[1] + .03, r[2] - .06, r[3] - .06, .005, .03, RCOL(nm), { kind: 'unit', name: nm, unit: u, floor: f.id }); });
    const cut = cfg.cut === false ? H : 1.25;
    const opsBy = {}; f.op.forEach(o => (opsBy[o[0]] = opsBy[o[0]] || []).push(o));
    f.wl.forEach((wv, wi) => {
      const [ax, av, bx, bv, t, k] = wv; const L = Math.hypot(bx - ax, bv - av); if (L < .01) return;
      if (!inBox((ax + bx) / 2, (av + bv) / 2)) return;
      const col = (k === 0 || k === 1) ? '#e7e2d8' : (k === 4 || k === 5 ? '#d9d4ca' : '#f6f4ef');
      const at = sx => [ax + (bx - ax) * sx / L, av + (bv - av) * sx / L];
      const piece = (s1, s2, y0, y1) => { y1 = Math.min(y1, cut); if (s2 - s1 < .01 || y1 - y0 < .01) return; const p = at(s1), q = at(s2); wall.seg(p[0], p[1], q[0], q[1], t, y0, y1, col); };
      let s0 = 0; (opsBy[wi] || []).slice().sort((a, b) => a[1] - b[1]).forEach(o => {
        const a = Math.max(0, o[1] - o[2] / 2), b = Math.min(L, o[1] + o[2] / 2);
        piece(s0, a, 0, H); const sill = o[5] ? o[4] : 0, head = Math.min(H, sill + o[3]);
        piece(a, b, 0, sill); piece(a, b, head, H);
        if (o[5] && sill < cut) { const p = at(a), q = at(b); gl.seg(p[0], p[1], q[0], q[1], .04, sill, Math.min(head, cut), '#ffffff'); }
        s0 = Math.max(s0, b);
      });
      piece(s0, L, 0, H);
    });
    (f.fx || []).forEach(z => { if (inBox(z[0] + z[2] / 2, z[1] + z[3] / 2, -.02)) addFixture(fx, z); });
    const mk = (b, m) => { const me = b.mesh(m); if (me) { me.position.y = f.el; unitG.add(me); } return me; };
    mk(slab, new T.MeshStandardMaterial({ vertexColors: true, roughness: .9 }));
    const pm = mk(pat, new T.MeshStandardMaterial({ vertexColors: true, roughness: .9 })); if (pm) pickables.push(pm);
    mk(wall, new T.MeshStandardMaterial({ vertexColors: true, roughness: .85 }));
    const gm = mk(gl, new T.MeshStandardMaterial({ color: '#9fc0cf', transparent: true, opacity: .45, roughness: .05, metalness: .2 })); if (gm) gm.castShadow = false;
    mk(fx, new T.MeshStandardMaterial({ vertexColors: true, roughness: .6 }));
    unitG.visible = true;
    // room labels
    if (host) {
      if (!labelLayer || labelLayer.parentNode !== host) { labelLayer = document.createElement('div'); labelLayer.className = 'unit-labels'; labelLayer.dataset.rt = ''; host.appendChild(labelLayer); }
      labelLayer.innerHTML = '';
      labels = rooms.map(r => { const el = document.createElement('span'); el.innerHTML = `${esc(M.names[r[5]])}<small>${(r[2] * r[3]).toFixed(1)}㎡</small>`; labelLayer.appendChild(el); return { el, p: new T.Vector3(r[0] + r[2] / 2, f.el + 1.35, -(r[1] + r[3] / 2)) }; });
    }
  }
  const tmpV = new T.Vector3();
  function updateLabels() {
    if (!labels.length || !host) return;
    const w = host.clientWidth, h = host.clientHeight;
    labels.forEach(L => { tmpV.copy(L.p).project(camera); const vis = tmpV.z < 1; L.el.style.display = vis ? '' : 'none'; L.el.style.left = ((tmpV.x + 1) / 2 * w) + 'px'; L.el.style.top = ((1 - tmpV.y) / 2 * h) + 'px'; });
  }

  /* ---------- parking simulation — routes found from ramps & aisles of the plan ---------- */
  const SIM = { group: new T.Group(), ready: false, ok: false, active: false, running: false, mode: 'in', speed: 2, cars: [], stalls: [], acc: 0, statAcc: 0, onStat: null, done: { in: 0, out: 0 }, floors: [] };
  root.add(SIM.group); SIM.group.visible = false;
  const GRID = .5, LANE = 1.3;
  function rampEnds(F, r) {
    const [x, y, w, h, axis, s0, s1, hi, lo] = r, c = axis === 'v' ? x + w / 2 : y + h / 2;
    const P = s => axis === 'v' ? [c, s] : [s, c];
    return { hi: [...P(s0), F.el + hi], lo: [...P(s1), F.el + lo], dir: Math.sign(s1 - s0) || 1, axis };
  }
  const descending = (F, r) => Math.abs(r[7]) < .3 && r[8] < -.5;   // starts at this floor level, goes down
  function walkGrid(F) {
    const xs = F.ol.map(p => p[0]), ys = F.ol.map(p => p[1]);
    const x0 = Math.min(...xs) - 1, y0 = Math.min(...ys) - 1, nx = Math.ceil((Math.max(...xs) + 1 - x0) / GRID), ny = Math.ceil((Math.max(...ys) + 1 - y0) / GRID);
    const W = new Uint8Array(nx * ny);
    const mark = (x, y, w, h) => {
      const i0 = Math.max(0, Math.floor((x - x0) / GRID + .25)), i1 = Math.min(nx - 1, Math.ceil((x + w - x0) / GRID - .25) - 1);
      const j0 = Math.max(0, Math.floor((y - y0) / GRID + .25)), j1 = Math.min(ny - 1, Math.ceil((y + h - y0) / GRID - .25) - 1);
      for (let j = j0; j <= j1; j++) for (let i = i0; i <= i1; i++) W[j * nx + i] = 1;
    };
    F.rm.forEach(r => { const z = M.zt[r[4]]; if (z === 'aisle' || z === 'ramp') mark(r[0], r[1], r[2], r[3]); });
    F.rp.forEach(r => mark(r[0], r[1], r[2], r[3]));
    return { x0, y0, nx, ny, W, F };
  }
  const cellXY = (g, k) => [g.x0 + (k % g.nx + .5) * GRID, g.y0 + (Math.floor(k / g.nx) + .5) * GRID];
  function nearestCell(g, px, py) {
    let best = -1, bd = 1e9;
    for (let k = 0; k < g.W.length; k++) if (g.W[k]) { const [x, y] = cellXY(g, k), d = (x - px) ** 2 + (y - py) ** 2; if (d < bd) { bd = d; best = k; } }
    return best;
  }
  function bfs(g, s) {
    const par = new Int32Array(g.W.length).fill(-2); const q = new Int32Array(g.W.length); let h = 0, t = 0;
    if (s < 0) return par; par[s] = -1; q[t++] = s;
    while (h < t) {
      const k = q[h++], i = k % g.nx, j = Math.floor(k / g.nx);
      for (const [di, dj] of [[1, 0], [-1, 0], [0, 1], [0, -1]]) {
        const a = i + di, b = j + dj; if (a < 0 || b < 0 || a >= g.nx || b >= g.ny) continue;
        const n = b * g.nx + a; if (!g.W[n] || par[n] !== -2) continue; par[n] = k; q[t++] = n;
      }
    }
    return par;
  }
  function los(g, a, b) {
    const L = Math.hypot(b[0] - a[0], b[1] - a[1]), n = Math.max(1, Math.ceil(L / (GRID / 3)));
    for (let s = 0; s <= n; s++) {
      const x = a[0] + (b[0] - a[0]) * s / n, y = a[1] + (b[1] - a[1]) * s / n;
      const i = Math.floor((x - g.x0) / GRID), j = Math.floor((y - g.y0) / GRID);
      if (i < 0 || j < 0 || i >= g.nx || j >= g.ny || !g.W[j * g.nx + i]) return false;
    }
    return true;
  }
  function route(g, par, goal) {
    if (goal < 0 || par[goal] === -2) return null;
    const cells = []; for (let k = goal; k !== -1; k = par[k]) cells.push(cellXY(g, k));
    cells.reverse();
    const out = [cells[0]]; let i = 0;
    while (i < cells.length - 1) { let j = cells.length - 1; while (j > i + 1 && !los(g, cells[i], cells[j])) j--; out.push(cells[j]); i = j; }
    return out;
  }
  // right-hand lane offset (keeps the last point = stall centre)
  function offsetPath(p, d, keepLast) {
    const n = p.length, out = [];
    for (let i = 0; i < n; i++) {
      if ((keepLast && i === n - 1) || (!keepLast && i === 0)) { out.push(p[i].slice()); continue; }
      const ns = [];
      [[p[i - 1], p[i]], [p[i], p[i + 1]]].forEach(([a, b]) => { if (!a || !b) return; const dx = b[0] - a[0], dy = b[1] - a[1], L = Math.hypot(dx, dy); if (L > .05) ns.push([dy / L, -dx / L]); });
      if (!ns.length) { out.push(p[i].slice()); continue; }
      let mx = ns.reduce((s, n) => s + n[0], 0), my = ns.reduce((s, n) => s + n[1], 0), ml = Math.hypot(mx, my);
      if (ml < .05) { mx = ns[0][0]; my = ns[0][1]; ml = 1; }
      mx /= ml; my /= ml; const cos = Math.max(.5, mx * ns[0][0] + my * ns[0][1]);
      out.push([p[i][0] + mx * d / cos, p[i][1] + my * d / cos, p[i][2]]);
    }
    return out;
  }
  function plen(p) { let s = 0; for (let i = 1; i < p.length; i++) s += Math.hypot(p[i][0] - p[i - 1][0], p[i][1] - p[i - 1][1], p[i][2] - p[i - 1][2]); return s; }
  function simInit() {
    if (SIM.ready) return; SIM.ready = true;
    const ground = FL.filter(f => f.lv > 0).sort((a, b) => a.lv - b.lv).find(f => f.rp.some(r => descending(f, r)));
    const base = FL.filter(f => f.lv < 0).sort((a, b) => b.lv - a.lv);
    if (!ground || !base.length) return;
    const r0 = rampEnds(ground, ground.rp.find(r => descending(ground, r)));
    const street = r0.axis === 'v' ? [r0.hi[0], r0.hi[1] - r0.dir * 14, ground.el] : [r0.hi[0] - r0.dir * 14, r0.hi[1], ground.el];
    let pre = [street, [r0.hi[0], r0.hi[1], ground.el], r0.lo], arrive = r0.lo;
    for (const F of base) {
      if (Math.abs(arrive[2] - F.el) > .6) break;
      const g = walkGrid(F), s = nearestCell(g, arrive[0], arrive[1]), par = bfs(g, s);
      const at = p => [p[0], p[1], F.el];
      F.sl.forEach(st => {
        const [x, y, w, h, ty] = st, cx = x + w / 2, cy = y + h / 2;
        const r = route(g, par, nearestCell(g, cx, cy)); if (!r) return;
        const path = pre.concat(r.map(at), [[cx, cy, F.el]]);
        SIM.stalls.push({ floor: F.id, cx, cy, el: F.el, ty, path, len: plen(path), car: null, busy: false });
      });
      SIM.floors.push(F.id);
      const down = F.rp.find(r => descending(F, r)); if (!down) break;
      const e = rampEnds(F, down), r = route(g, par, nearestCell(g, e.hi[0], e.hi[1])); if (!r) break;
      pre = pre.concat(r.map(at), [[e.hi[0], e.hi[1], F.el], e.lo]); arrive = e.lo;
    }
    SIM.stalls.forEach(s => { s.inPath = offsetPath(s.path, LANE, true); s.outPath = offsetPath(s.path.slice().reverse(), LANE, false); });
    SIM.stalls.sort((p, q) => p.len - q.len);
    SIM.ok = SIM.stalls.length > 0;
    const geo = (c) => { const b = new Batch(); b.rect(-2.2, -.9, 4.4, 1.8, .15, .82, c); b.rect(-1.15, -.82, 2.3, 1.64, .82, 1.38, '#3a454d'); return b.mesh(MAT.car).geometry; };
    SIM.geos = ['#d9dcde', '#2f3437', '#7d8287', '#a33b2f', '#2d4a6b', '#e8e6e0', '#5b6b4f'].map(geo);
    simReset(.25);
  }
  function makeCar() { const m = new T.Mesh(SIM.geos[Math.floor(Math.random() * SIM.geos.length)], MAT.car); m.castShadow = true; SIM.group.add(m); return m; }
  function place(m, p, q) { m.position.set(p[0], p[2] + .02, -p[1]); m.rotation.y = Math.atan2(q[1] - p[1], q[0] - p[0]); }
  function parkAt(m, s) { const p = s.path, a = p[p.length - 2], b = p[p.length - 1]; place(m, b, [b[0] + (b[0] - a[0]), b[1] + (b[1] - a[1])]); }
  function simReset(occ) {
    SIM.cars.forEach(c => SIM.group.remove(c.m)); SIM.cars = [];
    SIM.stalls.forEach(s => { if (s.car) SIM.group.remove(s.car); s.car = null; s.busy = false; });
    SIM.done = { in: 0, out: 0 }; SIM.lastLen = null;
    const n = Math.round(SIM.stalls.length * (occ || 0)), pool = SIM.stalls.slice().sort(() => Math.random() - .5).slice(0, n);
    pool.forEach(s => { const m = makeCar(); parkAt(m, s); s.car = m; s.busy = true; });
    needs = true; simStat(true);
  }
  function startTrip(s, out) {
    const path = out ? s.outPath : s.inPath;
    const m = out ? s.car : makeCar(); if (out) s.car = null;
    const cum = [0]; for (let i = 1; i < path.length; i++) cum.push(cum[i - 1] + Math.hypot(path[i][0] - path[i - 1][0], path[i][1] - path[i - 1][1], path[i][2] - path[i - 1][2]));
    SIM.cars.push({ m, path, cum, d: 0, s, out });
  }
  function simStep(dt) {
    if (!SIM.running) return;
    SIM.acc += dt * SIM.speed;
    const interval = 2.2;
    while (SIM.acc > interval) {
      SIM.acc -= interval;
      const wantIn = SIM.mode === 'in' || (SIM.mode === 'mix' && Math.random() < .5);
      if (wantIn) { const s = SIM.stalls.find(x => !x.busy); if (s) { s.busy = true; startTrip(s, false); } }
      else { const cand = SIM.stalls.filter(x => x.busy && x.car); if (cand.length) { const s = cand[Math.floor(Math.random() * cand.length)]; startTrip(s, true); } }
    }
    const v = 4.2 * SIM.speed;
    SIM.cars = SIM.cars.filter(c => {
      c.d += v * dt; const end = c.cum[c.cum.length - 1];
      if (c.d >= end) {
        if (c.out) { SIM.group.remove(c.m); c.s.busy = false; SIM.done.out++; }
        else { parkAt(c.m, c.s); c.s.car = c.m; SIM.done.in++; SIM.lastLen = c.s.len; SIM.lastFloor = c.s.floor; }
        return false;
      }
      let i = 1; while (c.cum[i] < c.d) i++;
      const p = c.path[i - 1], q = c.path[i], t = (c.d - c.cum[i - 1]) / ((c.cum[i] - c.cum[i - 1]) || 1);
      const pos = [p[0] + (q[0] - p[0]) * t, p[1] + (q[1] - p[1]) * t, p[2] + (q[2] - p[2]) * t];
      place(c.m, pos, Math.hypot(q[0] - p[0], q[1] - p[1]) > .01 ? [pos[0] + q[0] - p[0], pos[1] + q[1] - p[1]] : [pos[0] + 1, pos[1]]);
      return true;
    });
    needs = true;
    SIM.statAcc += dt; if (SIM.statAcc > .25) { SIM.statAcc = 0; simStat(); }
  }
  function simStat() {
    if (!SIM.onStat) return;
    const per = {}; SIM.floors.forEach(id => per[id] = { busy: 0, total: 0 });
    SIM.stalls.forEach(s => { per[s.floor].total++; if (s.car) per[s.floor].busy++; });
    const parked = SIM.stalls.filter(s => s.car).length;
    const lens = SIM.stalls.length ? SIM.stalls.map(s => s.len) : [0];
    SIM.onStat({ ok: SIM.ok, parked, total: SIM.stalls.length, moving: SIM.cars.length, per, done: SIM.done, last: SIM.lastLen, lastFloor: SIM.lastFloor,
      minLen: Math.min(...lens), maxLen: Math.max(...lens), avgLen: lens.reduce((a, b) => a + b, 0) / lens.length, running: SIM.running, mode: SIM.mode });
  }

  /* ---------- circulation flows — from 1F program zones, ramps and elevators ---------- */
  const FLOW = { group: new T.Group(), ready: false, active: false, items: [], dots: [] };
  root.add(FLOW.group); FLOW.group.visible = false;
  const FLOW_DEF = {
    car: { label: '차량', color: '#e0662f' }, res: { label: '주거 보행', color: '#2f7fc1' },
    lib: { label: '공공시설 보행', color: '#2f9d6e' }, shop: { label: '근생 보행', color: '#d49a1f' }, core: { label: '코어 수직', color: '#b5478a' },
  };
  function flowInit() {
    FLOW.ready = true;
    const ups = FL.filter(f => f.lv > 0).sort((a, b) => a.lv - b.lv), F1 = ups[0]; if (!F1) return;
    const top = FL[FL.length - 1], bot = FL[0], y = .25, paths = [];
    const front = Math.min(...F1.ol.map(p => p[1])), street = front - 6, edge = front + .3;
    const rooms = z => F1.rm.filter(r => M.zt[r[4]] === z).map(r => ({ x: r[0], y: r[1], w: r[2], h: r[3], cx: r[0] + r[2] / 2, cy: r[1] + r[3] / 2, a: r[2] * r[3] }));
    const frontMost = rs => rs.slice().sort((a, b) => a.y - b.y)[0];
    const ev = F1.ev.map(e => [e[0] + e[2] / 2, e[1] + e[3] / 2]);
    const nearestEv = (x, yy) => ev.slice().sort((a, b) => Math.hypot(a[0] - x, a[1] - yy) - Math.hypot(b[0] - x, b[1] - yy))[0];
    const lob = rooms('lobby');
    if (lob.length) {
      const e = frontMost(lob), big = lob.slice().sort((a, b) => b.a - a.a)[0], v = nearestEv(big.cx, big.cy);
      const p = [[e.cx, street, y], [e.cx, edge, y], [e.cx, e.cy, y]]; if (big !== e) p.push([big.cx, big.cy, y]); if (v) p.push([v[0], v[1], y]);
      paths.push({ k: 'res', n: 4, p });
    }
    const pub = rooms('public');
    if (pub.length) {
      const e = frontMost(pub), big = pub.slice().sort((a, b) => b.a - a.a)[0];
      const p = [[e.cx, street, y], [e.cx, edge, y], [e.cx, e.cy, y]]; if (big !== e) p.push([big.cx, big.cy, y]);
      paths.push({ k: 'lib', n: 3, p });
    }
    rooms('retail').filter(r => r.y < front + 1.5).slice(0, 5).forEach(r => paths.push({ k: 'shop', n: 2, p: [[r.cx, street, y], [r.cx, edge, y], [r.cx, Math.min(r.cy, r.y + 6), y]] }));
    simInit();
    if (SIM.ok) {
      const far = SIM.stalls[SIM.stalls.length - 1], lift = p => p.map(q => [q[0], q[1], q[2] + .3]);
      paths.push({ k: 'car', n: 5, car: true, p: lift(far.inPath) });
      paths.push({ k: 'car', n: 3, car: true, p: lift(far.outPath) });
    }
    ev.forEach(v => paths.push({ k: 'core', n: 2, pingpong: true, p: [[v[0], v[1], bot.el + .5], [v[0], v[1], top.el + 1.5]] }));
    paths.forEach(P => {
      const pts = P.p.map(q => new T.Vector3(q[0], q[2], -q[1]));
      const cp = new T.CurvePath(); for (let i = 1; i < pts.length; i++) cp.add(new T.LineCurve3(pts[i - 1], pts[i]));
      const col = FLOW_DEF[P.k].color;
      const tube = new T.Mesh(new T.TubeGeometry(cp, Math.max(8, pts.length * 12), P.k === 'core' ? .2 : .3, 8, false), new T.MeshBasicMaterial({ color: col, transparent: true, opacity: .8 }));
      tube.userData.k = P.k; FLOW.group.add(tube);
      const cum = [0]; for (let i = 1; i < pts.length; i++) cum.push(cum[i - 1] + pts[i].distanceTo(pts[i - 1]));
      const item = { k: P.k, pts, cum, len: cum[cum.length - 1] || 1, pingpong: P.pingpong, tube, dots: [] };
      for (let j = 0; j < P.n; j++) {
        const g = P.car ? new T.BoxGeometry(4.2, 1.3, 1.8) : new T.SphereGeometry(P.k === 'core' ? .8 : .7, 14, 10);
        const m = new T.Mesh(g, new T.MeshStandardMaterial({ color: col, emissive: col, emissiveIntensity: .35, roughness: .4 }));
        m.userData.k = P.k; m.userData.off = j / P.n; item.dots.push(m); FLOW.group.add(m);
      }
      FLOW.items.push(item);
    });
  }
  let flowT = 0;
  function flowStep(dt) {
    flowT += dt;
    FLOW.items.forEach(it => {
      if (!it.tube.visible) return;
      const speed = it.k === 'car' ? 7 : it.k === 'core' ? 5 : 2.2;
      it.dots.forEach(m => {
        let d = ((flowT * speed / it.len + m.userData.off) % 1) * it.len;
        if (it.pingpong) { const ph = ((flowT * speed / it.len + m.userData.off) % 2); d = (ph < 1 ? ph : 2 - ph) * it.len; }
        let i = 1; while (i < it.cum.length - 1 && it.cum[i] < d) i++;
        const a = it.pts[i - 1], b = it.pts[i], t = (d - it.cum[i - 1]) / ((it.cum[i] - it.cum[i - 1]) || 1);
        m.position.lerpVectors(a, b, Math.min(1, Math.max(0, t)));
        if (it.k === 'car') { m.position.y += .65; m.rotation.y = Math.atan2(-(b.z - a.z), b.x - a.x); }
      });
    });
    needs = true;
  }
  function flowShow(k, on) { FLOW.items.forEach(it => { if (it.k === k) { it.tube.visible = on; it.dots.forEach(d => d.visible = on); } }); needs = true; }

  /* ---------- slow pan (comment #1) ---------- */
  let pan = null;
  function panStep(dt) {
    if (!pan || tween) return;
    pan.t += dt;
    const o = new T.Vector3(Math.sin(pan.t * .16) * 26, 0, Math.sin(pan.t * .11) * 18);
    camera.position.copy(pan.p).add(o); controls.target.copy(pan.tg).add(o); needs = true;
  }

  /* ---------- host management ---------- */
  function attach(el, cfg) {
    if (!init()) { el.innerHTML = '<div class="scene-loading">이 브라우저에서 WebGL을 사용할 수 없습니다.<br>Chrome·Edge 최신 버전에서 열어 주세요.</div>'; return; }
    host = el;
    el.appendChild(renderer.domElement); el.appendChild(tip); tip.hidden = true;
    resize();
    apply(cfg, true);
    if (cfg.intro) { // slow push-in for cinematic pages
      const [p, t] = view(cfg.view || defaultView(cfg.mode), cfg);
      camera.position.copy(p.clone().sub(t).multiplyScalar(1.18).add(t)); controls.target.copy(t); controls.update();
      tween = { p0: camera.position.clone(), t0: t.clone(), p, t, s: performance.now(), d: 3200 };
    }
    start();
  }
  function detach() { host = null; stop(); SIM.running = false; labels = []; labelLayer = null; }
  function resize() {
    if (!host || !renderer) return;
    const w = host.clientWidth, h = host.clientHeight; if (!w || !h) return;
    const fit = parseFloat(getComputedStyle(document.documentElement).getPropertyValue('--fit')) || 1;
    renderer.setPixelRatio(Math.min(2.5, (devicePixelRatio || 1) * Math.max(1, fit)));
    renderer.setSize(w, h, false); renderer.domElement.style.width = '100%'; renderer.domElement.style.height = '100%';
    camera.aspect = w / h; camera.updateProjectionMatrix(); needs = true;
  }
  addEventListener('resize', () => setTimeout(resize, 30));
  const ease = t => t < .5 ? 4 * t * t * t : 1 - Math.pow(-2 * t + 2, 3) / 2;
  let lastT = 0;
  function loop(now) {
    raf = requestAnimationFrame(loop);
    const dt = Math.min(.1, (now - (lastT || now)) / 1000); lastT = now;
    if (SIM.active) simStep(dt);
    if (FLOW.active) flowStep(dt);
    panStep(dt);
    if (tween) {
      const k = Math.min(1, (now - tween.s) / tween.d), e = ease(k);
      camera.position.lerpVectors(tween.p0, tween.p, e); controls.target.lerpVectors(tween.t0, tween.t, e);
      if (k >= 1) tween = null; needs = true;
    }
    const ch = controls.update();
    if (controls.autoRotate) needs = true;
    if (needs || ch) { renderer.render(scene, camera); needs = false; if (labels.length) updateLabels(); }
  }
  function start() { if (!raf) raf = requestAnimationFrame(loop); }
  function stop() { cancelAnimationFrame(raf); raf = 0; }

  // render a config to an image (overview thumbnails, print)
  function snapshot(cfg, w, h) {
    if (!init()) return null;
    const prev = cur, pa = camera.aspect, pp = camera.position.clone(), pt = controls.target.clone();
    const size = new T.Vector2(); renderer.getSize(size); const pr = renderer.getPixelRatio();
    renderer.setPixelRatio(1); renderer.setSize(w, h, false); camera.aspect = w / h; camera.updateProjectionMatrix();
    apply(cfg, true); tween = null;
    renderer.render(scene, camera);
    const url = renderer.domElement.toDataURL('image/jpeg', .86);
    renderer.setPixelRatio(pr); renderer.setSize(size.x || 2, size.y || 2, false);
    camera.aspect = pa; camera.updateProjectionMatrix();
    apply(prev, true); camera.position.copy(pp); controls.target.copy(pt); controls.update(); needs = true;
    return url;
  }

  function pickAt(x, y) {
    if (!renderer) return null;
    const h = itemAt({ clientX: x, clientY: y }); if (!h || !h.item) return null;
    const d = document.createElement('div'); d.innerHTML = describe(h.item).replace(/<br>/g, ' · ').replace(/<\/b>/g, ' · ');
    return d.textContent.replace(/\s+/g, ' ').replace(/ · $/, '').trim();
  }
  window.SB3D = {
    pickAt,
    attach, detach, apply: c => apply(Object.assign({}, cur, c)), set: (c, instant) => apply(c, instant), resize, snapshot,
    flyTo: v => flyTo(v, cur), get config() { return cur; }, onPick: fn => hoverCb = fn,
    ZCOL, UCOL, ZLABEL, floors: FL.map(f => ({ id: f.id, nm: f.nm, lv: f.lv, el: f.el, h: f.h, pt: f.pt })),
    toggleAuto() { controls.autoRotate = !controls.autoRotate; needs = true; return controls.autoRotate; },
    flows: { show: flowShow, get defs() { const ks = new Set(FLOW.items.map(i => i.k)); return Object.fromEntries(Object.entries(FLOW_DEF).filter(([k]) => !FLOW.ready || ks.has(k))); } },
    sim: {
      play(mode) { if (!SIM.ready) simInit(); SIM.mode = mode || SIM.mode; SIM.running = true; simStat(); },
      pause() { SIM.running = false; simStat(); }, reset(occ) { if (!SIM.ready) simInit(); SIM.running = false; if (SIM.geos) simReset(occ); },
      get ok() { if (!SIM.ready) simInit(); return SIM.ok; }, get floors() { if (!SIM.ready) simInit(); return SIM.floors.slice(); },
      speed(v) { SIM.speed = v; }, onStat(fn) { SIM.onStat = fn; if (SIM.ready) simStat(); },
    },
  };
})();
