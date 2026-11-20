"""소방·피난 모델 — gen_plan.py가 층을 만든 뒤 같은 계획설계 데이터(plan.json) 위에서 계산합니다.

- 방화구획: 세대·공용복도·계단실·승강로·샤프트·용도별 구역으로 나누고 면적 한도를 확인
  → 구획 경계의 벽은 내화 구획벽, 문은 60분+ 방화문으로 **계획설계 데이터 자체를 바꿈** (창호·문 일람표에 반영)
- 피난: 세대 각 부분 → 직통계단 보행거리 (복도 격자 최단경로 + 세대 안 거리), 두 계단 이격, 2방향 피난
- 소화설비: 스프링클러 헤드 개략 배치, 옥내소화전 위치·25m 포용, 연결송수관 방수구·비상콘센트
- 제연: 특별피난계단 부속실 급기가압
- 기준값은 plan.json › disciplineInputs.fire 에서 읽음 (소방 페이지에서 수정)
"""
import heapq, math
from project_conf import SLUG, ROAD, CONF

DEFAULTS = {
    'travelLimit': 50.0,          # 주요구조부 내화 — 직통계단까지 보행거리 (건축법 시행령 제34조)
    'travelLimit16': 40.0,        # 16층 이상 공동주택의 16층 이상 층
    'stairSepRatio': 1/3,         # 두 직통계단 출입구 간 거리 ≥ 최대 대각선 × (스프링클러 설치 시 1/3)
    'compLimitLow': 3000.0,       # 10층 이하 방화구획 (스프링클러 설치 시 1,000㎡ × 3)
    'compLimitHigh': 600.0,       # 11층 이상 (스프링클러 설치 시 200㎡ × 3)
    'sprRadiusUnit': 2.6,         # 아파트 세대 안 헤드 수평거리 (NFPC 608 — 검토서 6.3)
    'sprRadiusOther': 2.3,        # 내화구조 기타 부분
    'sprRadiusParking': 2.1,      # 주차장 (기타)
    'hydrantRadius': 25.0,        # 옥내소화전 수평거리
    'standpipeFrom': 3,           # 연결송수관 방수구 설치 시작 층
    'outletFrom': 11,             # 비상콘센트 설치 시작 층
    'fireAccessMax': 30.0,        # 피난층 비상용 승강장 → 외부 보행거리
    'grid': 0.3,                  # 보행거리 계산 격자 (m)
    'corridorBoth': 1.8,          # 양옆 거실 공동주택 복도 유효너비 (피난규칙 제15조의2)
    'corridorOne': 1.2,           # 그 밖의 복도
    'stairWidthMin': 1.2,         # 계단 유효너비 (피난규칙 제15조)
}
r3 = lambda v: round(v + 0.0, 3)


def _wall_pt(w, at, off=0.0, toward=None):
    ax, ay = w['a']; bx, by = w['b']; L = w['len'] or 1
    ux, uy = (bx - ax) / L, (by - ay) / L
    x, y = ax + ux * at, ay + uy * at
    if toward is not None and off:
        nx, ny = -uy, ux
        cx, cy = toward['x'] + toward['w'] / 2, toward['y'] + toward['h'] / 2
        if (cx - x) * nx + (cy - y) * ny < 0:
            nx, ny = -nx, -ny
        x, y = x + nx * off, y + ny * off
    return (x, y)


def _raster(rects, g):
    xs0 = min(r['x'] for r in rects); ys0 = min(r['y'] for r in rects)
    xs1 = max(r['x'] + r['w'] for r in rects); ys1 = max(r['y'] + r['h'] for r in rects)
    nx, ny = int((xs1 - xs0) / g) + 2, int((ys1 - ys0) / g) + 2
    ok = bytearray(nx * ny)
    for r in rects:
        i0, i1 = int((r['x'] - xs0) / g + 0.5), int((r['x'] + r['w'] - xs0) / g - 0.5)
        j0, j1 = int((r['y'] - ys0) / g + 0.5), int((r['y'] + r['h'] - ys0) / g - 0.5)
        for j in range(max(0, j0), min(ny, j1 + 1)):
            row = j * nx
            for i in range(max(0, i0), min(nx, i1 + 1)):
                ok[row + i] = 1
    return {'x0': xs0, 'y0': ys0, 'nx': nx, 'ny': ny, 'g': g, 'ok': ok}


