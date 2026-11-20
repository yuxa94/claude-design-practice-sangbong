"""구조 상세 — struct_model 다음에 같은 plan.json 위에서 부재·내진·기초·지하외벽·흙막이·물량을 만듭니다.

- 기둥 일람: 층별 축력(위층 누적) → 단면 (fck, 축응력비), 철근비 1.5%
- 보: 기둥·라멘 층 격자선 위 기둥 사이 (춤 = 경간/12, 1층 외부 조경 슬래브 아래 경간/10), 전이보는 struct_model 결과
- 슬래브: 벽식 210mm (층간소음), 라멘 층 2방향 슬래브 ln/33 (최소 150), 1층 외부(지하 위 조경·소방차) 400mm
- 내진 (KDS 41 17 00, 등가정적): S=0.22 → SDS·SD1, T = Ct·hn^0.75, Cs, 밑면전단력 → 벽식층 벽 전단응력, 전이층 아래 코어벽 전단 (Ω0 증폭)
- 기초: 매트 두께 = 최대 기둥 뚫림전단 (φ(1/3)√fck·b0·d), 지하외벽: 정지토압 + 수압 → 휨 필요 두께, 흙막이(CIP+차수) 개략
- 물량: 콘크리트(㎥)·철근(t, 부재별 원단위)·거푸집(㎡) — 공사비 단계 입력
결과: floors[].struct.{beams, slabT, colSched, members3d}, disciplines.struct.detail
"""
import math
from project_conf import SLUG, ROAD, CONF

r2 = lambda v: round(v + 0.0, 2)
ceil05 = lambda v: math.ceil(v / 0.05 - 1e-9) * 0.05
REBAR = {'벽체': 85, '슬래브': 100, '기둥': 180, '보': 160, '전이보': 250, '매트기초': 110, '지하외벽': 100}   # kg/㎥ (개략 원단위)


def poly_area(p):
    return abs(sum(p[i][0] * p[(i + 1) % len(p)][1] - p[(i + 1) % len(p)][0] * p[i][1] for i in range(len(p)))) / 2


def poly_perim(p):
    return sum(math.hypot(p[(i + 1) % len(p)][0] - p[i][0], p[(i + 1) % len(p)][1] - p[i][1]) for i in range(len(p)))


def inside(pt, poly):
    x, y = pt; ins = False
    for i in range(len(poly)):
        (x1, y1), (x2, y2) = poly[i], poly[(i + 1) % len(poly)]
        if (y1 > y) != (y2 > y) and x < (x2 - x1) * (y - y1) / (y2 - y1) + x1:
            ins = not ins
    return ins


