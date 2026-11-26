"""프로젝트 JSON → 제안서용 데이터 (build/model.json, build/metrics.json).

프로젝트 폴더(이 proposal/ 폴더의 상위)에 있는 파일을 읽기만 합니다.
  project.json · plan/<slug>_plan.json (필수)
  site/<slug>_context.json · legal/<slug>_legal.json · legal/<slug>_reference.json
  zoning/<slug>_zoning.json · workflow.json (있으면 사용)
"""
import collections, json, os, sys

HERE = os.path.dirname(os.path.abspath(__file__))
PROPOSAL = os.path.dirname(HERE)
ROOT = os.environ.get('PROJECT_ROOT') or os.path.dirname(PROPOSAL)
OUT = os.path.join(PROPOSAL, 'build')
os.makedirs(OUT, exist_ok=True)


def load(*parts, required=False):
    p = os.path.join(ROOT, *parts)
    if not os.path.exists(p):
        if required:
            sys.exit(f'[제안서] 파일이 없습니다: {p}\n  → 계획설계를 먼저 만들었는지 확인하세요 ("계획설계 만들어줘").')
        return None
    with open(p, encoding='utf-8') as f:
        return json.load(f)


proj = load('project.json') or {}
slug = proj.get('slug')
if not slug:
    cands = [f[:-10] for f in os.listdir(os.path.join(ROOT, 'plan')) if f.endswith('_plan.json')] if os.path.isdir(os.path.join(ROOT, 'plan')) else []
    slug = cands[0] if cands else ''
plan = load('plan', f'{slug}_plan.json', required=True)
ctx = load('site', f'{slug}_context.json') or {'buildings': [], 'roads': [], 'rail': [], 'parcels': [], 'zoning': [], 'labels': []}
legal = load('legal', f'{slug}_legal.json') or {}
ref = load('legal', f'{slug}_reference.json') or {}
zon = load('zoning', f'{slug}_zoning.json') or {}
wf = load('workflow.json') or {}

r2 = lambda v: round(float(v), 2)


def area(p):
    s = 0
    for i in range(len(p)):
        x1, y1 = p[i]; x2, y2 = p[(i + 1) % len(p)]
        s += x1 * y2 - x2 * y1
    return abs(s) / 2


ZT = ['unit', 'core', 'corridor', 'mep', 'retail', 'public', 'lobby', 'community', 'parking', 'aisle', 'ramp']
WK = ['exterior', 'basement', 'core', 'core_inner', 'party', 'unit_corr', 'fire', 'partition', 'light']
UT = ['원룸', '1.5룸', '투룸', '쓰리룸']
FXK = ['shoe', 'counter', 'fridge', 'shower', 'toilet', 'basin', 'table', 'table_s', 'bed_s', 'bed_d', 'washer', 'wardrobe', 'sofa', 'sofa_s', 'tv', 'shelf']
types_by_key = {t['key']: t for t in plan.get('types', [])}
names, name_ix = [], {}


def nid(s):
    if s not in name_ix:
        name_ix[s] = len(names); names.append(s)
    return name_ix[s]


def ut_ix(t):
    if t not in UT:
        UT.append(t)
    return UT.index(t)


