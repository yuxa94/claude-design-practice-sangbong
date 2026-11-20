"""소화 배관 — 스프링클러·옥내소화전(호스릴)·연결송수관·비상콘센트를 실제 경로로 만듭니다 (mep_detail에서 호출).

근거 (2026-11 확인, 원문 대조 필요한 값은 CODES의 conf로 표시):
- 스프링클러설비 화재안전성능·기술기준 (NFPC/NFTC 103)
  · 별표 1 헤드 수별 급수관 구경 (폐쇄형, 가 란): 25A 2개 · 32A 3 · 40A 5 · 50A 10 · 65A 30 · 80A 60 · 90A 80 · 100A 100 · 125A 160 · 150A 161 이상
  · 한 층 하나의 급수배관(밸브)이 담당하는 구역 3,000㎡ 이하 → 층마다 유수검지장치
  · 교차배관에서 분기되는 한쪽 가지배관 헤드 8개 이하
  · 헤드 설치 제외: 계단실(부속실 포함)·경사로·승강로·비상용승강기 승강장·파이프덕트·덕트피트·화장실(욕실) 등
- 공동주택의 화재안전성능·기술기준 (NFPC/NFTC 608, 2024.01.01 시행)
  · 2.3.1.1 수원 = 기준개수 10 × 1.6㎥ (주차장과 연결된 경우 주차장 부분 기준개수 30)
  · 2.3.1.2 화장실 반자 안 소방용 합성수지배관 가능 · 2.3.1.3 방호구역 2개 층 이내
  · 2.3.1.4 세대 헤드 수평거리 2.6m 이하 · 2.3.1.5 외벽 창문에서 0.6m 이내 헤드 · 2.3.1.6 거실 조기반응형
  · 2.2.1.1 옥내소화전 호스릴 방식 · 2.13 연결송수관 방수구: 층마다(1·2층 제외 가능), 계단 출입구 5m 이내, 수평 50m 초과 시 추가,
    단구형 가능(아파트 층), 송수구 동별 · 펌프 2,400L/min (방수구 3개 초과 시 1개당 800 가산) · 2.14 비상콘센트 계단 5m 이내
- 연결송수관 (NFTC 502): 주배관 100mm 이상, 방수구 연결 65mm, 지표면 높이 70m 이상이면 가압송수장치
- 옥내소화전 (NFTC 102): 방수압 0.17MPa · 130L/min, 수원 N(최대 2) × 2.6㎥ (30층 미만)
"""
import math
from project_conf import SLUG, ROAD, CONF

r2 = lambda v: round(v + 0.0, 2)
SP_TBL = [(25, 2), (32, 3), (40, 5), (50, 10), (65, 30), (80, 60), (90, 80), (100, 100), (125, 160), (150, 10 ** 9)]
NO_HEAD = ('stair', 'vestibule', 'ev', 'shaft')


def sp_d(n, minimum=25):
    for d, cap in SP_TBL:
        if n <= cap:
            return max(d, minimum)
    return 150


def heads_axis(L, S, win_lo=False, win_hi=False):
    """한 축 위치: 간격 ≤ S, 양 끝 벽에서 ≤ S/2, 창 쪽 벽에서 ≤ 0.6m"""
    lo = min(0.6, L / 2) if win_lo else min(S / 2, L / 2)
    hi = L - (min(0.6, L / 2) if win_hi else min(S / 2, L / 2))
    if hi - lo < 0.3:
        return [r2(L / 2)] if not (win_lo or win_hi) else [r2(lo if win_lo else hi)]
    n = max(2, 1 + math.ceil((hi - lo) / S - 1e-9))
    return [r2(lo + (hi - lo) * k / (n - 1)) for k in range(n)]


def route(a, b, z, first='x'):
    mid = (b[0], a[1]) if first == 'x' else (a[0], b[1])
    pts = [[r2(a[0]), r2(a[1]), r2(z)], [r2(mid[0]), r2(mid[1]), r2(z)], [r2(b[0]), r2(b[1]), r2(z)]]
    out = [pts[0]]
    for p in pts[1:]:
        if abs(p[0] - out[-1][0]) + abs(p[1] - out[-1][1]) > 0.01:
            out.append(p)
    return out