def build(floors, P, ctx):
    order = sorted(floors, key=lambda f: f['level'])
    cols, grid, tf, under = ctx['cols'], ctx['grid'], ctx['tf'], ctx['under']
    wallF = [f for f in order if f['units']]; frameF = [f for f in order if f not in wallF]
    issues = {f['id']: [] for f in floors}
    def add(fl, level, msg, ref=None, aff=None):
        i = {'kind': 'struct', 'ref': ref or fl['id'], 'level': level, 'discipline': 'struct', 'msg': msg}
        if aff:
            i['affects'] = aff
        issues[fl['id']].append(i)
    fck = P['fck']; fy = P.get('fy', 500.0)
    tower = tf['outline'] if tf else None
    # ---------- 기둥 일람 (층별 축력)
    load_of = lambda f: P['loadWall'] if f['units'] else (P['loadBasement'] if f['level'] < 0 else P['loadFrame'])
    sched = []
    for c in cols:
        rows = []
        for f in order:
            if f['id'] not in c['floors']:
                continue
            above = [g for g in order if g['level'] >= f['level'] and ((g['units'] and c['tower']) or g['id'] in c['floors'])]
            w = sum(c['trib'] * load_of(g) for g in above)
            if not any(g['level'] > 0 for g in order if g['id'] in c['floors']):
                w += c['trib'] * (P['loadRoof1F'] - P['loadBasement'])
            pu = w * P['loadFactor']
            side = max(P['colMin'], ceil05(math.sqrt(pu * 1000 / (P['colStressRatio'] * fck)) / 1000))
            rows.append({'floor': f['id'], 'pu': round(pu), 'size': r2(side), 'rebar': r2(side * side * 0.015 * 1e4)})   # 철근 단면적 cm²
        c['sched'] = rows
        sched.append({'id': c['id'], 'grid': c['grid'], 'rows': rows})
    # 층별 기둥 크기를 층 데이터에
    for f in frameF:
        for c in f['struct'].get('columns', []):
            r = next((x for x in c.get('sched', []) if x['floor'] == f['id']), None)
            if r:
                c['sizeAt'] = c.get('sizeAt') or {}
                c['sizeAt'][f['id']] = r['size']
    # ---------- 보 (라멘 층: 그 층 천장 = 윗층 바닥을 받는 보)
    tr_lines = set()
    if under:
        for m in under['struct'].get('transfers', []):
            tr_lines.add((m['axis'], round(m['line'], 1), round(m['from'], 1), round(m['to'], 1)))
    beam_rows = []
    for f in frameF:
        up = next((g for g in order if g['level'] == f['level'] + 1), None)
        fc = [c for c in cols if f['id'] in c['floors']]
        beams = []
        if not up:
            f['struct']['beams'] = beams; continue
        pos = {(c['grid'][0], c['grid'][1]): c for c in fc}
        for (i, j), c in pos.items():
            for di, dj, axis in ((1, 0, 'h'), (0, 1, 'v')):
                n = pos.get((i + di, j + dj))
                if not n:
                    continue
                a, b = (c['gx'], c['gy']), (n['gx'], n['gy'])
                mid = ((a[0] + b[0]) / 2, (a[1] + b[1]) / 2)
                if not inside(mid, up['outline']) and not inside(mid, f['outline']):
                    continue
                span = math.hypot(b[0] - a[0], b[1] - a[1])
                line = a[1] if axis == 'h' else a[0]; lo, hi = sorted((a[0], b[0]) if axis == 'h' else (a[1], b[1]))
                if f is under and any(t[0] == axis and abs(t[1] - line) < 0.4 and t[2] < hi - 0.2 and t[3] > lo + 0.2 for t in tr_lines):
                    continue          # 전이보가 대신함
                outdoor = up['level'] == 1 and not inside(mid, up['outline']) or (up['level'] == 1 and tower and not inside(mid, tower))
                ratio = 10 if outdoor or f['level'] == 0 else 12
                d = max(0.6, ceil05(span / ratio)); w_ = max(0.4, ceil05(d * 0.6))
                beams.append({'a': [r2(a[0]), r2(a[1])], 'b': [r2(b[0]), r2(b[1])], 'span': r2(span), 'd': r2(d), 'w': r2(w_), 'axis': axis, 'tag': '%sG%d' % ('B' if f['level'] < 0 else '', len(beams) + 1), 'outdoor': outdoor})
        f['struct']['beams'] = beams
        if beams:
            beam_rows.append({'floor': f['id'], 'n': len(beams), 'maxSpan': max(b['span'] for b in beams), 'maxD': max(b['d'] for b in beams), 'sizes': sorted({'%d×%d' % (b['w'] * 1000, b['d'] * 1000) for b in beams})})
    # ---------- 슬래브
    slab_rows = []
    for f in order:
        if f['units']:
            t = P['slabT']; kind = '벽식 슬래브 (층간소음 210mm)'
        elif f['level'] < 0 or f['level'] >= 1:
            spans = [b['span'] for b in f['struct'].get('beams', [])] or [P['maxSpan']]
            ln = min(sorted(spans)[len(spans) // 2], P['maxSpan'])
            t = max(0.15, ceil05(ln / 33)); kind = '2방향 슬래브 (보 지지, ln/33)'
        f['struct']['slabT'] = r2(t)
        slab_rows.append({'floor': f['id'], 't': r2(t), 'kind': kind, 'area': r2(poly_area(f['outline']))})
    f1 = next((f for f in order if f['level'] == 1), None)
    b1 = next((f for f in order if f['level'] == -1), None)
    out_area = (poly_area(b1['outline']) - poly_area(f1['outline'])) if (f1 and b1) else 0
    if f1:
        f1['struct']['slabOut'] = {'t': 0.4, 'area': r2(max(0, out_area)), 'why': '지하 위 외부 — 조경 토심·소방차 하중'}
    # ---------- 내진 (등가정적, KDS 41 17 00)
    S = P.get('seisS', 0.22); Fa = P.get('seisFa', 1.38); Fv = P.get('seisFv', 1.96); Ie = P.get('seisIe', 1.2); R = P.get('seisR', 4.0); Om = P.get('seisOmega', 2.5)
    SDS = S * 2.5 * Fa * 2 / 3; SD1 = S * Fv * 2 / 3
    above = [f for f in order if f['level'] > 0]
    hn = sum(f['height'] for f in above)
    T = 0.0488 * hn ** 0.75
    Cs = min(SD1 / (R / Ie * T), SDS / (R / Ie)); Cs = max(Cs, 0.044 * SDS * Ie, 0.01)
    W = sum(poly_area(f['outline']) * load_of(f) for f in above)
    V = Cs * W
    cat = 'D' if (SDS >= 0.50 or SD1 >= 0.20) else 'C'
    # 층 전단력 분포 (k: T 0.5~2.5 보간)
    k = 1.0 if T <= 0.5 else 2.0 if T >= 2.5 else 1 + (T - 0.5) / 2
    z = 0; wh = []
    for f in above:
        z += f['height']; wh.append((f, poly_area(f['outline']) * load_of(f), z))
    swh = sum(w * h ** k for _, w, h in wh)
    story = []
    for f, w, h in wh:
        story.append({'floor': f['id'], 'F': w * h ** k / swh * V})
    cum = 0
    for s in reversed(story):
        cum += s['F']; s['V'] = r2(cum); s['F'] = r2(s['F'])
    rhot = P.get('wallRhoT', 0.005)
    vunit = 0.75 * min(math.sqrt(fck) / 6 + rhot * fy, 5 / 6 * math.sqrt(fck)) * 1000      # kN/㎡ — φ·(√fck/6 + ρt·fy), 상한 (5/6)√fck
    vcap = lambda A: vunit * A
    def wall_area(f, kinds, axis):
        a = 0.0
        for w in f['walls']:
            if w['kind'] not in kinds and not ('shear' in kinds and w.get('shearWall')):
                continue
            horiz = abs(w['a'][1] - w['b'][1]) < 1e-6
            if (axis == 'u') == horiz:
                a += w['len'] * w['t']
        return a
    # ---------- 저층부 전단벽 추가 (전이층 아래 지상 라멘 층): 실내 경계벽 → 인접대지 쪽 외벽 순, 격자선 위 벽만
    shear_rows = []
    gx, gy = grid.get('x', []), grid.get('y', [])
    tsw = P.get('shearWallT', 0.4); target = P.get('shearTarget', 0.9)
    for f in [g for g in frameF if g['level'] > 0]:
        sv = next((x for x in story if x['floor'] == f['id']), None)
        if not sv:
            continue
        need = sv['V'] * Om / (vunit * target)
        added = {'u': [], 'v': []}
        for ax in ('u', 'v'):
            have = wall_area(f, ('core', 'core_inner'), ax)
            cands = []
            for w in f['walls']:
                if w['kind'] in ('core', 'core_inner', 'basement') or w['len'] < 2.0:
                    continue
                horiz = abs(w['a'][1] - w['b'][1]) < 1e-6
                if (ax == 'u') != horiz:
                    continue
                line = w['a'][1] if horiz else w['a'][0]
                if not any(abs(line - g_) < 0.36 for g_ in (gy if horiz else gx)):
                    continue
                if horiz and abs(line) < 0.5:
                    continue                                    # 정면(건축선) 제외
                ops = [o for o in f['openings'] if o['wall'] == w['id']]
                if any(o['kind'] == 'door' for o in ops):
                    continue
                ext = None in w['rooms']
                cands.append((1 if ext else 0, -w['len'], w))
            cands.sort(key=lambda c: (c[0], c[1]))
            for _, _, w in cands:
                if have >= need:
                    break
                w['struct'].update({'bearing': True, 'shear': True}); w['t'] = max(w['t'], tsw)
                w['shearWall'] = True
                # 전단벽 개구부: 벽 길이의 30% 이내로 축소 (커튼월 → 펀칭창) — 계획설계 창호 데이터에 반영
                ops = [o for o in f['openings'] if o['wall'] == w['id']]
                tot_o = sum(o['w'] for o in ops); cap = 0.3 * w['len']
                if tot_o > cap and ops:
                    k_ = cap / tot_o
                    for o in ops:
                        o['wBefore'] = o.get('wBefore', o['w']); o['w'] = r2(max(0.9, o['w'] * k_))
                        if '커튼월' in o['type']:
                            o['type'] = '창 (전단벽 개구부, 커튼월에서 축소)'; o['operation'] = '고정+프로젝트'; o['frame'] = 'AL 창호'
                have += w['len'] * w['t']
                added[ax].append({'wall': w['id'], 'len': w['len'], 'ext': None in w['rooms'], 'a': w['a'], 'b': w['b'],
                                  'rooms': [x for x in w['rooms'] if x]})
            shear_rows.append({'floor': f['id'], 'axis': ax, 'need': r2(need), 'have': r2(have), 'added': len(added[ax]), 'len': r2(sum(a['len'] for a in added[ax])),
                               'extLen': r2(sum(a['len'] for a in added[ax] if a['ext'])), 'ok': have >= need - 1e-6})
        f['struct']['shearWalls'] = added['u'] + added['v']
        ext = [a for a in added['u'] + added['v'] if a['ext']]
        tot = sum(a['len'] for a in added['u'] + added['v'])
        if tot:
            add(f, 'info', '%s 전단벽 추가 %d개 %.0fm (두께 %.0fmm, 실내 경계벽 %.0fm + 인접대지 쪽 외벽 %.0fm) — 코어벽과 함께 지진 전단 부담%s' % (
                f['id'], len(added['u']) + len(added['v']), tot, tsw * 1000, tot - sum(a['len'] for a in ext), sum(a['len'] for a in ext),
                ' → 외벽 커튼월 일부가 RC 벽이 됨 (공공·근생 채광·입면 확인)' if ext else ''), f['id'], ['arch'] if ext else None)
    seis_rows = []
    checks_f = ([tf] if tf else []) + [g for g in frameF if g['level'] > 0]
    for f in checks_f:
        sv = next((s for s in story if s['floor'] == f['id']), None)
        if not sv:
            continue
        kinds = ('exterior', 'core', 'core_inner', 'party', 'unit_corr', 'fire') if f['units'] else ('core', 'core_inner', 'shear')
        amp = 1.0 if f['units'] else Om
        for ax in ('u', 'v'):
            A = wall_area(f, kinds, ax); cap = vcap(A); dem = sv['V'] * 1.0 * amp
            seis_rows.append({'floor': f['id'], 'axis': ax, 'A': r2(A), 'Vu': r2(dem), 'phiVn': r2(cap), 'ratio': r2(dem / cap) if cap else None, 'amp': amp, 'walls': '내력벽 전체' if f['units'] else '코어벽 + 추가 전단벽'})
            if cap and dem > cap:
                add(f, 'warn', '%s %s방향 지진 전단 %.0f kN%s > 벽 전단강도 %.0f kN (벽 %.1f㎡) → %s' % (
                    f['id'], ax, dem, ' (Ω0 %.1f 증폭)' % amp if amp > 1 else '', cap, A,
                    '전단벽 후보(격자선 위 벽) 부족 — 조닝에서 저층부 벽 위치 추가' if not f['units'] else '벽 두께 증가'), f['id'], ['arch', 'zoning'] if not f['units'] else ['arch'])
    if tf and under:
        st_up = min(wall_area(tf, ('exterior', 'core', 'core_inner', 'party', 'unit_corr', 'fire'), a) for a in ('u', 'v'))
        st_dn = min(wall_area(under, ('core', 'core_inner', 'shear'), a) for a in ('u', 'v'))
        soft = r2(st_dn / st_up) if st_up else None
        if soft is not None and soft < 0.7:
            add(under, 'info', '%s 벽 단면적이 위층(%s)의 %.0f%% → 연층(강성 비정형%s) — 동적해석·구조기술사 검토 전제' % (under['id'], tf['id'], soft * 100, ', 60% 미만은 극심한 연층' if soft < 0.6 else ''), under['id'])
    else:
        soft = None
    seismic = {'shear': shear_rows, 'soft': soft, 'vunit': round(vunit), 'rhot': rhot, 'S': S, 'Fa': Fa, 'Fv': Fv, 'SDS': r2(SDS), 'SD1': r2(SD1), 'Ie': Ie, 'R': R, 'Omega': Om, 'cat': cat, 'hn': r2(hn), 'T': r2(T), 'Cs': round(Cs, 4), 'W': round(W), 'V': round(V), 'story': story, 'checks': seis_rows,
               'system': '철근콘크리트 보통전단벽 (내력벽 방식) + 저층부 전이 — 수직 비정형'}
    # ---------- 기초 (매트) — 최대 기둥 뚫림전단
    lowest = order[0]
    pmax = max((c['pu'] for c in cols), default=0); cmax = next((c['size'] for c in cols if c['pu'] == pmax), 1.0)
    Pu = pmax * 1000; cmm = cmax * 1000
    d = 300.0
    while d < 4000:
        b0 = 4 * (cmm + d)
        if 0.75 * math.sqrt(fck) / 3 * b0 * d >= Pu:
            break
        d += 50
    mat_t = max(1.2, math.ceil((d + 100) / 100) / 10)
    found = lowest['struct'].get('foundation') or {}
    found.update({'t': r2(mat_t), 'dReq': r2(d / 1000), 'Pu': round(pmax), 'col': cmax, 'punch': '%.0f kN · b0 %.1fm' % (pmax, 4 * (cmax + d / 1000))})
    lowest['struct']['foundation'] = found
    # ---------- 지하외벽 (정지토압 K0 0.5, γ 19, 지하수위 가정 GL−3m, 수압 10)
    gw = P.get('gwDepth', 3.0); K0 = 0.5; gam = 19.0
    bw_rows = []; depth = 0.0
    for f in sorted([f for f in order if f['level'] < 0], key=lambda f: -f['level']):
        depth += f['height']
        p = K0 * gam * depth + 10 * max(0, depth - gw) + 10 * 0.5     # 상재하중 10kPa
        Mu = 1.6 * p * f['height'] ** 2 / 10                           # kN·m/m (1방향, 상·하 슬래브 지지)
        rho = 0.008; Rn = rho * fy * (1 - 0.59 * rho * fy / fck)       # MPa
        dreq = math.sqrt(Mu * 1e6 / (0.85 * Rn * 1000))                  # mm
        treq = max(0.3, math.ceil((dreq + 70) / 50) * 0.05)
        tcur = max((w['t'] for w in f['walls'] if w['kind'] == 'basement'), default=0.4)
        bw_rows.append({'floor': f['id'], 'depth': r2(depth), 'p': r2(p), 'Mu': r2(Mu), 'tReq': r2(treq), 't': tcur, 'ok': tcur >= treq - 1e-6})
        if tcur < treq - 1e-6:
            add(f, 'warn', '%s 지하외벽 %.0fmm < 필요 %.0fmm (깊이 %.1fm, 토압+수압 %.0fkPa, 지하수위 GL−%.0fm 가정)' % (f['id'], tcur * 1000, treq * 1000, depth, p, gw), f['id'], ['zoning'])
    exc = depth + mat_t + 0.1
    shoring = {'depth': r2(exc), 'type': 'CIP Ø500@450 + SGR 차수 그라우팅, 어스앵커 또는 스트럿 %d단 (약 3m 간격)' % max(1, round(exc / 3)),
               'embed': r2(exc * 0.5), 'perim': r2(poly_perim(lowest['outline'])), 'note': '철도보호지구 → 계측·변위 관리, 기존 허가는 흙막이 지보재·차수공법 변경 고시(S04)',
               'line': [[r2(p[0]), r2(p[1])] for p in lowest['outline']]}
    # ---------- 물량
    Q = {}
    def addq(elem, fl, conc, form):
        q = Q.setdefault(elem, {'conc': 0.0, 'form': 0.0, 'byFloor': {}})
        q['conc'] += conc; q['form'] += form; q['byFloor'][fl] = q['byFloor'].get(fl, 0) + conc
    for f in order:
        st = f['struct']; t = st.get('slabT', 0.21); H = f['height']
        for w in f['walls']:
            if not w.get('struct', {}).get('bearing'):
                continue
            el = '지하외벽' if w['kind'] == 'basement' else '벽체'
            addq(el, f['id'], w['len'] * w['t'] * (H - t), 2 * w['len'] * (H - t))
        if f['level'] > order[0]['level']:
            A = poly_area(f['outline'])
            addq('슬래브', f['id'], A * t, A)
        for c in st.get('columns', []):
            sz = (c.get('sizeAt') or {}).get(f['id'], c['size'])
            addq('기둥', f['id'], sz * sz * (H - t), 4 * sz * (H - t))
        for b in st.get('beams', []):
            dd = max(0, b['d'] - st.get('slabT', 0.2))
            addq('보', f['id'], b['w'] * dd * b['span'], (2 * dd + b['w']) * b['span'])
        for m in st.get('transfers', []):
            addq('전이보', f['id'], 0.9 * m['depth'] * m['span'], (2 * m['depth'] + 0.9) * m['span'])
    if f1 and out_area > 0:
        addq('슬래브', '1F 외부', out_area * 0.4, out_area)
    addq('매트기초', lowest['id'], poly_area(lowest['outline']) * mat_t, poly_perim(lowest['outline']) * mat_t)
    qty = []
    for el, q in Q.items():
        qty.append({'elem': el, 'conc': round(q['conc'], 1), 'rebar': round(q['conc'] * REBAR[el] / 1000, 1), 'form': round(q['form']), 'rate': REBAR[el],
                    'byFloor': {k: round(v, 1) for k, v in q['byFloor'].items()}})
    qty.sort(key=lambda x: -x['conc'])
    gfa = sum(poly_area(f['outline']) for f in order)
    tot = {'conc': round(sum(x['conc'] for x in qty)), 'rebar': round(sum(x['rebar'] for x in qty)), 'form': round(sum(x['form'] for x in qty)), 'gfa': round(gfa)}
    tot['concPerM2'] = round(tot['conc'] / gfa, 3); tot['rebarPerM2'] = round(tot['rebar'] * 1000 / gfa, 1)
    # ---------- 3D용 부재 (층별 컴팩트)
    for f in order:
        st = f['struct']
        st['members3d'] = {'cols': [[c['x'], c['y'], (c.get('sizeAt') or {}).get(f['id'], c['size'])] for c in st.get('columns', [])],
                           'beams': [[b['a'], b['b'], b['w'], b['d']] for b in st.get('beams', [])],
                           'tg': [[m['a'], m['b'], 0.9, m['depth']] for m in st.get('transfers', [])]}
    codes = [
        ('내진', '설계스펙트럼 가속도 S', '0.22 (지진구역 I, 2400년 재현주기)', 'KDS 41 17 00', 'SDS·SD1', '일반 지식 — 확인 필요'),
        ('내진', '지반증폭계수 (S4 가정)', 'Fa %.2f · Fv %.2f — 지반조사 후 확정' % (Fa, Fv), 'KDS 41 17 00 표 (지반종류)', 'SDS %.2f · SD1 %.2f' % (SDS, SD1), '일반 지식 — 확인 필요'),
        ('내진', '중요도계수·반응수정계수', 'Ie %.1f · RC 보통전단벽 R %.1f · Ω0 %.1f' % (Ie, R, Om), 'KDS 41 17 00', 'Cs·전이부 증폭', '일반 지식 — 확인 필요'),
        ('내진', '근사 고유주기', 'Ta = 0.0488 hn^0.75', 'KDS 41 17 00 등가정적', 'T %.2fs' % T, '일반 지식 — 확인 필요'),
        ('내진', '벽 전단강도', 'φVn = 0.75·Acv·(√fck/6 + ρt·fy) ≤ 0.75·(5/6)√fck·Acv, ρt %.3f' % rhot, 'KDS 14 20 22 (벽체 전단)', '코어·전단벽 검토', '일반 지식 — 확인 필요'),
        ('내진', '저층부 전단벽 수요', '층 전단력 × Ω0 (전이 구조 보수적 적용) ÷ 목표 %.1f' % target, 'KDS 41 17 00 (비정형) — 보수적 가정', '추가 전단벽 길이', '가정'),
        ('내진', '전이 구조', '수직 비정형 → 전이보·하부 기둥 특별지진하중(Ω0)', 'KDS 41 17 00 (비정형)', '코어벽 전단 Ω0 증폭', '검토서 인용'),
        ('하중', '활하중', '주거 2.0 · 주차장 3.0 · 상점 4.0~5.0 kN/㎡ (사용하중 개략은 기준값 탭)', 'KDS 41 10 15', '층별 사용하중', '일반 지식 — 확인 필요'),
        ('철근콘크리트', '1방향 전단강도 Vc', '(1/6)√fck · bw · d', 'KDS 14 20 22', '벽 전단 검토', '일반 지식'),
        ('철근콘크리트', '뚫림전단 Vc', '(1/3)√fck · b0 · d (정사각 기둥)', 'KDS 14 20 22', '매트 두께', '일반 지식'),
        ('철근콘크리트', '2방향 슬래브 최소 두께', 'ln/33 내외 (보 지지, fy 500)', 'KDS 14 20 30', '라멘 층 슬래브', '일반 지식 — 확인 필요'),
        ('주택', '바닥 슬래브', '210mm 이상 (벽식)', '주택건설기준 제14조의2 · 바닥충격음 기준', '벽식 층 슬래브', '원문 확인 필요'),
        ('절차', '구조안전 확인', '16층 이상 → 건축구조기술사 협력, 내진능력 공개', '건축법 시행령 제32조·제91조의3', '구조 검토 절차', '일반 지식 — 확인 필요'),
        ('지하', '지하외벽', '정지토압 K0 0.5 · γ 19kN/㎥ · 지하수위 GL−%.0fm 가정 · 상재 10kPa' % gw, 'KDS 11 80 / 지반조사', '지하외벽 두께', '가정'),
        ('지하', '흙막이', '굴착 %.1fm — 지하안전평가 대상 (10~20m 소규모)' % exc, '지하안전법 시행령', 'CIP+차수, 앵커/스트럿', '검토서 인용'),
    ]
    detail = {'colSched': sched, 'beams': beam_rows, 'slabs': slab_rows, 'seismic': seismic, 'foundation': found, 'basementWalls': bw_rows, 'shoring': shoring,
              'qty': qty, 'qtyTotal': tot, 'codes': codes, 'fy': fy}
    return issues, detail
