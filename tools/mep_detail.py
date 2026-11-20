"""설비 상세 — mep_model 다음에 같은 plan.json 위에서 실제 배관·덕트·전기 경로와 장비를 만듭니다.

좌표: x,y = 층 평면(u,v), z = 그 층 바닥(FL)에서의 높이(m). 3D는 floors[].elev + z.
- 세대: 위생기구(가구 데이터) → 세대 PS(입상관)까지 오수·배수(층상, 기울기), 급수·급탕(분배기·보일러), 가스, 전열교환기 급·배기 덕트,
        욕실 배기(→ 세대 PS 공용배기), 주방 후드(→ 외벽), 세대 분전반·조명
- 층: 복도 천장의 전력·통신 트레이, 급수·가스 주관, 세대 분기, 공용 조명, 코어 샤프트 입상관(구경 계산)
- 지하: 주차장 유인팬·배기팬·조명, 기계·전기실 장비 배치(저수조·펌프·수변전·발전기), 장비실 → 코어 주관
- 계산: 기구배수부하단위(DFU)·급수부하단위(WSFU, 헌터 곡선 근사)로 입상관·주관 구경, 부스터·소화펌프 양정·동력,
        팬 풍량·동력, 변압기(전등·동력 분리)·발전기 용량, 간선 전류·전선 굵기·전압강하
결과: floors[].mep.{pipes, ducts, trays, lights, devices, risers}, disciplines.mep.detail
"""
import math
import fire_piping
from project_conf import SLUG, ROAD, CONF

r2 = lambda v: round(v + 0.0, 2)
PIPE_D = [15, 20, 25, 32, 40, 50, 65, 80, 100, 125, 150, 200]
DFU = {'toilet': 4, 'basin': 1, 'shower': 2, 'sink': 2, 'washer': 3}
WSFU = {'toilet': 2.2, 'basin': 0.7, 'shower': 1.4, 'sink': 1.4, 'washer': 1.4}
STACK_TBL = [(50, 24), (65, 42), (75, 60), (100, 500), (125, 1100), (150, 1900), (200, 3600)]   # 입상 배수관 허용 DFU
HORIZ_TBL = [(75, 20), (100, 180), (125, 390), (150, 700), (200, 1600)]                     # 1/100 횡주관 허용 DFU
CABLE = [(6, 47), (10, 65), (16, 87), (25, 114), (35, 141), (50, 170), (70, 216), (95, 263), (120, 304), (150, 350), (185, 400), (240, 472), (300, 530)]
STD_TR = [300, 500, 750, 1000, 1250, 1500, 2000]
STD_GEN = [100, 150, 200, 250, 300, 400, 500, 600, 750, 1000]


def hunter(F):          # 헌터 곡선(세정탱크식) 근사: L/s
    return 0.104 * F ** 0.71 if F > 0 else 0


def pipe_for(q_ls, vmax=2.0):
    for d in PIPE_D:
        a = math.pi * (d / 1000) ** 2 / 4
        if q_ls / 1000 / a <= vmax:
            return d
    return PIPE_D[-1]


def from_tbl(tbl, v):
    for d, cap in tbl:
        if v <= cap:
            return d
    return tbl[-1][0]


def cable_for(I):
    for a, cap in CABLE:
        if I <= cap * 0.8:
            return a
    return CABLE[-1][0]


def route(a, b, z0, z1=None, first='x'):
    """맨해튼 경로 (점 목록 [x,y,z]). z0→z1 선형 (배수 기울기)"""
    z1 = z0 if z1 is None else z1
    mid = (b[0], a[1]) if first == 'x' else (a[0], b[1])
    L1 = abs(mid[0] - a[0]) + abs(mid[1] - a[1]); L2 = abs(b[0] - mid[0]) + abs(b[1] - mid[1]); L = (L1 + L2) or 1
    pts = [[r2(a[0]), r2(a[1]), r2(z0)], [r2(mid[0]), r2(mid[1]), r2(z0 + (z1 - z0) * L1 / L)], [r2(b[0]), r2(b[1]), r2(z1)]]
    out = [pts[0]]
    for p in pts[1:]:
        if abs(p[0] - out[-1][0]) + abs(p[1] - out[-1][1]) > 0.01:
            out.append(p)
    return out


def plen(pts):
    return sum(abs(b[0] - a[0]) + abs(b[1] - a[1]) + abs(b[2] - a[2]) for a, b in zip(pts, pts[1:]))


def ctr(r):
    return (r['x'] + r['w'] / 2, r['y'] + r['h'] / 2)


def seg_near(p, segs):
    """가장 가까운 선분 위 점"""
    best = None
    for s in segs:
        (ax, ay), (bx, by) = s['a'], s['b']
        dx, dy = bx - ax, by - ay; L2 = dx * dx + dy * dy or 1
        t = max(0, min(1, ((p[0] - ax) * dx + (p[1] - ay) * dy) / L2))
        q = (ax + t * dx, ay + t * dy); d = math.hypot(p[0] - q[0], p[1] - q[1])
        if best is None or d < best[0]:
            best = (d, q, s)
    return best


def wall_pt(w, at):
    L = w['len'] or 1
    return (w['a'][0] + (w['b'][0] - w['a'][0]) / L * at, w['a'][1] + (w['b'][1] - w['a'][1]) / L * at)


def ext_point(p, rooms_ids, walls, inset=0.15):
    """세대 실들의 외벽 중 p에 가장 가까운 점 (안쪽으로 inset)"""
    best = None
    for w in walls:
        if None not in w['rooms']:
            continue
        rid = next(x for x in w['rooms'] if x)
        if rid not in rooms_ids:
            continue
        (ax, ay), (bx, by) = w['a'], w['b']
        dx, dy = bx - ax, by - ay; L2 = dx * dx + dy * dy or 1
        t = max(0.1, min(0.9, ((p[0] - ax) * dx + (p[1] - ay) * dy) / L2))
        q = (ax + t * dx, ay + t * dy); d = abs(p[0] - q[0]) + abs(p[1] - q[1])
        if best is None or d < best[0]:
            best = (d, q, w)
    if not best:
        return None
    q, w = best[1], best[2]
    # 안쪽 방향: 벽에 수직, p 쪽
    nx, ny = (0, 1) if abs(w['a'][1] - w['b'][1]) < 1e-6 else (1, 0)
    s = 1 if (p[0] - q[0]) * nx + (p[1] - q[1]) * ny >= 0 else -1
    return (q[0] + nx * s * inset, q[1] + ny * s * inset, best[0])