floors, fmetrics = [], []
for f in plan['floors']:
    wall_ix = {w['id']: i for i, w in enumerate(f.get('walls', []))}
    unit_of_room = {}
    for ui, u in enumerate(f.get('units', [])):
        for rid in u['rooms']:
            unit_of_room[rid] = ui
    rooms, units = [], []
    zarea = collections.defaultdict(float)
    for r in f.get('rooms', []):
        zt = 'unit' if r.get('kind') == 'unitroom' else r.get('ztype') or 'corridor'
        if zt not in ZT:
            zt = 'corridor'
        rooms.append([r2(r['x']), r2(r['y']), r2(r['w']), r2(r['h']), ZT.index(zt), nid(r.get('name', '')), unit_of_room.get(r['id'], -1)])
        if zt != 'unit':
            zarea[zt] += r['w'] * r['h']
    rid = {r['id']: r for r in f.get('rooms', [])}
    for u in f.get('units', []):
        rs = [rid[x] for x in u['rooms'] if x in rid] or [{'x': 0, 'y': 0, 'w': 0, 'h': 0}]
        x0 = min(r['x'] for r in rs); y0 = min(r['y'] for r in rs)
        x1 = max(r['x'] + r['w'] for r in rs); y1 = max(r['y'] + r['h'] for r in rs)
        units.append([u['name'], ut_ix(u['unitType']), r2(u['area']), r2(x0), r2(y0), r2(x1 - x0), r2(y1 - y0), types_by_key.get(u.get('type'), {}).get('code', '')])
    walls = [[r2(w['a'][0]), r2(w['a'][1]), r2(w['b'][0]), r2(w['b'][1]), r2(w['t']), WK.index(w['kind']) if w['kind'] in WK else 7,
              1 if (w.get('struct') or {}).get('bearing') else 0] for w in f.get('walls', [])]
    ops = [[wall_ix[o['wall']], r2(o['at']), r2(o['w']), r2(o['h']), r2(o.get('sill', 0)), 1 if o['kind'] == 'window' else 0]
           for o in f.get('openings', []) if o['wall'] in wall_ix]
    stairs = []
    for s in f.get('stairs', []):
        for fl in s.get('flights', []):
            stairs.append([r2(fl['x']), r2(fl['y']), r2(fl['w']), r2(fl['h']), fl['risers'], r2(fl['z0']), r2(fl['z1']), fl['dir'], s.get('axis', 'v')])
    evs = [[r2(e['x']), r2(e['y']), r2(e['w']), r2(e['h']), 1 if e.get('emergency') else 0] for e in f.get('elevators', [])]
    stalls = [[r2(s['x']), r2(s['y']), r2(s['w']), r2(s['h']), 1 if '장애인' in s.get('type', '') else (2 if '확장' in s.get('type', '') else 0)] for s in f.get('stalls', [])]
    ramps = [[r2(r['x']), r2(r['y']), r2(r['w']), r2(r['h']), r['axis'], r2(r['sStart']), r2(r['sEnd']), r2(r['relHigh']), r2(r['relLow'])] for r in f.get('ramps', [])]
    cols_src = (f.get('struct') or {}).get('columns', [])
    cols = [[r2(c['x']), r2(c['y']), r2((c.get('sizeAt') or {}).get(f['id'], c.get('size', 0.8)))] for c in cols_src]
    utc = collections.Counter(u['unitType'] for u in f.get('units', []))
    floors.append({'id': f['id'], 'nm': f.get('name', f['id']), 'lv': f['level'], 'h': f['height'], 'el': f['elev'],
                   'pt': f.get('planType') or '', 'ol': [[r2(x), r2(y)] for x, y in f['outline']],
                   'rm': rooms, 'wl': walls, 'op': ops, 'st': stairs, 'ev': evs, 'sl': stalls, 'rp': ramps, 'co': cols, 'un': units,
                   'fx': [[r2(x['x']), r2(x['y']), r2(x['w']), r2(x['h']), r2(x.get('h3', .8)), FXK.index(x['kind']) if x['kind'] in FXK else 6] for x in f.get('fixtures', [])]})
    fs = (f.get('fire') or {}).get('summary') or {}
    fmetrics.append({
        'id': f['id'], 'name': f.get('name', f['id']), 'level': f['level'], 'height': f['height'], 'elev': f['elev'],
        'planType': f.get('planType') or '', 'area': r2(area(f['outline'])),
        'zones': {k: r2(v) for k, v in zarea.items()},
        'units': len(f.get('units', [])), 'unitTypes': {k: utc.get(k, 0) for k in UT},
        'unitArea': r2(sum(u['area'] for u in f.get('units', []))),
        'stalls': len(f.get('stalls', [])), 'columns': len(cols_src),
        'sprinklers': fs.get('sprinklers'), 'compartments': fs.get('compartments'),
        'maxTravel': fs.get('maxTravel'), 'issues': len(f.get('issues', [])),
    })