def near_seg(p, segs):
    best = None
    for s in segs:
        (ax, ay), (bx, by) = s
        dx, dy = bx - ax, by - ay; L2 = dx * dx + dy * dy or 1
        t = max(0, min(1, ((p[0] - ax) * dx + (p[1] - ay) * dy) / L2))
        q = (ax + t * dx, ay + t * dy); d = abs(p[0] - q[0]) + abs(p[1] - q[1])
        if best is None or d < best[0]:
            best = (d, q)
    return best


def room_heads(r, R, walls, unit):
    S = R * math.sqrt(2)
    win = {'W': False, 'E': False, 'S': False, 'N': False}
    if unit:
        for w in walls:
            if r['id'] in w['rooms'] and None in w['rooms']:
                (ax, ay), (bx, by) = w['a'], w['b']
                if abs(ay - by) < 1e-6:
                    win['S' if abs(ay - r['y']) < 0.05 else 'N'] = True
                else:
                    win['W' if abs(ax - r['x']) < 0.05 else 'E'] = True
    xs = heads_axis(r['w'], S, win['W'], win['E'])
    ys = heads_axis(r['h'], S, win['S'], win['N'])
    return [(r2(r['x'] + x), r2(r['y'] + y)) for y in ys for x in xs], S


def build(fl, P, ctx):
    """ctx: risers(list), cls(centerlines [{'a','b','axis'}]), ceil, fireOut(fl['fire'])"""
    m = fl['mep']; H = fl['height']; byid = {r['id']: r for r in fl['rooms']}
    wby = {w['id']: w for w in fl['walls']}
    units = fl['units']; zc = (2.45 if units else H - 0.45)       # 헤드 높이(천장면)
    zmain = ctx['ceil'] - 0.25
    heads = []; pipes = []; devices = []; stats = {'branches': 0, 'maxPerBranch': 0, 'heads': 0, 'early': 0}
    trunk = [((s['a'][0], s['a'][1] - 0.25), (s['b'][0], s['b'][1] - 0.25)) if s['axis'] == 'h' else ((s['a'][0] - 0.25, s['a'][1]), (s['b'][0] - 0.25, s['b'][1])) for s in ctx['cls']]
    if fl['level'] < 0:
        aisles = [r for r in fl['rooms'] if r['ztype'] == 'aisle']
        for r in aisles:
            if r['w'] >= r['h']:
                trunk.append(((r['x'], r['y'] + r['h'] / 2), (r['x'] + r['w'], r['y'] + r['h'] / 2)))
            else:
                trunk.append(((r['x'] + r['w'] / 2, r['y']), (r['x'] + r['w'] / 2, r['y'] + r['h'])))
    for (a, b) in trunk:
        pipes.append({'sys': 'sp', 'd': 0, 'role': 'cross', 'pts': [[r2(a[0]), r2(a[1]), r2(zmain)], [r2(b[0]), r2(b[1]), r2(zmain)]]})
    feed_pts = []        # (점, 헤드 수) — 교차배관에 붙는 분기
    # ---- 세대: 현관문 → 세대 가지배관 → 실별 헤드 줄
    unit_rooms = set()
    for u in units:
        door = next((o for o in fl['openings'] if o['kind'] == 'door' and o.get('unit') == u['name'] and o['type'].startswith('세대 현관문')), None)
        if not door:
            continue
        w = wby[door['wall']]; L = w['len'] or 1
        dp = (w['a'][0] + (w['b'][0] - w['a'][0]) / L * (door['at'] + 0.6), w['a'][1] + (w['b'][1] - w['a'][1]) / L * (door['at'] + 0.6))
        ent = byid.get(door['owner'])
        if ent:
            dp = (min(max(dp[0], ent['x'] + 0.3), ent['x'] + ent['w'] - 0.3), min(max(dp[1], ent['y'] + 0.3), ent['y'] + ent['h'] - 0.3))
        uh = []
        for rid in u['rooms']:
            r = byid.get(rid)
            if not r:
                continue
            unit_rooms.add(rid)
            if '욕실' in r['name']:
                continue
            hs, S = room_heads(r, P['sprRadiusUnit'], fl['walls'], True)
            early = r['name'].startswith(('거실', '침실', '주방'))
            for h in hs:
                heads.append([h[0], h[1], zc, 'E' if early else 'S']); uh.append((h, r))
                stats['early'] += early
        if not uh:
            continue
        # 실별 헤드 줄(같은 y) → 줄 끝을 세대 가지배관(현관 기점)에 연결
        groups = {}
        for h, r in uh:
            groups.setdefault((r['id'], h[1]), []).append(h)
        n_tot = len(uh); nb = math.ceil(n_tot / 8)
        stats['branches'] += nb; stats['maxPerBranch'] = max(stats['maxPerBranch'], math.ceil(n_tot / nb))
        for (rid, y), hs in groups.items():
            hs.sort()
            if len(hs) > 1:
                pipes.append({'sys': 'sp', 'd': sp_d(len(hs)), 'unit': u['name'], 'role': 'branch', 'pts': [[hs[0][0], y, zc + 0.1], [hs[-1][0], y, zc + 0.1]]})
            end = min(hs, key=lambda h: abs(h[0] - dp[0]))
            pipes.append({'sys': 'sp', 'd': sp_d(len(hs) + 1), 'unit': u['name'], 'role': 'branch', 'pts': route(dp, end, zc + 0.15, first='y')})
        feed_pts.append((dp, n_tot, u['name']))
        devices.append({'kind': 'spvalve', 'name': '%s 세대 SP 분기 (가지배관 %d개 · 헤드 %d)' % (u['name'], nb, n_tot), 'unit': u['name'], 'x': r2(dp[0] - 0.1), 'y': r2(dp[1] - 0.1), 'w': 0.2, 'h': 0.2, 'z0': zc, 'z1': zc + 0.25})
    # ---- 공용·비주거 실: 실 장축 교차배관 + 열마다 가지배관 (한쪽 8개 이하)
    for r in fl['rooms']:
        if r['id'] in unit_rooms or r.get('corekind') in NO_HEAD or r['ztype'] in ('ramp',) or r['w'] * r['h'] < 1.0:
            continue
        R = P['sprRadiusParking'] if r['ztype'] in ('parking', 'aisle') else P['sprRadiusOther']
        hs, S = room_heads(r, R, fl['walls'], False)
        zr = H - 0.45 if not units else (2.4 if r['ztype'] in ('corridor', 'lobby') else H - 0.6)
        for h in hs:
            heads.append([h[0], h[1], r2(zr), 'S'])
        horiz = r['w'] >= r['h']
        cols = {}
        for h in hs:
            cols.setdefault(h[0] if horiz else h[1], []).append(h)
        if len(cols) == 1 or min(r['w'], r['h']) < 3.5:
            # 좁은 실(복도 등): 한 줄 가지배관, 8개씩 끊어 급수
            line = sorted(hs, key=lambda h: h[0] if horiz else h[1])
            for k in range(0, len(line), 8):
                seg = line[k:k + 8]
                if len(seg) > 1:
                    pipes.append({'sys': 'sp', 'd': sp_d(len(seg)), 'role': 'branch', 'pts': [[seg[0][0], seg[0][1], r2(zr + 0.1)], [seg[-1][0], seg[-1][1], r2(zr + 0.1)]]})
                feed_pts.append((seg[0], len(seg), None))
                stats['branches'] += 1; stats['maxPerBranch'] = max(stats['maxPerBranch'], len(seg))
            continue
        cy = r['y'] + r['h'] / 2 if horiz else None; cx = r['x'] + r['w'] / 2 if not horiz else None
        if horiz:
            a, b = (r['x'] + 0.3, cy), (r['x'] + r['w'] - 0.3, cy)
        else:
            a, b = (cx, r['y'] + 0.3), (cx, r['y'] + r['h'] - 0.3)
        pipes.append({'sys': 'sp', 'd': sp_d(len(hs), 40), 'role': 'cross', 'pts': [[r2(a[0]), r2(a[1]), r2(zr + 0.25)], [r2(b[0]), r2(b[1]), r2(zr + 0.25)]]})
        for c, col in cols.items():
            for side in (1, -1):
                part = sorted([h for h in col if ((h[1] - cy) if horiz else (h[0] - cx)) * side > -1e-6 and not (side == -1 and abs((h[1] - cy) if horiz else (h[0] - cx)) < 1e-6)],
                              key=lambda h: abs((h[1] - cy) if horiz else (h[0] - cx)))
                if not part:
                    continue
                for k in range(0, len(part), 8):
                    seg = part[k:k + 8]
                    far = seg[-1]
                    st = (c, cy) if horiz else (cx, c)
                    pipes.append({'sys': 'sp', 'd': sp_d(len(seg)), 'role': 'branch', 'pts': [[r2(st[0]), r2(st[1]), r2(zr + 0.15)], [far[0], far[1], r2(zr + 0.15)]]})
                    stats['branches'] += 1; stats['maxPerBranch'] = max(stats['maxPerBranch'], len(seg))
        feed_pts.append(((r['x'] + r['w'] / 2, r['y'] + r['h'] / 2), len(hs), None))
    # ---- 교차배관 ← 분기 연결, 주배관(입상) → 유수검지장치 → 교차배관
    for p, n, un in feed_pts:
        if not trunk:
            break
        nb = near_seg(p, trunk)
        if nb and nb[0] > 0.05:
            pipes.append({'sys': 'sp', 'd': sp_d(n, 25), 'unit': un, 'role': 'feed', 'pts': route(nb[1], p, zmain, first='y')})
    nheads = len(heads); stats['heads'] = nheads
    riser = next((r for r in ctx['risers'] if r['sys'] == 'fire'), None)
    if riser and trunk:
        nb = near_seg((riser['x'], riser['y']), trunk)
        pipes.append({'sys': 'sp', 'd': sp_d(nheads, 65), 'role': 'main', 'pts': route((riser['x'], riser['y']), nb[1], zmain)})
        devices.append({'kind': 'alarm', 'tag': 'AV-%s' % fl['id'], 'name': '%s 습식 유수검지장치 (알람밸브) · 개폐표시형 밸브 · 압력스위치' % fl['id'], 'x': r2(riser['x'] - 0.25), 'y': r2(riser['y'] - 0.25), 'w': 0.5, 'h': 0.5, 'z0': 0.8, 'z1': 1.6})
    trunk_d = sp_d(nheads, 40)
    for p in pipes:
        if p['role'] == 'cross' and p['d'] == 0:
            p['d'] = trunk_d
    # 말단 시험장치: 입상관에서 가장 먼 헤드
    if heads and riser:
        far = max(heads, key=lambda h: abs(h[0] - riser['x']) + abs(h[1] - riser['y']))
        devices.append({'kind': 'testv', 'tag': 'TV-%s' % fl['id'], 'name': '%s 말단 시험장치 (개폐밸브·압력계·오리피스)' % fl['id'], 'x': r2(far[0] - 0.15), 'y': r2(far[1] - 0.15), 'w': 0.3, 'h': 0.3, 'z0': 1.0, 'z1': 1.8})
    # ---- 옥내소화전 (호스릴): 수직배관 → 소화전함
    fo = ctx['fireOut']; hyd_r = next((r for r in ctx['risers'] if r['sys'] == 'fire'), None)
    for k, h in enumerate(fo.get('hydrants', [])):
        devices.append({'kind': 'hydrant', 'tag': 'H%d' % (k + 1), 'name': '%s 호스릴 옥내소화전함 %d (0.17MPa · 130L/min)%s' % (fl['id'], k + 1, ' — 25m 포용 보완' if h.get('added') else ''),
                        'x': r2(h['x'] - 0.4), 'y': r2(h['y'] - 0.12), 'w': 0.8, 'h': 0.25, 'z0': 0.3, 'z1': 1.8})
        if hyd_r:
            pipes.append({'sys': 'hyd', 'd': 40, 'role': 'branch', 'pts': route((hyd_r['x'] + 0.2, hyd_r['y']), (h['x'], h['y']), zmain - 0.15, first='y') + [[r2(h['x']), r2(h['y']), 1.5]]})
    # ---- 연결송수관: 계단 옆 입상 100A → 방수구 65A (1·2층 제외), 방수기구함 3개 층마다
    outlets = []
    if fl['level'] < 0 or fl['level'] >= 3:
        for s in fo.get('standpipes', []):
            st = next((x for x in fl.get('stairs', []) if x['name'] == s['stair']), None)
            rx, ry = (st['x'] + 0.25, st['y'] + 0.25) if st else (s['x'], s['y'])
            ctx['risers'].append({'sys': 'conn', 'd': 100, 'x': r2(rx), 'y': r2(ry), 'role': 'main', 'shaft': s['stair']})
            pipes.append({'sys': 'conn', 'd': 65, 'role': 'branch', 'pts': route((rx, ry), (s['x'], s['y']), 1.0)})
            devices.append({'kind': 'outlet', 'name': '%s 연결송수관 방수구 65A (%s, 계단 5m 이내 · %s)' % (fl['id'], s['stair'].replace('특별피난계단 ', '계단 '), '단구형' if units else '쌍구형'),
                            'x': r2(s['x'] - 0.2), 'y': r2(s['y'] - 0.2), 'w': 0.4, 'h': 0.4, 'z0': 0.5, 'z1': 1.0})
            outlets.append(s)
        if outlets and (fl['level'] < 0 or fl['level'] % 3 == 0):
            s = outlets[0]
            devices.append({'kind': 'hosebox', 'name': '%s 방수기구함 (호스 15m×2·노즐, 3개 층마다)' % fl['id'], 'x': r2(s['x'] + 0.3), 'y': r2(s['y'] - 0.2), 'w': 0.6, 'h': 0.25, 'z0': 0.3, 'z1': 1.6})
    # ---- 비상콘센트 (11층 이상·지하) — 내화 배선
    for s in fo.get('outlets', []):
        devices.append({'kind': 'econ', 'name': '%s 비상콘센트 (단상 220V 1.5kVA, 계단 5m 이내)' % fl['id'], 'x': r2(s['x'] + 0.6), 'y': r2(s['y'] - 0.15), 'w': 0.3, 'h': 0.3, 'z0': 0.8, 'z1': 1.5})
    stats.update({'trunk': trunk_d, 'hydrants': len(fo.get('hydrants', [])), 'outlets': len(outlets), 'econ': len(fo.get('outlets', []))})
    return heads, pipes, devices, stats


