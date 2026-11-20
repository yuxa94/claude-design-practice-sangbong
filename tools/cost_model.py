"""공사비 모델 — 같은 plan.json의 계획설계·구조·설비·소방 물량 × 단가 → 공종별 공사비 (gen_plan 마지막 단계)

- 물량은 모두 모델에서 셈: 벽·문·창·실 면적·가구(계획설계), 콘크리트·철근·거푸집·흙막이(구조), 배관·덕트·트레이·조명·장비(설비), 헤드·소화전·밸브(소방)
- 단가는 2026년 시세 개략 가정 (disciplineInputs.cost로 덮어씀). 실시설계 내역서·견적으로 바꿔야 함
- 원가 구성: 직접공사비 → 간접공사비(현장관리·안전·보험) → 일반관리비 → 이윤 → 부가세
  (국민주택규모 85㎡ 이하 주택 건설용역은 부가세 면세 → 비주거 면적 비율만 과세)
- 참고 기준: 기본형건축비 2026.09 고시 ㎡당 232.8만원 (분양가상한제용, 비교 지표로만)
"""
import math
from project_conf import SLUG, ROAD, CONF

r0 = lambda v: int(round(v))
r2 = lambda v: round(v + 0.0, 2)

# 키: (공종, 항목, 단위, 단가 원, 근거)
PRICES = {
    'temp':        ('가설공사', '공통가설·안전·타워크레인 (연면적)', '㎡', 65000, '연면적 기준 개략'),
    'excav':       ('토공·흙막이', '터파기 (기계)', '㎥', 18000, ''),
    'haul':        ('토공·흙막이', '잔토 반출·처리', '㎥', 28000, '도심 반출거리 가정'),
    'cip':         ('토공·흙막이', '흙막이 CIP Ø500 (벽면적)', '㎡', 290000, '근입 포함'),
    'anchor':      ('토공·흙막이', '어스앵커 / 스트럿', '개소', 1600000, '2m 간격 × 단수'),
    'sgr':         ('토공·흙막이', '차수 그라우팅 (SGR)', '㎡', 65000, ''),
    'monitor':     ('토공·흙막이', '계측 (철도보호지구 포함)', '식', 180000000, '철도 인접 가산'),
    'conc':        ('철근콘크리트', '레미콘 + 타설', '㎥', 125000, '25~35MPa'),
    'rebar':       ('철근콘크리트', '철근 (자재 + 가공조립)', 't', 1650000, 'SD500 · 2026.07 철근 가격 상승 반영'),
    'form':        ('철근콘크리트', '거푸집 (알폼·유로폼 혼합)', '㎡', 47000, ''),
    'partition':   ('조적·방수·단열', '경량 칸막이벽 (세대 내·공용)', '㎡', 58000, ''),
    'wp_bath':     ('조적·방수·단열', '욕실 방수 (바닥 + 벽 1.8m)', '㎡', 48000, ''),
    'wp_roof':     ('조적·방수·단열', '옥상 방수 (복합)', '㎡', 75000, ''),
    'wp_base':     ('조적·방수·단열', '지하 외벽·기초 방수', '㎡', 58000, ''),
    'insul_wall':  ('조적·방수·단열', '외벽 단열 (에너지 기준)', '㎡', 42000, ''),
    'insul_roof':  ('조적·방수·단열', '옥상·필로티 단열', '㎡', 38000, ''),
    'win_pvc':     ('창호·유리·금속', 'PVC 시스템창 + 로이 복층유리', '㎡', 480000, ''),
    'win_al':      ('창호·유리·금속', 'AL 창호 (공용·비주거)', '㎡', 420000, ''),
    'cw':          ('창호·유리·금속', '커튼월·쇼윈도', '㎡', 950000, ''),
    'd_unit':      ('창호·유리·금속', '세대 현관문 (방화 60분+)', '개', 1150000, ''),
    'd_fire':      ('창호·유리·금속', '방화문 (자동폐쇄 포함)', '개', 950000, ''),
    'd_in':        ('창호·유리·금속', '실내문·욕실문·수납문', '개', 380000, ''),
    'd_auto':      ('창호·유리·금속', '자동문·출입문 (1층)', '개', 6500000, ''),
    'rail':        ('창호·유리·금속', '추락방지 안전난간', 'm', 190000, ''),
    'ext_wall':    ('외장', '외벽 마감 (도장·석재 혼합)', '㎡', 125000, '창 제외 외벽'),
    'fin_unit':    ('내장·마감', '세대 마감 (바닥재·도배·천장·도장)', '㎡', 260000, '세대 면적'),
    'fin_bath':    ('내장·마감', '욕실 타일·천장 (UBR 수준)', '실', 3800000, ''),
    'fin_common':  ('내장·마감', '공용부 마감 (복도·EV홀·로비·계단)', '㎡', 190000, ''),
    'fin_nonres':  ('내장·마감', '근생·공공 기본 마감 (쉘 + 바닥·천장)', '㎡', 230000, '임차 인테리어 별도'),
    'fin_park':    ('내장·마감', '주차장 바닥 (하드너·도장·주차구획)', '㎡', 32000, ''),
    'fin_mep':     ('내장·마감', '기계·전기실 바닥·흡음', '㎡', 60000, ''),
    'kit':         ('가구·위생기구', '주방가구 (상·하부장·인덕션)', '세대', 3200000, ''),
    'wardrobe':    ('가구·위생기구', '붙박이장', '개', 1300000, ''),
    'shoe':        ('가구·위생기구', '신발장', '개', 650000, ''),
    'sani':        ('가구·위생기구', '위생기구 세트 (변기·세면기·샤워·수전)', '세대', 1600000, ''),
    'pipe':        ('기계설비', '배관 (급수·급탕·오수·배수·통기·가스)', 'm', 0, '구경별 단가 = 18,000 + 950 × 구경(mm)'),
    'duct_r':      ('기계설비', '원형 덕트 (세대 환기·배기·후드·연도)', 'm', 38000, ''),
    'duct_x':      ('기계설비', '각형 덕트 (급기가압·주차 배기)', 'm', 0, '단가 = 260,000 × (폭+높이)'),
    'boiler':      ('기계설비', '가스 콘덴싱 보일러', '대', 1350000, ''),
    'hrv':         ('기계설비', '전열교환기 (세대)', '대', 1900000, ''),
    'manifold':    ('기계설비', '분배기·계량기 (+감압밸브)', '세대', 480000, ''),
    'pump_kw':     ('기계설비', '펌프 (급수 부스터·배수)', 'kW', 1700000, '세트·제어반 포함'),
    'tank':        ('기계설비', 'SMC 저수조', '㎥', 950000, ''),
    'fan_kw':      ('기계설비', '송풍기 (주차 배기·옥상 배기)', 'kW', 2300000, ''),
    'jetfan':      ('기계설비', '주차장 유인팬', '대', 2700000, ''),
    'sp_head':     ('소방설비', '스프링클러 헤드 (조기반응형 포함)', '개', 48000, ''),
    'sp_pipe':     ('소방설비', '소화 배관 (SP·소화전·연결송수관)', 'm', 0, '구경별 단가 × 1.1'),
    'alarm':       ('소방설비', '유수검지장치 (알람밸브) 세트', '개소', 3800000, ''),
    'testv':       ('소방설비', '말단 시험장치', '개소', 650000, ''),
    'hydrant':     ('소방설비', '호스릴 옥내소화전함', '개', 1700000, ''),
    'conn_out':    ('소방설비', '연결송수관 방수구·방수기구함·송수구', '개', 1100000, ''),
    'fpump_kw':    ('소방설비', '소화펌프 (주·예비·충압) + 제어반', 'kW', 1900000, ''),
    'fire_tank':   ('소방설비', '소화수조', '㎥', 950000, ''),
    'damper':      ('소방설비', '급기가압 댐퍼 (자동차압)', '개', 1900000, ''),
    'press_kw':    ('소방설비', '급기가압 송풍기', 'kW', 2800000, ''),
    'fire_misc':   ('소방설비', '자동화재탐지·비상방송·유도등·비상조명 (연면적)', '㎡', 30000, 'NFTC 608 연기감지기·세대 확성기'),
    'econ':        ('소방설비', '비상콘센트', '개', 900000, ''),
    'sub_kva':     ('전기·통신', '수변전설비 (수전반·변압기)', 'kVA', 190000, ''),
    'gen_kva':     ('전기·통신', '비상발전기 (디젤)', 'kVA', 470000, ''),
    'lv_kva':      ('전기·통신', '저압 배전반·ATS', 'kVA', 85000, ''),
    'feeder':      ('전기·통신', '층 간선 (F-CV 4C)', 'm', 0, '단가 = 4,500 × 단면적^0.78'),
    'tray':        ('전기·통신', '케이블 트레이', 'm', 48000, ''),
    'ufeed':       ('전기·통신', '세대 간선 (F-CV 10㎟)', 'm', 11000, ''),
    'panel_u':     ('전기·통신', '세대 분전반·통신단자함', '개', 480000, ''),
    'panel_f':     ('전기·통신', '층 분전반', '개', 2600000, ''),
    'light_d':     ('전기·통신', 'LED 조명 (다운라이트·방등)', '개', 65000, ''),
    'light_p':     ('전기·통신', 'LED 주차장등', '개', 120000, ''),
    'elec_misc':   ('전기·통신', '전열·약전·홈네트워크·CCTV·정보통신 (연면적)', '㎡', 52000, '정보통신 2등급 (기존안)'),
    'ev_charger':  ('전기·통신', '전기차 충전기 (완속)', '기', 2600000, ''),
    'lift':        ('승강기', '승객용 승강기 (속도 2.5m/s급)', '대', 165000000, '정지층 수 반영 전'),
    'lift_e':      ('승강기', '비상용 승강기', '대', 200000000, ''),
    'landscape':   ('조경·부대', '조경·외부 포장', '㎡', 190000, '대지 − 1층 바닥'),
    'utility':     ('조경·부대', '인입 (상수·하수·전기·가스·통신)', '식', 450000000, ''),
    'septic':      ('조경·부대', '정화조·오수처리', '인조', 380000, '기존안 1,100인조'),
    'misc':        ('잡공사', '잡철물·실링·코킹·사인·준공청소 등 (직접공사비 비율)', '식', 0, 'miscRate %'),
}
DEFAULTS = {'miscRate': 6.0, 'indirectRate': 17.0, 'gmRate': 5.0, 'profitRate': 5.0, 'vatRate': 10.0, 'priceFactor': 1.0, 'contingency': 3.0,
            'benchmark': 2328000, 'demolitionM2': 120000, 'existingGfa': float(CONF.get('existingGfa') or 0)}