cb = [{'n': b.get('name', ''), 'h': b.get('height') or 6.6, 'f': b.get('floors'), 'on': b.get('onSite', False),
       'p': [[r2(x), r2(y)] for x, y in b['pts']]} for b in ctx.get('buildings', [])]
site = plan.get('site', {})
model = {
    'names': names, 'zt': ZT, 'ut': UT, 'fxk': FXK,
    'site': [[r2(x), r2(y)] for x, y in site.get('uv', [])],
    'env': [[r2(x), r2(y)] for x, y in site.get('envelopeUv', site.get('uv', []))],
    'bline': (plan.get('context') or {}).get('bline', []),
    'ctx': {
        'bld': cb,
        'roads': [{'n': r.get('name', ''), 'k': r.get('kind', ''), 'p': [[r2(x), r2(y)] for x, y in r['pts']]} for r in ctx.get('roads', [])],
        'rail': [{'n': r.get('name', ''), 'p': [[r2(x), r2(y)] for x, y in r['pts']]} for r in ctx.get('rail', [])],
        'parcels': [[[r2(x), r2(y)] for x, y in p['pts']] for p in ctx.get('parcels', [])],
        'zoning': [{'n': z.get('name', ''), 'p': [[r2(x), r2(y)] for x, y in z['pts']]} for z in ctx.get('zoning', [])],
        'labels': ctx.get('labels', []),
    },
    'floors': floors,
}

# ---------------------------------------------------------------- 요약 수치
up = [m for m in fmetrics if m['level'] > 0]
dn = [m for m in fmetrics if m['level'] < 0]
up_by_id = {m['id']: m for m in up}
ut = collections.Counter(); ua = collections.defaultdict(float)
for f in plan['floors']:
    for u in f.get('units', []):
        ut[u['unitType']] += 1; ua[u['unitType']] += u['area']
gfa_up = sum(m['area'] for m in up); gfa_dn = sum(m['area'] for m in dn)
site_area = (plan.get('project') or {}).get('siteAreaOfficial') or proj.get('siteAreaOfficial') or area(site.get('uv', [[0, 0]])) or 1
retail = sum(m['zones'].get('retail', 0) for m in up)
nonres = sum(m['zones'].get(k, 0) for m in up for k in ('retail', 'public'))
top = max((m['elev'] + m['height'] for m in up), default=0)
units_total = sum(ut.values())
f1 = up_by_id.get('1F') or (up[0] if up else None)
# 기준층 = 세대가 가장 많은 지상층 (같으면 아래층)
typ = max(up, key=lambda m: (m['units'], -m['level'])) if up else None
max_fp = max(up, key=lambda m: m['area']) if up else None
lim = proj.get('limits') or {}
dp = proj.get('districtPlan') or {}
prog = proj.get('program') or {}
stall_req = units_total * prog.get('parkingPerUnit', 0.3) + retail / prog.get('retailM2PerStall', 134)

# 층 묶음: 연속한 지상층 중 층 유형·세대수·외곽 면적·층고가 같은 층끼리
groups = []
for m in sorted(up, key=lambda m: m['level']):
    key = (m['planType'], m['units'], round(m['area']), m['height'], tuple(sorted(m['zones'])))
    if groups and groups[-1]['key'] == key and groups[-1]['ids'][-1] == f"{m['level'] - 1}F":
        groups[-1]['ids'].append(m['id'])
    else:
        groups.append({'key': key, 'ids': [m['id']]})