def _cell(R, p):
    i, j = int((p[0] - R['x0']) / R['g'] + 0.5), int((p[1] - R['y0']) / R['g'] + 0.5)
    best = None
    for dj in range(-3, 4):
        for di in range(-3, 4):
            ii, jj = i + di, j + dj
            if 0 <= ii < R['nx'] and 0 <= jj < R['ny'] and R['ok'][jj * R['nx'] + ii]:
                d = di * di + dj * dj
                if best is None or d < best[0]:
                    best = (d, jj * R['nx'] + ii)
    return best[1] if best else None


def _dijkstra(R, sources):
    nx, ny, g, ok = R['nx'], R['ny'], R['g'], R['ok']
    INF = float('inf'); dist = [INF] * (nx * ny); prev = [-1] * (nx * ny); h = []
    for c, d0 in sources:
        if c is not None and d0 < dist[c]:
            dist[c] = d0; heapq.heappush(h, (d0, c))
    steps = [(1, 0, g), (-1, 0, g), (0, 1, g), (0, -1, g), (1, 1, g * 1.4142), (1, -1, g * 1.4142), (-1, 1, g * 1.4142), (-1, -1, g * 1.4142)]
    while h:
        d, c = heapq.heappop(h)
        if d > dist[c]:
            continue
        i, j = c % nx, c // nx
        for di, dj, w in steps:
            ii, jj = i + di, j + dj
            if 0 <= ii < nx and 0 <= jj < ny:
                k = jj * nx + ii
                if ok[k] and (di == 0 or dj == 0 or (ok[j * nx + ii] and ok[jj * nx + i])):
                    nd = d + w
                    if nd < dist[k]:
                        dist[k] = nd; prev[k] = c; heapq.heappush(h, (nd, k))
    return dist, prev


