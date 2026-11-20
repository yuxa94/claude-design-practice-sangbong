#!/usr/bin/env python3
"""계획설계 생성기.

zoning/<SLUG>_zoning.json (조닝) → plan/<SLUG>_plan.json (계획설계)

- 세대(unit) 구역을 현관·욕실·주방·거실·침실로 나누고
- 모든 실 경계에서 벽(종류·두께)을 뽑고
- 문·창호·계단·엘리베이터를 배치합니다.
- 기존 계획설계 파일의 comments(수정 요청)는 그대로 보존합니다.

표준 라이브러리만 씁니다:  python3 tools/gen_plan.py
"""
import json, os, sys, datetime, math
from project_conf import SLUG, ROAD, CONF
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fire_model
import mep_model
import struct_model
import cost_model

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ZFILE = os.path.join(ROOT, 'zoning', SLUG + '_zoning.json')
PFILE = os.path.join(ROOT, 'plan', SLUG + '_plan.json')
SCHEMA = 'sangbong-plan/1'

# ---------------------------------------------------------------- 기준값
WALL = {  # 종류: (두께 m, 표시 이름)
    'exterior':   (0.30, '외벽 (RC 200 + 단열)'),
    'basement':   (0.40, '지하 외벽 (RC 400)'),
    'core':       (0.25, '코어벽 (RC 250, 내력)'),
    'core_inner': (0.20, '코어 내부벽 (RC 200)'),
    'party':      (0.20, '세대간 경계벽 (RC 200)'),
    'unit_corr':  (0.20, '세대-공용 경계벽 (RC 200)'),
    'fire':       (0.20, '방화구획벽 (RC 200)'),
    'partition':  (0.10, '세대 내 칸막이 (경량 100)'),
    'light':      (0.15, '칸막이벽 (조적·경량 150)'),
}

# ---------------------------------------------------------------- 디테일 라이브러리 (details/*.json)
DET_DIR = os.path.join(ROOT, 'details')


def _det(name):
    p = os.path.join(DET_DIR, name + '.json')
    try:
        return json.load(open(p, encoding='utf-8'))
    except FileNotFoundError:
        return {}


DET_WALL, DET_DOOR, DET_WIN = _det('walls'), _det('doors'), _det('windows')
WALL_LAYERS = {}
for _k, _v in (DET_WALL.get('types') or {}).items():
    WALL[_k] = (float(_v['t']), _v.get('label', WALL.get(_k, (0, _k))[1]))
    WALL_LAYERS[_k] = {kk: _v[kk] for kk in ('layers', 'cad') if kk in _v}
DOOR_H = float(DET_DOOR.get('height', 2.1))
DOOR_W = {k: float(v['w']) for k, v in (DET_DOOR.get('sizes') or {}).items()}


def door_w(key, default):
    return DOOR_W.get(key, default)


def _match(rule, t):
    if 'match' not in rule and 'exact' not in rule:
        return True
    return any(t.startswith(m) or m in t for m in rule.get('match', [])) or t in rule.get('exact', [])
CIRC = {'corridor', 'lobby'}
GARAGE = {'parking', 'aisle', 'ramp'}
UNIT_OPEN = {'현관', '주방·식당', '거실', '거실·주방', '거실·침실', '복도(세대)', '주방'}
HABITABLE = {'거실', '거실·주방', '거실·침실', '침실1', '침실2', '침실3', '침실(알코브)'}
ZONE_TYPED = {'retail', 'public', 'community', 'lobby', 'mep'}
CONFLICTS = []  # 조닝 변경·편집 충돌 알림
EDITS = {}   # 타입 편집: key → {'rooms': [...]}  (계획설계 파일 typeEdits 에서 읽음)
TYPES = {}   # 이번 생성에서 모은 타입


def reg_type(key, cat, ztype, gen, inst, **kw):
    T = TYPES.get(key)
    if not T:
        T = TYPES[key] = {'key': key, 'cat': cat, 'ztype': ztype, 'gen': gen, 'instances': []}
        T.update({k: (r3(v) if isinstance(v, float) else v) for k, v in kw.items()})
    T['instances'].append(inst)
r3 = lambda v: round(v + 0.0, 3)


def load(p):
    with open(p, encoding='utf-8') as f:
        return json.load(f)


# ---------------------------------------------------------------- 기하 도우미
def rect(x0, y0, x1, y1):
    return {'x': r3(min(x0, x1)), 'y': r3(min(y0, y1)), 'w': r3(abs(x1 - x0)), 'h': r3(abs(y1 - y0))}


def pip(p, poly):
    c = False
    j = len(poly) - 1
    for i in range(len(poly)):
        a, b = poly[i], poly[j]
        if (a[1] > p[1]) != (b[1] > p[1]) and p[0] < (b[0] - a[0]) * (p[1] - a[1]) / (b[1] - a[1]) + a[0]:
            c = not c
        j = i
    return c


def side_line(z, s):
    x0, y0, x1, y1 = z['x'], z['y'], z['x'] + z['w'], z['y'] + z['h']
    return {'S': ('h', y0, x0, x1), 'N': ('h', y1, x0, x1), 'W': ('v', x0, y0, y1), 'E': ('v', x1, y0, y1)}[s]


def outline_edges(poly):
    out = []
    for i in range(len(poly)):
        a, b = poly[i], poly[(i + 1) % len(poly)]
        if abs(a[1] - b[1]) < 1e-6:
            out.append(('h', a[1], min(a[0], b[0]), max(a[0], b[0])))
        else:
            out.append(('v', a[0], min(a[1], b[1]), max(a[1], b[1])))
    return out


def facade_intervals(z, s, oedges):
    o, c, lo, hi = side_line(z, s)
    res = []
    for oo, cc, a, b in oedges:
        if oo == o and abs(cc - c) < 0.02:
            l, h = max(lo, a), min(hi, b)
            if h - l > 0.05:
                res.append((l, h))
    return res


def contact_intervals(z, s, others):
    o, c, lo, hi = side_line(z, s)
    opp = {'S': 'N', 'N': 'S', 'W': 'E', 'E': 'W'}[s]
    res = []
    for q in others:
        oo, cc, a, b = side_line(q, opp)
        if abs(cc - c) < 0.02:
            l, h = max(lo, a), min(hi, b)
            if h - l > 0.05:
                res.append((l, h, q))
    return res


# ---------------------------------------------------------------- 세대 내부 계획
UNIT_ROOMS = {'원룸': ['거실·주방'], '1.5룸': ['거실', '침실(알코브)'], '투룸': ['거실', '침실1'], '쓰리룸': ['거실', '침실1', '침실2']}
MINW = {'거실': 3.0, '거실·주방': 2.8, '침실1': 2.6, '침실2': 2.6, '침실(알코브)': 2.2}


def unit_frame(z, back):
    """back = 주 채광면(창). front = 반대편(현관 쪽). 로컬 (t: 폭, d: 현관→창 깊이)."""
    x0, y0, x1, y1 = z['x'], z['y'], z['x'] + z['w'], z['y'] + z['h']
    if back == 'S':
        W, D = z['w'], z['h']; g = lambda t0, t1, d0, d1: rect(x0 + t0, y1 - d1, x0 + t1, y1 - d0)
        lat = {0: 'W', 1: 'E'}; t_of = lambda u: u - x0; d_of = lambda v: y1 - v
    elif back == 'N':
        W, D = z['w'], z['h']; g = lambda t0, t1, d0, d1: rect(x0 + t0, y0 + d0, x0 + t1, y0 + d1)
        lat = {0: 'W', 1: 'E'}; t_of = lambda u: u - x0; d_of = lambda v: v - y0
    elif back == 'W':
        W, D = z['h'], z['w']; g = lambda t0, t1, d0, d1: rect(x1 - d1, y0 + t0, x1 - d0, y0 + t1)
        lat = {0: 'S', 1: 'N'}; t_of = lambda v: v - y0; d_of = lambda u: x1 - u
    else:
        W, D = z['h'], z['w']; g = lambda t0, t1, d0, d1: rect(x0 + d0, y0 + t0, x0 + d1, y0 + t1)
        lat = {0: 'S', 1: 'N'}; t_of = lambda v: v - y0; d_of = lambda u: u - x0
    return W, D, g, lat, t_of, d_of


def unit_analyze(z, oedges, circ):
    """창 방향(back)·출입 방향을 고르고, 타입 비교용 정규화 정보(현관이 t=0 쪽)를 돌려준다."""
    opp = {'S': 'N', 'N': 'S', 'W': 'E', 'E': 'W'}
    best = None
    for back in 'SNWE':
        fb = facade_intervals(z, back, oedges)
        if sum(h - l for l, h in fb) < 2.0:
            continue
        W, D, g, lat, t_of, d_of = unit_frame(z, back)
        front = opp[back]
        ent = None
        fc = contact_intervals(z, front, circ)
        if fc:
            l, h, q = max(fc, key=lambda c: c[1] - c[0])
            if h - l >= 0.9:
                a, b = sorted((t_of(l), t_of(h)))
                ent = ('front', a, b)
        if not ent:
            for k in (0, 1):
                for l, h, q in contact_intervals(z, lat[k], circ):
                    a, b = sorted((d_of(l), d_of(h)))
                    if b - a >= 0.9 and a < 2.8:
                        ent = ('lat%d' % k, a, min(b, 2.6))
        if not ent:
            continue
        lat_f = {k: [tuple(sorted((d_of(l), d_of(h)))) for l, h in facade_intervals(z, lat[k], oedges)] for k in (0, 1)}
        back_f = [tuple(sorted((t_of(l), t_of(h)))) for l, h in fb]
        score = sum(b - a for a, b in back_f) + 0.5 * sum(b - a for k in (0, 1) for a, b in lat_f[k]) - (100 if D < 6.5 else 0) + (5 if ent[0] == 'front' else 0)
        if not best or score > best[0]:
            best = (score, back, W, D, ent, back_f, lat_f)
    if not best:
        return None
    _, back, W, D, ent, back_f, lat_f = best
    if ent[0] == 'lat0':
        left_side = True
    elif ent[0] == 'lat1':
        left_side = False
    else:
        left_side = (ent[1] + ent[2]) / 2 <= W / 2 or ent[1] < 0.3
    mirror = not left_side
    if mirror:  # 정규화: 현관을 항상 t=0 쪽으로
        back_f = [(W - b, W - a) for a, b in back_f]
        lat_f = {0: lat_f[1], 1: lat_f[0]}
    return {'back': back, 'mirror': mirror, 'W': W, 'D': D, 'back_f': back_f, 'lat_f': lat_f}


def unit_key(ut, A):
    W, D = A['W'], A['D']
    full = sum(b - a for a, b in A['back_f']) >= W - 0.3
    lat = ';'.join(','.join('%.1f-%.1f' % (a, b) for a, b in A['lat_f'][k]) for k in (0, 1))
    return 'U|%s|%.2f|%.2f|%s|%s' % (ut, W, D, 'F' if full else 'P', lat)


def unit_layout(ut, W, D, back_f, lat_f):
    """정규화 좌표(t: 0..W, 현관 쪽 t=0 / d: 0=출입 … D=창)에서 실 배치. → ([{name,t0,t1,d0,d1}], issues)"""
    eD = 2.4 if D >= 7.5 else 2.0
    rem = D - eD
    kD0 = 0.0
    if ut != '원룸' and rem >= 7.4:
        kD0 = 2.4
    elif ut != '원룸' and rem >= 5.6:   # 깊이 8.0~8.4m 세대 (복도 유효폭 확보로 8.1m가 된 북측 세대 포함)
        kD0 = 1.8
    elif ut == '원룸' and rem >= 7.6:
        kD0 = 2.0
    names0 = list(UNIT_ROOMS.get(ut, ['거실·주방']))
    full_back = sum(b - a for a, b in back_f) >= W - 0.3
    MIN_L, MIN_B = 2.7, 2.5

    def allocate(kD):
        P, I = [], []
        hw = 1.3
        P.append(('현관', 0.0, hw, 0, eD))
        rl = W - hw
        # 주방·식당 띠가 없고 침실이 둘이면 현관 → 주방 → 거실로 이어지게 (욕실은 띠 끝) — 피난 동선: 모든 실이 현관과 연결 (검토서 F-01)
        hub = kD == 0 and len([n for n in names0 if n.startswith('침실')]) >= 2 and rl >= 4.0
        if rl >= 1.4:
            bw = min(2.6, rl) if rl < 4.0 else 2.4
            if hub:
                P.append(('주방', hw, W - bw, 0, eD)); P.append(('욕실', W - bw, W, 0, eD))
            else:
                P.append(('욕실', hw, hw + bw, 0, eD))
            if not hub and W - (hw + bw) > 0.05:
                P.append(('주방' if kD == 0 and W - (hw + bw) >= 1.6 else '수납', hw + bw, W, 0, eD))
        else:
            I.append('욕실 자리 부족 (세대 폭 %.1fm)' % W)
        dL = eD + kD
        if kD > 0:
            P.append(('주방·식당', 0, W, eD, dL))
        names = list(names0)
        if kD > 0 and names == ['거실·주방']:
            names = ['거실·침실']
        liv = names[0]
        beds = [n for n in names if n.startswith('침실')]
        LD = D - dL
        best = None
        cands = [None]
        if beds and LD >= 5.0:
            for kk in (0, 1):
                if sum(min(b, D) - max(a, dL) for a, b in lat_f[kk] if min(b, D) > max(a, dL)) >= 2.5:
                    cands.append(kk)
                    break
        for use in cands:
            L, LI = [], []
            x0c, x1c = 0.0, W
            lat_beds = []
            if use is not None:
                nl = 2 if (LD >= 5.2 and len(beds) >= 2) else 1
                lat_beds = beds[-nl:]
                cw = 2.6
                tA, tB = (0.0, cw) if use == 0 else (W - cw, W)
                if nl == 2:
                    mid = dL + LD / 2
                    L += [(lat_beds[0], tA, tB, mid, D), (lat_beds[1], tA, tB, dL, mid)]
                else:
                    L.append((lat_beds[0], tA, tB, dL, D))
                x0c, x1c = (cw, W) if use == 0 else (0.0, W - cw)
            remain = [b for b in beds if b not in lat_beds]
            span = x1c - x0c
            k = len(remain)
            while k > 0 and not (full_back and span >= MIN_L + MIN_B * k - 1e-6):
                k -= 1
            fb, rb = remain[:k], remain[k:]
            if span < MIN_L - 0.2:
                continue
            bws = [max(MIN_B, min(3.2, (span - MIN_L) / len(fb))) for _ in fb] if fb else []
            lw = span - sum(bws)
            cols = list(zip(fb, bws)) + [(liv, lw)]
            if hub and len(fb) >= 2:
                cols = [(fb[0], bws[0]), (liv, lw)] + list(zip(fb[1:], bws[1:]))   # 거실을 가운데 — 주방 뒤, 침실 양옆
            inner = rb.pop(0) if rb and LD >= 5.4 and lw >= 3.4 else None
            t = x0c
            for n, w in cols:
                if n == liv and inner:
                    iw = min(2.6, w - 1.2)
                    a0, a1 = t + w - iw, t + w
                    p0, p1 = t, t + w - iw
                    L += [(inner, a0, a1, dL, dL + 2.6), ('복도(세대)', p0, p1, dL, dL + 2.6), (n, t, t + w, dL + 2.6, D)]
                    if inner != '침실(알코브)':
                        LI.append('%s 창 없음 (내부 알코브형, 미닫이로 거실 채광 공유)' % inner)
                else:
                    L.append((n, t, t + w, dL, D))
                t += w
            for b in rb:
                LI.append(('[참고] 알코브 둘 공간 부족 → 거실 일체형(가변 가구) (세대 폭 %.1fm)' % W) if b == '침실(알코브)' else ('%s 채광창 확보 불가 → 거실과 통합 필요 (세대 폭 %.1fm)' % (b, W)))
            win_beds = len(lat_beds) + len(fb)
            score = win_beds * 10 + lw - (4 if inner and inner != '침실(알코브)' else 0) - 8 * len(rb) - (0.5 if kD == 0 and kD0 > 0 else 0)
            if best is None or score > best[0]:
                best = (score, L, LI)
        if best is None:
            P.append((liv, 0, W, dL, D)); best = (-99, [], ['거주실 배치 불가'])
        return best[0], P + best[1], I + best[2]

    opts = [kD0] + ([0.0] if kD0 > 0 and any(lat_f[k] for k in (0, 1)) else [])
    sc, placed, iss = max((allocate(k) for k in opts), key=lambda r: r[0])
    lay = [{'name': n, 't0': r3(t0), 't1': r3(t1), 'd0': r3(d0), 'd1': r3(d1)} for n, t0, t1, d0, d1 in placed if t1 - t0 > 0.05 and d1 - d0 > 0.05]
    return lay, iss