GROUPS = []
for g in groups:
    ms = [up_by_id[i] for i in g['ids']]; m0 = ms[0]
    label = g['ids'][0] if len(g['ids']) == 1 else f"{g['ids'][0][:-1]}–{g['ids'][-1]}"
    if m0['units']:
        mix = ' · '.join(f'{k} {v}' for k, v in m0['unitTypes'].items() if v)
        sub = f"{m0['planType'] or '주거층'} · {m0['units']}세대/층"
    else:
        zs = sorted(m0['zones'].items(), key=lambda kv: -kv[1])
        ZK = {'retail': '근린생활시설', 'public': '공공시설', 'lobby': '로비', 'community': '주민공동시설', 'ramp': '램프', 'core': '코어', 'corridor': '복도', 'mep': '설비'}
        mix = ' · '.join(ZK.get(k, k) for k, _ in zs[:3])
        sub = mix
    GROUPS.append({'label': label, 'ids': g['ids'], 'sub': sub, 'mix': mix, 'h': m0['height'], 'area': m0['area'], 'units': m0['units'], 'kind': 'res' if m0['units'] else 'podium'})
if dn:
    ids = [m['id'] for m in sorted(dn, key=lambda m: -m['level'])]
    GROUPS.append({'label': ids[0] if len(ids) == 1 else f'{ids[0]}–{ids[-1]}', 'ids': ids, 'sub': '주차·설비', 'mix': f"주차 {sum(m['stalls'] for m in dn)}면",
                   'h': dn[0]['height'], 'area': max(m['area'] for m in dn), 'units': 0, 'kind': 'basement'})
GROUPS.reverse()  # 위층부터

disc = plan.get('disciplines') or {}
dsum = lambda k: (disc.get(k) or {}).get('summary') or {}
metrics = {
    'project': {**(plan.get('project') or {}), **{k: proj[k] for k in ('name', 'address', 'zone', 'use', 'site') if k in proj}},
    'slug': slug,
    'limits': {'coverage': lim.get('coverage', 60), 'far': lim.get('far', 960), 'farBase': lim.get('farBase', lim.get('far', 960)), 'height': lim.get('height', 0)},
    'districtPlan': dp,
    'siteArea': site_area,
    'floorsUp': len(up), 'floorsDown': len(dn), 'height': r2(top),
    'gfa': r2(gfa_up + gfa_dn), 'gfaUp': r2(gfa_up), 'gfaDown': r2(gfa_dn),
    'far': r2(gfa_up / site_area * 100),
    'typFloor': typ['id'] if typ else '', 'typArea': typ['area'] if typ else 0,
    'buildingArea': max_fp['area'] if max_fp else 0, 'buildingAreaFloor': max_fp['id'] if max_fp else '',
    'coverage1F': r2(f1['area'] / site_area * 100) if f1 else 0,
    'coverageTyp': r2(typ['area'] / site_area * 100) if typ else 0,
    'coverageMax': r2(max_fp['area'] / site_area * 100) if max_fp else 0,
    'units': units_total,
    'unitTypes': {k: {'n': ut[k], 'avg': r2(ua[k] / ut[k]) if ut[k] else 0, 'sum': r2(ua[k])} for k in UT},
    'stalls': sum(m['stalls'] for m in fmetrics),
    'stallReqAssumed': r2(stall_req),
    'retailArea': r2(retail),
    'nonResZones': r2(nonres),
    'nonResRatio': r2(nonres / gfa_up * 100) if gfa_up else 0,
    'groups': GROUPS,
    'floors': fmetrics,
    'fire': dsum('fire'), 'mep': dsum('mep'), 'struct': dsum('struct'), 'cost': dsum('cost'),
    'mepLoads': {k: v for k, v in ((disc.get('mep') or {}).get('loads') or {}).items() if not isinstance(v, (dict, list))},
    'costGroups': (disc.get('cost') or {}).get('groups', []),
    'legal': legal.get('summary') if isinstance(legal.get('summary'), dict) else None,
    'legalItems': (legal.get('summary') or {}).get('items', []) if isinstance(legal.get('summary'), dict) else [],
    'issues': dict(collections.Counter(i.get('kind', '') for i in plan.get('issues', []))),
    'built': (ref.get('built') or {}).get('rows', []),
    'review': {'verdict': (ref.get('review') or {}).get('verdict', ''), 'fire': (ref.get('review') or {}).get('fire', [])},
    'workflow': [{'no': st['no'], 'id': st['id'], 'title': st['title'], 'status': st.get('status', 'planned'), 'outputs': st.get('outputs', [])} for st in wf.get('stages', [])],
}
f1_ev = next((f.get('elevators', []) for f in plan['floors'] if f1 and f['id'] == f1['id']), [])
metrics['elevators'] = len(f1_ev)
metrics['emergencyElevators'] = sum(1 for e in f1_ev if e.get('emergency'))
metrics['stairs'] = max((len(f.get('stairs', [])) for f in plan['floors']), default=0)
cm = lambda c: {'n': c.get('n'), 'status': c.get('status', ''), 'view': c.get('view', ''), 'text': c.get('text', ''), 'reply': c.get('reply', '')}
metrics['comments'] = {'zoning': [cm(c) for c in zon.get('comments', [])], 'plan': [cm(c) for c in plan.get('comments', [])]}
metrics['counts'] = {'zones': sum(len(f.get('zones', [])) for f in zon.get('floors', [])), 'rooms': sum(len(f.get('rooms', [])) for f in plan['floors']),
    'walls': sum(len(f.get('walls', [])) for f in plan['floors']), 'doors': sum(1 for f in plan['floors'] for o in f.get('openings', []) if o['kind'] == 'door'),
    'windows': sum(1 for f in plan['floors'] for o in f.get('openings', []) if o['kind'] == 'window'), 'types': len(plan.get('types', [])), 'typeEdits': len(plan.get('typeEdits', [])),
    'issues': len(plan.get('issues', []))}

