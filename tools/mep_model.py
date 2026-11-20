"""설비 모델 — gen_plan.py가 층을 만들고 소방을 계산한 뒤 같은 plan.json 위에서 계산합니다.

- 샤프트: 코어 EPS·TPS / PS·AD, 설비 구역의 PS·덕트를 계통(전기·배관·덕트)으로 분류하고 위·아래 층 연속성 확인
- 세대 배관: 욕실마다 세대 PS(오수·배수 입상관)를 제안하고, 주방·세탁 배수의 층상 배관 낙차 확인
  → 입상관이 아래 층과 어긋나면 그 층 천장에서 횡주(오프셋) — 3층 세대 입상관은 2층(근생·공공) 천장에서 코어 PS로 모음
- 부하: 세대·용도별 전력 → 수변전 용량, 급수·소화 수량 → 저수조, 지하주차장 환기량 → 덕트 면적
- 결과는 room['mep'], floors[].mep, 이슈(discipline 'mep')로 같은 모델에 기록. 구조 모듈이 2층 천장 배관 공간을 읽어 감
- 기준값은 plan.json › disciplineInputs.mep (설비 페이지에서 수정)
"""
import math, re
import mep_detail
from project_conf import SLUG, ROAD, CONF

DEFAULTS = {
    'personsStudio': 1.0,      # 원룸 거주 인원
    'personsOneHalf': 1.5,     # 1.5룸
    'personsTwo': 2.0,         # 투룸
    'personsThree': 3.0,       # 쓰리룸
    'waterLpd': 250.0,         # 1인 1일 급수량 (L)
    'retailWaterLm2': 10.0,    # 근생·공공 1일 급수 (L/㎡)
    'tankRatio': 0.5,          # 저수조 = 1일 사용량 × (일)
    'tankDepth': 3.0,          # 저수조 유효 수심 (m)
    'tankClear': 1.6,          # 점검 공간 포함 바닥 면적 배수
    'sprHeads': 10,            # 스프링클러 기준 개수 (아파트 10개, NFTC 608 2.3.1.1)
    'sprHeadsParking': 30,     # 주차장과 연결된 경우 주차장 부분 기준개수 (NFTC 608 2.3.1.1)
    'sprM3PerHead': 1.6,       # 헤드 1개당 수원 (80L/min × 20분)
    'hydM3': 2.6,              # 옥내소화전 1개당 (130L/min × 20분)
    'unitKW': 3.0,             # 세대당 전력 (kW)
    'retailWm2': 80.0,         # 근생 (W/㎡)
    'publicWm2': 60.0,         # 공공시설
    'commonWm2': 20.0,         # 공용부·주민공동
    'parkingWm2': 10.0,        # 주차장 (조명·환기)
    'evKW': 15.0,              # 승강기 1대
    'evChargerRate': 5.0,      # 전기차 충전 주차면 비율 (%)
    'chargerKW': 7.0,          # 완속 충전기 1기
    'demandFactor': 0.6,       # 수용률
    'powerFactor': 0.9,
    'drainSlope': 0.02,        # 세대 안 배수 기울기 (1/50)
    'floorDrop': 0.15,         # 층상 배관 허용 낙차 (슬래브 위 공간, m)
    'trapDepth': 0.05,
    'collectorSlope': 0.01,    # 천장 횡주관 기울기 (1/100)
    'plenumBase': 0.35,        # 천장 설비 기본 공간 (덕트·트레이, m)
    'stackTol': 0.6,           # 위·아래 입상관 일치 허용 (m)
    'parkingExhaust': 25.0,    # 지하주차장 배기 (m³/h·㎡)
    'ductVel': 10.0,           # 주덕트 풍속 (m/s)
    'unitAch': 0.5,            # 세대 환기 횟수 (회/h)
    'ceilingH': 2.3,
}
r3 = lambda v: round(v + 0.0, 3)
WET = (('욕실', 'bath'), ('주방', 'kitchen'), ('다용도', 'laundry'), ('세탁', 'laundry'))


def systems_of(name):
    s = []
    if re.search(r'EPS|TPS|전기', name):
        s.append('elec')
    if re.search(r'(?<![ET])PS|배관', name):
        s.append('plumb')
    if re.search(r'AD|DA|덕트', name):
        s.append('duct')
    return s or ['plumb']