def fdc(f1, risers_all):
    """송수구 (1층 외벽, 정면 도로 쪽) → 계단 연결송수관 입상관"""
    ext = [w for w in f1['walls'] if None in w['rooms'] and abs(w['a'][1]) < 0.05 and abs(w['b'][1]) < 0.05]
    stairs = f1.get('stairs', [])
    if not ext or not stairs:
        return [], []
    st = stairs[0]
    w = min(ext, key=lambda w: abs((w['a'][0] + w['b'][0]) / 2 - (st['x'] + st['w'] / 2)))
    p = ((w['a'][0] + w['b'][0]) / 2, 0.3)
    dev = [{'kind': 'fdc', 'tag': 'FDC', 'name': '연결송수관·스프링클러 송수구 (쌍구형 65A, 지면 0.5~1.0m, %s 쪽)' % ROAD, 'x': r2(p[0] - 0.4), 'y': 0.05, 'w': 0.8, 'h': 0.3, 'z0': 0.5, 'z1': 1.0}]
    pipes = []
    for s in stairs:
        pipes.append({'sys': 'conn', 'd': 100, 'role': 'main', 'pts': route(p, (s['x'] + 0.25, s['y'] + 0.25), f1['height'] - 0.6)})
    return dev, pipes


CODES = [
    ('스프링클러', '헤드 수별 급수관 구경 (폐쇄형 가 란)', '25A 2 · 32A 3 · 40A 5 · 50A 10 · 65A 30 · 80A 60 · 90A 80 · 100A 100 · 125A 160 · 150A 161+', 'NFPC 103 별표 1', '가지·교차·주배관 구경', '원문 확인'),
    ('스프링클러', '한 층 급수배관 담당 구역', '3,000㎡ 이하', 'NFPC 103 별표 1 비고', '층마다 유수검지장치', '원문 확인'),
    ('스프링클러', '한쪽 가지배관 헤드 수', '8개 이하', 'NFTC 103', '세대 분기·실별 가지배관 8개씩', '원문 확인'),
    ('스프링클러', '세대 헤드 수평거리', '2.6m 이하 (정방형 간격 3.68m)', 'NFTC 608 2.3.1.4', '세대 실 헤드 격자', '원문 확인'),
    ('스프링클러', '외벽 창문 헤드', '창문에서 0.6m 이내', 'NFTC 608 2.3.1.5', '외벽 접한 세대 실의 첫 줄', '원문 확인'),
    ('스프링클러', '거실 헤드', '조기반응형', 'NFTC 608 2.3.1.6', '거실·침실·주방 헤드 E', '원문 확인'),
    ('스프링클러', '수원', '기준개수 10 × 1.6㎥ · 주차장 연결 시 30개', 'NFTC 608 2.3.1.1', '소화수조·소화펌프 유량', '원문 확인'),
    ('스프링클러', '화장실 반자 배관', '소방용 합성수지배관 가능 (항상 충수)', 'NFTC 608 2.3.1.2', '세대 가지배관 재질 메모', '원문 확인'),
    ('스프링클러', '헤드 설치 제외', '계단실·부속실·경사로·승강로·덕트피트·화장실 등', 'NFTC 103 (설치 제외)', '욕실·계단·부속실·샤프트·램프 제외', '일반 지식 — 확인 필요'),
    ('스프링클러', '지하주차장 방식', '습식 원칙 (또는 규정된 대체 작동방식)', 'NFPC 103 제8조 (2026.03 시행) — 검토서 6.3', '주차층 습식 유수검지장치', '검토서 인용'),
    ('스프링클러', '헤드 수평거리 (기타·주차)', '내화구조 2.3m · 기타 2.1m', 'NFTC 103', '공용부 2.3 · 주차장 2.1', '일반 지식 — 확인 필요'),
    ('옥내소화전', '방식', '호스릴', 'NFTC 608 2.2.1.1', '소화전함 = 호스릴', '원문 확인'),
    ('옥내소화전', '성능', '방수압 0.17MPa · 130L/min (2개 동시)', 'NFTC 102', '소화펌프 유량·양정', '2차 자료'),
    ('옥내소화전', '수원', 'N(최대 2) × 2.6㎥ (30층 미만)', 'NFTC 102', '소화수조', '2차 자료'),
    ('연결송수관', '방수구 위치', '층마다(아파트 1·2층 제외 가능), 계단 출입구 5m 이내, 수평 50m 초과 시 추가', 'NFTC 608 2.13.1.1~2', '지하·3층 이상 계단 옆', '원문 확인'),
    ('연결송수관', '방수구 형식·송수구', '아파트 층 단구형 가능 · 송수구 동별', 'NFTC 608 2.13.1.3~4', '송수구 1개 (%s 쪽)' % ROAD, '원문 확인'),
    ('연결송수관', '배관 구경', '주배관 100mm 이상 · 방수구 연결 65mm', 'NFTC 502', '입상 100A · 분기 65A', '2차 자료'),
    ('연결송수관', '가압송수장치', '지표면 높이 70m 이상', 'NFTC 502', '높이 약 52m → 미설치', '2차 자료'),
    ('비상콘센트', '위치', '계단 출입구 5m 이내, 수평 50m 초과 시 추가', 'NFTC 608 2.14.1', '11층 이상·지하 계단 옆', '원문 확인'),
    ('제연', '부속실 차압·개방력', '40Pa 이상 (SP 시 12.5Pa) · 110N 이하', 'NFPC 501A 제6조 — 검토서 F-06', '급기가압 송풍기 검토', '검토서 인용'),
    ('제연', '방연풍속', '부속실이 방화구조 복도에 면하면 0.5m/s (거실에 면하면 0.7m/s)', 'NFTC 501A', '급기량 산정 0.5m/s', '일반 지식 — 확인 필요'),
    ('급배수', '배관 매설', '콘크리트 구조체 안 매설 금지', '주택건설기준 제43조①', '층상 배관 (바닥 마감층)', '원문 확인'),
    ('급배수', '화장실 배수', '층상 또는 층하(저소음 +5dB) · 급수 감압밸브', '주택건설기준 제43조②', '층상 배관 + 저층부 감압밸브', '원문 확인'),
    ('급배수', '세대 계량기·급수전', '세대별 수도계량기 · 급수전 2개소 이상', '주택건설기준 제43조③', '분배기·계량기 세대마다', '원문 확인'),
    ('급배수', '배수설비', '부엌·욕실·화장실·다용도실 배수 + 악취·역류 방지', '주택건설기준 제43조④⑤', '트랩·통기관', '원문 확인'),
    ('환기', '신축 공동주택 환기', '0.5회/h 이상 · 기계환기 24시간 가동 가능', '건축물 설비기준 규칙 제11조 · 별표 1의5', '전열교환기 세대마다', '원문 확인'),
    ('전기', '전압강하', '고압 이상 수전: 조명 6% · 기타 8% (설계 목표 3%)', 'KEC 232.3.9', '간선·세대 간선 검토', '일반 지식 — 확인 필요'),
]