def c2f(inst, t0, t1, d0, d1):
    """정규화 좌표 사각형 → 층 좌표 사각형 (inst: back, mirror, W, x0,y0,x1,y1)."""
    W = inst['W']
    if inst['mirror']:
        t0, t1 = W - t1, W - t0
    x0, y0, x1, y1 = inst['x0'], inst['y0'], inst['x1'], inst['y1']
    b = inst['back']
    if b == 'S':
        return rect(x0 + t0, y1 - d1, x0 + t1, y1 - d0)
    if b == 'N':
        return rect(x0 + t0, y0 + d0, x0 + t1, y0 + d1)
    if b == 'W':
        return rect(x1 - d1, y0 + t0, x1 - d0, y0 + t1)
    return rect(x0 + d0, y0 + t0, x0 + d1, y0 + t1)


# ---------------------------------------------------------------- 코어 계획
def plan_core(z, core_idx):
    x0, y0, x1, y1 = z['x'], z['y'], z['x'] + z['w'], z['y'] + z['h']
    R = []
    if z['h'] >= 8.5:   # 코어 A (6.6 x 9.5)
        R += [dict(rect(x1 - 2.4, y0, x1, y0 + 2.2), name='승강기 1', kind='ev', door='E'),
              dict(rect(x1 - 2.4, y0 + 2.2, x1, y0 + 4.4), name='승강기 2', kind='ev', door='E'),
              dict(rect(x1 - 2.4, y0 + 4.4, x1, y0 + 6.6), name='비상용 승강기', kind='ev', door='N', emergency=True),
              dict(rect(x1 - 3.8, y0 + 6.6, x1, y1), name='부속실 (특피·비상EV 승강장 겸용)', kind='vestibule'),
              dict(rect(x0, y1 - 6.0, x0 + 2.8, y1), name='특별피난계단 A', kind='stair'),
              dict(rect(x0, y0, x0 + 2.8, y1 - 6.0), name='EPS·TPS', kind='shaft'),
              dict(rect(x0 + 2.8, y0, x1 - 2.4, y0 + 6.6), name='PS·AD', kind='shaft')]
    else:                # 코어 B (6.6 x 6.1)
        R += [dict(rect(x0, y0, x0 + 2.8, y1), name='특별피난계단 B', kind='stair'),
              dict(rect(x0 + 2.8, y0, x1, y0 + 2.7), name='부속실 B', kind='vestibule'),
              dict(rect(x1 - 2.2, y0 + 2.7, x1, y1), name='승강기 3', kind='ev', door='E'),
              dict(rect(x0 + 2.8, y0 + 2.7, x1 - 2.2, y1), name='PS', kind='shaft')]
    return R


def core_layout(z):
    out = []
    for r in plan_core(z, 0):
        d = {'name': r['name'], 't0': r3(r['x'] - z['x']), 't1': r3(r['x'] + r['w'] - z['x']), 'd0': r3(r['y'] - z['y']), 'd1': r3(r['y'] + r['h'] - z['y']), 'kind': r['kind']}
        if r.get('door'):
            d['door'] = r['door']
        if r.get('emergency'):
            d['emergency'] = True
        out.append(d)
    return out


# ---------------------------------------------------------------- 벽 분해
def classify(ra, rb, inside_gap):
    if ra is None or rb is None:
        return None if inside_gap else 'exterior'
    if ra['zone'] == rb['zone']:
        if ra['kind'] == 'unitroom':
            if ra['name'] in UNIT_OPEN and rb['name'] in UNIT_OPEN:
                return None
            return 'partition'
        if ra['ztype'] == 'core':
            return 'core_inner'
        if ra['kind'] == 'zone':
            return 'light'
    ta, tb = ra['ztype'], rb['ztype']
    if 'core' in (ta, tb):
        return 'core'
    if ta == 'unit' and tb == 'unit':
        return 'party'
    if 'unit' in (ta, tb):
        return 'unit_corr'
    if ta in CIRC and tb in CIRC:
        return None
    if ta in GARAGE and tb in GARAGE:
        return None
    if ta == tb and ta in ('public', 'community'):
        return None
    if ta == tb == 'retail' and ra['zname'].split(' (')[0] == rb['zname'].split(' (')[0]:
        return None
    if 'mep' in (ta, tb):
        return 'fire'
    return 'light'


def decompose(rooms, outline, below_ground):
    xs = sorted({r3(v) for r in rooms for v in (r['x'], r['x'] + r['w'])} | {r3(p[0]) for p in outline})
    ys = sorted({r3(v) for r in rooms for v in (r['y'], r['y'] + r['h'])} | {r3(p[1]) for p in outline})
    def find(px, py):
        for r in rooms:
            if r['x'] - 1e-6 < px < r['x'] + r['w'] + 1e-6 and r['y'] - 1e-6 < py < r['y'] + r['h'] + 1e-6:
                return r
        return None
    segs = []
    d = 0.01
    for y in ys:
        for i in range(len(xs) - 1):
            a, b = xs[i], xs[i + 1]; m = (a + b) / 2
            r1, r2 = find(m, y - d), find(m, y + d)
            if r1 is r2:
                continue
            gap = (r1 is None and pip((m, y - d), outline)) or (r2 is None and pip((m, y + d), outline))
            k = classify(r1, r2, gap)
            if k:
                segs.append(('h', y, a, b, r1['id'] if r1 else None, r2['id'] if r2 else None, k))
    for x in xs:
        for i in range(len(ys) - 1):
            a, b = ys[i], ys[i + 1]; m = (a + b) / 2
            r1, r2 = find(x - d, m), find(x + d, m)
            if r1 is r2:
                continue
            gap = (r1 is None and pip((x - d, m), outline)) or (r2 is None and pip((x + d, m), outline))
            k = classify(r1, r2, gap)
            if k:
                segs.append(('v', x, a, b, r1['id'] if r1 else None, r2['id'] if r2 else None, k))
    segs.sort(key=lambda s: (s[0], s[1], s[4] or '', s[5] or '', s[2]))
    walls = []
    for s in segs:
        if walls:
            w = walls[-1]
            if w['o'] == s[0] and abs(w['c'] - s[1]) < 1e-6 and w['r'] == [s[4], s[5]] and abs(w['hi'] - s[2]) < 1e-6 and w['kind'] == s[6]:
                w['hi'] = s[3]; continue
        walls.append({'o': s[0], 'c': s[1], 'lo': s[2], 'hi': s[3], 'r': [s[4], s[5]], 'kind': s[6]})
    out = []
    for i, w in enumerate(walls):
        kind = w['kind']
        if kind == 'exterior' and below_ground:
            kind = 'basement'
        a = [w['lo'], w['c']] if w['o'] == 'h' else [w['c'], w['lo']]
        b = [w['hi'], w['c']] if w['o'] == 'h' else [w['c'], w['hi']]
        out.append({'id': None, 'a': [r3(a[0]), r3(a[1])], 'b': [r3(b[0]), r3(b[1])], 'kind': kind,
                    't': WALL[kind][0], 'rooms': w['r'], 'len': r3(w['hi'] - w['lo'])})
    return out


# ---------------------------------------------------------------- 문·창
def place(wall, width, prefer='center', avoid=()):
    L = wall['len']
    if L < width + 0.1:
        width = max(0.6, L - 0.1)
    if prefer == 'center':
        c = L / 2
    elif prefer == 'start':
        c = 0.15 + width / 2 + 0.1
    else:
        c = L - 0.15 - width / 2 - 0.1
    c = min(max(c, width / 2 + 0.05), L - width / 2 - 0.05)
    for a, b in avoid:
        if c + width / 2 > a - 0.15 and c - width / 2 < b + 0.15:
            c2 = b + 0.2 + width / 2
            if c2 + width / 2 <= L - 0.05:
                c = c2
            else:
                c = max(width / 2 + 0.05, a - 0.2 - width / 2)
    return r3(c), r3(width)



# ---------------------------------------------------------------- 상세: 창호·문·계단·가구
RISER_MAX, TREAD_MIN, TREAD_STD, STAIR_W_MIN = 0.18, 0.26, 0.28, 1.2   # 공동주택 계단 (피난·방화규칙 제15조)


def detail_windows_doors(f, ops, walls):
    """창·문 사양 — details/windows.json · details/doors.json 규칙 (없으면 기본값)."""
    wby = {w['id']: w for w in walls}
    above = f['level'] >= int((DET_WIN.get('rail') or {}).get('fromLevel', 2))
    wtypes = DET_WIN.get('types') or []
    rail = DET_WIN.get('rail') or {'belowSill': 0.9, 'height': 1.2, 'type': '추락방지 안전난간 (AL, 높이 1.2m)'}
    for o in ops:
        w = wby[o['wall']]
        if o['kind'] == 'window':
            t = o['type']
            rule = next((r for r in wtypes if _match(r, t)), None)
            if rule:
                pn = rule.get('panels', 1)
                if 'panelWidth' in rule:
                    pn = max(1, math.ceil(o['w'] / float(rule['panelWidth'])))
                elif isinstance(pn, dict):
                    pn = pn['wide'] if o['w'] >= pn.get('wideFrom', 3.0) else pn['narrow']
                glass = rule.get('glass', '')
                if any(k in t for k in rule.get('glassOpaqueFor', [])):
                    glass += ' (불투명)'
                o.update(operation=rule.get('operation'), panels=pn, frame=rule.get('frame'), glass=glass, tag=rule.get('tag'))
            elif t.startswith('커튼월'):
                o.update(operation='커튼월 (고정+여닫이)', panels=max(1, math.ceil(o['w'] / 1.5)), frame='AL 커튼월 멀리언 50×150', glass='로이 복층유리 24mm')
            elif t.startswith('거실창') or t.startswith('침실창') or t in ('창', '복도창'):
                o.update(operation='미서기', panels=4 if o['w'] >= 3.0 else 2, frame='PVC 시스템창호 (이중창)', glass='로이 복층유리 24mm')
            else:
                o.update(operation='프로젝트 (위걸이)', panels=1, frame='PVC 시스템창호', glass='복층유리 22mm' + (' (불투명)' if '욕실' in t else ''))
            if above and not t.startswith('커튼월') and o.get('sill', 0) < float(rail.get('belowSill', 0.9)) and None in w['rooms']:   # 바닥까지 내려오는 여닫는 창만
                o['rail'] = {'height': float(rail.get('height', 1.2)), 'type': rail.get('type')}
        elif o['kind'] == 'door':
            t = o['type']
            rules = DET_DOOR.get('types') or [{'match': ['승강기'], 'leaf': '승강기 2짝 미닫이', 'leaves': 2}, {'sliding': True, 'leaf': '미닫이', 'leaves': 1},
                                              {'minWidth': 1.5, 'leaf': '양개 여닫이', 'leaves': 2}, {'leaf': '외여닫이', 'leaves': 1}]
            for r in rules:
                if r.get('sliding') and not o.get('sliding'):
                    continue
                if 'minWidth' in r and o['w'] < float(r['minWidth']):
                    continue
                if not r.get('sliding') and 'match' in r and not _match(r, t):
                    continue
                if o.get('sliding') and not r.get('sliding') and 'match' not in r:
                    continue
                o.update(leaf=r['leaf'], leaves=r.get('leaves', 1), tag=r.get('tag'))
                break
            fr = next((r for r in (DET_DOOR.get('fire') or [{'match': ['방화', '현관문'], 'fire': '갑종 (60분+)', 'frame': '강재 문틀'}]) if _match(r, t)), None)
            o['fire'] = fr['fire'] if fr else None
            o['frame'] = fr.get('frame', '강재 문틀') if fr else DET_DOOR.get('frameDefault', '목재 문틀')
            if fr and fr.get('tag'):
                o['tag'] = fr['tag']