def overlap(a, b):
    w = min(a['x'] + a['w'], b['x'] + b['w']) - max(a['x'], b['x'])
    h = min(a['y'] + a['h'], b['y'] + b['h']) - max(a['y'], b['y'])
    return max(0, w) * max(0, h)


def persons(P, t):
    return {'원룸': P['personsStudio'], '1.5룸': P['personsOneHalf'], '투룸': P['personsTwo'], '쓰리룸': P['personsThree']}.get(t, P['personsTwo'])


def analyze_building(floors, inputs, fire_params=None):
    P = dict(DEFAULTS); P.update({k: v for k, v in (inputs or {}).items() if k in DEFAULTS})
    issues = {fl['id']: [] for fl in floors}
    order = sorted(floors, key=lambda f: f['level'])
    below = {order[i]['id']: (order[i - 1] if i else None) for i in range(len(order))}
    above = {order[i]['id']: (order[i + 1] if i + 1 < len(order) else None) for i in range(len(order))}
    def add(fl, level, msg, ref=None, affects=None):
        i = {'kind': 'mep', 'ref': ref or fl['id'], 'level': level, 'discipline': 'mep', 'msg': msg}
        if affects:
            i['affects'] = affects
        issues[fl['id']].append(i)
    # ---------- 샤프트 분류
    for fl in floors:
        out = {'shafts': [], 'wet': [], 'stacks': [], 'collectors': [], 'equipment': [], 'plenum': P['plenumBase'], 'loads': {}}
        for r in fl['rooms']:
            is_shaft = r.get('corekind') == 'shaft' or (r['ztype'] == 'mep' and re.search(r'PS|AD|DA|덕트|EPS', r['name']) and r['w'] * r['h'] < 40 and fl['level'] > 0)
            if is_shaft:
                sy = systems_of(r['name'])
                r['mep'] = {'shaft': True, 'systems': sy}
                out['shafts'].append({'room': r['id'], 'name': r['name'], 'systems': sy, 'x': r['x'], 'y': r['y'], 'w': r['w'], 'h': r['h'], 'area': r3(r['w'] * r['h'])})
        fl['mep'] = out
    for fl in floors:
        dn, up = below[fl['id']], above[fl['id']]
        for s in fl['mep']['shafts']:
            s['below'] = None if not dn else any(overlap(s, t) > 0.3 * min(s['w'] * s['h'], t['w'] * t['h']) for t in dn['mep']['shafts'] + [
                {'x': r['x'], 'y': r['y'], 'w': r['w'], 'h': r['h']} for r in dn['rooms'] if r['ztype'] == 'mep'])
            s['above'] = None if not up else any(overlap(s, t) > 0.3 * min(s['w'] * s['h'], t['w'] * t['h']) for t in up['mep']['shafts'])
    for fl in floors:
        dn = below[fl['id']]
        if not dn:
            continue
        broken = [s for s in fl['mep']['shafts'] if s['below'] is False]
        if broken and fl['level'] > 1:
            add(fl, 'warn', '%s 샤프트 %s가 아래 %s에서 이어지지 않음 → %s 천장에서 횡주 또는 샤프트 위치 맞춤' % (
                fl['id'], ', '.join(s['name'] for s in broken), dn['id'], dn['id']), broken[0]['name'], ['arch', 'zoning'])
    # ---------- 세대 PS 제안 + 배수 낙차
    for fl in floors:
        byid = {r['id']: r for r in fl['rooms']}
        fx_by_room = {}
        for f in fl.get('fixtures', []):
            fx_by_room.setdefault(f['room'], []).append(f)
        for u in fl['units']:
            urs = [byid[i] for i in u['rooms'] if i in byid]
            bath = next((r for r in urs if '욕실' in r['name']), None)
            if not bath:
                add(fl, 'warn', '%s %s호 욕실 없음 — 배관 계획 불가' % (fl['id'], u['name']), u['name'])
                continue
            wets = []
            for r in urs:
                kind = next((k for n, k in WET if n in r['name']), None)
                washer = next((f for f in fx_by_room.get(r['id'], []) if f['kind'] == 'washer'), None)
                if kind and kind != 'bath':
                    wets.append((r, kind, (r['x'] + r['w'] / 2, r['y'] + r['h'] / 2)))
                elif washer and kind != 'bath':
                    wets.append((r, 'laundry', (washer['x'] + washer['w'] / 2, washer['y'] + washer['h'] / 2)))
                if washer and kind in ('kitchen',) and r is not bath:
                    wets.append((r, 'laundry', (washer['x'] + washer['w'] / 2, washer['y'] + washer['h'] / 2)))
            # 입상관: 욕실 모서리 중 주방·세탁과 가장 가까운 곳 (0.6×0.4)
            sw, sh = 0.6, 0.4
            corners = [(bath['x'], bath['y']), (bath['x'] + bath['w'] - sw, bath['y']), (bath['x'], bath['y'] + bath['h'] - sh), (bath['x'] + bath['w'] - sw, bath['y'] + bath['h'] - sh)]
            def cost(c):
                cx, cy = c[0] + sw / 2, c[1] + sh / 2
                return sum(abs(cx - p[0]) + abs(cy - p[1]) for _, _, p in wets) + 0.01 * (cx + cy)
            c = min(corners, key=cost)
            st = {'unit': u['name'], 'room': bath['id'], 'x': r3(c[0]), 'y': r3(c[1]), 'w': sw, 'h': sh}
            fl['mep']['stacks'].append(st)
            bath['mep'] = {'wet': 'bath', 'stack': [st['x'], st['y'], sw, sh]}
            cx, cy = c[0] + sw / 2, c[1] + sh / 2
            fl['mep']['wet'].append({'room': bath['id'], 'unit': u['name'], 'name': bath['name'], 'kind': 'bath', 'x': r3(bath['x'] + bath['w'] / 2), 'y': r3(bath['y'] + bath['h'] / 2),
                                     'run': r3(abs(bath['x'] + bath['w'] / 2 - cx) + abs(bath['y'] + bath['h'] / 2 - cy)), 'drop': None, 'ok': True})
            for r, kind, p in wets:
                run = abs(p[0] - cx) + abs(p[1] - cy)
                drop = run * P['drainSlope'] + P['trapDepth']
                ok = drop <= P['floorDrop'] + 1e-6
                rec = {'room': r['id'], 'unit': u['name'], 'name': r['name'], 'kind': kind, 'x': r3(p[0]), 'y': r3(p[1]), 'run': r3(run), 'drop': r3(drop), 'ok': ok,
                       'path': [[r3(p[0]), r3(p[1])], [r3(cx), r3(p[1])], [r3(cx), r3(cy)]]}
                fl['mep']['wet'].append(rec)
                r.setdefault('mep', {}).update({'wet': kind, 'run': r3(run), 'drop': r3(drop)})
                if not ok:
                    add(fl, 'warn', '%s %s호 %s 배수 %.1fm × %.0f%% + 트랩 = 낙차 %.2fm > 층상 허용 %.2fm → 욕실·주방을 붙이거나 바닥 단차 필요' % (
                        fl['id'], u['name'], r['name'], run, P['drainSlope'] * 100, drop, P['floorDrop']), u['name'], ['arch'])
    # ---------- 입상관 연속성 → 오프셋(횡주)
    for fl in order:
        dn = below[fl['id']]
        if not fl['mep']['stacks'] or not dn:
            continue
        tol = P['stackTol']
        lower = dn['mep']['stacks']
        miss = []
        for st in fl['mep']['stacks']:
            m = next((t for t in lower if abs(t['x'] - st['x']) <= tol and abs(t['y'] - st['y']) <= tol), None)
            st['continuous'] = bool(m)
            if not m:
                miss.append(st)
        if not miss:
            continue
        # 아래 층에서 받을 곳: 아래 층 세대 입상관(같은 세대 천장 → 층하 배관) 또는 코어 배관 샤프트
        targets = [dict(t, k='stack') for t in lower] if lower else []
        shafts = [s for s in dn['mep']['shafts'] if 'plumb' in s['systems'] and s['below'] is not False]
        targets += [dict(s, k='shaft') for s in shafts]
        for st in miss:
            sx, sy = st['x'] + st['w'] / 2, st['y'] + st['h'] / 2
            if not targets:
                continue
            best = min(targets, key=lambda t: abs(t['x'] + t['w'] / 2 - sx) + abs(t['y'] + t['h'] / 2 - sy) if not (t['k'] == 'stack' and dn['units'] == []) else 1e9)
            tx, ty = best['x'] + best['w'] / 2, best['y'] + best['h'] / 2
            L = abs(tx - sx) + abs(ty - sy)
            st['offset'] = {'to': best.get('unit') or best.get('name'), 'len': r3(L)}
            dn['mep']['collectors'].append({'from': st['unit'], 'fromFloor': fl['id'], 'to': best.get('unit') and ('%s호 입상관' % best['unit']) or best['name'], 'len': r3(L),
                                            'drop': r3(L * P['collectorSlope']), 'path': [[r3(sx), r3(sy)], [r3(tx), r3(sy)], [r3(tx), r3(ty)]], 'inUnit': bool(dn['units'])})
        cols = dn['mep']['collectors']
        mx = max(c['drop'] for c in cols)
        dn['mep']['plenum'] = r3(max(dn['mep']['plenum'], P['plenumBase'] + mx + 0.15))
        tot = sum(c['len'] for c in cols)
        if dn['units']:
            add(fl, 'warn', '%s 세대 입상관 %d곳이 %s와 어긋남 (평면 타입 변경) → %s 세대 천장 안 횡주 %.0fm (층하 배관·소음) — 욕실 위치를 아래 타입과 맞추는 것이 좋음' % (
                fl['id'], len(miss), dn['id'], dn['id'], tot), miss[0]['unit'], ['arch', 'zoning'])
        else:
            add(dn, 'warn', '%s 세대 입상관 %d개를 %s 천장에서 코어 PS로 모음 — 횡주 %.0fm, 최장 %.1fm × 1%% → 천장 배관 공간 %.2fm 필요 (구조 전이보와 함께 층고 검토)' % (
                fl['id'], len(miss), dn['id'], tot, max(c['len'] for c in cols), dn['mep']['plenum']), dn['id'], ['arch', 'struct'])
    # ---------- 부하 (건물 전체)
    units = [u for fl in floors for u in fl['units']]
    area = lambda zt: sum(r['w'] * r['h'] for fl in floors for r in fl['rooms'] if r['ztype'] in zt)
    a_ret, a_pub, a_com = area(('retail',)), area(('public',)), area(('corridor', 'lobby', 'community'))
    a_park = area(('parking', 'aisle', 'ramp')) if True else 0
    a_park_b = sum(r['w'] * r['h'] for fl in floors if fl['level'] < 0 for r in fl['rooms'] if r['ztype'] in ('parking', 'aisle', 'ramp'))
    n_ev = len({(e['name']) for fl in floors for e in fl.get('elevators', [])})
    n_ev = max(n_ev, max((len(fl.get('elevators', [])) for fl in floors), default=0))
    stalls = sum(len(fl.get('stalls', [])) for fl in floors)
    chargers = math.ceil(stalls * P['evChargerRate'] / 100)
    pers = sum(persons(P, u.get('unitType')) for u in units)
    kw = {'세대': len(units) * P['unitKW'], '근생': a_ret * P['retailWm2'] / 1000, '공공시설': a_pub * P['publicWm2'] / 1000, '공용·주민공동': a_com * P['commonWm2'] / 1000,
          '주차장': a_park * P['parkingWm2'] / 1000, '승강기': n_ev * P['evKW'], '전기차 충전': chargers * P['chargerKW']}
    conn = sum(kw.values()); dem = conn * P['demandFactor']; kva = dem / P['powerFactor']
    tr = next((t for t in (300, 500, 750, 1000, 1250, 1500, 2000, 2500, 3000, 4000) if t >= kva * 1.2), 4000)
    elec_area = 2.15 * tr ** 0.52
    gen_area = 25 + 0.02 * tr
    daily = pers * P['waterLpd'] / 1000 + (a_ret + a_pub) * P['retailWaterLm2'] / 1000
    max_hyd = max((min(2, len(fl['fire']['hydrants'])) for fl in floors if fl.get('fire')), default=2)
    has_park = any(r['ztype'] in ('parking', 'aisle') for fl in floors for r in fl['rooms'])
    P['_sprBasis'] = max(P['sprHeads'], P['sprHeadsParking'] if has_park else 0)
    fire_w = P['_sprBasis'] * P['sprM3PerHead'] + max_hyd * P['hydM3']
    tank = daily * P['tankRatio'] + fire_w
    b_rooms = [(fl, r) for fl in floors if fl['level'] < 0 for r in fl['rooms'] if r['ztype'] == 'mep']
    tank_room = [(fl, r) for fl, r in b_rooms if '저수조' in r['name']]
    depth = min(P['tankDepth'], min((fl['height'] - 1.0 for fl, _ in tank_room), default=P['tankDepth']))
    tank_area = tank / max(depth, 0.5) * P['tankClear']
    elec_rooms = [(fl, r) for fl, r in b_rooms if '전기' in r['name']]
    mech_rooms = [(fl, r) for fl, r in b_rooms if re.search(r'기계|설비', r['name']) and '배관' not in r['name'] and 'DA' not in r['name']]
    pump_area = 40.0
    need = {'저수조 (생활+소화)': tank_area, '수변전실': elec_area, '발전기실': gen_area, '펌프실 (소화·급수 가압)': pump_area}
    have_tank_elec = sum(r['w'] * r['h'] for _, r in {r['id']: (f, r) for f, r in tank_room + elec_rooms}.values())
    have_mech = sum(r['w'] * r['h'] for _, r in mech_rooms if '전기' not in r['name'] and '저수조' not in r['name'])
    equip = {'rooms': [{'floor': f['id'], 'room': r['id'], 'name': r['name'], 'area': r3(r['w'] * r['h'])} for f, r in {r['id']: (f, r) for f, r in tank_room + elec_rooms + mech_rooms}.values()],
             'need': {k: r3(v) for k, v in need.items()}, 'needTotal': r3(sum(need.values())), 'have': r3(have_tank_elec + have_mech), 'haveTankElec': r3(have_tank_elec)}
    for fl, r in tank_room + elec_rooms + mech_rooms:
        use = [k for k, pat in (('저수조', '저수조'), ('수변전', '전기'), ('기계·펌프', '기계|설비')) if re.search(pat, r['name'])]
        r.setdefault('mep', {})['equipment'] = use
    # 지하주차장 환기
    exh = a_park_b * P['parkingExhaust'] / 3600   # m³/s
    duct_need = 2 * exh / P['ductVel']            # 급기 + 배기
    b1 = next((f for f in floors if f['level'] == -1), None)
    da = [r for r in (b1['rooms'] if b1 else []) if re.search(r'DA|AD|덕트', r['name'])]
    da_area = sum(r['w'] * r['h'] for r in da)
    f1 = next((f for f in floors if f['level'] == 1), None)
    da_out = []
    for r in da:
        if r.get('corekind') == 'shaft':
            continue
        hit = [x for x in (f1['rooms'] if f1 else []) if overlap(r, x) > 0.2 * r['w'] * r['h']]
        da_out.append({'room': r['id'], 'name': r['name'], 'area': r3(r['w'] * r['h']), 'above': [x['name'] for x in hit],
                       'outlet': any(x['ztype'] in ('mep',) or x.get('corekind') == 'shaft' for x in hit)})
    vent = {'parkingArea': r3(a_park_b), 'exhaustM3h': round(exh * 3600), 'ductNeed': r3(duct_need), 'daArea': r3(da_area), 'da': da_out,
            'unitAirflow': r3(sum(u['area'] for u in units) * P['ceilingH'] * P['unitAch'] / len(units)) if units else 0}
    loads = {'kw': {k: r3(v) for k, v in kw.items()}, 'connected': r3(conn), 'demand': r3(dem), 'kva': r3(kva), 'transformer': tr, 'chargers': chargers, 'stalls': stalls,
             'persons': r3(pers), 'units': len(units), 'dailyWater': r3(daily), 'fireWater': r3(fire_w), 'tank': r3(tank), 'tankDepth': r3(depth)}
    lowest = order[0]
    if sum(need.values()) > have_tank_elec + have_mech + 1e-6:
        add(lowest, 'warn', '지하 설비실 부족: 필요 %.0f㎡ (저수조 %.0f · 수변전 %.0f · 발전기 %.0f · 펌프 %.0f) > 확보 %.0f㎡ → 조닝에서 설비 구역 확대' % (
            sum(need.values()), tank_area, elec_area, gen_area, pump_area, have_tank_elec + have_mech), '기계·전기실', ['zoning'])
    if tank_area > sum(r['w'] * r['h'] for _, r in tank_room) + 1e-6 and tank_room:
        add(tank_room[0][0], 'warn', '저수조 %.0fm³ (생활 %.0f×%.1f일 + 소화 %.0f) → 수심 %.1fm·점검공간 포함 %.0f㎡ > 저수조·전기실 %.0f㎡' % (
            tank, daily, P['tankRatio'], fire_w, depth, tank_area, sum(r['w'] * r['h'] for _, r in tank_room)), '저수조·전기', ['zoning'])
    if b1 and duct_need > da_area + 1e-6:
        add(b1, 'warn', '지하주차장 환기 %s m³/h (%.0f㎡ × %.0f) → 급·배기 주덕트 %.1f㎡ 필요 > DA·덕트 %.1f㎡' % (
            format(round(exh * 3600), ','), a_park_b, P['parkingExhaust'], duct_need, da_area), '설비 (DA·배관)', ['zoning'])
    for d in da_out:
        if not d['above']:
            add(b1, 'info', 'B1 %s 위는 1F 건물 밖 대지 → 지상 DA 환기구(높이 2m 이상·보행로와 이격) 자리로 조경·공개공지 배치와 조정' % d['name'], d['name'], ['arch'])
        elif not d['outlet']:
            add(b1, 'warn', 'B1 %s 위 1F가 %s — 주차장 배기·급기가 지상으로 나갈 DA(환기탑·외벽 루버) 자리 없음 → 1F 배치에 DA 필요' % (
                d['name'], ', '.join(d['above']) or '외부'), d['name'], ['arch', 'zoning'])
    add(lowest, 'info', '수변전 %s kVA 변압기 (연결 %.0fkW × 수용률 %.1f ÷ 역률 %.1f = %.0fkVA, 여유 20%%) · 전기차 충전 %d기 포함' % (
        format(tr, ','), conn, P['demandFactor'], P['powerFactor'], kva, chargers), '수변전')
    summary = {'kva': r3(kva), 'transformer': tr, 'tank': r3(tank), 'stacks': sum(len(fl['mep']['stacks']) for fl in floors),
               'offsets': sum(1 for fl in floors for s in fl['mep']['stacks'] if s.get('continuous') is False),
               'collectorLen': r3(sum(c['len'] for fl in floors for c in fl['mep']['collectors'])),
               'drainBad': sum(1 for fl in floors for w in fl['mep']['wet'] if not w['ok']),
               'shaftBroken': sum(1 for fl in floors if fl['level'] > 1 for s in fl['mep']['shafts'] if s['below'] is False),
               'equipNeed': equip['needTotal'], 'equipHave': equip['have'], 'ductNeed': vent['ductNeed'], 'daArea': vent['daArea'],
               'maxPlenum': max((fl['mep']['plenum'] for fl in floors), default=0)}
    for fl in floors:
        m = fl['mep']
        m['summary'] = {'shafts': len(m['shafts']), 'stacks': len(m['stacks']), 'offsets': sum(1 for s in m['stacks'] if s.get('continuous') is False),
                        'collectors': len(m['collectors']), 'drainBad': sum(1 for w in m['wet'] if not w['ok']), 'plenum': m['plenum']}
    di, detail = mep_detail.build(floors, P, {'loads': loads, 'fire': fire_params or {}})
    for k, v in di.items():
        issues[k] += v
    summary.update({'trL': detail['trL'], 'trP': detail['trP'], 'gen': detail['gen'], 'pipeLen': detail['counts']['pipeLen'], 'ductLen': detail['counts']['ductLen'],
                    'devices': detail['counts']['devices'], 'boostKw': detail['boost']['kw'], 'firePumpKw': detail['firePump']['kw']})
    return issues, P, {'loads': loads, 'equipment': equip, 'vent': vent, 'summary': summary, 'detail': detail}