def pipe_price(d):
    return 18000 + 950 * (d or 20)


def plen(pts):
    return sum(abs(b[0] - a[0]) + abs(b[1] - a[1]) + abs(b[2] - a[2]) for a, b in zip(pts, pts[1:]))


def poly_area(p):
    return abs(sum(p[i][0] * p[(i + 1) % len(p)][1] - p[(i + 1) % len(p)][0] * p[i][1] for i in range(len(p)))) / 2


def poly_perim(p):
    return sum(math.hypot(p[(i + 1) % len(p)][0] - p[i][0], p[(i + 1) % len(p)][1] - p[i][1]) for i in range(len(p)))


def analyze(doc, floors, inputs):
    P = dict(DEFAULTS); price = {k: v[3] for k, v in PRICES.items()}
    for k, v in (inputs or {}).items():
        if k in DEFAULTS:
            P[k] = v
        elif k.startswith('p_') and k[2:] in price:
            price[k[2:]] = v
    F = P['priceFactor']
    items = []
    def add(key, qty, src, note='', amount=None, name=None):
        g, n, unit, _, basis = PRICES[key]
        u = price[key] * F
        amt = amount * F if amount is not None else qty * u
        if amt <= 0 and qty <= 0:
            return
        items.append({'key': key, 'grp': g, 'name': name or n, 'unit': unit, 'qty': r2(qty), 'price': r0(u) if amount is None else None, 'amount': r0(amt), 'src': src, 'note': note or basis})
    order = sorted(floors, key=lambda f: f['level'])
    gfa = sum(poly_area(f['outline']) for f in floors)
    gfa_up = sum(poly_area(f['outline']) for f in floors if f['level'] > 0)
    S = (doc.get('disciplines') or {}).get('struct', {}).get('detail') or {}
    M = (doc.get('disciplines') or {}).get('mep', {}).get('detail') or {}
    ML = (doc.get('disciplines') or {}).get('mep', {}).get('loads') or {}
    # ---- 가설
    add('temp', gfa, 'arch', '연면적 %s㎡' % format(r0(gfa), ','))
    # ---- 토공·흙막이
    sh = S.get('shoring') or {}
    base = order[0]
    if sh:
        A = poly_area(base['outline']); vol = A * sh['depth']
        add('excav', vol, 'struct', '지하 바닥 %.0f㎡ × 굴착 %.1fm' % (A, sh['depth']))
        add('haul', vol * 1.25, 'struct', '터파기 × 1.25 (부피 증가)')
        wa = sh['perim'] * (sh['depth'] + sh['embed'])
        add('cip', wa, 'struct', '둘레 %.0fm × (굴착 %.1f + 근입 %.1f)m' % (sh['perim'], sh['depth'], sh['embed']))
        n_lv = max(1, round(sh['depth'] / 3)); add('anchor', math.ceil(sh['perim'] / 2.0) * n_lv, 'struct', '둘레 2m 간격 × %d단' % n_lv)
        add('sgr', wa, 'struct', 'CIP 배면 차수')
        add('monitor', 1, 'struct', '', amount=price['monitor'])
    # ---- 철근콘크리트
    for q in S.get('qty', []):
        pass
    T = S.get('qtyTotal') or {}
    if T:
        add('conc', T['conc'], 'struct', '부재별: ' + ' · '.join('%s %s' % (q['elem'], format(r0(q['conc']), ',')) for q in S.get('qty', [])[:5]))
        add('rebar', T['rebar'], 'struct', '부재별 원단위 (벽 85 · 슬래브 100 · 기둥 180 · 전이보 250 kg/㎥)')
        add('form', T['form'], 'struct', '')
    # ---- 계획설계 물량
    part = sum(w['len'] * (f['height'] - 0.25) for f in floors for w in f['walls'] if w['kind'] in ('partition', 'light') and not w.get('shearWall'))
    add('partition', part, 'arch', 'partition·light 벽 길이 × 층고')
    baths = [r for f in floors for r in f['rooms'] if r['kind'] == 'unitroom' and '욕실' in r['name']]
    add('wp_bath', sum(r['w'] * r['h'] + 2 * (r['w'] + r['h']) * 1.8 for r in baths), 'arch', '욕실 %d실' % len(baths))
    top = max(floors, key=lambda f: f['level']); roof = poly_area(top['outline'])
    f1 = next((f for f in floors if f['level'] == 1), None); b1 = next((f for f in floors if f['level'] == -1), None)
    out_roof = max(0, poly_area(b1['outline']) - poly_area(f1['outline'])) if (f1 and b1) else 0
    add('wp_roof', roof + out_roof, 'arch', '옥상 %.0f㎡ + 지하 위 외부 슬래브 %.0f㎡' % (roof, out_roof))
    depth = sum(f['height'] for f in floors if f['level'] < 0)
    add('wp_base', poly_area(base['outline']) + poly_perim(base['outline']) * depth, 'arch', '기초 바닥 + 지하 외벽')
    ext_area = 0; win = {'pvc': 0, 'al': 0, 'cw': 0}; rail = 0; doors = {'d_unit': 0, 'd_fire': 0, 'd_in': 0, 'd_auto': 0}
    for f in floors:
        if f['level'] < 0:
            continue
        wby = {w['id']: w for w in f['walls']}
        for w in f['walls']:
            if None in w['rooms']:
                ext_area += w['len'] * f['height']
        for o in f['openings']:
            a = o['w'] * o.get('h', 2.1)
            if o['kind'] == 'window':
                if '커튼월' in o['type']:
                    win['cw'] += a
                elif f['units']:
                    win['pvc'] += a
                else:
                    win['al'] += a
                if o.get('rail'):
                    rail += o['w']
                if None in wby.get(o['wall'], {}).get('rooms', [0]):
                    ext_area -= a
            else:
                t = o['type']
                if '승강기' in t:
                    continue
                if t.startswith('세대 현관문'):
                    doors['d_unit'] += 1
                elif '자동' in t or t.startswith('출입문 (' + ROAD):
                    doors['d_auto'] += 1
                elif '방화' in t or o.get('fire'):
                    doors['d_fire'] += 1
                else:
                    doors['d_in'] += 1
    for f in floors:
        if f['level'] < 0:
            for o in f['openings']:
                if o['kind'] == 'door' and '승강기' not in o['type']:
                    doors['d_fire' if ('방화' in o['type'] or o.get('fire')) else 'd_in'] += 1
    add('insul_wall', ext_area, 'arch', '외벽 − 창'); add('insul_roof', roof + out_roof, 'arch', '')
    add('ext_wall', ext_area, 'arch', '창·커튼월 제외 외벽 %s㎡' % format(r0(ext_area), ','))
    add('win_pvc', win['pvc'], 'arch', '세대 창 (창호 일람)'); add('win_al', win['al'], 'arch', '공용·비주거 창'); add('cw', win['cw'], 'arch', '1·2층 커튼월 (전단벽 구간은 축소됨)')
    for k, v in doors.items():
        add(k, v, 'arch' if k != 'd_fire' else 'fire', '방화구획 경계 문 포함 (소방 모듈 지정)' if k == 'd_fire' else '')
    add('rail', rail, 'arch', '창대 0.9m 미만 창')
    units = [u for f in floors for u in f['units']]
    ua = sum(u['area'] for u in units)
    add('fin_unit', ua, 'arch', '%d세대 %s㎡' % (len(units), format(r0(ua), ',')))
    add('fin_bath', len(baths), 'arch', '')
    area_t = lambda ts: sum(r['w'] * r['h'] for f in floors for r in f['rooms'] if r['ztype'] in ts or (r.get('corekind') in ts))
    add('fin_common', area_t(('corridor', 'lobby', 'community', 'stair', 'vestibule')), 'arch', '복도·로비·주민공동·계단·부속실')
    nonres = area_t(('retail', 'public')); add('fin_nonres', nonres, 'arch', '근생·공공 %s㎡' % format(r0(nonres), ','))
    add('fin_park', area_t(('parking', 'aisle', 'ramp')), 'arch', '')
    add('fin_mep', area_t(('mep',)), 'arch', '')
    fx = {}
    for f in floors:
        for x in f.get('fixtures', []):
            fx[x['kind']] = fx.get(x['kind'], 0) + 1
    add('kit', fx.get('counter', 0), 'arch', '주방 카운터 %d' % fx.get('counter', 0))
    add('wardrobe', fx.get('wardrobe', 0), 'arch', ''); add('shoe', fx.get('shoe', 0), 'arch', '')
    add('sani', fx.get('toilet', 0), 'arch', '변기 %d · 세면기 %d · 샤워 %d' % (fx.get('toilet', 0), fx.get('basin', 0), fx.get('shower', 0)))
    # ---- 기계·소방 (설비 모델)
    pamt = {'mech': 0.0, 'fire': 0.0}; plen_ = {'mech': 0.0, 'fire': 0.0}; damt = 0.0; dlen = 0.0; xamt = 0.0; xlen = 0.0
    cnt = {}
    for f in floors:
        m = f.get('mep') or {}
        for p in m.get('pipes', []):
            L = plen(p['pts']); fire = p['sys'] in ('sp', 'hyd', 'conn', 'fire', 'fire2')
            k = 'fire' if fire else 'mech'
            plen_[k] += L; pamt[k] += L * pipe_price(p.get('d') or 25) * (1.1 if fire else 1.0)
        # 입상관 (층고만큼)
        for rs in m.get('risers', []):
            if rs['sys'] in ('feeder', 'comm', 'press', 'exh'):
                continue
            fire = rs['sys'] in ('fire', 'fire2', 'conn'); k = 'fire' if fire else 'mech'
            plen_[k] += f['height']; pamt[k] += f['height'] * pipe_price(rs.get('d') or 100) * (1.1 if fire else 1.0)
        for dd in m.get('ducts', []):
            L = plen(dd['pts'])
            if dd.get('w'):
                xlen += L; xamt += L * 260000 * (dd['w'] + dd.get('h', dd['w']))
            else:
                dlen += L
        for dv in m.get('devices', []):
            cnt[dv['kind']] = cnt.get(dv['kind'], 0) + 1
        cnt['heads'] = cnt.get('heads', 0) + len(m.get('heads', []))
        cnt['light_p'] = cnt.get('light_p', 0) + sum(1 for l in m.get('lights', []) if '주차' in l['kind'])
        cnt['light_d'] = cnt.get('light_d', 0) + sum(1 for l in m.get('lights', []) if '주차' not in l['kind'])
        for t in m.get('trays', []):
            if t['sys'] == 'unitfeed':
                cnt['ufeed'] = cnt.get('ufeed', 0) + plen(t['pts'])
            else:
                cnt['tray'] = cnt.get('tray', 0) + plen(t['pts'])
    add('pipe', plen_['mech'], 'mep', '배관 %s m (입상관 포함)' % format(r0(plen_['mech']), ','), amount=pamt['mech'])
    add('duct_r', dlen, 'mep', '')
    add('duct_x', xlen, 'mep', '', amount=xamt)
    add('boiler', cnt.get('boiler', 0), 'mep', ''); add('hrv', cnt.get('hrv', 0), 'mep', ''); add('manifold', cnt.get('manifold', 0), 'mep', '감압밸브 %d개 층' % len(M.get('prvFloors', [])))
    pumps = M.get('pumps', [])
    pk = sum(p['kw'] * p.get('n', 1) for p in pumps if not p['tag'].startswith(('FP', 'JP')))
    fk = sum(p['kw'] * p.get('n', 1) for p in pumps if p['tag'].startswith(('FP', 'JP')))
    add('pump_kw', pk, 'mep', ' · '.join('%s %.0fkW' % (p['tag'], p['kw']) for p in pumps if not p['tag'].startswith(('FP', 'JP'))))
    tk = M.get('tanks') or {}
    add('tank', tk.get('life', 0), 'mep', '생활용수')
    fans = M.get('fans', [])
    add('fan_kw', sum(x['kw'] for x in fans if x['tag'].startswith(('EF', 'RF'))), 'mep', '주차 배기·옥상 공용배기')
    add('jetfan', sum(x.get('n', 0) for x in fans if x['tag'].startswith('JF')), 'mep', '')
    add('sp_head', cnt.get('heads', 0), 'fire', '')
    add('sp_pipe', plen_['fire'], 'fire', '소화 배관 %s m' % format(r0(plen_['fire']), ','), amount=pamt['fire'])
    add('alarm', cnt.get('alarm', 0), 'fire', ''); add('testv', cnt.get('testv', 0), 'fire', '')
    add('hydrant', cnt.get('hydrant', 0), 'fire', ''); add('conn_out', cnt.get('outlet', 0) + cnt.get('hosebox', 0) + cnt.get('fdc', 0), 'fire', '')
    add('fpump_kw', fk, 'fire', '기준개수 %s' % M.get('sprBasis', '–')); add('fire_tank', tk.get('fire', 0), 'fire', '')
    add('damper', cnt.get('damper', 0), 'fire', ''); add('press_kw', sum(x['kw'] for x in fans if x['tag'].startswith('SF')), 'fire', '')
    add('fire_misc', gfa, 'fire', ''); add('econ', cnt.get('econ', 0), 'fire', '')
    # ---- 전기
    add('sub_kva', (M.get('trL') or 0) + (M.get('trP') or 0), 'mep', 'TR-L %s + TR-P %s kVA' % (M.get('trL'), M.get('trP')))
    add('gen_kva', M.get('gen') or 0, 'mep', '')
    add('lv_kva', (M.get('trL') or 0) + (M.get('trP') or 0), 'mep', '')
    famt = 0; flen = 0
    for x in M.get('feeders', []):
        a = float(x['cable'].split()[1].replace('㎟', '')); famt += x['len'] * 4500 * a ** 0.78; flen += x['len']
    add('feeder', flen, 'mep', '%d개 층' % len(M.get('feeders', [])), amount=famt)
    add('tray', cnt.get('tray', 0), 'mep', ''); add('ufeed', cnt.get('ufeed', 0), 'mep', '')
    add('panel_u', cnt.get('upanel', 0), 'mep', ''); add('panel_f', cnt.get('panel', 0), 'mep', '')
    add('light_d', cnt.get('light_d', 0), 'mep', ''); add('light_p', cnt.get('light_p', 0), 'mep', '')
    add('elec_misc', gfa, 'mep', ''); add('ev_charger', ML.get('chargers', 0), 'mep', '주차 %s면의 5%%' % ML.get('stalls', '–'))
    # ---- 승강기
    ev = max(floors, key=lambda f: len(f.get('elevators', [])))
    evs = ev.get('elevators', [])
    add('lift', sum(1 for e in evs if not e.get('emergency')), 'arch', '%d개 층 운행' % len(floors))
    add('lift_e', sum(1 for e in evs if e.get('emergency')), 'arch', '')
    # ---- 조경·부대
    site = doc.get('site', {}).get('uv') or []
    land = max(0, poly_area(site) - poly_area(f1['outline'])) if (site and f1) else 0
    add('landscape', land, 'arch', '대지 %.0f㎡ − 1층 바닥' % poly_area(site) if site else '')
    add('utility', 1, 'arch', '', amount=price['utility'])
    add('septic', ML.get('persons', 0) * 1.6 + nonres * 0.1, 'mep', '거주 %s인 × 1.6 + 비주거' % ML.get('persons', '–'))
    # ---- 잡공사 (모델에 없는 소항목)
    base_d = sum(it['amount'] for it in items)
    add('misc', 1, 'arch', '직접공사비의 %.0f%%' % P['miscRate'], amount=base_d * P['miscRate'] / 100 / F)
    # ---- 집계
    groups = {}
    for it in items:
        g = groups.setdefault(it['grp'], {'grp': it['grp'], 'amount': 0, 'n': 0})
        g['amount'] += it['amount']; g['n'] += 1
    direct = sum(it['amount'] for it in items)
    cont = direct * P['contingency'] / 100
    indirect = (direct + cont) * P['indirectRate'] / 100
    gm = (direct + cont + indirect) * P['gmRate'] / 100
    profit = (direct + cont + indirect + gm) * P['profitRate'] / 100
    sub = direct + cont + indirect + gm + profit
    res_area = sum(r['w'] * r['h'] for f in floors for r in f['rooms'] if r['kind'] == 'unitroom' or (f['units'] and r['ztype'] in ('corridor', 'community', 'core', 'mep')))
    taxable = nonres / (nonres + res_area) if (nonres + res_area) else 0
    vat = sub * taxable * P['vatRate'] / 100
    total = sub + vat
    pyeong = gfa / 3.305785
    demo = P['existingGfa'] * P['demolitionM2'] * F
    lines = [{'k': '직접공사비', 'amount': r0(direct)}, {'k': '예비비 (%.0f%%)' % P['contingency'], 'amount': r0(cont)},
             {'k': '간접공사비 — 간접노무비·산재·고용보험·안전관리비·기타경비 (%.0f%%)' % P['indirectRate'], 'amount': r0(indirect)},
             {'k': '일반관리비 (%.0f%%)' % P['gmRate'], 'amount': r0(gm)}, {'k': '이윤 (%.0f%%)' % P['profitRate'], 'amount': r0(profit)},
             {'k': '부가세 (비주거 %.1f%%만 과세, 85㎡ 이하 주택 면세)' % (taxable * 100), 'amount': r0(vat)}]
    summary = {'direct': r0(direct), 'total': r0(total), 'perM2': r0(total / gfa), 'perPyeong': r0(total / pyeong), 'perUnit': r0(total / len(units)) if units else None,
               'gfa': r2(gfa), 'gfaUp': r2(gfa_up), 'units': len(units), 'benchmark': P['benchmark'], 'benchRatio': r2(total / gfa / P['benchmark']),
               'taxable': r2(taxable), 'demolition': r0(demo), 'items': len(items),
               'bySrc': {k: r0(sum(it['amount'] for it in items if it['src'] == k)) for k in ('arch', 'struct', 'mep', 'fire')}}
    issues = []
    if total / gfa > P['benchmark'] * 1.3:
        issues.append({'kind': 'cost', 'ref': '공사비', 'level': 'info', 'discipline': 'cost', 'msg': '공사비 ㎡당 %s원 — 기본형건축비(㎡당 %s원)의 %.0f%% (지하 3층·전이구조·철도 인접 흙막이 영향)' % (format(r0(total / gfa), ','), format(P['benchmark'], ','), total / gfa / P['benchmark'] * 100)})
    if demo > 0: issues.append({'kind': 'cost', 'ref': '철거', 'level': 'info', 'discipline': 'cost', 'msg': '기존 건물(연면적 %s㎡) 해체 시 약 %s억원 별도 — 사업 전제 정해지면 반영' % (format(r0(P['existingGfa']), ','), format(round(demo / 1e8, 1), ','))})
    params = dict(P); params.update({'p_' + k: v for k, v in price.items()})
    meta = {k: [v[0], v[1], v[2], v[4]] for k, v in PRICES.items()}
    return {'params': params, 'priceMeta': meta, 'items': items, 'groups': sorted(groups.values(), key=lambda g: -g['amount']), 'lines': lines, 'summary': summary}, issues
