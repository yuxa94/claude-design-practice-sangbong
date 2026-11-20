"""구조 모델 — gen_plan.py가 층을 만들고 소방·설비를 계산한 뒤 같은 plan.json 위에서 계산합니다.

- 구조형식: 세대가 있는 층 = 벽식 (내력벽: 외벽·코어·세대간·세대-복도 경계벽), 그 아래(근생·공공·주차) = 기둥·보 (라멘)
- 기둥 격자: 첫 벽식 층(전이층)의 내력벽 선에서 격자선을 고르고(최대 경간 이하) 지하 외곽까지 확장
  → 주차 차로·램프에 걸리는 기둥은 옮겨 보고, 주차면을 막는 기둥 수를 셈 (주차 대수에 영향)
- 전이: 전이층 내력벽 중 아래 코어벽·기둥으로 받지 못하는 구간 → 전이보(1차: 격자선 위, 2차: 격자선 밖) 경간·춤
  → 아래 층 천장고 = 층고 − 전이 슬래브 − 전이보 춤 − 설비 천장 공간(설비 모듈의 횡주 배관 반영)
- 벽식 층끼리: 위층 내력벽이 아래층 벽 위에 없는 길이 (평면 타입 변경 층)
- 벽량(방향별), 슬래브 경간, 기둥 크기(지지 면적 × 층수), 기초 접지압
- 결과는 wall['struct'], floors[].struct, 이슈(discipline 'struct')로 같은 모델에 기록
- 기준값은 plan.json › disciplineInputs.struct (구조 페이지에서 수정)
"""
import math
import struct_detail
from project_conf import SLUG, ROAD, CONF

DEFAULTS = {
    'maxSpan': 9.0,          # 기둥 최대 경간 (m)
    'gridMerge': 1.2,        # 격자선 후보 병합 거리
    'lineTol': 0.35,         # 벽·기둥이 같은 선 위로 보는 허용 오차
    'maxShift': 2.4,         # 차로 간섭 기둥 이동 한도
    'fck': 35.0,             # 콘크리트 강도 (MPa)
    'colStressRatio': 0.4,   # 기둥 축응력 / fck (개략)
    'loadWall': 13.0,        # 벽식 층 고정+활하중 (kN/㎡, 사용)
    'loadFrame': 12.0,       # 근생·공공 층
    'loadBasement': 14.0,    # 지하 주차층
    'loadRoof1F': 20.0,      # 1층 슬래브(지하 위 외부) 조경·소방차
    'loadFactor': 1.4,
    'girderRatio': 0.125,    # 1차 전이보 춤 / 경간 (1/8)
    'secRatio': 0.1,         # 2차 전이보 (1/10)
    'girderMin': 0.8,
    'transferSlab': 0.3,     # 전이층 슬래브 두께
    'finish': 0.1,           # 천장 마감
    'clearMin': 2.7,         # 근생·공공 천장고 목표
    'slabT': 0.21,           # 벽식 슬래브 (층간소음 기준)
    'slabSpanMax': 6.5,      # 벽식 슬래브 경간 목표
    'wallRatioMin': 0.012,   # 방향별 벽량 (벽 단면적 / 바닥면적, 개략)
    'soilBearing': 500.0,    # 허용 지내력 (kPa, 풍화암 가정)
    'colMin': 0.5,
    'fy': 500.0,             # 철근 항복강도 (MPa)
    'seisS': 0.22,           # 설계스펙트럼 S (지진구역 I)
    'seisFa': 1.38,          # 지반증폭 Fa (S4 가정)
    'seisFv': 1.96,          # 지반증폭 Fv (S4 가정)
    'seisIe': 1.2,           # 중요도계수
    'seisR': 4.0,            # 반응수정계수 (RC 보통전단벽)
    'seisOmega': 2.5,        # 시스템 초과강도계수
    'gwDepth': 3.0,          # 지하수위 GL−(m) 가정
    'wallRhoT': 0.005,       # 전단벽 수평철근비
    'shearWallT': 0.4,       # 저층부 추가 전단벽 두께
    'shearTarget': 0.9,      # 저층부 벽 전단 목표 (수요/강도)
}
BEARING = ('exterior', 'core', 'core_inner', 'party', 'unit_corr', 'fire', 'basement')
r3 = lambda v: round(v + 0.0, 3)