def detail_stairs(f, stairs, ops, walls, rooms):
    wby = {w['id']: w for w in walls}
    H = float(f['height'])
    out_iss = []
    for st in stairs:
        v = st['axis'] == 'v'
        L = st['h'] if v else st['w']; Wd = st['w'] if v else st['h']
        gap = 0.2; fw = (Wd - gap) / 2
        n = math.ceil(H / RISER_MAX - 1e-9)
        n1 = (n + 1) // 2; n2 = n - n1
        riser = H / n
        land = max(STAIR_W_MIN, fw)
        run = L - 2 * land
        tread = min(TREAD_STD, run / max(1, n1 - 1))
        # 출입(층) 계단참: 계단실 문이 있는 쪽
        door = next((o for o in ops if o['kind'] == 'door' and st['room'] in wby[o['wall']]['rooms']), None)
        a0 = st['y'] if v else st['x']
        entry_lo = True
        if door:
            w = wby[door['wall']]; ax, ay = w['a']; bx, by = w['b']; Lw = w['len']
            cx, cy = ax + (bx - ax) / Lw * door['at'], ay + (by - ay) / Lw * door['at']
            entry_lo = ((cy if v else cx) - a0) < L / 2
        q0 = st['x'] if v else st['y']
        def R(alo, ahi, qlo, qhi):
            return rect(*((q0 + qlo, a0 + alo, q0 + qhi, a0 + ahi) if v else (a0 + alo, q0 + qlo, a0 + ahi, q0 + qhi)))
        sgn = 1 if entry_lo else -1
        el_lo, el_hi = (0, land) if entry_lo else (L - land, L)
        f1len, f2len = (n1 - 1) * tread, (n2 - 1) * tread
        # 오르는 계단(1)은 층 계단참에서 시작해 중간 계단참까지, 내려오는 쪽(2)은 중간 계단참에서 위층 계단참까지 — 두 계단이 같은 계단참에서 만나도록
        if entry_lo:
            f1 = (land, land + f1len); M = land + f1len; far = (M, L); f2 = (M - f2len, M); up = (0, M - f2len)
        else:
            f1 = (L - land - f1len, L - land); M = L - land - f1len; far = (0, M); f2 = (M, M + f2len); up = (M + f2len, L)
        mid_depth = far[1] - far[0]
        if tread < TREAD_MIN - 1e-6:
            # 층고가 높아 두 번 꺾기로 단너비가 안 나옴 → 세 번 꺾기 (4개 계단·중간 계단참 3곳, 같은 쪽으로 도착)
            ns = [n // 4 + (1 if k < n % 4 else 0) for k in range(4)]
            tread = min(TREAD_STD, run / max(1, max(ns) - 1))
            fl_len = [(k - 1) * tread for k in ns]
            zs = [0.0]
            for k in ns:
                zs.append(r3(zs[-1] + k * riser))
            if entry_lo:
                M = land + fl_len[0]; B = M - fl_len[1]
                fr = [(land, M), (B, M), (B, B + fl_len[2]), (M - fl_len[3], M)]
                farL = (M, L); nearL = (0, land); midL = (0, B); upL = (0, M - fl_len[3])
            else:
                M = L - land - fl_len[0]; B = M + fl_len[1]
                fr = [(M, L - land), (M, B), (B - fl_len[2], B), (M, M + fl_len[3])]
                farL = (0, M); nearL = (L - land, L); midL = (B, L); upL = (M + fl_len[3], L)
            sides = [(0, fw), (fw + gap, Wd), (0, fw), (fw + gap, Wd)]
            dirs = [sgn, -sgn, sgn, -sgn]
            mid_depth = min(farL[1] - farL[0], land)
            head = zs[2] - 0.2   # 세 번째 계단 아래 첫 계단 위 유효높이
            st.update(
                risers=n, riser=r3(riser), tread=r3(tread), flightWidth=r3(fw), landingDepth=r3(land), entry='lo' if entry_lo else 'hi', upSign=sgn, turns=3,
                flights=[dict(R(fr[k][0], fr[k][1], sides[k][0], sides[k][1]), risers=ns[k], z0=zs[k], z1=zs[k + 1], dir=dirs[k], side=k % 2) for k in range(4)],
                landings=[dict(R(nearL[0], nearL[1], 0, Wd), z=0.0, kind='층 계단참'), dict(R(farL[0], farL[1], 0, Wd), z=zs[1], kind='중간 계단참'),
                          dict(R(midL[0], midL[1], 0, Wd), z=zs[2], kind='중간 계단참'), dict(R(farL[0], farL[1], 0, Wd), z=zs[3], kind='중간 계단참'),
                          dict(R(upL[0], upL[1], fw + gap, Wd), z=r3(H), kind='위층 계단참')],
                headroom=r3(head),
                handrail={'height': 0.9, 'sides': 'both', 'center': True, 'note': '양측 손잡이 (높이 0.85~0.9m)'})
            checks = [('단높이', riser <= RISER_MAX + 1e-6, '%.1fcm (≤18)' % (riser * 100)),
                      ('단너비', tread >= TREAD_MIN - 1e-6, '%.1fcm (≥26)' % (tread * 100)),
                      ('계단 너비', fw >= STAIR_W_MIN - 1e-6, '%.2fm (≥1.2)' % fw),
                      ('계단참 너비', land >= fw - 1e-6 and mid_depth >= fw - 1e-6, '층 %.2fm · 중간 %.2fm (≥계단 너비)' % (land, farL[1] - farL[0])),
                      ('계단참 높이 간격', max(ns) * riser <= 3.0 + 1e-6, '%.2fm (≤3m마다)' % (max(ns) * riser)),
                      ('계단 위 유효높이', head >= 2.1 - 1e-6, '%.2fm (≥2.1, 겹친 계단 아래)' % head)]
            st['checks'] = [{'item': a, 'ok': ok, 'value': val} for a, ok, val in checks]
            bad = [c for c in st['checks'] if not c['ok']]
            out_iss.append({'kind': 'stair', 'ref': st['name'], 'level': 'warn' if bad else 'info',
                            'msg': '%s %s: 세 번 꺾기 %d단 (%s) · 단높이 %.1fcm · 단너비 %.1fcm · 폭 %.2fm · 겹친 계단 아래 %.2fm %s' % (
                                f['id'], st['name'], n, '+'.join(map(str, ns)), riser * 100, tread * 100, fw, head,
                                '✓' if not bad else '— ' + ', '.join('%s %s' % (c['item'], c['value']) for c in bad))})
            continue
        st.update(
            risers=n, riser=r3(riser), tread=r3(tread), flightWidth=r3(fw), landingDepth=r3(land), entry='lo' if entry_lo else 'hi', upSign=sgn,
            flights=[dict(R(f1[0], f1[1], 0, fw), risers=n1, z0=0.0, z1=r3(n1 * riser), dir=sgn, side=0),
                     dict(R(f2[0], f2[1], fw + gap, Wd), risers=n2, z0=r3(n1 * riser), z1=r3(H), dir=-sgn, side=1)],
            landings=[dict(R(el_lo, el_hi, 0, Wd), z=0.0, kind='층 계단참'), dict(R(far[0], far[1], 0, Wd), z=r3(n1 * riser), kind='중간 계단참'),
                      dict(R(up[0], up[1], fw + gap, Wd), z=r3(H), kind='위층 계단참')],
            handrail={'height': 0.9, 'sides': 'both', 'center': True, 'note': '양측 손잡이 (높이 0.85~0.9m)'})
        checks = [('단높이', riser <= RISER_MAX + 1e-6, '%.1fcm (≤18)' % (riser * 100)),
                  ('단너비', tread >= TREAD_MIN - 1e-6, '%.1fcm (≥26)' % (tread * 100)),
                  ('계단 너비', fw >= STAIR_W_MIN - 1e-6, '%.2fm (≥1.2)' % fw),
                  ('계단참 너비', land >= fw - 1e-6 and mid_depth >= fw - 1e-6, '층 %.2fm · 중간 %.2fm (≥계단 너비)' % (land, mid_depth)),
                  ('계단참 높이 간격', n1 * riser <= 3.0 + 1e-6, '%.2fm (≤3m마다)' % (n1 * riser))]
        st['checks'] = [{'item': a, 'ok': ok, 'value': val} for a, ok, val in checks]
        bad = [c for c in st['checks'] if not c['ok']]
        out_iss.append({'kind': 'stair', 'ref': st['name'], 'level': 'warn' if bad else 'info',
                        'msg': '%s %s: %d단 (%s) · 단높이 %.1fcm · 단너비 %.1fcm · 폭 %.2fm · 계단참 %.2fm %s' % (
                            f['id'], st['name'], n, '%d+%d' % (n1, n2), riser * 100, tread * 100, fw, land,
                            '✓' if not bad else '— ' + ', '.join('%s %s' % (c['item'], c['value']) for c in bad))})
    return out_iss


# ---- 가구·설비 (평면 표현용, IFC IfcFurnishingElement·IfcSanitaryTerminal 후보)
FIX = {  # kind: (폭(벽 따라), 깊이, 높이, 표시)
    'shower': (0.9, 0.9, 0.05, '샤워'), 'toilet': (0.45, 0.7, 0.4, '양변기'), 'basin': (0.6, 0.45, 0.85, '세면대'),
    'counter': (None, 0.6, 0.85, '주방 카운터'), 'fridge': (0.75, 0.75, 1.8, '냉장고'), 'shoe': (None, 0.35, 1.0, '신발장'),
    'wardrobe': (None, 0.6, 2.1, '붙박이장'), 'shelf': (None, 0.5, 2.1, '선반장'), 'washer': (0.65, 0.65, 0.9, '세탁기'),
    'bed_d': (1.5, 2.0, 0.45, '침대 (퀸)'), 'bed_s': (1.1, 2.0, 0.45, '침대 (슈퍼싱글)'), 'sofa': (2.0, 0.85, 0.8, '소파'),
    'sofa_s': (1.5, 0.8, 0.8, '소파 (2인)'), 'tv': (1.6, 0.4, 0.5, 'TV장'), 'table': (1.2, 0.75, 0.75, '식탁 (4인)'), 'table_s': (0.8, 0.7, 0.75, '식탁 (2인)'),
}
CLEAR = {'toilet': 0.55, 'basin': 0.6, 'shower': 0.6, 'counter': 0.9, 'fridge': 0.9, 'washer': 0.7, 'shoe': 0.6, 'wardrobe': 0.6, 'shelf': 0.6,
         'bed_d': 0.55, 'bed_s': 0.45, 'sofa': 0.5, 'sofa_s': 0.5, 'tv': 0.0}
MOVABLE = {'bed_d', 'bed_s', 'sofa', 'sofa_s', 'tv', 'table', 'table_s'}
OPP = {'S': 'N', 'N': 'S', 'W': 'E', 'E': 'W'}


def fixtures_for(f, rooms, walls, ops):
    """실 안목(벽 두께 안쪽)에 맞춰 가구·설비를 놓는다. 문 열림·창·서로의 사용 공간(앞 여유)을 피하고,
    주방은 카운터 한 줄(냉장고·세탁기 이어서), 침실은 침대 먼저·붙박이장 나중."""
    wby = {w['id']: w for w in walls}
    out, iss = [], []
    by_room, walls_of = {}, {}
    for o in ops:
        for rid in wby[o['wall']]['rooms']:
            if rid:
                by_room.setdefault(rid, []).append(o)
    for w in walls:
        for rid in w['rooms']:
            if rid:
                walls_of.setdefault(rid, []).append(w)

    def on_side(w, o_, c):
        return (o_ == 'h' and abs(w['a'][1] - w['b'][1]) < 1e-6 and abs(w['a'][1] - c) < 1e-6) or (o_ == 'v' and abs(w['a'][0] - w['b'][0]) < 1e-6 and abs(w['a'][0] - c) < 1e-6)

    def raw_side(r, sd):
        x0, y0, x1, y1 = r['x'], r['y'], r['x'] + r['w'], r['y'] + r['h']
        return {'S': ('h', y0, x0, x1, 1), 'N': ('h', y1, x0, x1, -1), 'W': ('v', x0, y0, y1, 1), 'E': ('v', x1, y0, y1, -1)}[sd]

    inset_cache = {}

    def insets(r):
        if r['id'] not in inset_cache:
            d = {}
            for sd in 'SNWE':
                o_, c, lo, hi, inw = raw_side(r, sd)
                d[sd] = max([w['t'] / 2 for w in walls_of.get(r['id'], []) if on_side(w, o_, c)] or [0.0])
            inset_cache[r['id']] = d
        return inset_cache[r['id']]

    def inner(r):
        I = insets(r)
        return (r['x'] + I['W'], r['y'] + I['S'], r['x'] + r['w'] - I['E'], r['y'] + r['h'] - I['N'])

    def side_geom(r, sd):  # 안목 기준: (방향, 벽면 좌표, 구간, 안쪽 부호)
        o_, c, lo, hi, inw = raw_side(r, sd)
        I = insets(r); ix = inner(r)
        cin = c + inw * I[sd]
        lo, hi = (ix[0], ix[2]) if o_ == 'h' else (ix[1], ix[3])
        return o_, c, cin, lo, hi, inw

    def covered(r, sd, a, b):
        o_, c, lo, hi, _ = raw_side(r, sd)
        k = 0 if o_ == 'h' else 1
        segs = sorted((min(w['a'][k], w['b'][k]), max(w['a'][k], w['b'][k])) for w in walls_of.get(r['id'], []) if on_side(w, o_, c))
        cur = a
        for s0, s1 in segs:
            if s0 <= cur + 1e-6 and s1 > cur:
                cur = s1
            if cur >= b - 1e-6:
                return True
        return cur >= b - 1e-6

    def keepouts(r, tall):
        K = []
        for o in by_room.get(r['id'], []):
            w = wby[o['wall']]; ax, ay = w['a']; bx, by = w['b']; Lw = w['len']; ux, uy = (bx - ax) / Lw, (by - ay) / Lw
            cx, cy = ax + ux * o['at'], ay + uy * o['at']; hw = o['w'] / 2
            hz = abs(ay - by) < 1e-6
            if o['kind'] == 'door':
                dep = o['w'] + 0.15 if o.get('swingTo') == r['id'] else (0.6 if o.get('sliding') else 0.8)
            else:
                if not tall and o.get('sill', 0) >= 0.85:
                    continue
                dep = 0.55
            if hz:
                inward = 1 if cy < r['y'] + r['h'] / 2 else -1
                K.append((cx - hw - 0.1, min(cy, cy + inward * dep), cx + hw + 0.1, max(cy, cy + inward * dep)))
            else:
                inward = 1 if cx < r['x'] + r['w'] / 2 else -1
                K.append((min(cx, cx + inward * dep), cy - hw - 0.1, max(cx, cx + inward * dep), cy + hw + 0.1))
        return K

    def ov(a, b):
        return min(a[2], b[2]) - max(a[0], b[0]) > 1e-6 and min(a[3], b[3]) - max(a[1], b[1]) > 1e-6

    def front_rect(rc, o_, inw, c_front, a, b, dep):
        if dep <= 0:
            return None
        if o_ == 'h':
            y = rc[3] if inw > 0 else rc[1]
            return (a, min(y, y + inw * dep), b, max(y, y + inw * dep))
        x = rc[2] if inw > 0 else rc[0]
        return (min(x, x + inw * dep), a, max(x, x + inw * dep), b)

    def clash(r, rc, cl, K, placed):
        ix = inner(r)
        if rc[0] < ix[0] - 1e-6 or rc[2] > ix[2] + 1e-6 or rc[1] < ix[1] - 1e-6 or rc[3] > ix[3] + 1e-6:
            return True
        if any(ov(rc, k) for k in K):
            return True
        for p in placed:
            pr = (p['x'], p['y'], p['x'] + p['w'], p['y'] + p['h'])
            if ov(rc, pr) or (p.get('_clr') and ov(rc, p['_clr'])) or (cl and ov(cl, pr)):
                return True
        return False

    def add(r, kind, rc, sd, placed, cl, **kw):
        wid, dep, hgt, lab = FIX[kind]
        p = {'id': '%s-fx%d' % (f['id'], len(out) + 1), 'room': r['id'], 'kind': kind, 'label': lab, 'side': sd, 'h3': hgt,
             'x': r3(rc[0]), 'y': r3(rc[1]), 'w': r3(rc[2] - rc[0]), 'h': r3(rc[3] - rc[1])}
        if kind in MOVABLE:
            p['movable'] = True
        p.update(kw)
        p['_clr'] = cl
        placed.append(p); out.append(p)
        return p

    def try_place(r, kind, length, placed, tall=False, corner=False, sides='SNWE', min_len=None, near=None, far_from=None):
        wid, dep, hgt, lab = FIX[kind]
        K = keepouts(r, tall)
        Ls = [length] if length else [wid]
        if min_len:
            Ls = [x / 20.0 for x in range(int(round(length * 20)), int(round(min_len * 20)) - 1, -1)]
        ix = inner(r)

        def cand(sd, a, Lf):
            o_, c, cin, lo, hi, inw = side_geom(r, sd)
            b = a + Lf
            if a < lo - 1e-6 or b > hi + 1e-6:
                return None
            rs_ = raw_side(r, sd)
            if not covered(r, sd, max(a, rs_[2]), min(b, rs_[3])):
                return None
            rc = (a, min(cin, cin + inw * dep), b, max(cin, cin + inw * dep)) if o_ == 'h' else (min(cin, cin + inw * dep), a, max(cin, cin + inw * dep), b)
            cl = front_rect(rc, o_, inw, cin, a, b, CLEAR.get(kind, 0))
            if cl:  # 앞 여유가 맞은편 벽을 넘으면(열린 쪽이 아니면) 안 됨
                over = (cl[1] < ix[1] - 1e-6 or cl[3] > ix[3] + 1e-6) if o_ == 'h' else (cl[0] < ix[0] - 1e-6 or cl[2] > ix[2] + 1e-6)
                if over:
                    ro = raw_side(r, OPP[sd])
                    if covered(r, OPP[sd], max(a, ro[2]), min(b, ro[3])):
                        return None
                cl = (max(cl[0], ix[0]), max(cl[1], ix[1]), min(cl[2], ix[2]), min(cl[3], ix[3]))
            if clash(r, rc, cl, K, placed):
                return None
            return rc, cl

        if near:  # 카운터에 이어 붙이기
            sd = near[0]
            for Lf in Ls:
                for a in (near[2], near[1] - Lf):
                    got = cand(sd, a, Lf)
                    if got:
                        return add(r, kind, got[0], sd, placed, got[1])
            return None
        for Lf in Ls:  # 긴 것부터, 처음 들어가는 길이에서 멈춤
            cands = []
            for sd in sides:
                o_, c, cin, lo, hi, inw = side_geom(r, sd)
                span = hi - lo
                if Lf > span + 1e-6:
                    continue
                starts = [lo, hi - Lf] if corner else [lo + i * 0.05 for i in range(int((span - Lf) / 0.05 + 1e-6) + 1)] + [hi - Lf]
                for a in starts:
                    got = cand(sd, a, Lf)
                    if not got:
                        continue
                    rc, cl = got
                    score = 0.0
                    if far_from:
                        mx, my = (rc[0] + rc[2]) / 2, (rc[1] + rc[3]) / 2
                        score += min(((mx - fx) ** 2 + (my - fy) ** 2) ** 0.5 for fx, fy in far_from)
                    else:  # 벽 가운데보다 구석 쪽을 선호 (동선 확보)
                        score -= min(abs(a - lo), abs(hi - (a + Lf))) * 0.1
                    cands.append((score, rc, sd, cl))
            if cands:
                sc, rc, sd, cl = max(cands, key=lambda c: c[0])
                return add(r, kind, rc, sd, placed, cl)
        return None

    def place_free(r, kind, placed, chairs=0.55):
        """식탁: 벽에 붙이지 않고 놓을 수 있는 자리 (의자 자리 포함). 후보 좌표는 장애물 가장자리에서만."""
        wid, dep, hgt, lab = FIX[kind]
        K = keepouts(r, False); ix = inner(r); best = None
        cx0, cy0 = (ix[0] + ix[2]) / 2, (ix[1] + ix[3]) / 2
        obs = list(K) + [(p['x'], p['y'], p['x'] + p['w'], p['y'] + p['h']) for p in placed] + [p['_clr'] for p in placed if p.get('_clr')]
        for rot in (False, True):
            W, D = (wid, dep) if not rot else (dep, wid)
            fw, fd = (W, D + 2 * chairs) if not rot else (W + 2 * chairs, D)
            xs = {ix[0], ix[2] - fw, cx0 - fw / 2} | {o[2] for o in obs} | {o[0] - fw for o in obs}
            ys = {ix[1], ix[3] - fd, cy0 - fd / 2} | {o[3] for o in obs} | {o[1] - fd for o in obs}
            for x in xs:
                if x < ix[0] - 1e-6 or x + fw > ix[2] + 1e-6:
                    continue
                for y in ys:
                    if y < ix[1] - 1e-6 or y + fd > ix[3] + 1e-6:
                        continue
                    foot = (x, y, x + fw, y + fd)
                    if clash(r, foot, None, K, placed):
                        continue
                    tb = (x, y + chairs, x + W, y + chairs + D) if not rot else (x + chairs, y, x + chairs + W, y + D)
                    d = ((x + fw / 2 - cx0) ** 2 + (y + fd / 2 - cy0) ** 2) ** 0.5
                    if best is None or d < best[0]:
                        best = (d, tb, foot, rot)
        if not best:
            return None
        p = add(r, kind, best[1], None, placed, None, chairs='long' if not best[3] else 'wide')
        p['_clr'] = best[2]
        return p

    units = {}
    for r in rooms:
        if r['kind'] == 'unitroom':
            units.setdefault(r['unit'], []).append(r)
    for un, urs in units.items():
        names = {r['name'] for r in urs}
        has_utility = '다용도실' in names
        placed_by = {r['id']: [] for r in urs}
        doors_of = lambda r: [(lambda w: (w['a'][0] + (w['b'][0] - w['a'][0]) / w['len'] * o['at'], w['a'][1] + (w['b'][1] - w['a'][1]) / w['len'] * o['at']))(wby[o['wall']]) for o in by_room.get(r['id'], []) if o['kind'] == 'door']
        miss = []
        # 1) 고정 설비
        for r in urs:
            n = r['name']; P = placed_by[r['id']]
            if n.startswith('욕실'):
                if not try_place(r, 'shower', None, P, tall=True, corner=True, far_from=doors_of(r)):
                    miss.append('샤워')
                if not try_place(r, 'toilet', None, P):
                    miss.append('양변기')
                if not try_place(r, 'basin', None, P):
                    miss.append('세면대')
            elif n in ('주방', '주방·식당', '거실·주방'):
                c = try_place(r, 'counter', 3.0 if n == '주방·식당' else 2.4, P, min_len=1.5)
                if not c:
                    miss.append('주방 카운터')
                    continue
                o_ = 'h' if c['side'] in 'SN' else 'v'
                a, b = (c['x'], c['x'] + c['w']) if o_ == 'h' else (c['y'], c['y'] + c['h'])
                fr = None
                fr = try_place(r, 'fridge', None, P, tall=True, near=(c['side'], a, b)) or try_place(r, 'fridge', None, P, tall=True)
                if not fr:  # 주방이 좁으면 다용도실·식당·거실 쪽 벽으로
                    for r2 in sorted((q for q in urs if q['name'] in ('다용도실', '주방·식당', '거실')), key=lambda q: ('다용도실', '주방·식당', '거실').index(q['name'])):
                        if try_place(r2, 'fridge', None, placed_by[r2['id']], tall=True):
                            fr = True; break
                if not fr:
                    miss.append('냉장고')
                    fr = None
                if not has_utility:
                    fd = fr if isinstance(fr, dict) and fr.get('side') == c['side'] else None
                    nb = (c['side'], min(a, (fd['x'] if o_ == 'h' else fd['y']) if fd else a), max(b, ((fd['x'] + fd['w']) if o_ == 'h' else (fd['y'] + fd['h'])) if fd else b))
                    if not try_place(r, 'washer', None, P, near=nb):
                        if c['w'] * c['h'] / 0.6 >= 1.8:
                            c['washer'] = True       # 카운터 하부 빌트인 세탁기
                        elif not any(try_place(q, 'washer', None, placed_by[q['id']]) for q in urs if q['name'].startswith('욕실')):
                            miss.append('세탁기')
            elif n == '현관':
                try_place(r, 'shoe', 1.2, P, tall=True, min_len=0.6)
            elif n == '다용도실':
                try_place(r, 'washer', None, P)
                try_place(r, 'shelf', 1.2, P, tall=True, min_len=0.6)
            elif n == '수납':
                try_place(r, 'shelf', 2.0, P, tall=True, min_len=0.6)
            elif n == '드레스룸':
                try_place(r, 'shelf', 2.6, P, tall=True, min_len=0.9)
        # 2) 침실: 침대 먼저, 붙박이장은 남는 벽에
        for r in urs:
            n = r['name']; P = placed_by[r['id']]
            if n.startswith('침실') or n in ('거실·침실', '거실·주방'):
                big = n in ('침실1', '거실·침실', '침실(알코브)') or (n.startswith('침실') and min(r['w'], r['h']) >= 3.0)
                bed = (try_place(r, 'bed_d', None, P, sides='SNWE') if big else None) or try_place(r, 'bed_s', None, P)
                if not bed and n != '거실·주방':
                    miss.append('%s 침대' % n)
                if n != '거실·주방':
                    try_place(r, 'wardrobe', 2.4, P, tall=True, min_len=0.9)
        # 3) 거실: 소파 + 맞은편 TV
        studio = '거실·주방' in names or '거실·침실' in names
        for r in urs:
            if r['name'] in ('거실', '거실·침실') and not (studio and r['name'] == '거실·침실'):
                P = placed_by[r['id']]
                s = try_place(r, 'sofa', None, P) or try_place(r, 'sofa_s', None, P)
                if s:
                    try_place(r, 'tv', None, P, sides=OPP[s['side']])
        # 4) 식탁: 주방·식당 → 주방 → 거실 순
        order = sorted(urs, key=lambda r: {'주방·식당': 0, '주방': 1, '거실·주방': 2, '거실': 3}.get(r['name'], 9))
        done = False
        for kind in ('table', 'table_s'):
            for r in order:
                if r['name'] in ('주방·식당', '주방', '거실·주방', '거실') and place_free(r, kind, placed_by[r['id']]):
                    done = True; break
            if done:
                break
        if not done and not studio:
            miss.append('식탁')
        if miss:
            lvl = 'warn' if any(m in ('양변기', '주방 카운터') or m.endswith('침대') for m in miss) else 'info'
            iss.append({'kind': 'fixture', 'ref': un, 'level': lvl, 'msg': '%s: %s 둘 자리 부족' % (un, ', '.join(miss))})
    for p in out:
        p.pop('_clr', None)
    return out, iss



def daylight_check(rooms, walls, ops, units):
    """거실(침실 포함)의 채광: 창 면적 ≥ 바닥면적 1/10 (피난·방화규칙 제17조). 창이 없는 실은 벽 없이 트인 실이나
    알코브 미닫이로 이어진 실과 묶어서 확인한다 (간접 채광)."""
    out = []
    wby = {w['id']: w for w in walls}
    byid = {r['id']: r for r in rooms}
    win_area = {}
    sliding_pairs = set()
    for o in ops:
        w = wby[o['wall']]
        if o['kind'] == 'window' and None in w['rooms']:
            rid = next(x for x in w['rooms'] if x)
            win_area[rid] = win_area.get(rid, 0) + o['w'] * o['h']
        if o['kind'] == 'door' and o.get('sliding') and all(w['rooms']):
            sliding_pairs.add(frozenset(w['rooms']))
    walled = {frozenset(w['rooms']) for w in walls if all(w['rooms'])}
    for u in units:
        rs = [byid[i] for i in u['rooms'] if i in byid]
        def touching(a, b):
            ox = min(a['x'] + a['w'], b['x'] + b['w']) - max(a['x'], b['x']); oy = min(a['y'] + a['h'], b['y'] + b['h']) - max(a['y'], b['y'])
            return (ox > 0.3 and abs(oy) < 1e-6) or (oy > 0.3 and abs(ox) < 1e-6)
        link = {r['id']: set() for r in rs}
        for i, a in enumerate(rs):
            for b in rs[i + 1:]:
                if not touching(a, b):
                    continue
                pair = frozenset((a['id'], b['id']))
                if pair not in walled or pair in sliding_pairs:   # 벽 없이 트였거나 미닫이 칸막이
                    link[a['id']].add(b['id']); link[b['id']].add(a['id'])
        for r in rs:
            if r['name'] not in HABITABLE:
                continue
            area = r['w'] * r['h']; own = win_area.get(r['id'], 0)
            if own >= area / 10 - 1e-6:
                continue
            grp, todo = {r['id']}, [r['id']]
            while todo:
                k = todo.pop()
                for j in link[k]:
                    if j not in grp:
                        grp.add(j); todo.append(j)
            g_win = sum(win_area.get(j, 0) for j in grp)
            g_area = sum(byid[j]['w'] * byid[j]['h'] for j in grp if byid[j]['name'] in HABITABLE or byid[j]['name'] in UNIT_OPEN)
            via = [byid[j]['name'] for j in grp if j != r['id'] and win_area.get(j)]
            if g_win >= g_area / 10 - 1e-6 and via:
                out.append({'kind': 'daylight', 'ref': u['name'], 'level': 'info', 'msg': '%s: [참고] %s 채광은 %s 쪽 창으로 (창 %.1f㎡ ≥ 이어진 실 %.1f㎡의 1/10)' % (
                    u['name'], r['name'], '·'.join(sorted(set(via))), g_win, g_area)})
            else:
                out.append({'kind': 'daylight', 'ref': u['name'], 'level': 'warn', 'msg': '%s %s: 채광 부족 — 창 %.1f㎡ < 바닥면적 1/10 (%.1f㎡)' % (
                    u['name'], r['name'], own if not via else g_win, area / 10 if not via else g_area / 10)})
    return out


def build_floor(f, elev):
    below = f['level'] < 0
    oedges = outline_edges(f['outline'])
    zones = f['zones']
    circ = [z for z in zones if z['type'] in CIRC]
    if below:
        circ = circ + [z for z in zones if z['type'] in GARAGE]
    rooms, units, issues, stairs, evs = [], [], [], [], []
    rid = [0]

    def nid(p):
        rid[0] += 1
        return '%s-%s%d' % (f['id'], p, rid[0])
    inst_by_zone = {}
    for z in zones:
        zr = (z['x'], z['y'], z['x'] + z['w'], z['y'] + z['h'])
        if z['type'] == 'unit':
            ut = z.get('unitType') or '원룸'
            A = unit_analyze(z, oedges, circ)
            if not A:
                rooms.append(dict(rect(*zr), id=nid('r'), name=z['name'], kind='unitroom', zone=z['id'], ztype='unit', zname=z['name'], unit=z['name']))
                issues.append({'kind': 'unit', 'ref': z['name'], 'msg': '%s: 출입 또는 창 방향을 찾지 못해 내부 계획 생략' % z['name']})
                continue
            key = unit_key(ut, A)
            gen, uiss = unit_layout(ut, A['W'], A['D'], A['back_f'], A['lat_f'])
            lay = EDITS.get(key, {}).get('rooms') or gen
            if key in EDITS:
                uiss = []
            inst = {'floor': f['id'], 'zone': z['id'], 'name': z['name'], 'back': A['back'], 'mirror': A['mirror'], 'W': r3(A['W']), 'D': r3(A['D']),
                    'x0': zr[0], 'y0': zr[1], 'x1': zr[2], 'y1': zr[3]}
            reg_type(key, 'unit', 'unit', gen, inst, unitType=ut, W=A['W'], D=A['D'], issues=uiss)
            inst_by_zone[z['id']] = (key, inst)
            ids = []
            for L in lay:
                r = dict(c2f(inst, L['t0'], L['t1'], L['d0'], L['d1']), name=L['name'])
                r.update(id=nid('r'), kind='unitroom', zone=z['id'], ztype='unit', zname=z['name'], unit=z['name'])
                rooms.append(r); ids.append(r['id'])
            units.append({'name': z['name'], 'zoneId': z['id'], 'unitType': ut, 'area': r3(z['w'] * z['h']), 'back': A['back'], 'type': key, 'rooms': ids, 'issues': uiss})
            for m in uiss:
                issues.append({'kind': 'unit', 'ref': z['name'], 'level': 'info' if m.startswith('[참고]') else 'warn', 'msg': '%s: %s' % (z['name'], m.replace('[참고] ', ''))})
        elif z['type'] == 'core':
            key = 'C|%.2f|%.2f' % (z['w'], z['h'])
            gen = core_layout(z)
            lay = EDITS.get(key, {}).get('rooms') or gen
            inst = {'floor': f['id'], 'zone': z['id'], 'name': z['name'], 'back': 'N', 'mirror': False, 'W': r3(z['w']), 'D': r3(z['h']), 'x0': zr[0], 'y0': zr[1], 'x1': zr[2], 'y1': zr[3]}
            reg_type(key, 'core', 'core', gen, inst, W=z['w'], D=z['h'])
            inst_by_zone[z['id']] = (key, inst)
            for L in lay:
                r = dict(c2f(inst, L['t0'], L['t1'], L['d0'], L['d1']), name=L['name'], id=nid('c'), zone=z['id'], ztype='core', zname=z['name'], corekind=L.get('kind', 'shaft'), kind='core', type=key)
                if L.get('door'):
                    r['door'] = L['door']
                if L.get('emergency'):
                    r['emergency'] = True
                rooms.append(r)
                if r['corekind'] == 'stair':
                    long_v = r['h'] >= r['w']
                    stairs.append({'id': r['id'] + '-st', 'room': r['id'], 'name': r['name'], 'x': r['x'], 'y': r['y'], 'w': r['w'], 'h': r['h'], 'axis': 'v' if long_v else 'u',
                                   'flightWidth': r3(((r['w'] if long_v else r['h']) - 0.2) / 2), 'risers': max(2, round(f['height'] / 0.17)), 'riser': r3(f['height'] / max(2, round(f['height'] / 0.17)))})
                if r['corekind'] == 'ev':
                    evs.append({'id': r['id'] + '-ev', 'room': r['id'], 'name': r['name'], 'x': r['x'], 'y': r['y'], 'w': r['w'], 'h': r['h'], 'door': r.get('door', 'E'), 'emergency': bool(r.get('emergency'))})
        elif z['type'] in ZONE_TYPED:
            key = 'Z|%s|%.2f|%.2f' % (z['type'], z['w'], z['h'])
            gen = [{'name': '*', 't0': 0.0, 't1': r3(z['w']), 'd0': 0.0, 'd1': r3(z['h'])}]
            lay = EDITS.get(key, {}).get('rooms') or gen
            inst = {'floor': f['id'], 'zone': z['id'], 'name': z['name'], 'back': 'N', 'mirror': False, 'W': r3(z['w']), 'D': r3(z['h']), 'x0': zr[0], 'y0': zr[1], 'x1': zr[2], 'y1': zr[3]}
            reg_type(key, 'zone', z['type'], gen, inst, W=z['w'], D=z['h'])
            inst_by_zone[z['id']] = (key, inst)
            for L in lay:
                main = L['name'] == '*'
                r = dict(c2f(inst, L['t0'], L['t1'], L['d0'], L['d1']), id=nid('z'), name=z['name'] if main else L['name'], kind='zone', zone=z['id'], ztype=z['type'], zname=z['name'], type=key)
                if not main:
                    r['sub'] = True
                rooms.append(r)
        else:
            rooms.append(dict(rect(*zr), id=nid('z'), name=z['name'], kind='zone', zone=z['id'], ztype=z['type'], zname=z['name']))
    byid = {r['id']: r for r in rooms}
    walls = decompose(rooms, f['outline'], below)
    # 지상 램프 진입부(하강 반대쪽 끝)는 차량 출입구 — 외벽을 두지 않음
    if not below:
        for z in zones:
            if z['type'] != 'ramp':
                continue
            dn = z.get('rampDown', 'N')
            edge = {'N': ('h', z['y']), 'S': ('h', z['y'] + z['h']), 'E': ('v', z['x']), 'W': ('v', z['x'] + z['w'])}[dn]
            rid = next((r['id'] for r in rooms if r['zone'] == z['id']), None)
            def is_entry(w):
                if rid not in w['rooms'] or None not in w['rooms']:
                    return False
                hz = abs(w['a'][1] - w['b'][1]) < 1e-6
                return (edge[0] == 'h' and hz and abs(w['a'][1] - edge[1]) < 1e-6) or (edge[0] == 'v' and not hz and abs(w['a'][0] - edge[1]) < 1e-6)
            gone = [w for w in walls if is_entry(w)]
            if gone:
                walls = [w for w in walls if w not in gone]
                issues.append({'kind': 'ramp', 'ref': z['name'], 'level': 'info', 'msg': '%s 램프 진입부 %.1fm: 차량 출입구 (외벽 없음)' % (f['id'], sum(w['len'] for w in gone))})
    for i, w in enumerate(walls):
        w['id'] = '%s-w%d' % (f['id'], i + 1)
    # 벽 찾기
    def walls_between(a_ids, b_pred):
        res = []
        for w in walls:
            r0, r1 = w['rooms']
            for x, y in ((r0, r1), (r1, r0)):
                if x in a_ids and b_pred(byid.get(y) if y else None):
                    res.append(w)
        return res
    ops = []
    oc = [0]

    def op(kind, wall, width, typ, prefer='center', h=None, sill=0.0, **kw):
        h = DOOR_H if h is None else h
        avoid = [(o['at'] - o['w'] / 2, o['at'] + o['w'] / 2) for o in ops if o['wall'] == wall['id']]
        c, wd = place(wall, width, prefer, avoid)
        oc[0] += 1
        o = {'id': '%s-%s%d' % (f['id'], 'd' if kind == 'door' else 'n', oc[0]), 'kind': kind, 'type': typ, 'wall': wall['id'], 'at': c, 'w': wd, 'h': r3(h), 'sill': r3(sill)}
        o.update(kw)
        ops.append(o)
        return o
    is_circ = lambda r: r is not None and (r['ztype'] in CIRC or (below and r['ztype'] in GARAGE))
    # ---- 세대 문
    for u in units:
        rs = [byid[i] for i in u['rooms']]
        named = lambda n: [r['id'] for r in rs if r['name'] == n]
        pub = {r['id'] for r in rs if r['name'] in UNIT_OPEN}
        ent = walls_between(set(named('현관')), is_circ) or walls_between(pub, is_circ)
        if ent:
            op('door', max(ent, key=lambda w: w['len']), door_w('entrance', 1.0), '세대 현관문 (방화 갑종)', unit=u['name'])
        else:
            issues.append({'kind': 'door', 'ref': u['name'], 'msg': '%s: 현관문을 둘 공용 벽이 없음' % u['name']})
        for r in rs:
            if r['name'].startswith('욕실'):
                cand = walls_between({r['id']}, lambda q: q is not None and q['id'] in pub)
                if cand:
                    op('door', max(cand, key=lambda w: w['len']), door_w('bath', 0.8), '욕실문', prefer='start', unit=u['name'])
                else:
                    issues.append({'kind': 'door', 'ref': u['name'], 'msg': '%s: 욕실 출입 벽 없음' % u['name']})
            elif r['name'] not in UNIT_OPEN:
                alc = r['name'] == '침실(알코브)'
                pubw = walls_between({r['id']}, lambda q: q is not None and q['id'] in pub)
                anyw = pubw or walls_between({r['id']}, lambda q: q is not None and q['zone'] == r['zone'] and q['name'].startswith('침실'))
                if anyw:
                    w = max(anyw, key=lambda w: w['len'])
                    if alc:
                        op('door', w, min(door_w('alcoveMax', 2.4), w['len'] - 0.4), '미닫이 칸막이문 (알코브)', unit=u['name'], sliding=True)
                    else:
                        bed = r['name'].startswith('침실')
                        op('door', w, door_w('bedroom', 0.9) if bed else door_w('storage', 0.8), '실내문' if bed or r['name'] in ('드레스룸', '다용도실') else '수납문', prefer='start', unit=u['name'])
                else:
                    issues.append({'kind': 'door', 'ref': u['name'], 'msg': '%s: %s 출입 벽 없음' % (u['name'], r['name'])})
    # ---- 코어 문
    for r in rooms:
        if r['kind'] != 'core':
            continue
        ck = r['corekind']
        if ck == 'stair':
            vw = walls_between({r['id']}, lambda q: q is not None and q['zone'] == r['zone'] and q.get('corekind') == 'vestibule')
            if vw:
                op('door', max(vw, key=lambda w: w['len']), 0.9, '방화문 (계단실↔부속실)')
            else:
                issues.append({'kind': 'core', 'ref': r['name'], 'msg': '%s %s: 부속실과 맞닿지 않음' % (f['id'], r['name'])})
        elif ck == 'vestibule':
            cw = walls_between({r['id']}, is_circ)
            if cw:
                op('door', max(cw, key=lambda w: w['len']), 1.0, '방화문 (부속실↔복도)')
            else:
                other = walls_between({r['id']}, lambda q: q is not None and q['zone'] != r['zone'])
                if other:
                    w = max(other, key=lambda w: w['len']); q = [byid[x] for x in w['rooms'] if x and x != r['id']][0]
                    op('door', w, 1.0, '방화문 (부속실↔%s)' % q['name'])
                    if f['level'] >= 1 and q['ztype'] not in CIRC:
                        issues.append({'kind': 'core', 'ref': r['name'], 'msg': '%s %s 출입이 %s로 열림 (공용 복도 아님)' % (f['id'], r['name'], q['name'])})
        elif ck == 'ev':
            side = r.get('door', 'E')
            cand = [w for w in walls if r['id'] in w['rooms'] and ((side in 'EW' and w['a'][0] == w['b'][0] and abs(w['a'][0] - (r['x'] + (r['w'] if side == 'E' else 0))) < 1e-6) or (side in 'NS' and w['a'][1] == w['b'][1] and abs(w['a'][1] - (r['y'] + (r['h'] if side == 'N' else 0))) < 1e-6))]
            if cand:
                w = max(cand, key=lambda w: w['len']); q = [byid.get(x) for x in w['rooms'] if x != r['id']][0]
                op('door', w, 1.1 if r.get('emergency') else 0.9, '승강기 문 (비상용)' if r.get('emergency') else '승강기 문')
                if q is not None and not (q['ztype'] in CIRC or q.get('corekind') == 'vestibule' or (below and q['ztype'] in GARAGE)):
                    issues.append({'kind': 'core', 'ref': r['name'], 'msg': '%s %s 문이 %s로 열림' % (f['id'], r['name'], q['name'])})
        elif ck == 'shaft':
            cw = walls_between({r['id']}, is_circ)
            if cw:
                op('door', max(cw, key=lambda w: w['len']), 0.7, '점검문 (PS)')
    # ---- 기타 실 문
    shop_wait = []
    for r in rooms:
        if f['level'] == 1 and r['kind'] == 'zone' and r['ztype'] in ('lobby', 'corridor') and not r.get('sub'):
            # 피난층 주거 출입홀·로비 → 정면 도로(건축선) 쪽 외부 출입문
            ext = [w for w in walls if r['id'] in w['rooms'] and None in w['rooms'] and w['kind'] == 'exterior' and w['a'][1] == w['b'][1] == 0.0]
            if ext:
                w = max(ext, key=lambda w: w['len'])
                op('door', w, 1.8 if w['len'] >= 2.4 else 1.0, '출입문 (%s, 자동문)' % ROAD)
                continue
        if r['kind'] != 'zone' or r['ztype'] in CIRC or r['ztype'] in GARAGE:
            continue
        if r.get('sub'):
            cw = walls_between({r['id']}, is_circ)
            sib = walls_between({r['id']}, lambda q: q is not None and q['zone'] == r['zone'] and not q.get('sub'))
            sib = sib or walls_between({r['id']}, lambda q: q is not None and q['zone'] == r['zone'])
            if sib:
                op('door', max(sib, key=lambda w: w['len']), door_w('common', 0.9), '실내문')
            elif cw:
                op('door', max(cw, key=lambda w: w['len']), door_w('common', 0.9), '출입문')
            else:
                issues.append({'kind': 'door', 'ref': r['name'], 'msg': '%s %s (%s): 출입할 벽이 없음' % (f['id'], r['name'], r['zname'])})
            continue
        ext = [w for w in walls if r['id'] in w['rooms'] and None in w['rooms'] and w['kind'] == 'exterior']
        if f['level'] == 1 and r['ztype'] in ('retail', 'public', 'lobby') and ext and any(e['a'][1] == e['b'][1] == 0.0 for e in ext):
            w = max([e for e in ext if e['a'][1] == e['b'][1] == 0.0], key=lambda w: w['len'])
            op('door', w, 1.8 if w['len'] >= 3.0 else 1.0, '출입문 (%s, 자동문)' % ROAD if r['ztype'] == 'lobby' else '출입문 (%s)' % ROAD)
            continue
        if r['ztype'] == 'lobby':
            continue
        cw = walls_between({r['id']}, is_circ)
        if cw:
            w = max(cw, key=lambda w: w['len'])
            op('door', w, 0.9 if r['ztype'] == 'mep' else 1.2, '방화문' if r['ztype'] == 'mep' else '출입문')
        elif r['ztype'] == 'mep' and walls_between({r['id']}, lambda q: q is not None and q['ztype'] == 'mep'):
            w = max(walls_between({r['id']}, lambda q: q is not None and q['ztype'] == 'mep'), key=lambda w: w['len'])
            op('door', w, 0.7, '점검문 (인접 설비실 경유)')
        elif r['ztype'] == 'retail' and any(q is not r and q['ztype'] == 'retail' and q['zname'].split(' (')[0] == r['zname'].split(' (')[0] for q in rooms):
            shop_wait.append(r)  # 같은 점포(이름이 같은 근생)와 벽 없이 이어짐 → 점포 어딘가에 문이 있으면 OK
        elif r['ztype'] not in ('public',):
            if not any(o for o in ops if o['kind'] == 'door' and r['id'] in next((w['rooms'] for w in walls if w['id'] == o['wall']), [])):
                issues.append({'kind': 'door', 'ref': r['name'], 'msg': '%s %s: 공용부와 맞닿은 벽이 없어 출입문 미배치' % (f['id'], r['name'])})
    # 지하 보행통로·EV홀 ↔ 주차장 출입문
    if below:
        for r in rooms:
            if r['kind'] != 'zone' or r['ztype'] != 'corridor':
                continue
            cand = [w for w in walls if r['id'] in w['rooms'] and any(x and byid[x]['ztype'] in GARAGE and byid[x]['ztype'] != 'ramp' for x in w['rooms'] if x != r['id'])]
            if cand:
                w = max(cand, key=lambda w: w['len'])
                op('door', w, 1.2, '방화문 (EV홀↔주차장, 자동)')
    for r in shop_wait:
        base = r['zname'].split(' (')[0]
        same = {q['id'] for q in rooms if q['ztype'] == 'retail' and q['zname'].split(' (')[0] == base}
        if not any(o['kind'] == 'door' and set(next((w['rooms'] for w in walls if w['id'] == o['wall']), [])) & same for o in ops):
            issues.append({'kind': 'door', 'ref': r['name'], 'msg': '%s %s: 점포 전체에 공용부 출입문이 없음' % (f['id'], base)})
    # ---- 문 열림 방향·소속 실 (IFC·문 이동 편집용)
    wby = {w['id']: w for w in walls}
    CLOSED = lambda r: r is not None and ((r['kind'] == 'unitroom' and r['name'] not in UNIT_OPEN) or r.get('corekind') in ('stair', 'vestibule', 'shaft') or r['ztype'] == 'mep' or r.get('sub'))
    for o in ops:
        if o['kind'] != 'door':
            continue
        ra, rb = [byid.get(x) if x else None for x in wby[o['wall']]['rooms']]
        if o['type'].startswith('세대 현관문'):
            own = ra if ra is not None and ra['kind'] == 'unitroom' else rb
            o['owner'] = own['id']; oth = rb if own is ra else ra
            o['swingTo'] = oth['id'] if oth else None          # 세대 현관문은 공용 복도 쪽(밖)으로 열림
            continue
        if o.get('sliding') or '승강기' in o['type']:
            own = next((r for r in (ra, rb) if r is not None and (r.get('corekind') == 'ev' or CLOSED(r))), ra or rb)
            o['owner'] = own['id']; o['swingTo'] = None
            continue
        if ra is not None and rb is not None and ra.get('corekind') == 'stair' or (rb is not None and rb.get('corekind') == 'stair'):
            own = ra if ra is not None and ra.get('corekind') == 'stair' else rb   # 피난 방향: 부속실 → 계단실
        elif CLOSED(ra) and not CLOSED(rb):
            own = ra
        elif CLOSED(rb) and not CLOSED(ra):
            own = rb
        elif ra is None or rb is None:
            own = ra or rb
        else:
            own = ra if not is_circ(ra) else rb
        o['owner'] = own['id']; o['swingTo'] = own['id']
        if f['level'] == 1 and own.get('corekind') == 'stair':
            # 피난층: 계단실에서 빠져나가는 방향(부속실 쪽)으로 열림 (피난규칙 제9조②, 검토서 F-05)
            oth = rb if own is ra else ra
            o['swingTo'] = oth['id'] if oth else None
    # ---- 타입 편집: 문 위치
    for zid, (key, inst) in inst_by_zone.items():
        for oname, ov in ((EDITS.get(key) or {}).get('doors') or {}).items():
            zrooms = [r for r in rooms if r['zone'] == zid]
            tids = {r['id'] for r in zrooms if r['name'] == oname or (oname == '*' and r['kind'] == 'zone' and not r.get('sub'))}
            door = next((o for o in ops if o['kind'] == 'door' and o.get('owner') in tids), None)
            zname = zrooms[0]['zname'] if zrooms else zid
            if not door:
                CONFLICTS.append({'id': 'door|%s|%s|%s' % (f['id'], zid, oname), 'kind': 'door', 'floor': f['id'], 'unit': zname, 'typeKey': key,
                                  'msg': '%s: 문 위치를 편집한 ‘%s’ 실의 문을 찾지 못해 자동 위치로 둠' % (zname, '주 공간' if oname == '*' else oname)})
                continue
            cw = wby[door['wall']]; own = door['owner']
            other = lambda w: next((byid.get(x) for x in w['rooms'] if x != own), None)
            cat = lambda q: 'circ' if is_circ(q) else ('in' if q is not None and q['zone'] == zid else 'out')
            want = cat(other(cw))
            px, py = (lambda r: (r['x'], r['y']))(c2f(inst, ov['pos'][0], ov['pos'][0], ov['pos'][1], ov['pos'][1]))
            best = None
            for w in walls:
                if own not in w['rooms'] or None in w['rooms'] or w['len'] < door['w'] + 0.2 or cat(other(w)) != want:
                    continue
                ax, ay = w['a']; bx, by = w['b']; L = w['len']; ux, uy = (bx - ax) / L, (by - ay) / L
                at = min(max((px - ax) * ux + (py - ay) * uy, door['w'] / 2 + 0.05), L - door['w'] / 2 - 0.05)
                qx, qy = ax + ux * at, ay + uy * at; dd = (qx - px) ** 2 + (qy - py) ** 2
                if best is None or dd < best[0]:
                    best = (dd, w, at)
            if not best or best[0] > 1.0:
                CONFLICTS.append({'id': 'door|%s|%s|%s' % (f['id'], zid, oname), 'kind': 'door', 'floor': f['id'], 'unit': zname, 'typeKey': key,
                                  'msg': '%s: ‘%s’ 문을 편집한 위치에 둘 벽이 없어 자동 위치로 둠' % (zname, '주 공간' if oname == '*' else oname)})
                continue
            old_other = other(cw)
            door['wall'] = best[1]['id']; door['at'] = r3(best[2]); door['moved'] = True
            if door.get('swingTo') and old_other is not None and door['swingTo'] == old_other['id']:
                nb = other(best[1]); door['swingTo'] = nb['id'] if nb else None
    # ---- 문 열림 충돌 정리 (복도 끝·맞은편 현관문, 한 실 안에서 겹치는 문). 열림 범위 = 경첩 중심 1/4원
    def quarter(o, at=None, to=None, hinge=None):
        to = o.get('swingTo') if to is None else to
        if o['kind'] != 'door' or o.get('sliding') or '승강기' in o['type'] or not to or to not in byid:
            return None
        w = wby[o['wall']]; ax, ay = w['a']; bx, by = w['b']; L = w['len']; ux, uy = (bx - ax) / L, (by - ay) / L
        at = o['at'] if at is None else at; hinge = hinge or o.get('hinge', 'start'); hw = o['w'] / 2
        cx, cy = ax + ux * at, ay + uy * at
        H = (cx - ux * hw, cy - uy * hw) if hinge == 'start' else (cx + ux * hw, cy + uy * hw)
        d = (ux, uy) if hinge == 'start' else (-ux, -uy)
        R = byid[to]; nx, ny = (-uy, ux)
        if (R['x'] + R['w'] / 2 - cx) * nx + (R['y'] + R['h'] / 2 - cy) * ny < 0:
            nx, ny = -nx, -ny
        return (H, d, (nx, ny), o['w'])
    def q_pts(q):
        (hx, hy), (dx, dy), (nx, ny), r = q
        pts = []
        for i in range(1, 6):
            for j in range(0, 6):
                rr = r * i / 5.0 * 0.98; th = math.pi / 2 * j / 5.0
                pts.append((hx + rr * (math.cos(th) * dx + math.sin(th) * nx), hy + rr * (math.cos(th) * dy + math.sin(th) * ny)))
        return pts
    def q_in(q, p):
        (hx, hy), (dx, dy), (nx, ny), r = q
        vx, vy = p[0] - hx, p[1] - hy
        return vx * vx + vy * vy <= r * r and vx * dx + vy * dy >= -0.02 and vx * nx + vy * ny >= -0.02
    def q_ov(a, b):
        return bool(a and b) and (any(q_in(b, p) for p in q_pts(a)) or any(q_in(a, p) for p in q_pts(b)))
    prio = lambda o: (0 if o['type'].startswith('세대 현관문') else 1 if o.get('fire') or '방화' in o['type'] else 2 if '출입' in o['type'] else 3 if o['type'] == '실내문' else 4)
    doors = [o for o in ops if o['kind'] == 'door']
    def clashes(o, **kw):
        qa = quarter(o, **kw); to = kw.get('to') or o.get('swingTo')
        return [q for q in doors if q is not o and q.get('swingTo') == to and q_ov(qa, quarter(q))]
    hinge_n = fixed_n = slid_n = flip_n = 0
    for _ in range(4):
        changed = False
        for o1 in doors:
            cl = clashes(o1)
            if not cl:
                continue
            o2 = cl[0]
            # 1) 경첩 쪽 바꾸기 (두 문 조합)
            done = False
            for h1, h2 in (('start', 'end'), ('end', 'start'), ('end', 'end'), ('start', 'start')):
                if not q_ov(quarter(o1, hinge=h1), quarter(o2, hinge=h2)):
                    o1['hinge'], o2['hinge'] = h1, h2
                    if not clashes(o1) and not clashes(o2):
                        hinge_n += 1; changed = done = True; break
            if done:
                continue
            o = max((o1, o2), key=lambda q: (q.get('moved', False) is False, prio(q)))   # 사용자가 옮긴 문·중요한 문은 그대로
            other = o2 if o is o1 else o1
            w = wby[o['wall']]; L = w['len']; hw = o['w'] / 2
            on_wall = [(q['at'] - q['w'] / 2, q['at'] + q['w'] / 2) for q in ops if q is not o and q['wall'] == o['wall']]
            # 2) 벽을 따라 옮기기
            best = None
            for k in range(1, int(min(L, 2.4) / 0.05) + 1):
                for sg in (1, -1):
                    at = o['at'] + sg * k * 0.05
                    if at - hw < 0.1 or at + hw > L - 0.1 or any(at + hw > a0 - 0.1 and at - hw < b0 + 0.1 for a0, b0 in on_wall):
                        continue
                    for hh in ('start', 'end'):
                        if not clashes(o, at=at, hinge=hh):
                            best = (at, hh); break
                    if best:
                        break
                if best:
                    break
            if best:
                o['at'] = r3(best[0]); o['hinge'] = best[1]; fixed_n += 1; changed = True; continue
            # 2b) 두 문 모두 폭 0.9m로 줄이고 위치·경첩을 함께 찾기 (복도 끝 모서리)
            if o['w'] > 0.9 or other['w'] > 0.9:
                saved = [(q, q['w'], q['at'], q.get('hinge', 'start')) for q in (o, other)]
                found = False
                for q in (o, other):
                    if q['w'] > 0.9 and not q.get('moved'):
                        q['w'] = 0.9
                def positions(q):
                    wq = wby[q['wall']]; Lq = wq['len']; hq = q['w'] / 2
                    onw = [(x['at'] - x['w'] / 2, x['at'] + x['w'] / 2) for x in ops if x is not q and x['wall'] == q['wall'] and x is not o and x is not other]
                    res = []
                    k = hq + 0.1
                    while k <= Lq - hq - 0.1 + 1e-9:
                        if not any(k + hq > a0 - 0.1 and k - hq < b0 + 0.1 for a0, b0 in onw):
                            res.append(round(k, 3))
                        k += 0.05
                    return res or [q['at']]
                for ao in positions(o):
                    for ho in ('start', 'end'):
                        for at2 in positions(other):
                            for h2 in ('start', 'end'):
                                if not q_ov(quarter(o, at=ao, hinge=ho), quarter(other, at=at2, hinge=h2)):
                                    o['at'], o['hinge'], other['at'], other['hinge'] = ao, ho, at2, h2
                                    if not clashes(o) and not clashes(other):
                                        found = True; break
                            if found: break
                        if found: break
                    if found: break
                if found:
                    fixed_n += 1; changed = True; continue
                for q, w0, a0, h0 in saved:
                    q['w'], q['at'], q['hinge'] = w0, a0, h0
            # 3) 반대쪽(같은 세대 실)으로 열기 — 세대 현관문은 항상 밖으로
            oth = next((x for x in w['rooms'] if x and x != o.get('swingTo')), None)
            if oth and byid[oth]['kind'] == 'unitroom' and not o['type'].startswith('세대 현관문') and not clashes(o, to=oth):
                o['swingTo'] = oth; flip_n += 1; changed = True; continue
            # 4) 미닫이로
            if L >= 2 * o['w'] + 0.2 and not o['type'].startswith('세대 현관문') and not o.get('fire') and '방화' not in o['type']:
                o['sliding'] = True; o['swingTo'] = None; o['type'] = o['type'] + ' → 미닫이'; slid_n += 1; changed = True; continue
        if not changed:
            break
    for o1 in doors:
        cl = clashes(o1)
        if cl and id(o1) < id(cl[0]):
            own = byid.get(o1.get('owner')) or {}
            o2 = cl[0]; own2 = byid.get(o2.get('owner')) or {}
            issues.append({'kind': 'door', 'ref': own.get('unit') or own.get('name'), 'level': 'warn',
                           'msg': '%s %s·%s 현관문 열림 범위가 복도 끝 모서리에서 겹침 → 한쪽 현관을 0.6m 들여 넣거나(현관 포치) 복도 끝을 0.6m 늘리는 조닝 조정 필요' % (
                               f['id'], own.get('unit') or own.get('name'), own2.get('unit') or own2.get('name')) if o1['type'].startswith('세대 현관문') and o2['type'].startswith('세대 현관문')
                           else '%s %s과 %s의 열림 범위가 겹침' % (f['id'], o1['type'], o2['type'])})
    if hinge_n or fixed_n or slid_n or flip_n:
        issues.append({'kind': 'door', 'ref': f['id'], 'level': 'info', 'msg': '%s 문 열림 충돌 정리: 경첩 방향 %d · 위치 이동 %d · 반대로 열기 %d · 미닫이 전환 %d' % (f['id'], hinge_n, fixed_n, flip_n, slid_n)})
    # ---- 창
    win_rule = {'거실': ('거실창 (바닥까지)', 3.6, 0.1, 2.2), '거실·주방': ('거실창 (바닥까지)', 3.0, 0.1, 2.2), '거실·침실': ('거실창 (바닥까지)', 3.0, 0.1, 2.2),
                '침실1': ('침실창', 2.4, 0.9, 1.4), '침실2': ('침실창', 2.4, 0.9, 1.4), '침실3': ('침실창', 2.4, 0.9, 1.4), '다용도실': ('환기창', 0.9, 1.2, 0.9), '욕실2': ('욕실 환기창', 0.6, 1.5, 0.6), '침실(알코브)': ('침실창', 2.0, 0.9, 1.4),
                '주방·식당': ('주방창', 1.2, 1.0, 1.0), '주방': ('주방창', 1.2, 1.0, 1.0), '욕실': ('욕실 환기창', 0.6, 1.5, 0.6)}
    _wr = lambda d: (d['type'], float(d['w']), float(d['sill']), float(d['h']))
    if DET_WIN.get('byRoom'):
        win_rule = {k: _wr(v) for k, v in DET_WIN['byRoom'].items()}
    dark_rule = _wr(DET_WIN['darkLiving']) if DET_WIN.get('darkLiving') else ('거실창 (바닥까지)', 3.6, 0.1, 2.2)
    com_rule = _wr(DET_WIN['common']) if DET_WIN.get('common') else ('창', 1.2, 0.9, 1.4)
    cor_rule = _wr(DET_WIN['corridor']) if DET_WIN.get('corridor') else ('복도창', 1.2, 0.9, 1.4)
    ext_of = lambda r: [w for w in walls if r['id'] in w['rooms'] and None in w['rooms'] and w['kind'] == 'exterior']
    dark_living = {r['unit'] for r in rooms if r['kind'] == 'unitroom' and r['name'] in ('거실', '거실·주방', '거실·침실') and not ext_of(r)}
    for r in rooms:
        ext = ext_of(r)
        if not ext:
            continue
        if r['kind'] == 'unitroom':
            rule = win_rule.get(r['name'])
            if r['name'] == '침실(알코브)' and r['unit'] in dark_living:   # 거실이 알코브를 통해 채광 → 알코브 창을 거실창으로
                rule = dark_rule
            if not rule:
                continue
            typ, cap, sill, hh = rule
            for w in sorted(ext, key=lambda w: -w['len'])[: (2 if r['name'] in HABITABLE else 1)]:
                if w['len'] >= 1.0:
                    op('window', w, min(cap, w['len'] - 0.6), typ, sill=sill, h=hh, unit=r['unit'])
        elif r.get('sub'):
            w = max(ext, key=lambda w: w['len'])
            if w['len'] >= 1.0:
                op('window', w, min(com_rule[1], w['len'] - 0.6), com_rule[0], sill=com_rule[2], h=com_rule[3])
        elif r['ztype'] in ('retail', 'public', 'lobby', 'community'):
            for w in ext:
                if w['len'] < 1.2:
                    continue
                doors = [(o['at'] - o['w'] / 2, o['at'] + o['w'] / 2) for o in ops if o['wall'] == w['id']]
                cuts = [0.2] + [v for a, b in sorted(doors) for v in (a - 0.2, b + 0.2)] + [w['len'] - 0.2]
                for i in range(0, len(cuts), 2):
                    a, b = cuts[i], cuts[i + 1]
                    if b - a >= 1.0:
                        oc[0] += 1
                        ops.append({'id': '%s-n%d' % (f['id'], oc[0]), 'kind': 'window', 'type': '커튼월·쇼윈도' if r['ztype'] in ('retail', 'public', 'lobby') else '창', 'wall': w['id'],
                                    'at': r3((a + b) / 2), 'w': r3(b - a), 'sill': 0.1 if r['ztype'] != 'community' else 0.9, 'h': r3(f['height'] - 0.8 if r['ztype'] != 'community' else 1.5)})
        elif r['ztype'] == 'corridor' and f['level'] >= 1:
            for w in ext:
                if w['len'] >= 1.2:
                    op('window', w, min(cor_rule[1], w['len'] - 0.4), cor_rule[0], sill=cor_rule[2], h=cor_rule[3])
    issues += daylight_check(rooms, walls, ops, units)
    stalls = parking_stalls(f, zones, rooms)
    detail_windows_doors(f, ops, walls)
    issues += detail_stairs(f, stairs, ops, walls, rooms)
    fixtures, fiss = fixtures_for(f, rooms, walls, ops)
    issues += fiss
    return {'rooms': rooms, 'walls': walls, 'openings': ops, 'stairs': stairs, 'elevators': evs, 'units': units, 'issues': issues, 'stalls': stalls, 'fixtures': fixtures}


# ---------------------------------------------------------------- 주차·램프
STALL_W, STALL_D = 2.5, 5.0


def parking_stalls(f, zones, rooms):
    """직각 1열 주차: 구역 긴 변을 따라 배치, 차로에 닿은 쪽이 진입 쪽.
    zone.stallType = '확장형'(2.6×5.2) | 일반형(2.5×5.0), zone.accessible = 장애인(3.3×5.0) 수 — EV홀·보행통로에 가까운 끝부터."""
    aisles = [q for q in zones if q['type'] in ('aisle', 'ramp')]
    walks = [q for q in zones if q['type'] in ('corridor', 'lobby', 'core')]
    out = []
    for z in sorted((q for q in zones if q['type'] == 'parking'), key=lambda q: (q['y'], q['x'])):
        ext = z.get('stallType') == '확장형'
        SW, SD = (2.6, 5.2) if ext else (STALL_W, STALL_D)
        along_u = z['h'] <= z['w']
        L = z['w'] if along_u else z['h']
        D = z['h'] if along_u else z['w']
        if D < SD - 1e-6:
            continue
        acc = max(0, min(int(z.get('accessible') or 0), int(L / 3.3 + 1e-6)))
        widths = [3.3] * acc
        while sum(widths) + SW <= L + 1e-6:
            widths.append(SW)
        sides = ('S', 'N') if along_u else ('W', 'E')
        cont = {sd: sum(h - l for l, h, q in contact_intervals(z, sd, aisles)) for sd in sides}
        entry = max(sides, key=lambda sd: cont[sd])
        lo = (L - sum(widths)) / 2
        if acc:  # 장애인 구획은 보행통로·EV홀에 가까운 끝으로
            c0 = (z['x'], z['y'] + z['h'] / 2) if along_u else (z['x'] + z['w'] / 2, z['y'])
            c1 = (z['x'] + z['w'], z['y'] + z['h'] / 2) if along_u else (z['x'] + z['w'] / 2, z['y'] + z['h'])
            dist = lambda p: min(((q['x'] + q['w'] / 2 - p[0]) ** 2 + (q['y'] + q['h'] / 2 - p[1]) ** 2) for q in walks) if walks else 0
            if dist(c1) < dist(c0):
                widths = widths[::-1]
        room = next((r for r in rooms if r['zone'] == z['id']), None)
        a = lo
        for wd in widths:
            if along_u:
                y0 = z['y'] if entry == 'S' else z['y'] + z['h'] - SD
                r = rect(z['x'] + a, y0, z['x'] + a + wd, y0 + SD)
            else:
                x0 = z['x'] if entry == 'W' else z['x'] + z['w'] - SD
                r = rect(x0, z['y'] + a, x0 + SD, z['y'] + a + wd)
            a += wd
            typ = '장애인 3.3×5.0' if wd > 3 else ('확장형 2.6×5.2' if ext else '일반형 2.5×5.0')
            out.append(dict(r, id='%s-p%d' % (f['id'], len(out) + 1), no='%s-%03d' % (f['id'], len(out) + 1), zone=z['id'], row=z['name'],
                            room=room['id'] if room else None, entry=entry, type=typ))
    return out


def ramp_profiles(zfloors, floors):
    """램프 경사 구간·표고. 층마다 그 층에서 아래층으로 내려가는 램프(down) 또는 위층 램프가 도착하는 자리(arrive)."""
    byid = {f['id']: f for f in floors}
    chain = sorted([f for f in zfloors if f['level'] <= 1], key=lambda f: -f['level'])  # 1F, B1, B2, B3
    down = {}
    for i, zf in enumerate(chain):
        below = chain[i + 1] if i + 1 < len(chain) else None
        for z in zf['zones']:
            if z['type'] != 'ramp':
                continue
            dn = z.get('rampDown', 'N'); sgn = 1 if dn in 'NE' else -1; ax = 'v' if dn in 'NS' else 'u'
            c_lo, c_hi = (z['y'], z['y'] + z['h']) if ax == 'v' else (z['x'], z['x'] + z['w'])
            c0 = c_lo if sgn > 0 else c_hi
            landing = float(z.get('landing') or 0)
            R = {'id': '%s-ramp-%s' % (zf['id'], z['id']), 'zone': z['id'], 'name': z['name'], 'x': z['x'], 'y': z['y'], 'w': z['w'], 'h': z['h'],
                 'dir': dn, 'axis': ax, 'sgn': sgn, 'width': r3(z['w'] if ax == 'v' else z['h'])}
            if below is not None and not z['name'].startswith('램프 도착'):
                run = float(z.get('runLength') or (c_hi - c_lo - landing))
                drop = float(below['height'])
                R.update(mode='down', drop=r3(drop), run=r3(run), sStart=r3(c0 + sgn * landing), sEnd=r3(c0 + sgn * (landing + run)), landing=r3(landing),
                         slopePct=round(drop / run * 100, 1), fromFloor=zf['id'], toFloor=below['id'], relHigh=0.0, relLow=r3(-drop))
                down[zf['id']] = R
            else:
                R.update(mode='arrive', landing=r3(landing))
            byid[zf['id']].setdefault('ramps', []).append(R)
    # 도착 램프 = 위층 down 램프를 이 층 기준 표고로
    for i, zf in enumerate(chain):
        for R in byid[zf['id']].get('ramps', []):
            if R['mode'] != 'arrive' or i == 0:
                continue
            U = down.get(chain[i - 1]['id'])
            if not U:
                continue
            h = float(zf['height'])
            R.update(drop=U['drop'], run=U['run'], sStart=U['sStart'], sEnd=U['sEnd'], slopePct=U['slopePct'], fromFloor=U['fromFloor'], toFloor=zf['id'],
                     relHigh=r3(h), relLow=0.0, upper=U['id'])
    # 위·아래 램프가 겹치는 층: 유효 높이 확인
    rel = lambda R, c: R['relHigh'] + (R['relLow'] - R['relHigh']) * min(1.0, max(0.0, (c - R['sStart']) * R['sgn'] / R['run']))
    for i, zf in enumerate(chain[1:], 1):
        R = down.get(zf['id']); U = down.get(chain[i - 1]['id'])
        if not R or not U or R['axis'] != U['axis']:
            continue
        lo = max(min(R['sStart'], R['sEnd']), min(U['sStart'], U['sEnd'])); hi = min(max(R['sStart'], R['sEnd']), max(U['sStart'], U['sEnd']))
        eu, el_ = byid[chain[i - 1]['id']]['elev'], byid[zf['id']]['elev']
        best = None; c = lo
        while c <= hi + 1e-6:
            hr = (eu + rel(U, c)) - (el_ + rel(R, c)) - 0.3
            if best is None or hr < best[0]:
                best = (hr, c)
            c += 0.5
        if best:
            R['headroom'] = r3(best[0]); R['headroomAt'] = r3(best[1]); R['upper'] = U['id']
    return chain


def elevations(floors):
    out = {}
    for f in floors:
        z = 0.0
        if f['level'] > 0:
            z = sum(g['height'] for g in floors if 0 < g['level'] < f['level'])
        else:
            z = -sum(g['height'] for g in floors if f['level'] <= g['level'] < 0)
        out[f['id']] = r3(z)
    return out


ZT_LABEL = {'retail': '근생', 'public': '공공', 'community': '커뮤니티', 'lobby': '로비', 'mep': '설비'}


def type_list():
    out = []
    order = {'unit': 0, 'core': 1, 'zone': 2}
    uo = {'원룸': 0, '1.5룸': 1, '투룸': 2, '쓰리룸': 3}
    items = sorted(TYPES.values(), key=lambda T: (order[T['cat']], uo.get(T.get('unitType'), 9), T['ztype'], -len(T['instances']), -T['W'] * T['D']))
    seq = {}
    for T in items:
        grp = T.get('unitType') if T['cat'] == 'unit' else ('코어' if T['cat'] == 'core' else ZT_LABEL.get(T['ztype'], T['ztype']))
        seq[grp] = seq.get(grp, 0) + 1
        letter = chr(64 + seq[grp]) if seq[grp] <= 26 else str(seq[grp])
        T['code'] = '%s-%s' % (grp, letter)
        T['group'] = grp
        T['edited'] = T['key'] in EDITS
        T['rooms'] = EDITS[T['key']]['rooms'] if T['edited'] else T['gen']
        if T['edited']:
            EDITS[T['key']]['code'] = T['code']
            if EDITS[T['key']].get('doors'):
                T['doors'] = EDITS[T['key']]['doors']
        if T['edited']:
            T['editedAt'] = EDITS[T['key']].get('at')
        else:
            T.pop('gen')
        out.append(T)
    for k in EDITS:
        if k not in TYPES:
            print('주의: 편집된 타입 %s 은 현재 조닝에 없음 (보관만 함)' % k)
    return out


def fire_summary(floors):
    F = [fl for fl in floors if fl.get('fire')]
    eg = [fl['fire']['egress'] for fl in F if fl['fire']['egress'] and fl['fire']['egress']['max'] is not None]
    worst = max(eg, key=lambda e: e['max'] / e['limit']) if eg else None
    return {'compartments': sum(fl['fire']['summary']['compartments'] for fl in F), 'compOver': sum(fl['fire']['summary']['compOver'] for fl in F),
            'sprinklers': sum(fl['fire']['summary']['sprinklers'] for fl in F), 'hydrants': sum(fl['fire']['summary']['hydrants'] for fl in F),
            'doorChanges': sum(fl['fire']['summary']['doorChanges'] for fl in F),
            'maxTravel': worst and worst['max'], 'travelLimit': worst and worst['limit'],
            'unitLinks': sum(fl['fire']['summary'].get('unitLinks', 0) for fl in F), 'unitLinkList': [d['unit'] for fl in F for d in fl['fire'].get('unitLinks', [])],
            'corridorBad': sum(fl['fire']['summary'].get('corridorBad', 0) for fl in F), 'corridorMin': min([c['eff'] for fl in F for c in fl['fire'].get('corridors', [])] or [None]),
            'stairNarrow': sum(fl['fire']['summary'].get('stairNarrow', 0) for fl in F), 'stairEffMin': min([x['eff'] for fl in F for x in fl['fire'].get('stairWidths', [])] or [None]),
            'swingBad': sum(fl['fire']['summary'].get('swingBad', 0) for fl in F),
            'warn': sum(1 for fl in floors for i in fl['issues'] if i.get('discipline') == 'fire' and i.get('level') == 'warn')}


def main():
    zdoc = load(ZFILE)
    old = load(PFILE) if os.path.exists(PFILE) else {}
    EDITS.clear(); EDITS.update(old.get('typeEdits') or {})
    TYPES.clear(); CONFLICTS.clear()
    old_unit_type = {(f['id'], u['zoneId']): (u.get('type'), u['name']) for f in old.get('floors', []) for u in f.get('units', [])}
    el = elevations(zdoc['floors'])
    floors, allissues = [], []
    for f in sorted(zdoc['floors'], key=lambda g: g['level']):
        b = build_floor(f, el[f['id']])
        fl = {'id': f['id'], 'name': f['name'], 'level': f['level'], 'height': f['height'], 'elev': el[f['id']], 'planType': f.get('planType'), 'outline': f['outline']}
        fl.update(b)
        floors.append(fl)
    ramp_profiles(zdoc['floors'], floors)
    for fl in floors:
        for R in fl.get('ramps', []):
            if R.get('slopePct') is not None:
                lvl = 'warn' if R['slopePct'] > 17 else 'info'
                fl['issues'].append({'kind': 'ramp', 'ref': R['name'], 'level': lvl, 'msg': '%s 램프 %s: 경사 %.1f%% (직선램프 17%% 이하%s), 길이 %.1fm·높이차 %.2fm%s' % (
                    fl['id'], '하강' if R['mode'] == 'down' else '도착', R['slopePct'], ' ✓' if lvl == 'info' else ' 초과', R['run'], R['drop'],
                    (' · 위 램프 아래 유효높이 최소 %.2fm' % R['headroom']) if R.get('headroom') is not None else '')})
                if R['mode'] == 'down':
                    R['curb'] = {'width': 0.3, 'height': 0.15}
                    eff = R['width'] - 0.6
                    fl['issues'].append({'kind': 'ramp', 'ref': R['name'], 'level': 'warn' if eff < 6.0 else 'info',
                                         'msg': '%s 램프 연석(높이 15cm·너비 30cm) 양측 → 유효 차로 %.1fm. 직선 2차선 6.0m를 연석 안쪽으로 확보하려면 램프 구역 폭 %.1fm 필요 (조닝 검토)' % (fl['id'], eff, 6.0 + 0.6) if eff < 6.0 else '%s 램프 유효 차로 %.1fm ✓' % (fl['id'], eff)})
                if R.get('headroom') is not None and R['headroom'] < 2.3:
                    fl['issues'].append({'kind': 'ramp', 'ref': R['name'], 'level': 'warn', 'msg': '%s 램프 위 유효높이 %.2fm < 2.3m (주차장법)' % (fl['id'], R['headroom'])})
        if fl.get('stalls'):
            n = len(fl['stalls']); ne = sum(1 for p in fl['stalls'] if p['type'].startswith('확장형')); na = sum(1 for p in fl['stalls'] if p['type'].startswith('장애인'))
            fl['issues'].append({'kind': 'parking', 'ref': fl['id'], 'level': 'info', 'msg': '%s 주차 %d대 — 일반형 %d · 확장형 %d · 장애인 %d' % (fl['id'], n, n - ne - na, ne, na)})
    allst = [p for fl in floors for p in fl.get('stalls', [])]
    if allst:
        n = len(allst); ne = sum(1 for p in allst if p['type'].startswith('확장형')); na = sum(1 for p in allst if p['type'].startswith('장애인'))
        b1 = next((fl for fl in floors if fl['id'] == 'B1'), floors[0])
        b1['issues'].append({'kind': 'parking', 'ref': '주차 합계', 'level': 'info' if ne >= 0.3 * n else 'warn',
                             'msg': '주차 합계 %d대: 확장형 %d대(%.0f%%, 30%% 이상 %s) · 장애인 %d대(%.1f%%, 서울시 조례 3%% 가정 %s)' % (
                                 n, ne, ne / n * 100, '✓' if ne >= 0.3 * n else '부족', na, na / n * 100, '✓' if na >= -(-3 * n // 100) else '부족')})
        if na < -(-3 * n // 100):
            b1['issues'][-1]['level'] = 'warn'
    # ---------- 분야별 해석 (같은 모델 위에서) — 소방·피난
    DIN = old.get('disciplineInputs') or {}
    n_above = max(fl['level'] for fl in floors)
    fire_params = None
    for fl in floors:
        fo, fi, fire_params = fire_model.analyze(fl, DIN.get('fire'), n_above, n_above)
        fl['fire'] = fo
        fl['issues'] += fi
    # 설비 → 구조 순서 (구조가 설비의 천장 배관 공간을 읽음)
    mi, mep_params, mep_bld = mep_model.analyze_building(floors, DIN.get('mep'), fire_params)
    si, struct_params, struct_bld = struct_model.analyze_building(floors, DIN.get('struct'))
    for fl in floors:
        fl['issues'] += mi.get(fl['id'], []) + si.get(fl['id'], [])
        for i in fl['issues']:
            i.setdefault('discipline', 'arch')
    for bld in (mep_bld, struct_bld):
        bld['summary']['warn'] = 0
    mep_bld['summary']['warn'] = sum(1 for fl in floors for i in fl['issues'] if i.get('discipline') == 'mep' and i.get('level') == 'warn')
    struct_bld['summary']['warn'] = sum(1 for fl in floors for i in fl['issues'] if i.get('discipline') == 'struct' and i.get('level') == 'warn')
    allissues = [dict(i, floor=fl['id']) for fl in floors for i in fl['issues']]
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    tl = type_list()
    code = {T['key']: T['code'] for T in tl}
    old_code = {T['key']: T.get('code') for T in old.get('types', [])}
    ecode = lambda k: (EDITS.get(k) or {}).get('code') or old_code.get(k) or k
    # 1) 편집한 타입을 쓰던 세대가 조닝 변경으로 다른 타입이 됨
    for fl in floors:
        for u in fl['units']:
            ok, oname = old_unit_type.get((fl['id'], u['zoneId']), (None, None))
            if ok and ok != u.get('type') and ok in EDITS:
                CONFLICTS.append({'id': 'unit|%s|%s|%s' % (fl['id'], u['zoneId'], ok), 'kind': 'unit', 'floor': fl['id'], 'unit': u['name'], 'typeKey': u.get('type'), 'editedKey': ok,
                                  'msg': '%s: 조닝 변경으로 평면 타입이 ‘%s’ → ‘%s’로 바뀌어 편집한 내용이 적용되지 않음' % (u['name'], ecode(ok), code.get(u.get('type'), '?'))})
    # 지난번 충돌 중 아직 유효한 것 유지
    cur_ids = {c['id'] for c in CONFLICTS}
    unit_now = {(fl['id'], u['zoneId']): u.get('type') for fl in floors for u in fl['units']}
    for c in old.get('conflicts', []):
        if c['id'] in cur_ids or c['kind'] != 'unit':
            continue
        zid = c['id'].split('|')[2]
        if c.get('editedKey') in EDITS and unit_now.get((c['floor'], zid)) not in (None, c.get('editedKey')):
            CONFLICTS.append(c)
    # 2) 편집한 타입이 더 이상 쓰이지 않음
    for k in EDITS:
        if k not in TYPES:
            CONFLICTS.append({'id': 'orphan|%s' % k, 'kind': 'orphan', 'editedKey': k, 'msg': '편집한 타입 ‘%s’을 쓰는 곳이 조닝 변경으로 없어짐 (편집 내용은 보관 중)' % ecode(k)})
    # 3) 열린 수정 요청의 대상 타입이 사라짐
    for c in old.get('comments', []):
        if c.get('status') == 'done':
            continue
        gone = sorted({t.get('typeCode') or t.get('typeKey') for t in c.get('targets', []) if t.get('typeKey') and t['typeKey'] not in TYPES})
        if gone:
            CONFLICTS.append({'id': 'comment|%s|%s' % (c.get('id'), ','.join(gone)), 'kind': 'comment', 'comment': c.get('id'),
                              'msg': '요청 #%s의 대상 타입 %s이 조닝 변경으로 사라짐' % (c.get('n'), ', '.join(gone))})
    ack = set(old.get('conflictAck') or [])
    seen, conflicts = set(), []
    for c in CONFLICTS:
        if c['id'] in seen:
            continue
        seen.add(c['id']); c = dict(c, acked=c['id'] in ack, at=c.get('at') or now); conflicts.append(c)
    for c in conflicts:
        if c.get('floor') and not c['acked']:
            fl = next((x for x in floors if x['id'] == c['floor']), None)
            if fl:
                fl['issues'].append({'kind': 'conflict', 'ref': c.get('unit'), 'level': 'conflict', 'msg': c['msg'], 'cid': c['id']})
                allissues.append({'kind': 'conflict', 'ref': c.get('unit'), 'level': 'conflict', 'msg': c['msg'], 'cid': c['id'], 'floor': c['floor']})
                u = next((u for u in fl['units'] if u['name'] == c.get('unit')), None)
                if u is not None:
                    u['issues'] = u['issues'] + ['⚡ ' + c['msg'].split(': ', 1)[-1]]
    doc = {'schema': SCHEMA, 'generated': now, 'source': {'zoning': os.path.relpath(ZFILE, ROOT), 'zoningUpdated': zdoc.get('updated')},
           'project': zdoc['project'], 'frame': zdoc['frame'], 'site': zdoc['site'], 'context': zdoc.get('context', {}),
           'wallTypes': {k: dict({'t': v[0], 'label': v[1]}, **WALL_LAYERS.get(k, {})) for k, v in WALL.items()},
           'details': {'walls': 'details/walls.json', 'doors': 'details/doors.json', 'windows': 'details/windows.json'},
           'contextFile': (lambda p: os.path.relpath(p, ROOT) if os.path.exists(p) else None)(os.path.join(ROOT, 'site', os.path.basename(PFILE).replace('_plan.json', '_context.json'))),
           'floors': floors, 'issues': allissues, 'types': tl, 'typeEdits': EDITS, 'conflicts': conflicts, 'conflictAck': sorted(ack & {c['id'] for c in conflicts}),
           'disciplineInputs': DIN, 'disciplines': {'fire': {'params': fire_params, 'summary': fire_summary(floors)},
                           'mep': dict(mep_bld, params=mep_params), 'struct': dict(struct_bld, params=struct_params)},
           'comments': old.get('comments', []), 'updated': now}
    # 공사비 (모든 분야 물량 × 단가) — 마지막 단계
    cost, ci = cost_model.analyze(doc, floors, DIN.get('cost'))
    doc['disciplines']['cost'] = cost
    for i in ci:
        i['floor'] = floors[0]['id'] if floors else ''
        doc['issues'].append(i)
    os.makedirs(os.path.dirname(PFILE), exist_ok=True)
    tmp = PFILE + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as fo:
        json.dump(doc, fo, ensure_ascii=False, separators=(',', ':'))
    os.replace(tmp, PFILE)
    n = lambda k: sum(len(f[k]) for f in floors)
    print('floors %d · rooms %d · walls %d · openings %d (doors %d, windows %d) · stairs %d · elevators %d · issues %d' % (
        len(floors), n('rooms'), n('walls'), n('openings'), sum(1 for f in floors for o in f['openings'] if o['kind'] == 'door'),
        sum(1 for f in floors for o in f['openings'] if o['kind'] == 'window'), n('stairs'), n('elevators'), len(allissues)))
    print('types %d (edited %d) · conflicts %d (new %d)' % (len(TYPES), sum(1 for k in EDITS if k in TYPES), len(conflicts), sum(1 for c in conflicts if not c['acked'])))
    print('saved', os.path.relpath(PFILE, ROOT), '%.0f KB' % (os.path.getsize(PFILE) / 1024))


if __name__ == '__main__':
    main()