# ---------------------------------------------------------------- 세대 평면 (대표 세대 = 그 평면이 놓인 층 중 가운데 층)
lvl = {f['id']: f['level'] for f in plan['floors']}
occ = collections.defaultdict(list)
for f in plan['floors']:
    for u in f.get('units', []):
        occ[types_by_key.get(u.get('type'), {}).get('code', '') or u['unitType']].append((f, u))
subs = []
for code, lst in occ.items():
    lst.sort(key=lambda fu: lvl[fu[0]['id']])
    f, u = lst[len(lst) // 2]
    rid = {r['id']: r for r in f['rooms']}; wall = {w['id']: w for w in f['walls']}
    rs = [rid[r] for r in u['rooms'] if r in rid]
    x0 = min(r['x'] for r in rs); y0 = min(r['y'] for r in rs); x1 = max(r['x'] + r['w'] for r in rs); y1 = max(r['y'] + r['h'] for r in rs)
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    win = collections.defaultdict(float)
    for o in f.get('openings', []):
        w = wall.get(o['wall'])
        if not w or o['kind'] != 'window' or w['kind'] != 'exterior': continue
        mx, my = (w['a'][0] + w['b'][0]) / 2, (w['a'][1] + w['b'][1]) / 2
        if not (x0 - .3 <= mx <= x1 + .3 and y0 - .3 <= my <= y1 + .3): continue
        hz = abs(w['a'][1] - w['b'][1]) < .01
        win[('남' if my < cy else '북') if hz else ('서' if mx < cx else '동')] += o['w']
    fx = collections.Counter(x['kind'] for x in f.get('fixtures', []) if x.get('room') in u['rooms'])
    fl = sorted({fu[0]['id'] for fu in lst}, key=lambda i: lvl[i])
    subs.append({'code': code, 'type': u['unitType'], 'n': len(lst), 'avg': r2(sum(fu[1]['area'] for fu in lst) / len(lst)),
                 'floors': fl[0] + ('–' + fl[-1] if len(fl) > 1 else ''),
                 'rep': {'floor': f['id'], 'name': u['name'], 'area': r2(u['area']), 'W': r2(x1 - x0), 'D': r2(y1 - y0),
                         'rooms': [[r.get('name', ''), r2(r['w'] * r['h'])] for r in rs], 'win': {k: r2(v) for k, v in win.items()}, 'fx': dict(fx)}})
order = {t: i for i, t in enumerate(UT)}
subs.sort(key=lambda e: (order.get(e['type'], 99), e['code']))
metrics['subTypes'] = subs
# 타입별 대표 = 가장 많이 쓰인 평면의 대표 세대
metrics['typeReps'] = {}
for t in UT:
    c = [s for s in subs if s['type'] == t]
    if c:
        s = max(c, key=lambda s: s['n'])
        metrics['typeReps'][t] = {'code': s['code'], 'floor': s['rep']['floor'], 'unit': s['rep']['name'], 'variants': len(c)}

# ---------------------------------------------------------------- 전용 / 공용
rows = []; res_common_total = 0; excl_total = 0
for m in fmetrics:
    z = m['zones']; lv = m['level']; excl = m['unitArea']
    if lv < 0:
        common = comm = nr = 0
    elif m['units']:
        common = z.get('core', 0) + z.get('corridor', 0) + z.get('mep', 0) + z.get('lobby', 0); comm = z.get('community', 0)
        nr = z.get('retail', 0) + z.get('public', 0) + z.get('ramp', 0)
    else:
        common = z.get('lobby', 0) + z.get('core', 0) + z.get('corridor', 0) + z.get('mep', 0); comm = z.get('community', 0)
        nr = z.get('retail', 0) + z.get('public', 0) + z.get('ramp', 0)
    other = m['area'] if lv < 0 else 0
    rows.append({'id': m['id'], 'excl': r2(excl), 'common': r2(common), 'comm': r2(comm), 'nonres': r2(nr), 'other': r2(other), 'area': m['area']})
    res_common_total += common + comm; excl_total += excl
nonres_total = sum(r['nonres'] for r in rows); other_total = sum(r['other'] for r in rows)
den = excl_total + res_common_total + nonres_total
res_share = (excl_total + res_common_total) / den if den else 1
kc = res_common_total / excl_total if excl_total else 0
ko = other_total * res_share / excl_total if excl_total else 0
metrics['areas'] = {'rows': rows, 'excl': r2(excl_total), 'resCommon': r2(res_common_total), 'nonres': r2(nonres_total),
                    'other': r2(other_total), 'kc': round(kc, 4), 'ko': round(ko, 4), 'resShare': round(res_share, 4),
                    'exclRatio': round(1 / (1 + kc), 4) if excl_total else 0}

# ---------------------------------------------------------------- 주차
pk = {}
for f in sorted(plan['floors'], key=lambda f: -f['level']):
    if not f.get('stalls'): continue
    c = collections.Counter(s.get('type', '').split(' ')[0] for s in f['stalls'])
    pk[f['id']] = {'total': len(f['stalls']), '일반형': c.get('일반형', 0), '확장형': c.get('확장형', 0), '장애인': c.get('장애인', 0)}
metrics['parking'] = pk
metrics['parkingFloors'] = list(pk)

with open(os.path.join(OUT, 'model.json'), 'w', encoding='utf-8') as fp:
    json.dump(model, fp, ensure_ascii=False, separators=(',', ':'))
with open(os.path.join(OUT, 'metrics.json'), 'w', encoding='utf-8') as fp:
    json.dump(metrics, fp, ensure_ascii=False, indent=1)

if __name__ == '__main__':
    print(f"[제안서] {metrics['project'].get('name', slug)} · 지상 {len(up)}층 / 지하 {len(dn)}층 · 세대 {units_total} · 주차 {metrics['stalls']}면 · "
          f"연면적 {metrics['gfa']:,.2f}㎡ · 용적률 {metrics['far']}% · 기준층 {metrics['typFloor']}")
