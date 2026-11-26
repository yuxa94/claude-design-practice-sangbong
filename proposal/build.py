"""인터랙티브 3D 제안서 빌드 — 파이썬만 있으면 됩니다 (추가 설치 없음).

    python3 proposal/build.py          (Windows: py proposal\\build.py)

1. src/extract.py 가 프로젝트의 plan.json 등을 읽어 build/model.json · metrics.json 을 만듭니다.
2. src/slides.html 의 {{ 수치 }} 자리를 계산값으로 채웁니다.
3. 글꼴·three.js·데이터·스크립트를 한 파일로 묶어 proposal/<slug>_제안서.html 로 저장합니다.
"""
import base64, html, json, os, re, runpy, sys

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(HERE, 'src')
VENDOR = os.path.join(HERE, 'vendor')
BUILD = os.path.join(HERE, 'build')

runpy.run_path(os.path.join(SRC, 'extract.py'), run_name='__main__')
rd = lambda p: open(p, encoding='utf-8').read()
model = json.load(open(os.path.join(BUILD, 'model.json'), encoding='utf-8'))
M = json.load(open(os.path.join(BUILD, 'metrics.json'), encoding='utf-8'))


# ------------------------------------------------------------------ 장표에 쓸 값
def derived(M):
    d = {}
    L = M['limits']; sa = M['siteArea']
    d['farCapArea'] = sa * L['far'] / 100
    d['coverageCapArea'] = sa * L['coverage'] / 100
    d['heightMargin'] = (L['height'] - M['height']) if L.get('height') else 0
    up = [f for f in M['floors'] if f['level'] > 0]; dn = [f for f in M['floors'] if f['level'] < 0]
    d['upRange'] = f"1–{len(up)}층"
    d['basementLabel'] = (f"{dn[-1]['id']}–{dn[0]['id']}" if len(dn) > 1 else dn[0]['id']) if dn else ''
    dn_sorted = sorted(dn, key=lambda f: -f['level'])
    d['basementLabel'] = (f"{dn_sorted[0]['id']}–{dn_sorted[-1]['id']}" if len(dn_sorted) > 1 else dn_sorted[0]['id']) if dn else ''
    d['basementIds'] = [f['id'] for f in dn_sorted]
    d['firstBasement'] = dn_sorted[0]['id'] if dn else ''
    d['scale'] = f"지하 {len(dn)}층 · 지상 {len(up)}층" if dn else f"지상 {len(up)}층"
    d['unitMix'] = ' · '.join(f"{k} {v['n']}" for k, v in M['unitTypes'].items() if v['n'])
    d['unitTypeList'] = [{'type': k, **v, **(M['typeReps'].get(k) or {})} for k, v in M['unitTypes'].items() if v['n']]
    d['unitTypeCount'] = len(d['unitTypeList'])
    d['planCount'] = len(M['subTypes'])
    d['stallMargin'] = M['stalls'] - M['stallReqAssumed']
    c = M.get('cost') or {}
    d['hasCost'] = bool(c.get('total'))
    d['costTotalEok'] = c.get('total', 0) / 1e8
    d['costPerPyeongMan'] = c.get('perPyeong', 0) / 1e4
    d['costPerUnitEok'] = c.get('perUnit', 0) / 1e8
    d['costBenchPct'] = c.get('benchRatio', 0) * 100
    fire = M.get('fire') or {}
    d['hasFire'] = bool(fire)
    d['maxTravel'] = fire.get('maxTravel') or 0
    d['travelLimit'] = fire.get('travelLimit') or 0
    d['sprinklers'] = fire.get('sprinklers') or 0
    s = M.get('struct') or {}
    d['transferFloor'] = s.get('transferFloor', '')
    d['columns'] = s.get('columns', 0)
    d['hasLegal'] = bool(M.get('legal'))
    lg = M.get('legal') or {}
    d['legalBad'] = lg.get('bad', 0); d['legalChk'] = lg.get('chk', 0); d['legalTotal'] = lg.get('total', 0)
    d['hasParking'] = bool(M['stalls'])
    d['parkingIds'] = M['parkingFloors']
    d['exclRatioPct'] = M['areas']['exclRatio'] * 100
    d['supplyArea'] = M['areas']['excl'] + M['areas']['resCommon']
    d['podiumGroups'] = [g for g in M['groups'] if g['kind'] == 'podium'][::-1]
    d['resGroups'] = [g for g in M['groups'] if g['kind'] == 'res'][::-1]
    d['typGroupIds'] = next((g['ids'] for g in M['groups'] if M['typFloor'] in g['ids']), [M['typFloor']])
    d['topFloor'] = up[-1]['id'] if up else ''
    d['hasRetail'] = M['retailArea'] > 0
    d['name'] = M['project'].get('name', '')
    d['title'] = re.sub(r'\s*\(실습\)\s*$', '', d['name'])
    d['address'] = M['project'].get('address', '')
    d['site'] = M['project'].get('site') or d['title']
    d['zone'] = M['project'].get('zone', '')
    d['use'] = M['project'].get('use', '')
    for g in M['groups']:
        ids = g['ids']; g['btnIds'] = ids if len(ids) <= 4 else [ids[0], ids[len(ids) // 2], ids[-1]]
    return d


C = {**M, **derived(M)}


def get(path, scope):
    if path == '.':
        return scope.get('.')
    if path.startswith('.'):
        cur = scope.get('.')
        path = path[1:]
    else:
        cur = C
    for k in path.split('.'):
        if k == '':
            continue
        if isinstance(cur, dict):
            cur = cur.get(k)
        elif isinstance(cur, list) and k.isdigit():
            cur = cur[int(k)] if int(k) < len(cur) else None
        else:
            return None
    return cur


def fmt(v, f):
    if v is None:
        return '—'
    try:
        if f in ('n0', 'n1', 'n2'):
            return f"{float(v):,.{int(f[1])}f}"
        if f == 'int':
            return f"{int(round(float(v))):,}"
        if f == 'join':
            return ','.join(map(str, v))
        if f == 'first':
            return str(v[0]) if v else ''
        if f == 'last':
            return str(v[-1]) if v else ''
        if f == 'len':
            return str(len(v))
        if f == 'json':
            return html.escape(json.dumps(v, ensure_ascii=False), quote=True)
        if f == 'pyeong':
            return f"{float(v) / 3.3058:,.1f}"
    except (TypeError, ValueError):
        return str(v)
    if isinstance(v, float):
        return f"{v:,.2f}"
    if isinstance(v, int):
        return f"{v:,}"
    return str(v)


VAR = re.compile(r'\{\{\s*([\w.@]+)\s*(?:\|\s*(\w+)\s*)?\}\}')
TAG = re.compile(r'(\{\{#(?:each|if) [^}]+\}\}|\{\{else\}\}|\{\{/(?:each|if)\}\})')


def parse(src):
    """{{#each}} · {{#if}} · {{else}} 를 겹쳐 써도 되도록 트리로 읽습니다."""
    root = {'body': []}; stack = [root]
    for tok in TAG.split(src):
        if tok.startswith('{{#'):
            kind, arg = tok[3:-2].split(' ', 1)
            node = {'kind': kind, 'arg': arg.strip(), 'body': [], 'else': None}
            stack[-1]['body'].append(node); stack.append(node)
        elif tok == '{{else}}':
            stack[-1]['else'] = []; stack[-1]['cur_else'] = True
        elif tok.startswith('{{/'):
            if len(stack) == 1:
                sys.exit(f'[제안서] slides.html: 여는 태그 없이 {tok} 가 있습니다.')
            stack.pop()
        else:
            n = stack[-1]
            (n['else'] if n.get('cur_else') else n['body']).append(tok)
    if len(stack) > 1:
        sys.exit(f"[제안서] slides.html: {{{{#{stack[-1]['kind']} {stack[-1]['arg']}}}}} 를 닫는 태그가 없습니다.")
    return root['body']


def render_nodes(nodes, scope):
    out = []
    for n in nodes:
        if isinstance(n, str):
            out.append(VAR.sub(lambda m: str(scope.get('@index', 0)) if m.group(1) == '@index' else fmt(get(m.group(1), scope), m.group(2)), n))
        elif n['kind'] == 'each':
            out += [render_nodes(n['body'], {'.': it, '@index': i}) for i, it in enumerate(get(n['arg'], scope) or [])]
        else:
            neg = n['arg'].startswith('!'); v = get(n['arg'].lstrip('!'), scope)
            out.append(render_nodes(n['body'] if bool(v) != neg else (n['else'] or []), scope))
    return ''.join(out)


src = re.sub(r'<!--.*?-->', '', rd(os.path.join(SRC, 'slides.html')), flags=re.S)
slides = render_nodes(parse(src), {})
left = re.findall(r'\{\{[^}]*\}\}', slides)
if left:
    print('[제안서] 채우지 못한 자리:', ', '.join(sorted(set(left))[:10]))

# ------------------------------------------------------------------ 이미지: src="asset:파일이름" → proposal/assets/파일 (png·jpg)
ASSETS = os.path.join(HERE, 'assets')
def asset(m):
    name = m.group(1)
    for ext, mime in (('', None), ('.jpg', 'image/jpeg'), ('.jpeg', 'image/jpeg'), ('.png', 'image/png'), ('.webp', 'image/webp')):
        p = os.path.join(ASSETS, name + ext)
        if os.path.isfile(p):
            mime = mime or {'jpg': 'image/jpeg', 'jpeg': 'image/jpeg', 'png': 'image/png', 'webp': 'image/webp'}.get(name.rsplit('.', 1)[-1].lower(), 'image/png')
            return f'src="data:{mime};base64,' + base64.b64encode(open(p, 'rb').read()).decode() + '"'
    print(f'[제안서] 이미지를 찾지 못했습니다: proposal/assets/{name}')
    return 'src=""'
slides = re.sub(r'src="asset:([^"]+)"', asset, slides)

# ------------------------------------------------------------------ 묶기
fonts = ''.join("@font-face{font-family:Deck;src:url(data:font/woff2;base64,%s) format('woff2');font-weight:%d;font-display:swap}"
                % (base64.b64encode(open(os.path.join(VENDOR, 'fonts', f'Pretendard-{w}.woff2'), 'rb').read()).decode(), w) for w in (300, 400, 600))
three = rd(os.path.join(VENDOR, 'three.min.js')) + '\n' + rd(os.path.join(VENDOR, 'OrbitControls.js'))
title = C['title'] + ' 건축계획 제안'
import hashlib
deck_id = hashlib.sha1((os.path.abspath(os.path.dirname(HERE)) + '|' + M['slug']).encode('utf-8')).hexdigest()[:10]
data = 'const DATA=' + json.dumps({'model': model, 'metrics': M, 'title': title, 'slug': M['slug'], 'deckId': deck_id}, ensure_ascii=False, separators=(',', ':')).replace('</', '<\\/') + ';'
css = rd(os.path.join(SRC, 'base.css')) + '\n' + rd(os.path.join(SRC, 'extra.css'))
shell = rd(os.path.join(SRC, 'shell.html')).replace('<!--TITLE-->', html.escape(title))
rc = os.path.join(HERE, 'review_comments.json')
if os.path.exists(rc):
    shell = shell.replace('{"comments":[],"seq":0}', rd(rc).replace('<', '\\u003c'))
out_html = (shell.replace('/*FONTS*/', fonts).replace('/*CSS*/', css).replace('<!--SLIDES-->', slides)
            .replace('/*THREE*/', three.replace('</script', '<\\/script')).replace('/*DATA*/', data)
            .replace('/*SCENE*/', rd(os.path.join(SRC, 'scene.js'))).replace('/*APP*/', rd(os.path.join(SRC, 'app.js')))
            .replace('/*COMMENTS*/', rd(os.path.join(SRC, 'comments.js'))))
OUT = os.path.join(HERE, f"{M['slug'] or 'project'}_제안서.html")
with open(OUT, 'w', encoding='utf-8') as fp:
    fp.write(out_html)
n = len(re.findall(r'<section class="slide', slides))
print(f'[제안서] {n}장 · {os.path.getsize(OUT) / 1e6:.2f}MB → {os.path.relpath(OUT, os.path.dirname(HERE))}')