def _path(R, prev, c):
    pts = []
    while c != -1 and len(pts) < 4000:
        pts.append([r3(R['x0'] + (c % R['nx']) * R['g']), r3(R['y0'] + (c // R['nx']) * R['g'])]); c = prev[c]
    return pts[::-1][::3] + ([pts[0]] if pts else [])


def analyze(fl, inputs, n_floors_above, top_level):
    P = dict(DEFAULTS); P.update({k: v for k, v in (inputs or {}).items() if k in DEFAULTS})
    rooms = fl['rooms']; walls = fl['walls']; ops = fl['openings']; byid = {r['id']: r for r in rooms}
    wby = {w['id']: w for w in walls}; level = fl['level']; below = level < 0
    out = {'compartments': [], 'boundaryWalls': [], 'egress': None, 'sprinklers': [], 'hydrants': [], 'standpipes': [], 'outlets': [], 'pressurized': [], 'checks': [], 'changes': []}
    issues = []
    # ---------- 방화구획
    comp_of = {}
    def comp(key, kind, name):
        c = next((c for c in out['compartments'] if c['key'] == key), None)
        if not c:
            c = {'key': key, 'id': '%s-fc%d' % (fl['id'], len(out['compartments']) + 1), 'kind': kind, 'name': name, 'rooms': [], 'area': 0.0}
            out['compartments'].append(c)
        return c
    for r in rooms:
        if r['kind'] == 'unitroom':
            c = comp('u:' + r['unit'], '세대', '%s호' % r['unit'])
        elif r['kind'] == 'core':
            ck = r.get('corekind')
            if ck in ('stair', 'vestibule'):
                c = comp('st:' + r['zone'] + ':' + ('A' if 'A' in r['name'] or ck == 'vestibule' and '특피' in r['name'] else r['name'][-1]), '계단실·부속실', '특별피난계단·부속실 (%s)' % r['zname'].split(' (')[0])
                # 계단실과 부속실은 서로 다른 구획 (계단실 단독)
                if ck == 'stair':
                    c = comp('stair:' + r['id'], '계단실', r['name'])
                else:
                    c = comp('vest:' + r['id'], '부속실', r['name'].split(' (')[0])
            elif ck == 'ev':
                c = comp('ev:' + r['id'], '승강로', r['name'])
            else:
                c = comp('sh:' + r['id'], '샤프트', r['name'])
        elif r['ztype'] in ('corridor', 'lobby'):
            c = comp('circ', '공용 복도·로비', '공용 복도·로비')
        elif r['ztype'] in ('parking', 'aisle', 'ramp'):
            c = comp('park', '주차장', '주차장 (차로·램프 포함)')
        elif r['ztype'] == 'retail':
            base = r['zname'].split(' (')[0]
            c = comp('rt:' + base, '근린생활', base)
        elif r['ztype'] == 'public':
            c = comp('pub', '공공시설', '공공시설')
        elif r['ztype'] == 'community':
            c = comp('com', '주민공동시설', '주민공동시설')
        elif r['ztype'] == 'mep':
            c = comp('mep:' + r['id'], '설비실', r['name'])
        else:
            c = comp('etc:' + r['id'], '기타', r['name'])
        c['rooms'].append(r['id']); c['area'] += r['w'] * r['h']; comp_of[r['id']] = c['id']
    limit = P['compLimitHigh'] if level >= 11 else P['compLimitLow']
    for c in out['compartments']:
        c['area'] = r3(c['area']); c['limit'] = limit; c['ok'] = c['area'] <= limit + 1e-6
        c.pop('key', None)
        if not c['ok']:
            issues.append({'kind': 'fire', 'ref': c['name'], 'level': 'warn', 'discipline': 'fire',
                           'msg': '%s 방화구획 %s %.0f㎡ > %.0f㎡ (스프링클러 설치 기준)' % (fl['id'], c['name'], c['area'], limit)})
    # 구획 경계 벽 → 내화 구획벽, 그 위 문 → 60분+ 방화문 (모델에 직접 반영)
    for w in walls:
        a, b = w['rooms']
        if a and b and comp_of.get(a) != comp_of.get(b):
            party = byid[a]['kind'] == 'unitroom' and byid[b]['kind'] == 'unitroom'
            w['fire'] = {'boundary': True, 'rating': '내화 1시간 (세대간 경계벽 겸 방화구획)' if party else '내화 1시간 (방화구획)', 'party': party}
            out['boundaryWalls'].append(w['id'])
    for o in ops:
        if o['kind'] != 'door' or not wby[o['wall']].get('fire'):
            continue
        if '승강기' in o['type']:
            o['fire'] = o.get('fire') or '승강장 방화문 (차염성)'
            continue
        if not o.get('fire') or '갑종' not in str(o.get('fire')) and '60분' not in str(o.get('fire')):
            before = o['type']
            o['fire'] = '60분+ 방화문 (자동폐쇄)'
            if '방화' not in o['type']:
                o['type'] = o['type'].replace(' → 미닫이', '') + ' → 방화문'
                o.pop('sliding', None)
                out['changes'].append({'door': o['id'], 'from': before, 'to': o['type'], 'why': '방화구획 경계'})
    # ---------- 계단·피난
    stairs = [r for r in rooms if r.get('corekind') == 'stair']
    circ = [r for r in rooms if r['ztype'] in ('corridor', 'lobby') or (below and r['ztype'] in ('parking', 'aisle'))]
    stair_doors = []
    for s in stairs:
        sd = next((o for o in ops if o['kind'] == 'door' and s['id'] in wby[o['wall']]['rooms']), None)
        if not sd:
            continue
        vest = next((byid[x] for x in wby[sd['wall']]['rooms'] if x and x != s['id']), None)
        vd = next((o for o in ops if o['kind'] == 'door' and vest and vest['id'] in wby[o['wall']]['rooms'] and o is not sd and '승강기' not in o['type']
                   and any(x and byid[x] in circ for x in wby[o['wall']]['rooms'])), None) if vest else None
        sp = _wall_pt(wby[sd['wall']], sd['at'])
        if vd:
            cr = next(byid[x] for x in wby[vd['wall']]['rooms'] if x and byid[x] in circ)
            vp = _wall_pt(wby[vd['wall']], vd['at'], 0.25, cr)
            stair_doors.append({'stair': s, 'pt': sp, 'entry': vp, 'inner': math.hypot(vp[0] - sp[0], vp[1] - sp[1])})
        else:
            stair_doors.append({'stair': s, 'pt': sp, 'entry': None, 'inner': 0})
    for s in stair_doors:   # 소방: 부속실 급기가압, 방수구, 비상콘센트
        out['pressurized'] += [x for x in wby[next(o for o in ops if o['kind'] == 'door' and s['stair']['id'] in wby[o['wall']]['rooms'])['wall']]['rooms'] if x and x != s['stair']['id']]
        if level >= P['standpipeFrom'] or below:
            out['standpipes'].append({'x': r3(s['pt'][0]), 'y': r3(s['pt'][1]), 'stair': s['stair']['name']})
        if level >= P['outletFrom']:
            out['outlets'].append({'x': r3(s['pt'][0]), 'y': r3(s['pt'][1]), 'stair': s['stair']['name']})
    eg = {'limit': P['travelLimit16'] if level >= 16 else P['travelLimit'], 'units': [], 'max': None, 'worst': None, 'twoWay': None, 'stairSep': None}
    if circ and stair_doors:
        R = _raster(circ, P['grid'])
        fields = []
        for s in stair_doors:
            if s['entry']:
                d, pv = _dijkstra(R, [(_cell(R, s['entry']), s['inner'])])
                fields.append((s, d, pv))
        # 세대: 현관문(복도 쪽) + 세대 안 가장 먼 곳
        for u in fl['units']:
            urs = [byid[i] for i in u['rooms'] if i in byid]
            door = next((o for o in ops if o['kind'] == 'door' and o['type'].startswith('세대 현관문') and o.get('unit') == u['name']), None)
            if not door or not fields:
                continue
            w = wby[door['wall']]; corr = next((byid[x] for x in w['rooms'] if x and byid[x] in circ), None)
            if not corr:
                continue
            dp = _wall_pt(w, door['at'], 0.25, corr); dc = _cell(R, dp); dp0 = _wall_pt(w, door['at'])
            inner = max(max(abs(x - dp0[0]) + abs(y - dp0[1]) for r in urs for x in (r['x'], r['x'] + r['w']) for y in (r['y'], r['y'] + r['h'])), 0)
            ds = [(f[1][dc] if dc is not None else float('inf'), f) for f in fields]
            reach = [x for x in ds if x[0] < float('inf')]
            if not reach:
                eg['units'].append({'unit': u['name'], 'dist': None, 'stairs': 0}); continue
            best = min(reach, key=lambda x: x[0])
            tot = best[0] + inner
            rec = {'unit': u['name'], 'dist': r3(tot), 'corridor': r3(best[0]), 'inUnit': r3(inner), 'stairs': len(reach), 'stair': best[1][0]['stair']['name']}
            eg['units'].append(rec)
            if eg['max'] is None or tot > eg['max']:
                eg['max'] = r3(tot); eg['worst'] = dict(rec, path=[[r3(dp0[0]), r3(dp0[1])]] + _path(R, best[1][2], dc)[::-1] + [[r3(best[1][0]['pt'][0]), r3(best[1][0]['pt'][1])]])
        if eg['units']:
            eg['twoWay'] = all(x['stairs'] >= 2 for x in eg['units'])
        elif fields:
            # 세대가 없는 층(지하 주차장·근생층): 공용부 격자에서 가장 먼 지점 → 가까운 계단
            best_c, best_d, best_f = None, -1, None
            for c in range(R['nx'] * R['ny']):
                if not R['ok'][c]:
                    continue
                dd = min((f[1][c], i) for i, f in enumerate(fields))
                if dd[0] < float('inf') and dd[0] > best_d:
                    best_c, best_d, best_f = c, dd[0], fields[dd[1]]
            if best_c is not None:
                eg['max'] = r3(best_d)
                p0 = [r3(R['x0'] + (best_c % R['nx']) * R['g']), r3(R['y0'] + (best_c // R['nx']) * R['g'])]
                eg['worst'] = {'unit': '공용부 최원점', 'dist': r3(best_d), 'corridor': r3(best_d), 'inUnit': 0, 'stair': best_f[0]['stair']['name'],
                               'path': _path(R, best_f[2], best_c)[::-1] + [[r3(best_f[0]['pt'][0]), r3(best_f[0]['pt'][1])]]}
                eg['worst']['path'][0] = p0
    if len(stair_doors) >= 2:
        pts = [p for p in fl['outline']]
        diag = max(math.hypot(a[0] - b[0], a[1] - b[1]) for a in pts for b in pts)
        sep = max(math.hypot(a['pt'][0] - b['pt'][0], a['pt'][1] - b['pt'][1]) for a in stair_doors for b in stair_doors)
        eg['stairSep'] = {'dist': r3(sep), 'diag': r3(diag), 'need': r3(diag * P['stairSepRatio']), 'ok': sep >= diag * P['stairSepRatio']}
    out['egress'] = eg
    if eg['max'] is not None and eg['max'] > eg['limit']:
        issues.append({'kind': 'fire', 'ref': eg['worst']['unit'], 'level': 'warn', 'discipline': 'fire', 'msg': '%s 보행거리 %s호 %.1fm > %.0fm' % (fl['id'], eg['worst']['unit'], eg['max'], eg['limit'])})
    if eg['twoWay'] is False and level >= 4 and fl['units']:
        one = [x['unit'] for x in eg['units'] if x['stairs'] < 2]
        issues.append({'kind': 'fire', 'ref': one[0], 'level': 'warn', 'discipline': 'fire', 'msg': '%s 계단 하나만 쓸 수 있는 세대 %d곳 (%s…) → 발코니 대피공간·하향식 피난구 필요' % (fl['id'], len(one), ', '.join(one[:3]))})
    if eg['stairSep'] and not eg['stairSep']['ok']:
        eg['effectiveTwoWay'] = False
        issues.append({'kind': 'fire', 'ref': fl['id'], 'level': 'warn', 'discipline': 'fire', 'affects': ['arch', 'zoning'],
                       'msg': '%s 두 직통계단 출입구 이격 %.1fm < 필요 %.1fm (최대 대각선 %.1fm × %s) — 계단 A·B가 붙어 있어 한쪽 화재 시 함께 막힐 수 있음 → 코어 B 위치 조정(조닝) 필요' % (
                           fl['id'], eg['stairSep']['dist'], eg['stairSep']['need'], eg['stairSep']['diag'], '1/3' if abs(P['stairSepRatio'] - 1/3) < 1e-6 else '%.2f' % P['stairSepRatio'])})
    elif eg['twoWay'] is not None:
        eg['effectiveTwoWay'] = eg['twoWay']
    if below and len(stairs) == 1:
        issues.append({'kind': 'fire', 'ref': fl['id'], 'level': 'info', 'discipline': 'fire',
                       'msg': '%s 직통계단 1곳 (주차장은 거실 아님 — 지하층 거실 200㎡ 이상이면 2곳 필요, 확인)' % fl['id']})
    # ---------- 스프링클러 (개략 배치)
    for r in rooms:
        if r['kind'] == 'core' and r.get('corekind') in ('ev', 'shaft', 'stair'):
            continue
        rad = P['sprRadiusUnit'] if r['kind'] == 'unitroom' else (P['sprRadiusParking'] if r['ztype'] in ('parking', 'aisle', 'ramp') else P['sprRadiusOther'])
        sp = rad * math.sqrt(2)
        nx, ny = max(1, math.ceil(r['w'] / sp - 1e-6)), max(1, math.ceil(r['h'] / sp - 1e-6))
        for i in range(nx):
            for j in range(ny):
                out['sprinklers'].append([r3(r['x'] + r['w'] * (i + 0.5) / nx), r3(r['y'] + r['h'] * (j + 0.5) / ny)])
    # ---------- 옥내소화전: 계단 출입 쪽 복도에 두고 25m 포용 확인, 부족하면 추가
    hyd = [{'x': r3(s['entry'][0]), 'y': r3(s['entry'][1]), 'added': False} for s in stair_doors if s['entry']]
    def uncovered():
        pts = [(x, y) for r in rooms if not (r['kind'] == 'core' and r.get('corekind') in ('ev', 'shaft')) for x in (r['x'], r['x'] + r['w']) for y in (r['y'], r['y'] + r['h'])]
        return [p for p in pts if not any(math.hypot(p[0] - h['x'], p[1] - h['y']) <= P['hydrantRadius'] for h in hyd)]
    left = uncovered()
    guard = 0
    while left and guard < 4 and circ:
        guard += 1
        cx = sum(p[0] for p in left) / len(left); cy = sum(p[1] for p in left) / len(left)
        best = min(((math.hypot(r['x'] + r['w'] / 2 - cx, r['y'] + r['h'] / 2 - cy), r) for r in circ), key=lambda t: t[0])[1]
        hyd.append({'x': r3(min(max(cx, best['x'] + 0.3), best['x'] + best['w'] - 0.3)), 'y': r3(min(max(cy, best['y'] + 0.3), best['y'] + best['h'] - 0.3)), 'added': True})
        left = uncovered()
    out['hydrants'] = hyd
    out['hydrantUncovered'] = len(left)
    if left:
        issues.append({'kind': 'fire', 'ref': fl['id'], 'level': 'warn', 'discipline': 'fire', 'msg': '%s 옥내소화전 %.0fm 포용 밖 %d곳' % (fl['id'], P['hydrantRadius'], len(left))})
    # ---------- 피난층: 비상용 승강장 → 외부
    if level == 1:
        vest = next((r for r in rooms if r.get('corekind') == 'vestibule' and '비상' in r['name']), None)
        ext_doors = [o for o in ops if o['kind'] == 'door' and None in wby[o['wall']]['rooms'] and any(x and byid[x]['ztype'] in ('lobby', 'corridor') for x in wby[o['wall']]['rooms'] if x)]
        if vest and circ and ext_doors:
            R = _raster(circ, P['grid'])
            vd = next((o for o in ops if o['kind'] == 'door' and vest['id'] in wby[o['wall']]['rooms'] and any(x and byid[x] in circ for x in wby[o['wall']]['rooms'])), None)
            if vd:
                cr = next(byid[x] for x in wby[vd['wall']]['rooms'] if x and byid[x] in circ)
                d, pv = _dijkstra(R, [(_cell(R, _wall_pt(wby[vd['wall']], vd['at'], 0.25, cr)), 0)])
                best = None
                for o in ext_doors:
                    w = wby[o['wall']]; inside = next(byid[x] for x in w['rooms'] if x)
                    c = _cell(R, _wall_pt(w, o['at'], 0.25, inside))
                    if c is not None and d[c] < float('inf') and (best is None or d[c] < best[0]):
                        best = (d[c], o, c)
                if best:
                    out['fireAccess'] = {'dist': r3(best[0]), 'limit': P['fireAccessMax'], 'ok': best[0] <= P['fireAccessMax'], 'door': best[1]['type'], 'path': _path(R, pv, best[2])[::-1]}
                    if best[0] > P['fireAccessMax']:
                        issues.append({'kind': 'fire', 'ref': '1F', 'level': 'warn', 'discipline': 'fire', 'msg': '1F 비상용 승강장 → 외부 %.1fm > %.0fm' % (best[0], P['fireAccessMax'])})
    # ---------- 검토서(2026-11-17) 지적 항목을 모델에서 직접 계산
    # (1) 세대 안 각 실 → 현관 연결 (문으로만 연결 — 창은 출구 아님)
    disc = []
    for u in fl['units']:
        S = set(u['rooms'])
        ent = next((i for i in u['rooms'] if byid.get(i, {}).get('name') == '현관'), None)
        if not ent:
            continue
        adj = {i: set() for i in S}
        for o in ops:
            if o['kind'] != 'door':
                continue
            a, b = wby[o['wall']]['rooms']
            if a in S and b in S:
                adj[a].add(b); adj[b].add(a)
        walled = {frozenset(w['rooms']) for w in walls if w['rooms'][0] in S and w['rooms'][1] in S}
        ul = list(S)
        for i_ in range(len(ul)):
            for j_ in range(i_ + 1, len(ul)):
                A_, B_ = byid[ul[i_]], byid[ul[j_]]
                sx = min(A_['x'] + A_['w'], B_['x'] + B_['w']) - max(A_['x'], B_['x'])
                sy = min(A_['y'] + A_['h'], B_['y'] + B_['h']) - max(A_['y'], B_['y'])
                if ((abs(sx) < 1e-6 and sy > 0.3) or (abs(sy) < 1e-6 and sx > 0.3)) and frozenset((ul[i_], ul[j_])) not in walled:
                    adj[ul[i_]].add(ul[j_]); adj[ul[j_]].add(ul[i_])   # 벽 없이 트인 경계
        seen = {ent}; st = [ent]
        while st:
            x = st.pop()
            for y in adj[x] - seen:
                seen.add(y); st.append(y)
        miss = [i for i in u['rooms'] if i not in seen]
        if miss:
            disc.append({'unit': u['name'], 'rooms': miss, 'names': [byid[i]['name'] for i in miss]})
    out['unitLinks'] = disc
    if disc:
        issues.append({'kind': 'fire', 'ref': disc[0]['unit'], 'level': 'warn', 'discipline': 'fire', 'affects': ['arch'], 'priority': 'P0',
                       'msg': '%s 세대 안에서 현관까지 문으로 이어지지 않는 실: %s → 실내문 추가 필요 (건축령 제34조, 검토서 F-01)' % (
                           fl['id'], ', '.join('%s호 %s' % (d['unit'], '·'.join(d['names'])) for d in disc))})
    # (2) 복도 유효너비 = 벽 중심 간격 − 양쪽 벽 두께의 절반
    cors = []
    if fl['units']:
        for r in rooms:
            if r['ztype'] != 'corridor':
                continue
            horiz = r['w'] >= r['h']
            span = r['h'] if horiz else r['w']
            sides = {0: [], 1: []}
            for w in walls:
                if r['id'] not in w['rooms']:
                    continue
                (ax, ay), (bx, by) = w['a'], w['b']
                if horiz and abs(ay - by) < 1e-6:
                    k = 0 if abs(ay - r['y']) < 0.05 else 1 if abs(ay - (r['y'] + r['h'])) < 0.05 else None
                elif not horiz and abs(ax - bx) < 1e-6:
                    k = 0 if abs(ax - r['x']) < 0.05 else 1 if abs(ax - (r['x'] + r['w'])) < 0.05 else None
                else:
                    k = None
                if k is not None:
                    other = next((x for x in w['rooms'] if x != r['id']), None)
                    sides[k].append((w['t'], other))
            if not sides[0] or not sides[1]:
                continue
            t0 = max(t for t, _ in sides[0]); t1 = max(t for t, _ in sides[1])
            eff = span - t0 / 2 - t1 / 2
            both = all(any(o and byid.get(o, {}).get('kind') == 'unitroom' for _, o in sides[k]) for k in (0, 1))
            need = P['corridorBoth'] if both else P['corridorOne']
            cors.append({'room': r['id'], 'name': r['name'], 'axis': span, 'eff': r3(eff), 'need': need, 'both': both, 'ok': eff >= need - 1e-6})
        bad = [c for c in cors if not c['ok']]
        if bad:
            issues.append({'kind': 'fire', 'ref': bad[0]['name'], 'level': 'warn', 'discipline': 'fire', 'affects': ['zoning', 'arch'], 'priority': 'P0',
                           'msg': '%s 복도 유효너비 부족: %s → 조닝에서 복도 폭을 %.1fm + 벽 두께로 (검토서 F-03)' % (
                               fl['id'], ', '.join('%s %.2fm < %.1fm' % (c['name'].split(' (')[0], c['eff'], c['need']) for c in bad), max(c['need'] for c in bad))})
    out['corridors'] = cors
    # (3) 계단 유효너비 = 계획 계단 폭 − 계단실 벽 두께의 절반
    sw = []
    for stp in fl.get('stairs', []):
        rr = byid.get(stp['room'])
        tw = max([w['t'] for w in walls if stp['room'] in w['rooms']] or [0.25])
        eff = (stp.get('flightWidth') or 0) - tw / 2
        sw.append({'stair': stp['name'], 'plan': stp.get('flightWidth'), 'wall': tw, 'eff': r3(eff), 'ok': eff >= P['stairWidthMin'] - 1e-6})
    out['stairWidths'] = sw
    if any(not x['ok'] for x in sw):
        issues.append({'kind': 'fire', 'ref': fl['id'], 'level': 'warn', 'discipline': 'fire', 'affects': ['zoning'], 'priority': 'P1',
                       'msg': '%s 계단 유효너비: %s (벽 중심 기준 폭 − 벽 두께/2) < %.1fm → 계단실 폭을 약 %.2fm로 (검토서 F-07)' % (
                           fl['id'], ', '.join('%s %.2fm' % (x['stair'].replace('특별피난계단 ', ''), x['eff']) for x in sw if not x['ok']), P['stairWidthMin'],
                           2 * (P['stairWidthMin'] + max(x['wall'] for x in sw) / 2) + 0.2 + max(x['wall'] for x in sw))})
    # (4) 피난층 계단문은 계단실 밖(피난 방향)으로 열려야 함
    swing = []
    if level == 1:
        for s_ in stairs:
            for o in ops:
                if o['kind'] == 'door' and s_['id'] in wby[o['wall']]['rooms']:
                    swing.append({'door': o['id'], 'stair': s_['name'], 'swingTo': o.get('swingTo'), 'ok': o.get('swingTo') != s_['id']})
        if any(not x['ok'] for x in swing):
            issues.append({'kind': 'fire', 'ref': '1F', 'level': 'warn', 'discipline': 'fire', 'affects': ['arch'], 'priority': 'P0',
                           'msg': '1F(피난층) 계단문 %s이 계단실 안쪽으로 열림 → 부속실 쪽(피난 방향)으로 열리게 (피난규칙 제9조, 검토서 F-05)' % ', '.join(x['stair'] for x in swing if not x['ok'])})
    out['stairDoors'] = swing
    out['summary'] = {'compartments': len(out['compartments']), 'compOver': sum(1 for c in out['compartments'] if not c['ok']), 'sprinklers': len(out['sprinklers']),
                      'hydrants': len(hyd), 'maxTravel': eg['max'], 'travelLimit': eg['limit'], 'twoWay': eg['twoWay'], 'doorChanges': len(out['changes']), 'unitLinks': len(disc), 'corridorBad': sum(1 for c in cors if not c['ok']), 'stairNarrow': sum(1 for x in sw if not x['ok']), 'swingBad': sum(1 for x in swing if not x['ok'])}
    return out, issues, P