def centerlines(rects):
    out = []
    for r in rects:
        if r['w'] >= r['h']:
            out.append({'a': (r['x'], r['y'] + r['h'] / 2), 'b': (r['x'] + r['w'], r['y'] + r['h'] / 2), 'room': r['id'], 'axis': 'h'})
        else:
            out.append({'a': (r['x'] + r['w'] / 2, r['y']), 'b': (r['x'] + r['w'] / 2, r['y'] + r['h']), 'room': r['id'], 'axis': 'v'})
    return out


def offset(s, o):
    (ax, ay), (bx, by) = s['a'], s['b']
    if s['axis'] == 'h':
        return [(ax, ay + o), (bx, by + o)]
    return [(ax + o, ay), (bx + o, by)]


def pack(room, items, aisle=1.0, margin=0.6):
    """장비를 실 안에 줄 단위로 배치 → [{...item, x,y}] (넘치면 over=True)"""
    x0, y0 = room['x'] + margin, room['y'] + margin
    X1, Y1 = room['x'] + room['w'] - margin, room['y'] + room['h'] - margin
    horiz = room['w'] >= room['h']
    out = []; cx, cy, row = x0, y0, 0.0
    for it in items:
        w, h = (it['w'], it['h']) if horiz else (it['h'], it['w'])
        if horiz:
            if cx + w > X1 and cx > x0:
                cx = x0; cy += row + aisle; row = 0
            out.append(dict(it, x=r2(cx), y=r2(cy), w=r2(w), h=r2(h), over=cy + h > Y1 + 1e-6))
            cx += w + 0.6; row = max(row, h)
        else:
            if cy + h > Y1 and cy > y0:
                cy = y0; cx += row + aisle; row = 0
            out.append(dict(it, x=r2(cx), y=r2(cy), w=r2(w), h=r2(h), over=cx + w > X1 + 1e-6))
            cy += h + 0.6; row = max(row, w)
    return out