def pip(pt, poly, inset=0.0):
    x, y = pt; ins = False; n = len(poly)
    for i in range(n):
        (x1, y1), (x2, y2) = poly[i], poly[(i + 1) % n]
        if (y1 > y) != (y2 > y) and x < (x2 - x1) * (y - y1) / (y2 - y1) + x1:
            ins = not ins
    if not ins or inset <= 0:
        return ins
    for i in range(n):   # 변에서 inset 이상 떨어져야 안쪽
        (x1, y1), (x2, y2) = poly[i], poly[(i + 1) % n]
        dx, dy = x2 - x1, y2 - y1; L = math.hypot(dx, dy) or 1
        t = max(0, min(1, ((x - x1) * dx + (y - y1) * dy) / L / L))
        if math.hypot(x - x1 - t * dx, y - y1 - t * dy) < inset:
            return False
    return True


def inside(pt, poly, tol=0.05):
    if pip(pt, poly):
        return True
    x, y = pt; n = len(poly)
    for i in range(n):
        (x1, y1), (x2, y2) = poly[i], poly[(i + 1) % n]
        dx, dy = x2 - x1, y2 - y1; L = math.hypot(dx, dy) or 1
        t = max(0, min(1, ((x - x1) * dx + (y - y1) * dy) / L / L))
        if math.hypot(x - x1 - t * dx, y - y1 - t * dy) < tol:
            return True
    return False


def seg(w):
    """축 정렬 벽 → (축 'h'|'v', 선 좌표, 시작, 끝)"""
    (ax, ay), (bx, by) = w['a'], w['b']
    if abs(ay - by) < 1e-6:
        return 'h', ay, min(ax, bx), max(ax, bx)
    if abs(ax - bx) < 1e-6:
        return 'v', ax, min(ay, by), max(ay, by)
    return None


def merge_iv(iv):
    iv = sorted(iv); out = []
    for a, b in iv:
        if out and a <= out[-1][1] + 1e-6:
            out[-1][1] = max(out[-1][1], b)
        else:
            out.append([a, b])
    return out


def minus(a, b, ivs):
    """[a,b] − ivs"""
    cur = [[a, b]]
    for c, d in merge_iv(ivs):
        nxt = []
        for x, y in cur:
            if d <= x or c >= y:
                nxt.append([x, y]); continue
            if c > x:
                nxt.append([x, c])
            if d < y:
                nxt.append([d, y])
        cur = nxt
    return [[x, y] for x, y in cur if y - x > 0.3]


def choose_lines(cands, lo, hi, span):
    """후보(좌표, 가중치) 중 lo~hi를 경간 span 이하로 덮는 선을 고름"""
    cs = sorted(cands)
    merged = []
    for c, wgt in cs:
        if merged and c - merged[-1][0] < 1.2:
            if wgt > merged[-1][1]:
                merged[-1] = [c, wgt]
        else:
            merged.append([c, wgt])
    pts = [lo] + [c for c, _ in merged if lo + 1.5 < c < hi - 1.5] + [hi]
    chosen = [lo]
    while chosen[-1] < hi - 1e-6:
        cur = chosen[-1]
        nxt = [p for p in pts if cur + 1.5 < p <= cur + span + 1e-6]
        if hi <= cur + span + 1e-6:
            chosen.append(hi); break
        if nxt:
            # 가장 멀리 가되, 가중치(벽 길이) 큰 선을 조금 더 선호
            wt = {c: w for c, w in merged}
            chosen.append(max(nxt, key=lambda p: (p - cur) + 0.15 * wt.get(p, 0)))
        else:
            n = math.ceil((hi - cur) / span); chosen.append(cur + (hi - cur) / n)
    return [r3(c) for c in chosen]


def extend(lines, lo, hi, span):
    out = list(lines)
    while out[0] - lo > 1.0:
        d = out[0] - lo; n = math.ceil(d / span); out.insert(0, r3(out[0] - d / n))
    while hi - out[-1] > 1.0:
        d = hi - out[-1]; n = math.ceil(d / span); out.append(r3(out[-1] + d / n))
    return out


def analyze_building(floors, inputs):
    P = dict(DEFAULTS); P.update({k: v for k, v in (inputs or {}).items() if k in DEFAULTS})
    issues = {fl['id']: [] for fl in floors}
    def add(fl, level, msg, ref=None, affects=None):
        i = {'kind': 'struct', 'ref': ref or fl['id'], 'level': level, 'discipline': 'struct', 'msg': msg}
        if affects:
            i['affects'] = affects
        issues[fl['id']].append(i)
    order = sorted(floors, key=lambda f: f['level'])
    wallF = [f for f in order if f['units']]
    frameF = [f for f in order if not f['units'] and f['level'] < (wallF[0]['level'] if wallF else 99)]
    tf = wallF[0] if wallF else None                       # 전이층 (첫 벽식 층)
    under = frameF[-1] if frameF else None                # 전이보가 놓이는 층 (그 천장)
    for fl in floors:
        fl['struct'] = {'system': '벽식 (RC 내력벽)' if fl['units'] else ('기둥·보 (RC 라멘) + 코어벽' if fl['level'] > 0 else '기둥·보 (RC 라멘) + 지하외벽'),
                        'columns': [], 'transfers': [], 'discont': [], 'wallRatio': None}
        for w in fl['walls']:
            bearing = w['kind'] in BEARING and (fl['units'] or w['kind'] in ('core', 'core_inner', 'basement'))
            w['struct'] = {'bearing': bool(bearing)}
    def bearing_segs(fl):
        out = []
        for w in fl['walls']:
            if w['struct']['bearing']:
                s = seg(w)
                if s:
                    out.append((w, s))
        return out
    # ---------- 격자
    cols = []
    grid = {'x': [], 'y': []}
    if tf and frameF:
        bs = bearing_segs(tf)
        cx = [(s[1], s[3] - s[2]) for w, s in bs if s[0] == 'v']
        cy = [(s[1], s[3] - s[2]) for w, s in bs if s[0] == 'h']
        xs = [p[0] for p in tf['outline']]; ys = [p[1] for p in tf['outline']]
        gx = choose_lines(cx, min(xs), max(xs), P['maxSpan'])
        gy = choose_lines(cy, min(ys), max(ys), P['maxSpan'])
        allx = [p[0] for f in frameF for p in f['outline']]; ally = [p[1] for f in frameF for p in f['outline']]
        gx = extend(gx, min(allx), max(allx), P['maxSpan']); gy = extend(gy, min(ally), max(ally), P['maxSpan'])
        grid = {'x': gx, 'y': gy}
        def blocked(f, x, y, half):
            for r in f['rooms']:
                if r['ztype'] in ('aisle', 'ramp') and r['x'] - half < x < r['x'] + r['w'] + half and r['y'] - half < y < r['y'] + r['h'] + half:
                    return r
            return None
        def in_core(f, x, y):
            return any(r['kind'] == 'core' and r['x'] - 0.3 <= x <= r['x'] + r['w'] + 0.3 and r['y'] - 0.3 <= y <= r['y'] + r['h'] + 0.3 for r in f['rooms'])
        def on_bwall(f, x, y):
            for w in f['walls']:
                if w['kind'] == 'basement':
                    s = seg(w)
                    if s and ((s[0] == 'h' and abs(y - s[1]) < 0.6 and s[2] - 0.6 <= x <= s[3] + 0.6) or (s[0] == 'v' and abs(x - s[1]) < 0.6 and s[2] - 0.6 <= y <= s[3] + 0.6)):
                        return True
            return False
        n = 0
        for i, x in enumerate(gx):
            for j, y in enumerate(gy):
                fs = [f for f in frameF if inside((x, y), f['outline'])]
                if not fs:
                    continue
                if any(in_core(f, x, y) for f in fs):
                    continue
                above_ground = [f for f in fs if f['level'] > 0]
                if not above_ground and all(on_bwall(f, x, y) for f in fs):
                    continue
                # 차로·램프 간섭 → 이동 시도, 주차면도 되도록 피함
                half = 0.35
                def stall_hits(px_, py_, h=0.5):
                    k = 0
                    for f in fs:
                        for p in f.get('stalls', []):
                            ix = min(px_ + h, p['x'] + p['w']) - max(px_ - h, p['x']); iy = min(py_ + h, p['y'] + p['h']) - max(py_ - h, p['y'])
                            if min(ix, iy) > 0.3:
                                k += 1
                    return k
                hit = [(f, blocked(f, x, y, half)) for f in fs]
                hit = [(f, r) for f, r in hit if r]
                pos, shift = (x, y), None
                offs = [(0.0, 0.0)] + [(dx * k * 0.3, dy * k * 0.3) for k in range(1, int(P['maxShift'] / 0.3 + 1e-6) + 1) for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1))]
                okpos = [(dx, dy) for dx, dy in offs if all(not blocked(f, x + dx, y + dy, half) and (f['level'] > 0 or inside((x + dx, y + dy), f['outline'])) for f in fs)]
                if okpos:
                    best = min(okpos, key=lambda o: (stall_hits(x + o[0], y + o[1]), abs(o[0]) + abs(o[1])))
                    if best != (0.0, 0.0):
                        pos = (x + best[0], y + best[1]); shift = [r3(best[0]), r3(best[1])]
                    hit = []
                n += 1
                c = {'id': 'C%d' % n, 'grid': [i, j], 'gx': x, 'gy': y, 'x': r3(pos[0]), 'y': r3(pos[1]), 'shift': shift,
                     'conflict': [{'floor': f['id'], 'room': r['name']} for f, r in hit] if hit and not shift else [],
                     'floors': [f['id'] for f in fs], 'top': max(fs, key=lambda f: f['level'])['id']}
                # 지지 면적
                tx = ((gx[i + 1] - x) / 2 if i + 1 < len(gx) else 0) + ((x - gx[i - 1]) / 2 if i else 0)
                ty = ((gy[j + 1] - y) / 2 if j + 1 < len(gy) else 0) + ((y - gy[j - 1]) / 2 if j else 0)
                trib = tx * ty
                load = 0.0
                in_tower = inside((x, y), tf['outline'])
                for f in order:
                    if f['units'] and in_tower:
                        load += trib * P['loadWall']
                    elif f in fs:
                        if f['level'] < 0:
                            # 지하층 기둥이 받는 슬래브: 그 위층 슬래브. 1층이 없는 곳(지하 위 외부) = 1층 슬래브 조경 하중
                            load += trib * P['loadBasement']
                        else:
                            load += trib * P['loadFrame']
                if not any(f['level'] > 0 for f in fs):
                    load += trib * (P['loadRoof1F'] - P['loadBasement'])
                pu = load * P['loadFactor']
                ag = pu * 1000 / (P['colStressRatio'] * P['fck'])     # mm²
                side = max(P['colMin'], math.ceil(math.sqrt(ag) / 100) / 10)
                c.update({'trib': r3(trib), 'pu': round(pu), 'size': r3(side), 'tower': in_tower})
                cols.append(c)
        lost_by_floor = {}
        for c in cols:
            h = c['size'] / 2
            for f in frameF:
                if f['id'] not in c['floors']:
                    continue
                for p in f.get('stalls', []):
                    if p['x'] - h < c['x'] < p['x'] + p['w'] + h and p['y'] - h < c['y'] < p['y'] + p['h'] + h:
                        # 주차면 안쪽 0.3m 이상 침범하면 그 면은 못 씀
                        ix = min(c['x'] + h, p['x'] + p['w']) - max(c['x'] - h, p['x']); iy = min(c['y'] + h, p['y'] + p['h']) - max(c['y'] - h, p['y'])
                        if min(ix, iy) > 0.3:
                            c.setdefault('stallsLost', []).append({'floor': f['id'], 'stall': p.get('id') or p.get('no')})
                            lost_by_floor[f['id']] = lost_by_floor.get(f['id'], 0) + 1
        for f in frameF:
            f['struct']['columns'] = [c for c in cols if f['id'] in c['floors']]
            f['struct']['grid'] = grid
        for f in floors:
            f['struct']['gridRef'] = True
        tot_lost = sum(lost_by_floor.values())
        nconf = sum(1 for c in cols if c['conflict'])
        nshift = sum(1 for c in cols if c['shift'])
        b1 = next((f for f in order if f['level'] == -1), order[0])
        if nconf:
            add(b1, 'warn', '기둥 %d개가 차로·램프에 걸리고 %.1fm 안에서 피할 곳이 없음 (%s) → 격자 조정 또는 차로 위치 변경(조닝)' % (
                nconf, P['maxShift'], ', '.join('%s %s' % (c['id'], c['conflict'][0]['room'][:10]) for c in cols if c['conflict'][:1])[:200]), '기둥', ['zoning'])
        if nshift:
            add(b1, 'info', '차로·램프를 피해 기둥 %d개를 격자에서 옮김 (최대 %.1fm) — 위층 전이보에 편심, 구조 검토' % (
                nshift, max(max(abs(c['shift'][0]), abs(c['shift'][1])) for c in cols if c['shift'])), '기둥')
        if tot_lost:
            add(b1, 'warn', '기둥이 주차면 %d면을 막음 (%s) → 주차 대수 재확인 필요 (법규 페이지 주차 대수에는 아직 미반영)' % (
                tot_lost, ', '.join('%s %d' % (k, v) for k, v in sorted(lost_by_floor.items()))), '주차', ['arch', 'zoning'])
    # ---------- 전이 (전이층 내력벽 ↔ 아래 코어벽·기둥)
    def supports_below(dn, axis, line):
        """아래층에서 이 선(axis, line)을 받는 구간 = 같은 선 위 내력벽"""
        ivs = []
        for w, s in bearing_segs(dn):
            if s[0] == axis and abs(s[1] - line) <= P['lineTol']:
                ivs.append([s[2], s[3]])
        return ivs
    def crossing(dn, axis, line):
        """아래층 내력벽(코어벽 등) 중 이 선을 가로지르는 벽 → 보가 걸리는 점"""
        o = 'h' if axis == 'v' else 'v'
        return [s[1] for w, s in bearing_segs(dn) if s[0] == o and s[2] - 0.05 <= line <= s[3] + 0.05]
    if tf and under:
        members = {}
        gl = {'v': grid['x'], 'h': grid['y']}       # 'v' 벽(x 고정)은 x 격자선 위에 있을 수 있음
        cross = {'v': grid['y'], 'h': grid['x']}
        tcols = [c for c in cols if under['id'] in c['floors']]
        unsup_total = 0.0
        for w, s in bearing_segs(tf):
            axis, line, a, b = s
            ivs = supports_below(under, axis, line)
            # 기둥: 선 위 ±lineTol → 점 지지
            pts = sorted({(c['gy'] if axis == 'v' else c['gx']) for c in tcols if abs((c['gx'] if axis == 'v' else c['gy']) - line) <= max(P['lineTol'], c['size'] / 2)})
            un = minus(a, b, ivs + [[p - 0.3, p + 0.3] for p in pts])
            if not un:
                w['struct'].update({'supported': 1.0, 'transfer': False}); continue
            ul = sum(y - x for x, y in un)
            unsup_total += ul
            on_grid = any(abs(line - g) <= P['lineTol'] for g in gl[axis])
            sup = sorted(set(pts + [v for iv in merge_iv(ivs) for v in iv] + crossing(under, axis, line) + ([] if on_grid else [g for g in cross[axis]])))
            w['struct'].update({'supported': r3(1 - ul / (b - a)), 'transfer': True, 'onGrid': on_grid})
            for x0, x1 in un:
                lo = max([p for p in sup if p <= x0 + 1e-6], default=None)
                hi = min([p for p in sup if p >= x1 - 1e-6], default=None)
                inner = [p for p in sup if x0 + 1e-6 < p < x1 - 1e-6]
                cant = lo is None or hi is None
                pts2 = [x0 if lo is None else lo] + inner + [x1 if hi is None else hi]
                for q0, q1 in zip(pts2, pts2[1:]):
                    if q1 - q0 < 0.3:
                        continue
                    kind = 'cantilever' if cant and ((lo is None and q0 == pts2[0]) or (hi is None and q1 == pts2[-1])) else ('primary' if on_grid else 'secondary')
                    key = (axis, round(line, 1), round(q0, 2), round(q1, 2))
                    span = q1 - q0
                    ratio = P['girderRatio'] if kind != 'secondary' else P['secRatio']
                    depth = max(P['girderMin'], span * ratio * (2 if kind == 'cantilever' else 1))
                    m = members.get(key)
                    if not m:
                        members[key] = m = {'axis': axis, 'line': r3(line), 'from': r3(q0), 'to': r3(q1), 'span': r3(span), 'kind': kind, 'depth': r3(math.ceil(depth * 10) / 10), 'walls': []}
                    m['walls'].append(w['id'])
        # 2차 보가 걸리는 1차 격자선 구간도 1차 전이보로
        for m in list(members.values()):
            if m['kind'] != 'secondary':
                continue
            for end in (m['from'], m['to']):
                ax2 = 'h' if m['axis'] == 'v' else 'v'
                line2 = end
                # 그 격자선 위 기둥 사이 구간
                cp = sorted({(c['gy'] if ax2 == 'v' else c['gx']) for c in tcols if abs((c['gx'] if ax2 == 'v' else c['gy']) - line2) <= P['lineTol']})
                cp = sorted(set(cp + crossing(under, ax2, line2) + [v for iv in supports_below(under, ax2, line2) for v in iv]))
                lo = max([p for p in cp if p <= m['line']], default=None); hi = min([p for p in cp if p >= m['line']], default=None)
                if lo is None or hi is None or hi - lo < 0.5:
                    continue
                key = (ax2, round(line2, 1), round(lo, 2), round(hi, 2))
                if key not in members:
                    span = hi - lo
                    members[key] = {'axis': ax2, 'line': r3(line2), 'from': r3(lo), 'to': r3(hi), 'span': r3(span), 'kind': 'primary',
                                    'depth': r3(math.ceil(max(P['girderMin'], span * P['girderRatio']) * 10) / 10), 'walls': [], 'carries': 'secondary'}
        tr = sorted(members.values(), key=lambda m: -m['depth'])
        for k, m in enumerate(tr):
            m['id'] = 'TG%d' % (k + 1)
            if m['axis'] == 'h':
                m['a'] = [m['from'], m['line']]; m['b'] = [m['to'], m['line']]
            else:
                m['a'] = [m['line'], m['from']]; m['b'] = [m['line'], m['to']]
        under['struct']['transfers'] = tr
        tf['struct']['transferBelow'] = under['id']
        dmax = max((m['depth'] for m in tr), default=0)
        plenum = (under.get('mep') or {}).get('plenum', 0.35)
        clear = under['height'] - P['transferSlab'] - dmax - plenum - P['finish']
        under['struct']['ceiling'] = {'height': under['height'], 'slab': P['transferSlab'], 'girder': dmax, 'plenum': plenum, 'finish': P['finish'], 'clear': r3(clear), 'min': P['clearMin'],
                                      'needHeight': r3(under['height'] + max(0, P['clearMin'] - clear))}
        if tr:
            add(under, 'warn' if clear < P['clearMin'] else 'info', '%s 천장 = 전이층(%s) 아래: 전이보 %d개 (최대 경간 %.1fm·춤 %.1fm), 전이 슬래브 %.2fm, 설비 공간 %.2fm(보와 겹치지 않는다고 본 보수값) → 천장고 %.2fm %s 목표 %.1fm%s' % (
                under['id'], tf['id'], len(tr), max(m['span'] for m in tr), dmax, P['transferSlab'], plenum, clear, '<' if clear < P['clearMin'] else '≥', P['clearMin'],
                (' → %s 층고 %.2fm 필요 (조닝 층고 변경) 또는 격자 경간 축소' % (under['id'], under['height'] + P['clearMin'] - clear)) if clear < P['clearMin'] else ''),
                under['id'], ['zoning', 'mep'] if clear < P['clearMin'] else None)
            add(tf, 'info', '%s 내력벽 %.0fm가 아래 %s 코어벽·기둥 위에 있지 않음 → 전이보로 받음 (1차 %d · 2차 %d · 캔틸레버 %d)' % (
                tf['id'], unsup_total, under['id'], sum(1 for m in tr if m['kind'] == 'primary'), sum(1 for m in tr if m['kind'] == 'secondary'), sum(1 for m in tr if m['kind'] == 'cantilever')), tf['id'])
            add(under, 'info', '전이구조 → 수직 비정형(연층·약층) 가능: 전이보·하부 기둥은 특별지진하중(Ω0) 적용, 16층 이상 → 건축구조기술사 협력·구조안전 확인 대상', under['id'])
    # ---------- 벽식 층끼리 연속성
    for k in range(1, len(wallF)):
        fl, dn = wallF[k], wallF[k - 1]
        if dn['level'] != fl['level'] - 1:
            continue
        tot = 0.0; segs = []
        for w, s in bearing_segs(fl):
            axis, line, a, b = s
            un = minus(a, b, supports_below(dn, axis, line))
            for x0, x1 in un:
                tot += x1 - x0
                segs.append({'wall': w['id'], 'a': [x0, line] if axis == 'h' else [line, x0], 'b': [x1, line] if axis == 'h' else [line, x1], 'len': r3(x1 - x0)})
            if un:
                w['struct'].update({'supported': r3(1 - sum(y - x for x, y in un) / (b - a)), 'discont': True})
        fl['struct']['discont'] = segs
        if tot > 1.0:
            add(fl, 'warn', '%s 내력벽 %.0fm (%d구간)가 아래 %s 벽 위에 있지 않음 (%s) → 벽 위치를 아래층에 맞추거나 %s 슬래브를 전이 슬래브로' % (
                fl['id'], tot, len(segs), dn['id'], ('평면 타입 %s → %s' % (dn.get('planType'), fl.get('planType'))) if dn.get('planType') != fl.get('planType') else '%s 주민공동시설 등 평면 차이' % dn['id'], dn['id']), fl['id'], ['arch', 'zoning'])
    # ---------- 벽량·슬래브 경간 (벽식 층)
    slab_over = []
    for fl in wallF:
        area = sum(r['w'] * r['h'] for r in fl['rooms'])
        wv = {'h': 0.0, 'v': 0.0}
        wins = {}
        for o in fl['openings']:
            wins[o['wall']] = wins.get(o['wall'], 0) + o['w']
        for w, s in bearing_segs(fl):
            net = max(0, (s[3] - s[2]) - wins.get(w['id'], 0))
            wv[s[0]] += net * w['t']
        fl['struct']['wallRatio'] = {'u': r3(wv['h'] / area), 'v': r3(wv['v'] / area), 'min': P['wallRatioMin']}
        byid = {r['id']: r for r in fl['rooms']}
        spans = []
        for u in fl['units']:
            rs = [byid[i] for i in u['rooms'] if i in byid]
            x0 = min(r['x'] for r in rs); x1 = max(r['x'] + r['w'] for r in rs); y0 = min(r['y'] for r in rs); y1 = max(r['y'] + r['h'] for r in rs)
            spans.append((min(x1 - x0, y1 - y0), u['name']))
        if spans:
            mx = max(spans)
            fl['struct']['slabSpan'] = {'max': r3(mx[0]), 'unit': mx[1], 'limit': P['slabSpanMax'], 't': P['slabT']}
        low = [d for d in ('u', 'v') if fl['struct']['wallRatio'][d] < P['wallRatioMin']]
        if low:
            add(fl, 'warn', '%s 벽량 부족 (%s 방향 %.2f%% < %.1f%%) → 세대간 벽 두께·길이 보강 또는 코어벽 추가' % (
                fl['id'], '·'.join({'u': ROAD + ' 나란한(u)', 'v': '깊이(v)'}[d] for d in low), min(fl['struct']['wallRatio'][d] for d in low) * 100, P['wallRatioMin'] * 100), fl['id'], ['arch'])
        if spans and mx[0] > P['slabSpanMax']:
            slab_over.append((fl, mx))
    if slab_over:
        f0 = slab_over[0][0]
        lines = sorted({m[1][-2:] for _, m in slab_over})
        add(f0, 'warn', '슬래브 경간 %.1fm > %.1fm (벽식 %.0fmm): %s층 %s호 라인 (%s~%s) → 세대 안 보 추가, 슬래브 두께 증가 또는 세대 폭 조정' % (
            max(m[0] for _, m in slab_over), P['slabSpanMax'], P['slabT'] * 1000, len(slab_over), '·'.join(lines), slab_over[0][0]['id'], slab_over[-1][0]['id'], ), slab_over[0][1][1], ['arch'])
    # ---------- 기초
    lowest = order[0]
    W = 0.0
    for f in order:
        a = sum(r['w'] * r['h'] for r in f['rooms'])
        W += a * (P['loadWall'] if f['units'] else (P['loadBasement'] if f['level'] < 0 else P['loadFrame']))
    xs = lowest['outline']
    fa = abs(sum(xs[i][0] * xs[(i + 1) % len(xs)][1] - xs[(i + 1) % len(xs)][0] * xs[i][1] for i in range(len(xs)))) / 2
    q = W / fa if fa else 0
    tw = sum(sum(r['w'] * r['h'] for r in f['rooms']) * P['loadWall'] for f in wallF)
    ta = abs(sum(tf['outline'][i][0] * tf['outline'][(i + 1) % len(tf['outline'])][1] - tf['outline'][(i + 1) % len(tf['outline'])][0] * tf['outline'][i][1] for i in range(len(tf['outline'])))) / 2 if tf else 1
    qt = (tw + W - tw) / fa if fa else 0
    q_tower = (W * (ta / fa) + tw * (1 - ta / fa)) / ta if ta and fa else q     # 탑상부 아래 국부 (개략)
    found = {'weight': round(W), 'area': r3(fa), 'q': r3(q), 'qTower': r3(q_tower), 'allow': P['soilBearing'], 'type': '전면기초 (매트)'}
    lowest['struct']['foundation'] = found
    add(lowest, 'warn' if q_tower > P['soilBearing'] else 'info', '기초: 총 사용하중 약 %s kN ÷ 지하 바닥 %.0f㎡ = 평균 %.0f kPa · 탑상부 아래 약 %.0f kPa %s 허용 %.0f kPa (지반조사 전 가정) → %s' % (
        format(round(W), ','), fa, q, q_tower, '>' if q_tower > P['soilBearing'] else '≤', P['soilBearing'], '말뚝 또는 매트 두께 증가 검토' if q_tower > P['soilBearing'] else '전면기초(매트) 가능'), lowest['id'])
    summary = {'columns': len(cols), 'colMax': max((c['size'] for c in cols), default=0), 'colConflict': sum(1 for c in cols if c['conflict']),
               'colShift': sum(1 for c in cols if c['shift']), 'stallsLost': sum(len(c.get('stallsLost', [])) for c in cols),
               'transfers': len(under['struct']['transfers']) if under else 0,
               'girderMax': max((m['depth'] for m in (under['struct']['transfers'] if under else [])), default=0),
               'spanMax': max((m['span'] for m in (under['struct']['transfers'] if under else [])), default=0),
               'clear': (under['struct'].get('ceiling') or {}).get('clear') if under else None, 'clearMin': P['clearMin'],
               'transferFloor': tf and tf['id'], 'underFloor': under and under['id'],
               'discont': r3(sum(s['len'] for f in wallF for s in f['struct']['discont'])),
               'wallRatioMin': min((min(f['struct']['wallRatio']['u'], f['struct']['wallRatio']['v']) for f in wallF), default=None),
               'q': found['qTower'], 'soil': P['soilBearing'], 'grid': grid}
    di, detail = struct_detail.build(floors, P, {'cols': cols, 'grid': grid, 'tf': tf, 'under': under})
    for k, v in di.items():
        issues[k] += v
    summary.update({'conc': detail['qtyTotal']['conc'], 'rebar': detail['qtyTotal']['rebar'], 'matT': detail['foundation']['t'], 'V': detail['seismic']['V'],
                    'T': detail['seismic']['T'], 'beams': sum(b['n'] for b in detail['beams'])})
    return issues, P, {'grid': grid, 'foundation': detail['foundation'], 'summary': summary, 'detail': detail}