def build(floors, P, bld):
    order = sorted(floors, key=lambda f: f['level'])
    loads = bld['loads']
    top_res = max((f for f in floors if f['units']), key=lambda f: f['level'], default=None)
    issues = {f['id']: [] for f in floors}
    def add(fl, level, msg, ref=None, aff=None):
        i = {'kind': 'mep', 'ref': ref or fl['id'], 'level': level, 'discipline': 'mep', 'msg': msg}
        if aff:
            i['affects'] = aff
        issues[fl['id']].append(i)
    unit_stats = []
    # ================= 층별
    for fl in floors:
        m = fl['mep']; H = fl['height']
        m.update({'pipes': [], 'ducts': [], 'trays': [], 'lights': [], 'devices': [], 'risers': []})
        byid = {r['id']: r for r in fl['rooms']}
        wby = {w['id']: w for w in fl['walls']}
        ceil = H - (0.35 if fl['units'] else 0.6)                # 공용부 천장 속 주관 높이
        uceil = 2.45                                              # 세대 천장 속 (천장고 2.3)
        fx = {}
        for f in fl.get('fixtures', []):
            fx.setdefault(f['room'], []).append(f)
        stacks = {s['unit']: s for s in m['stacks']}
        circ = [r for r in fl['rooms'] if r['ztype'] in ('corridor', 'lobby')]
        cls = centerlines(circ)
        # ---- 공용 트레이·주관·조명
        for s in cls:
            for sysk, o, wdt, z in (('power', 0.35, 0.3, ceil + 0.05), ('comm', -0.35, 0.2, ceil + 0.05)):
                a, b = offset(s, o)
                m['trays'].append({'sys': sysk, 'w': wdt, 'pts': [[r2(a[0]), r2(a[1]), r2(z)], [r2(b[0]), r2(b[1]), r2(z)]]})
            if fl['units']:
                for sysk, o, d, z in (('cw', 0.0, 50, ceil - 0.15), ('gas', 0.6, 32, ceil - 0.1)):
                    a, b = offset(s, o)
                    m['pipes'].append({'sys': sysk, 'd': d, 'role': 'main', 'pts': [[r2(a[0]), r2(a[1]), r2(z)], [r2(b[0]), r2(b[1]), r2(z)]]})
            L = math.hypot(s['b'][0] - s['a'][0], s['b'][1] - s['a'][1]); n = max(1, int(L / 3.0))
            for k in range(n):
                t = (k + 0.5) / n
                m['lights'].append({'x': r2(s['a'][0] + (s['b'][0] - s['a'][0]) * t), 'y': r2(s['a'][1] + (s['b'][1] - s['a'][1]) * t), 'z': r2(H - 0.4 if not fl['units'] else 2.4),
                                    'kind': 'LED 다운라이트 15W', 'emerg': k % 3 == 0})
        # ---- 코어 샤프트 입상관 배치
        for sh in m['shafts']:
            r = byid.get(sh['room']); systems = []
            if not r:
                continue
            if 'plumb' in sh['systems']:
                systems += [('cw', 'main'), ('fire', 'main'), ('fire2', 'main'), ('gas', 'main'), ('sw', 'main')]
            if 'duct' in sh['systems']:
                systems += [('press', 'duct')]
            if 'elec' in sh['systems']:
                systems += [('feeder', 'cable'), ('comm', 'cable')]
            for k, (sk, role) in enumerate(systems):
                cols = max(1, int(r['w'] / 0.45)); i, j = k % cols, k // cols
                m['risers'].append({'sys': sk, 'shaft': sh['name'], 'x': r2(r['x'] + 0.3 + i * 0.45), 'y': r2(r['y'] + 0.3 + j * 0.45), 'role': role})
        # 주관 ← 코어 PS·AD 연결
        psa = next((byid[s['room']] for s in m['shafts'] if 'plumb' in s['systems'] and 'duct' in s['systems']), None) or next((byid[s['room']] for s in m['shafts'] if 'plumb' in s['systems']), None)
        eps = next((byid[s['room']] for s in m['shafts'] if 'elec' in s['systems']), None)
        if cls and psa and fl['units']:
            c = ctr(psa); nb = seg_near(c, cls)
            m['pipes'].append({'sys': 'cw', 'd': 50, 'role': 'main', 'pts': route(c, nb[1], ceil - 0.15)})
            m['pipes'].append({'sys': 'gas', 'd': 32, 'role': 'main', 'pts': route(c, nb[1], ceil - 0.1, first='y')})
        if cls and eps:
            c = ctr(eps); nb = seg_near(c, cls)
            m['trays'].append({'sys': 'power', 'w': 0.3, 'pts': route(c, nb[1], ceil + 0.05)})
            m['devices'].append({'kind': 'panel', 'name': '%s 층 분전반 (EPS)' % fl['id'], 'x': r2(eps['x'] + 0.2), 'y': r2(eps['y'] + 0.2), 'w': 0.8, 'h': 0.3, 'z0': 0.3, 'z1': 2.1})
        # ---- 소화 배관 (스프링클러·호스릴 소화전·연결송수관·비상콘센트)
        FP = dict(bld.get('fire') or {})
        for k, v in (('sprRadiusUnit', 2.6), ('sprRadiusOther', 2.3), ('sprRadiusParking', 2.1)):
            FP.setdefault(k, v)
        if fl.get('fire'):
            hs, sp_pipes, sp_dev, sp_st = fire_piping.build(fl, FP, {'risers': m['risers'], 'cls': cls, 'ceil': ceil, 'fireOut': fl['fire']})
            m['heads'] = hs; m['pipes'] += sp_pipes; m['devices'] += sp_dev; m['sp'] = sp_st
            fl['fire']['sprinklers'] = [[h[0], h[1]] for h in hs]; fl['fire']['summary']['sprinklers'] = len(hs)
            if sp_st['maxPerBranch'] > 8:
                add(fl, 'warn', '%s 스프링클러 한쪽 가지배관 헤드 %d개 > 8개 (NFTC 103)' % (fl['id'], sp_st['maxPerBranch']), fl['id'])
        # ---- 세대
        for u in fl['units']:
            urs = [byid[i] for i in u['rooms'] if i in byid]; ids = set(u['rooms'])
            st = stacks.get(u['name'])
            door = next((o for o in fl['openings'] if o['kind'] == 'door' and o.get('unit') == u['name'] and o['type'].startswith('세대 현관문')), None)
            if not st or not door:
                continue
            sc = (st['x'] + st['w'] / 2, st['y'] + st['h'] / 2)
            dw = wby[door['wall']]; dp0 = wall_pt(dw, door['at'])
            ent = byid.get(door['owner']) or urs[0]
            ec = ctr(ent)
            dp = (dp0[0] + (0.4 if ec[0] > dp0[0] + 0.05 else -0.4 if ec[0] < dp0[0] - 0.05 else 0), dp0[1] + (0.4 if ec[1] > dp0[1] + 0.05 else -0.4 if ec[1] < dp0[1] - 0.05 else 0))
            items = []
            for r in urs:
                for f in fx.get(r['id'], []):
                    c = (f['x'] + f['w'] / 2, f['y'] + f['h'] / 2)
                    if f['kind'] == 'counter':
                        items.append(('sink', c, r))
                        if f.get('washer'):
                            items.append(('washer', (f['x'] + min(f['w'], 0.6) / 2, f['y'] + min(f['h'], 0.6) / 2), r))
                    elif f['kind'] in DFU:
                        items.append((f['kind'], c, r))
            dfu = sum(DFU[k] for k, _, _ in items); wsfu = sum(WSFU[k] for k, _, _ in items)
            # 오수·배수 (층상, 2%)
            for k, c, r in items:
                sysk = 'sw' if k == 'toilet' else 'dw'
                d = 100 if k == 'toilet' else 50
                Lr = abs(c[0] - sc[0]) + abs(c[1] - sc[1])
                m['pipes'].append({'sys': sysk, 'd': d, 'unit': u['name'], 'fix': k, 'pts': route(c, sc, -0.08, -0.08 - Lr * P['drainSlope'], first='y' if k == 'toilet' else 'x')})
            # 분배기·보일러·계량기
            mani = (sc[0] + (0.5 if sc[0] < ec[0] else -0.5), sc[1])
            boiler_room = next((r for r in urs if '다용도' in r['name']), None) or next((r for _, _, r in items if r and '주방' in r['name']), None) or ent
            bx = ext_point(ctr(boiler_room), {boiler_room['id']}, fl['walls'], 0.3) or ext_point(ctr(boiler_room), ids, fl['walls'], 0.3)
            bpos = (bx[0], bx[1]) if bx and bx[2] < 6 else ctr(boiler_room)
            m['devices'].append({'kind': 'boiler', 'name': '%s 가스 콘덴싱 보일러 (난방·급탕)' % u['name'], 'unit': u['name'], 'x': r2(bpos[0] - 0.22), 'y': r2(bpos[1] - 0.17), 'w': 0.44, 'h': 0.34, 'z0': 1.2, 'z1': 1.95})
            m['devices'].append({'kind': 'manifold', 'name': '%s 급수 분배기·계량기' % u['name'], 'unit': u['name'], 'x': r2(mani[0] - 0.2), 'y': r2(mani[1] - 0.1), 'w': 0.4, 'h': 0.2, 'z0': 0.1, 'z1': 0.5})
            m['pipes'].append({'sys': 'cw', 'd': 20, 'unit': u['name'], 'role': 'inlet', 'pts': route(dp, mani, 0.05)})
            for k, c, r in items:
                m['pipes'].append({'sys': 'cw', 'd': 15, 'unit': u['name'], 'fix': k, 'pts': route((mani[0], mani[1] + 0.12), (c[0] + 0.12, c[1] + 0.12), 0.05)})
                if k in ('basin', 'shower', 'sink'):
                    m['pipes'].append({'sys': 'hw', 'd': 15, 'unit': u['name'], 'fix': k, 'pts': route(bpos, (c[0] - 0.12, c[1] - 0.12), 0.08, first='y')})
            m['pipes'].append({'sys': 'cw', 'd': 15, 'unit': u['name'], 'fix': 'boiler', 'pts': route(mani, bpos, 0.05, first='y')})
            # 가스: 복도 주관 → 현관 → 보일러 (천장)
            m['pipes'].append({'sys': 'gas', 'd': 20, 'unit': u['name'], 'pts': route(dp, bpos, uceil, first='x')})
            # 연도
            if bx:
                m['ducts'].append({'sys': 'flue', 'd': 80, 'unit': u['name'], 'pts': [[r2(bpos[0]), r2(bpos[1]), 2.0], [r2(bx[0]), r2(bx[1]), 2.0]]})
            # 전열교환기 (현관 천장) → 거실·침실 급기, 거실 환기구 환기, 외벽 OA/EA
            hrv = ec
            q = r2(u['area'] * P['ceilingH'] * P['unitAch'])
            cap = 100 if q <= 100 else 150 if q <= 150 else 250
            m['devices'].append({'kind': 'hrv', 'name': '%s 전열교환기 %d㎥/h (필요 %.0f)' % (u['name'], cap, q), 'unit': u['name'], 'x': r2(hrv[0] - 0.4), 'y': r2(hrv[1] - 0.3), 'w': 0.8, 'h': 0.6, 'z0': 2.32, 'z1': 2.6, 'q': q, 'cap': cap})
            living = [r for r in urs if r['name'].startswith('거실') or r['name'].startswith('침실')]
            for r in living:
                m['ducts'].append({'sys': 'sa', 'd': 100, 'unit': u['name'], 'pts': route(hrv, ctr(r), uceil)})
            lv = next((r for r in urs if r['name'].startswith('거실')), None)
            if lv:
                c = ctr(lv); m['ducts'].append({'sys': 'ra', 'd': 100, 'unit': u['name'], 'pts': route(hrv, (c[0] + 0.5, c[1] + 0.5), uceil + 0.08, first='y')})
            ex = ext_point(hrv, ids, fl['walls'])
            if ex:
                m['ducts'].append({'sys': 'oa', 'd': 100, 'unit': u['name'], 'pts': route(hrv, (ex[0], ex[1]), uceil)})
                m['ducts'].append({'sys': 'ea', 'd': 100, 'unit': u['name'], 'pts': route((hrv[0] + 0.2, hrv[1] + 0.2), (ex[0] + 0.25, ex[1] + 0.25), uceil + 0.08)})
            # 욕실 배기 → 세대 PS 공용배기
            bath = next((r for r in urs if '욕실' in r['name']), None)
            if bath:
                m['ducts'].append({'sys': 'exh', 'd': 100, 'unit': u['name'], 'pts': route(ctr(bath), (st['x'] + st['w'] - 0.12, st['y'] + 0.12), uceil)})
            # 주방 후드 → 외벽
            sink = next((c for k, c, r in items if k == 'sink'), None)
            hood_len = None
            if sink:
                ex2 = ext_point(sink, ids, fl['walls'])
                if ex2:
                    pts = route(sink, (ex2[0], ex2[1]), uceil - 0.05)
                    hood_len = plen(pts)
                    m['ducts'].append({'sys': 'hood', 'd': 150, 'unit': u['name'], 'pts': pts})
            # 전기: 세대 분전반(현관 문 옆) + 조명 + 트레이에서 간선
            pnl = (dp[0] + (0.6 if abs(dw['a'][1] - dw['b'][1]) < 1e-6 else 0), dp[1] + (0.6 if abs(dw['a'][0] - dw['b'][0]) < 1e-6 else 0))
            m['devices'].append({'kind': 'upanel', 'name': '%s 세대 분전반·통신단자함' % u['name'], 'unit': u['name'], 'x': r2(pnl[0] - 0.2), 'y': r2(pnl[1] - 0.1), 'w': 0.4, 'h': 0.15, 'z0': 1.5, 'z1': 2.0})
            feed_len = None
            if cls:
                nb = seg_near(dp, cls)
                pts = route(nb[1], dp, ceil + 0.05) + [[r2(pnl[0]), r2(pnl[1]), 2.0]]
                m['trays'].append({'sys': 'unitfeed', 'w': 0.05, 'unit': u['name'], 'pts': pts})
                feed_len = plen(pts) + (abs(ctr(eps)[0] - nb[1][0]) + abs(ctr(eps)[1] - nb[1][1]) if eps else 15)
                nbg = seg_near(dp, cls)
                m['pipes'].append({'sys': 'cw', 'd': 20, 'unit': u['name'], 'role': 'branch', 'pts': route(nbg[1], dp, ceil - 0.15)})
            for r in urs:
                if r['w'] * r['h'] >= 1.2:
                    c = ctr(r); m['lights'].append({'x': r2(c[0]), 'y': r2(c[1]), 'z': 2.3, 'kind': 'LED 방등' if r['name'].startswith(('거실', '침실')) else 'LED 다운라이트', 'unit': u['name']})
            unit_stats.append({'floor': fl['id'], 'unit': u['name'], 'dfu': dfu, 'wsfu': r2(wsfu), 'hood': hood_len and r2(hood_len), 'feed': feed_len and r2(feed_len),
                               'boilerExt': bool(bx and bx[2] < 6), 'hrv': cap, 'hrvNeed': q})
        # 세대 입상관 (오수 100 · 배수 75 · 통기 75 · 공용배기 150)
        for s in m['stacks']:
            for k, (sk, d) in enumerate((('sw', 100), ('dw', 75), ('vent', 75), ('exh', 150))):
                m['risers'].append({'sys': sk, 'd': d, 'unit': s['unit'], 'x': r2(s['x'] + 0.1 + (k % 2) * 0.35), 'y': r2(s['y'] + 0.1 + (k // 2) * 0.2), 'role': 'stack'})
    # ================= 입상관 구경 (세대 PS 열 단위: 위층부터 누적)
    cols = {}
    for fl in sorted([f for f in floors if f['units']], key=lambda f: -f['level']):
        for s in fl['mep']['stacks']:
            key = (round(s['x'], 1), round(s['y'], 1))
            us = next((x for x in unit_stats if x['floor'] == fl['id'] and x['unit'] == s['unit']), None)
            c = cols.setdefault(key, {'x': s['x'], 'y': s['y'], 'floors': [], 'dfu': 0, 'units': []})
            c['floors'].append(fl['id']); c['units'].append(s['unit']); c['dfu'] += (us or {}).get('dfu', 12)
    stack_rows = []
    for key, c in cols.items():
        sw = from_tbl(STACK_TBL, c['dfu'] * 0.45); dwd = from_tbl(STACK_TBL, c['dfu'] * 0.55)
        stack_rows.append({'pos': [r2(c['x']), r2(c['y'])], 'floors': len(c['floors']), 'from': c['floors'][-1], 'to': c['floors'][0], 'dfu': c['dfu'],
                           'sw': max(100, sw), 'dw': max(75, dwd), 'vent': max(75, dwd - 25 if dwd > 75 else 75), 'exh': 150 if len(c['floors']) <= 10 else 200, 'units': c['units'][:3]})
    stack_rows.sort(key=lambda r: -r['dfu'])
    # 저층부 횡주 (2F 천장) 구경
    for fl in floors:
        for cl in fl['mep']['collectors']:
            col = next((r for r in stack_rows if abs(r['pos'][0] - cl['path'][0][0]) < 0.6 and abs(r['pos'][1] - cl['path'][0][1]) < 0.6), None)
            dfu = col['dfu'] if col else 150
            cl['d'] = from_tbl(HORIZ_TBL, dfu); cl['dfu'] = dfu
            fl['mep']['pipes'].append({'sys': 'sw', 'd': cl['d'], 'role': 'collector', 'pts': [[p[0], p[1], r2(fl['height'] - 0.5 - i * 0.05)] for i, p in enumerate(cl['path'])]})
    # ================= 급수 주관 (코어 PS 입상, 위층부터 WSFU 누적)
    res = sorted([f for f in floors if f['units']], key=lambda f: -f['level']); acc = 0; main_rows = []
    for fl in res:
        acc += sum(x['wsfu'] for x in unit_stats if x['floor'] == fl['id'])
        q = hunter(acc); d = pipe_for(q, 2.0)
        main_rows.append({'floor': fl['id'], 'wsfu': r2(acc), 'q': r2(q), 'd': d})
        fl['mep']['mainD'] = d
        for rs in fl['mep']['risers']:
            if rs['sys'] == 'cw' and rs.get('role') == 'main':
                rs['d'] = d
    nonres_w = sum(r['w'] * r['h'] for f in floors for r in f['rooms'] if r['ztype'] in ('retail', 'public')) * 0.02
    total_wsfu = acc + nonres_w
    q_peak = hunter(total_wsfu)
    for fl in floors:
        if not fl['units']:
            for rs in fl['mep']['risers']:
                if rs['sys'] == 'cw':
                    rs['d'] = pipe_for(q_peak)
        for rs in fl['mep']['risers']:
            if rs['sys'] in ('fire', 'fire2'):
                rs['d'] = 100
            if rs['sys'] == 'gas':
                rs['d'] = 50
            if rs['sys'] == 'sw' and rs.get('role') == 'main':
                rs['d'] = 150
    # ================= 펌프·저수조
    lowest = order[0]; topz = top_res['elev'] + top_res['height'] if top_res else 50
    static = topz - lowest['elev'] + 1.5
    boost_q = q_peak * 1.2
    boost_h = static * 1.15 + 15
    boost_kw = 1000 * 9.81 * boost_q / 1000 * boost_h / 0.6 / 1000
    fire_q = (P.get('_sprBasis', P['sprHeads']) * 80 + 2 * 130) / 60            # L/s (주차장 연결 시 기준개수 30, NFTC 608)
    fire_h = static * 1.2 + 17
    fire_kw = 1000 * 9.81 * fire_q / 1000 * fire_h / 0.55 / 1000
    life_v = loads['dailyWater'] * P['tankRatio']; fire_v = loads['fireWater']
    tdepth = loads['tankDepth']
    pumps = [
        {'tag': 'P-1', 'name': '급수 부스터펌프 (인버터, 3대 병렬 교대)', 'q': '%.1f L/s (%.0f L/min)' % (boost_q, boost_q * 60), 'h': '%.0f m' % boost_h, 'kw': r2(boost_kw / 2), 'n': 3, 'w': 2.6, 'hgt': 1.4, 'emerg': False},
        {'tag': 'FP-1', 'name': '소화 주펌프 (SP·호스릴 소화전 겸용, 기준개수 %d)' % P.get('_sprBasis', 10), 'q': '%.0f L/min' % (fire_q * 60), 'h': '%.0f m' % fire_h, 'kw': r2(fire_kw), 'n': 1, 'w': 2.0, 'hgt': 0.9, 'emerg': True},
        {'tag': 'FP-2', 'name': '소화 예비펌프', 'q': '%.0f L/min' % (fire_q * 60), 'h': '%.0f m' % fire_h, 'kw': r2(fire_kw), 'n': 1, 'w': 2.0, 'hgt': 0.9, 'emerg': True},
        {'tag': 'JP-1', 'name': '충압펌프', 'q': '60 L/min', 'h': '%.0f m' % fire_h, 'kw': r2(fire_kw * 0.06), 'n': 1, 'w': 0.9, 'hgt': 0.6, 'emerg': True},
        {'tag': 'DP-1', 'name': '배수펌프 (집수정, 2대 교대)', 'q': '0.3 ㎥/min', 'h': '15 m', 'kw': 2.2, 'n': 2, 'w': 1.2, 'hgt': 1.2, 'emerg': True}]
    # ================= 팬
    park = [f for f in floors if f['level'] < 0]
    fans = []
    for f in park:
        a = sum(r['w'] * r['h'] for r in f['rooms'] if r['ztype'] in ('parking', 'aisle', 'ramp'))
        qx = a * P['parkingExhaust'] / 3600
        fans.append({'tag': 'EF-%s' % f['id'], 'name': '%s 주차장 배기팬 (화재 시 배연 겸용)' % f['id'], 'q': '%s ㎥/h' % format(round(qx * 3600), ','), 'pa': 600, 'kw': r2(qx * 600 / 0.6 / 1000), 'emerg': True, 'floor': f['id']})
        njet = max(2, math.ceil(a / 450))
        fans.append({'tag': 'JF-%s' % f['id'], 'name': '%s 유인팬 (제트팬) %d대' % (f['id'], njet), 'q': '1,200 ㎥/h × %d' % njet, 'pa': 0, 'kw': r2(0.4 * njet), 'emerg': False, 'floor': f['id'], 'n': njet})
    nstair = {s['name'] for f in floors for s in f.get('stairs', [])}
    n_up = len([f for f in floors if f['level'] > 0])
    for sn in sorted(nstair):
        qp = (0.5 * 0.9 * 2.1 + 0.02 * (n_up + len(park))) * 1.25   # 방연풍속 0.5m/s (부속실이 방화구조 복도에 면함, NFTC 501A)
        area = qp / 15
        fans.append({'tag': 'SF-%s' % sn[-1], 'name': '급기가압 송풍기 (%s 부속실)' % sn, 'q': '%.2f ㎥/s' % qp, 'pa': 800, 'kw': r2(qp * 800 / 0.6 / 1000), 'emerg': True, 'duct': '%.2f㎡ (□%d×%d)' % (area, 50 * math.ceil(math.sqrt(area * 1.5) * 20), 50 * math.ceil(math.sqrt(area / 1.5) * 20))})
    n_cols = len(stack_rows)
    fans.append({'tag': 'RF', 'name': '옥상 공용배기팬 (욕실 배기 입상 %d열)' % n_cols, 'q': '%d ㎥/h × %d' % (100 * max((r['floors'] for r in stack_rows), default=1), n_cols), 'pa': 250, 'kw': r2(0.25 * n_cols), 'emerg': False})
    hrv_cnt = {}
    for x in unit_stats:
        hrv_cnt[x['hrv']] = hrv_cnt.get(x['hrv'], 0) + 1
    # ================= 전기
    ev_n = max((len(f.get('elevators', [])) for f in floors), default=0)
    kwL = loads['kw']['세대'] + loads['kw']['근생'] + loads['kw']['공공시설'] + loads['kw']['공용·주민공동']
    kwP = loads['kw']['승강기'] + loads['kw']['전기차 충전'] + loads['kw']['주차장'] + boost_kw + sum(f['kw'] for f in fans) + 30
    trL = next((t for t in STD_TR if t >= kwL * 0.55 / 0.9 * 1.2), STD_TR[-1]); trP = next((t for t in STD_TR if t >= kwP * 0.7 / 0.85 * 1.2), STD_TR[-1])
    emerg_kw = sum(p['kw'] * (1 if p['tag'] != 'DP-1' else 2) for p in pumps if p['emerg'] and p['tag'] != 'FP-2') + sum(f['kw'] for f in fans if f['emerg']) + P['evKW'] + 0.02 * sum(len(f['mep']['lights']) for f in floors) * 0.3 + 10
    gen = next((g for g in STD_GEN if g >= emerg_kw * 1.25 / 0.8), STD_GEN[-1])
    feeders = []
    for fl in floors:
        if fl['units']:
            kw = len(fl['units']) * P['unitKW'] * 0.6 + 2
        elif fl['level'] > 0:
            kw = sum(r['w'] * r['h'] for r in fl['rooms'] if r['ztype'] in ('retail', 'public')) * 0.07 + 3
        else:
            kw = sum(r['w'] * r['h'] for r in fl['rooms'] if r['ztype'] in ('parking', 'aisle')) * 0.012 + 5
        I = kw * 1000 / (math.sqrt(3) * 380 * 0.9)
        A = cable_for(I)
        L = (fl['elev'] - lowest['elev']) + 25
        e = 17.8 * L * I / (1000 * A); vd = e / 220 * 100
        while vd > 3 and A < 300:
            A = next(a for a, _ in CABLE if a > A); e = 17.8 * L * I / (1000 * A); vd = e / 220 * 100
        feeders.append({'floor': fl['id'], 'kw': r2(kw), 'I': r2(I), 'cable': 'F-CV %d㎟ 4C' % A, 'len': r2(L), 'vd': r2(vd)})
    ufeed = [x for x in unit_stats if x['feed']]
    worst = max(ufeed, key=lambda x: x['feed']) if ufeed else None
    I1 = P['unitKW'] * 1000 / (220 * 0.9)
    uvd = 35.6 * worst['feed'] * I1 / (1000 * 10) / 220 * 100 if worst else None
    single = {'utility': '한전 22.9kV-Y 3φ4W 특고압 수전 (지중 인입, %s)' % ROAD, 'hv': ['LBS + 피뢰기(LA) + 계기용변성기(MOF)', 'VCB 수전반'],
              'tr': [{'tag': 'TR-L', 'kva': trL, 'use': '전등·전열 (세대·근생·공공·공용)', 'v': '22.9kV / 380-220V 몰드'}, {'tag': 'TR-P', 'kva': trP, 'use': '동력 (승강기·펌프·팬·전기차 충전)', 'v': '22.9kV / 380-220V 몰드'}],
              'gen': {'kva': gen, 'kw_emerg': r2(emerg_kw), 'use': '소방·비상 부하 (소화펌프, 급기가압·배연팬, 비상용 승강기, 비상조명, 배수펌프)', 'ats': 'ATS 4P'},
              'lv': [{'tag': 'MDP-L', 'from': 'TR-L', 'to': ['세대 층 분전반 (EPS 3~16F)', '근생 분전반 (1·2F)', '공공시설 분전반 (1·2F, 별도 계량)', '공용 전등 (복도·EV홀·계단)']},
                     {'tag': 'MDP-P', 'from': 'TR-P', 'to': ['승강기 제어반 ×%d' % ev_n, '급수 부스터 P-1', '주차 배기·유인팬', '전기차 충전기 ×%d' % loads['chargers'], '기계실 동력']},
                     {'tag': 'EMDP', 'from': 'ATS (TR-P / 발전기)', 'to': ['소화펌프 FP-1·2·JP-1', '급기가압 SF', '주차 배연 EF', '비상용 승강기', '비상조명·유도등', '배수펌프 DP-1']}]}
    # ================= 지하 장비 배치
    plant = None; tankroom = None; firetank = None
    for f in order:
        for r in f['rooms']:
            if r['ztype'] == 'mep' and '기계' in r['name'] and '전기' in r['name'] and r['w'] * r['h'] > 150 and not plant:
                plant = (f, r)
            if r['ztype'] == 'mep' and '저수조' in r['name'] and not tankroom:
                tankroom = (f, r)
            if r['ztype'] == 'mep' and '소화수조' in r['name'] and not firetank:
                firetank = (f, r)
    equip = []
    if tankroom:
        f, r = tankroom
        tw = max(1.5, min(3.0, (min(r['w'], r['h']) - 1.6)))
        lens = [life_v / 2 / tdepth / tw, life_v / 2 / tdepth / tw, fire_v / tdepth / tw]
        items = [{'tag': 'T-1', 'name': '생활용수 저수조 1 (SMC, %.0f㎥)' % (life_v / 2), 'w': r2(lens[0]), 'h': r2(tw), 'z1': tdepth + 0.5, 'kind': 'tank'},
                 {'tag': 'T-2', 'name': '생활용수 저수조 2 (SMC, %.0f㎥)' % (life_v / 2), 'w': r2(lens[1]), 'h': r2(tw), 'z1': tdepth + 0.5, 'kind': 'tank'}]
        ft = {'tag': 'T-F', 'name': '소화수조 (%.1f㎥, 2조 분할 · 흡수관 투입구)' % fire_v, 'w': r2(lens[2]), 'h': r2(tw), 'z1': tdepth + 0.5, 'kind': 'tank'}
        if firetank:
            fr = firetank[1]; tw2 = max(1.5, min(3.5, (min(fr['w'], fr['h']) - 1.6)))
            half = fire_v / 2 / tdepth / tw2
            fitems = [dict(ft, tag='T-F1', name='소화수조 1 (%.1f㎥)' % (fire_v / 2), w=r2(half), h=r2(tw2)), dict(ft, tag='T-F2', name='소화수조 2 (%.1f㎥)' % (fire_v / 2), w=r2(half), h=r2(tw2))]
            fp = pack(fr, fitems, aisle=0.8, margin=0.8)
            for it in fp:
                equip.append(dict(it, floor=firetank[0]['id'], z0=0.0))
            firetank[0]['mep']['devices'] += [dict(it, z0=0.0) for it in fp]
            if any(it['over'] for it in fp):
                add(firetank[0], 'warn', '%s 소화수조실에 소화수조가 다 들어가지 않음 (%.1f㎥)' % (firetank[0]['id'], fire_v), fr['name'], ['zoning'])
        else:
            items.append(ft)
        placed = pack(r, items, aisle=0.8, margin=0.8)
        for it in placed:
            equip.append(dict(it, floor=f['id'], z0=0.0))
        f['mep']['devices'] += [dict(it, z0=0.0) for it in placed]
        if any(it['over'] for it in placed):
            add(f, 'warn', '%s 저수조실에 저수조가 다 들어가지 않음 (생활 %.0f㎥ + 소화 %.1f㎥, 유효 수심 %.1fm) → 실 확대 또는 수조 높이 증가' % (f['id'], life_v, fire_v, tdepth), r['name'], ['zoning'])
    if plant:
        f, r = plant
        items = [{'tag': p['tag'], 'name': p['name'], 'w': p['w'], 'h': p['hgt'], 'z1': 1.2, 'kind': 'pump'} for p in pumps if p['tag'] != 'DP-1']
        items += [{'tag': 'FCP', 'name': '소화펌프 제어반', 'w': 1.0, 'h': 0.6, 'z1': 2.0, 'kind': 'panel'},
                  {'tag': 'VCB', 'name': '특고압 수전반 (LBS·MOF·VCB) 3면', 'w': 2.4, 'h': 1.8, 'z1': 2.3, 'kind': 'hv'},
                  {'tag': 'TR-L', 'name': 'TR-L %dkVA 몰드변압기' % trL, 'w': 2.2, 'h': 1.6, 'z1': 2.2, 'kind': 'tr'},
                  {'tag': 'TR-P', 'name': 'TR-P %dkVA 몰드변압기' % trP, 'w': 2.2, 'h': 1.6, 'z1': 2.2, 'kind': 'tr'},
                  {'tag': 'LV', 'name': '저압 배전반 (MDP-L·MDP-P·EMDP) 6면', 'w': 4.8, 'h': 1.0, 'z1': 2.3, 'kind': 'lv'},
                  {'tag': 'ATS', 'name': 'ATS·콘덴서반', 'w': 1.6, 'h': 0.8, 'z1': 2.0, 'kind': 'lv'},
                  {'tag': 'GEN', 'name': '비상발전기 %dkVA (디젤, 연도·급배기 갤러리)' % gen, 'w': 4.6 if gen >= 400 else 3.8, 'h': 1.8, 'z1': 2.2, 'kind': 'gen'},
                  {'tag': 'BAT', 'name': '축전지·감시반 (중앙감시)', 'w': 1.6, 'h': 0.8, 'z1': 2.0, 'kind': 'panel'}]
        placed = pack(r, items, aisle=1.2, margin=0.8)
        for it in placed:
            equip.append(dict(it, floor=f['id'], z0=0.0))
        f['mep']['devices'] += [dict(it, z0=0.0) for it in placed]
        if any(it['over'] for it in placed):
            add(f, 'warn', '%s %s에 장비가 다 들어가지 않음 (유지보수 통로 1.2m 기준) → 실 확대' % (f['id'], r['name']), r['name'], ['zoning'])
        # 집수정·배수펌프
        f['mep']['devices'].append({'tag': 'DP-1', 'kind': 'pump', 'name': '집수정 + 배수펌프 2대', 'x': r2(r['x'] + r['w'] - 2.0), 'y': r2(r['y'] + r['h'] - 2.0), 'w': 1.5, 'h': 1.5, 'z0': -1.2, 'z1': 0.3})
        # 장비실 → 코어 PS 주관·간선
        core = next((byid_r for byid_r in f['rooms'] if byid_r.get('corekind') == 'shaft' and 'PS' in byid_r['name']), None)
        epsr = next((x for x in f['rooms'] if x.get('corekind') == 'shaft' and 'EPS' in x['name']), None)
        pc = (r['x'] + 2.0, r['y'] + r['h'] / 2)
        if core:
            f['mep']['pipes'].append({'sys': 'cw', 'd': pipe_for(q_peak), 'role': 'main', 'pts': route(pc, ctr(core), f['height'] - 0.5)})
            f['mep']['pipes'].append({'sys': 'fire', 'd': 150, 'role': 'main', 'pts': route((pc[0] + 1.5, pc[1]), ctr(core), f['height'] - 0.8, first='y')})
        if epsr:
            f['mep']['trays'].append({'sys': 'power', 'w': 0.6, 'pts': route((r['x'] + r['w'] - 3, r['y'] + r['h'] / 2), ctr(epsr), f['height'] - 0.3)})
    # 주차장: 유인팬·조명·배기
    for f in park:
        aisles = [r for r in f['rooms'] if r['ztype'] == 'aisle']
        jf = next((x for x in fans if x['tag'] == 'JF-%s' % f['id']), None)
        segs = centerlines(aisles); tot = sum(math.hypot(s['b'][0] - s['a'][0], s['b'][1] - s['a'][1]) for s in segs) or 1
        n = jf['n'] if jf else 2; k = 0
        for s in segs:
            L = math.hypot(s['b'][0] - s['a'][0], s['b'][1] - s['a'][1]); m_ = max(1, round(n * L / tot))
            for i in range(m_):
                t = (i + 0.5) / m_
                f['mep']['devices'].append({'kind': 'jetfan', 'name': '유인팬 %d' % (k + 1), 'x': r2(s['a'][0] + (s['b'][0] - s['a'][0]) * t - 0.4), 'y': r2(s['a'][1] + (s['b'][1] - s['a'][1]) * t - 0.25),
                                            'w': 0.8, 'h': 0.5, 'z0': f['height'] - 0.9, 'z1': f['height'] - 0.4, 'dir': s['axis']}); k += 1
            nl = max(1, int(L / 5))
            for i in range(nl):
                t = (i + 0.5) / nl
                for o in (-2.0, 2.0):
                    p = offset(s, o)
                    f['mep']['lights'].append({'x': r2(p[0][0] + (p[1][0] - p[0][0]) * t), 'y': r2(p[0][1] + (p[1][1] - p[0][1]) * t), 'z': r2(f['height'] - 0.5), 'kind': 'LED 주차장등 40W', 'emerg': i % 4 == 0})
        da = next((r for r in f['rooms'] if r['ztype'] == 'mep' and 'DA' in r['name']), None)
        if da:
            f['mep']['devices'].append({'kind': 'fan', 'name': '%s 배기팬 EF (DA)' % f['id'], 'x': r2(da['x'] + 0.2), 'y': r2(da['y'] + 0.5), 'w': r2(min(1.6, da['w'] - 0.4)), 'h': 1.4, 'z0': 0.5, 'z1': 2.3})
            if segs:
                nb = seg_near(ctr(da), segs)
                f['mep']['ducts'].append({'sys': 'pexh', 'w': 1.2, 'h': 0.6, 'pts': route(nb[1], ctr(da), f['height'] - 0.6)})
    # 급기가압 덕트 (부속실마다)
    for f in floors:
        sh = next((s for s in f['mep']['risers'] if s['sys'] == 'press'), None)
        for r in f['rooms']:
            if r.get('corekind') == 'vestibule' and sh:
                f['mep']['ducts'].append({'sys': 'press', 'w': 0.4, 'h': 0.3, 'pts': route((sh['x'], sh['y']), ctr(r), f['height'] - 0.45)})
                f['mep']['devices'].append({'kind': 'damper', 'name': '급기 댐퍼 (자동차압)', 'x': r2(ctr(r)[0] - 0.3), 'y': r2(ctr(r)[1] - 0.15), 'w': 0.6, 'h': 0.3, 'z0': f['height'] - 0.7, 'z1': f['height'] - 0.3})
    # 송수구
    f1 = next((f for f in floors if f['level'] == 1), None)
    if f1:
        d_, p_ = fire_piping.fdc(f1, None)
        f1['mep']['devices'] += d_; f1['mep']['pipes'] += p_
    # 급수 감압밸브: 세대 정수압 0.4MPa(40m) 초과 층 (주택건설기준 제43조②)
    prv_floors = []
    for f in floors:
        if not f['units']:
            continue
        head = boost_h - (f['elev'] - lowest['elev']) - 1.0
        if head > 40:
            prv_floors.append(f['id'])
            for d_ in f['mep']['devices']:
                if d_['kind'] == 'manifold':
                    d_['name'] += ' + 감압밸브 (정수압 %.2fMPa → 0.35MPa)' % (head / 100)
    spst = {f['id']: f['mep'].get('sp') for f in floors if f['mep'].get('sp')}
    # ================= 검토
    if uvd is not None and uvd > 3:  # 설계 목표 3% (KEC 고압 수전 조명 6%)
        add(order[-1], 'warn', '세대 간선 전압강하 %.1f%% > 3%% (최장 %s호 %.0fm) → 10㎟ 이상 또는 층 분전반 추가' % (uvd, worst['unit'], worst['feed']), worst['unit'])
    nob = [x for x in unit_stats if not x['boilerExt']]
    if nob:
        add(order[-1], 'info', '보일러가 외벽에서 6m 넘는 세대 %d곳 (%s…) → 연도가 길어짐, 다용도실·외벽 쪽 주방 배치 또는 전기 온수기 검토' % (len(nob), ', '.join(x['unit'] for x in nob[:4])), nob[0]['unit'], ['arch'])
    longh = [x for x in unit_stats if x['hood'] and x['hood'] > 8]
    if longh:
        add(order[-1], 'info', '주방 후드 배기 덕트가 외벽까지 8m 넘는 세대 %d곳 (최장 %.1fm, %s…) → 후드 전용 입상 배기(PS) 또는 고정압 후드' % (len(longh), max(x['hood'] for x in longh), ', '.join(x['unit'] for x in longh[:4])), longh[0]['unit'], ['arch'])
    small = [x for x in unit_stats if x['hrvNeed'] > x['hrv']]
    detail = {'sp': spst, 'prvFloors': prv_floors, 'codes': fire_piping.CODES, 'heads': sum(len(f['mep'].get('heads', [])) for f in floors), 'sprBasis': P.get('_sprBasis'), 'stacks': stack_rows, 'waterMain': main_rows, 'qPeak': r2(q_peak), 'wsfuTotal': r2(total_wsfu), 'pumps': pumps, 'fans': fans, 'hrv': hrv_cnt,
              'tanks': {'life': r2(life_v), 'fire': r2(fire_v), 'depth': tdepth}, 'single': single, 'feeders': feeders,
              'unitFeeder': worst and {'unit': worst['unit'], 'floor': worst['floor'], 'len': worst['feed'], 'I': r2(I1), 'cable': 'F-CV 10㎟ 2C+E', 'vd': r2(uvd)},
              'trL': trL, 'trP': trP, 'gen': gen, 'emergKw': r2(emerg_kw), 'equip': [{k: v for k, v in e.items() if k != 'over'} for e in equip],
              'boost': {'q': r2(boost_q), 'h': r2(boost_h), 'kw': r2(boost_kw)}, 'firePump': {'q': r2(fire_q * 60), 'h': r2(fire_h), 'kw': r2(fire_kw)},
              'counts': {'pipes': sum(len(f['mep']['pipes']) for f in floors), 'ducts': sum(len(f['mep']['ducts']) for f in floors), 'lights': sum(len(f['mep']['lights']) for f in floors),
                         'devices': sum(len(f['mep']['devices']) for f in floors), 'pipeLen': r2(sum(plen(p['pts']) for f in floors for p in f['mep']['pipes'])),
                         'ductLen': r2(sum(plen(d['pts']) for f in floors for d in f['mep']['ducts'])), 'trayLen': r2(sum(plen(t['pts']) for f in floors for t in f['mep']['trays'] if t['sys'] != 'unitfeed'))},
              'assume': '난방·급탕 = 세대 가스 콘덴싱 보일러, 조리 = 인덕션, 급수 = 저수조 + 부스터 가압, 세대 환기 = 전열교환기 0.5회/h, 욕실 배기 = 세대 PS 공용배기 입상 + 옥상팬'}
    return issues, detail
