#!/usr/bin/env python3
"""조닝·계획설계 편집기용 로컬 서버.

- 프로젝트 폴더를 http://127.0.0.1:8765/ 로 제공합니다 (이 컴퓨터에서만 접속 가능).
- 편집기가 POST /api/zoning (조닝), /api/plan (계획설계) 으로 보내는 JSON을 zoning/<SLUG>_zoning.json 에 저장하고,
  zoning/history/ 에 최근 50개 버전을 남깁니다.
- GET /api/zoning 은 저장된 JSON을 돌려줍니다.
"""
import json, os, sys, time, glob, subprocess, threading, importlib.util
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler
from project_conf import SLUG, ROAD, CONF

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ZDIR = os.path.join(ROOT, 'zoning')
ZFILE = os.path.join(ZDIR, SLUG + '_zoning.json')
HIST = os.path.join(ZDIR, 'history')
SCHEMA = 'sangbong-zoning/1'
DOCS = {  # 경로: (파일, 스키마, 기록 폴더)
    '/api/zoning': (ZFILE, 'sangbong-zoning/1', HIST),
    '/api/plan': (os.path.join(ROOT, 'plan', SLUG + '_plan.json'), 'sangbong-plan/1', os.path.join(ROOT, 'plan', 'history')),
    '/api/legal': (os.path.join(ROOT, 'legal', SLUG + '_legal.json'), 'sangbong-legal/1', os.path.join(ROOT, 'legal', 'history')),
}
RFILE = os.path.join(ROOT, 'requests', 'requests.json')   # 모든 페이지 공통 수정 요청 (아래 가운데 요청 바)
MAX_BYTES = 10 * 1024 * 1024
KEEP = 50
_last_hist = {}
PFILE = DOCS['/api/plan'][0]
GEN = os.path.join(ROOT, 'tools', 'gen_plan.py')
_regen_lock = threading.Lock()


_gen = {'mtime': None, 'mod': None}


def gen_module():
    mt = os.path.getmtime(GEN)
    if _gen['mtime'] != mt:
        spec = importlib.util.spec_from_file_location('gen_plan_live', GEN)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        _gen.update(mtime=mt, mod=mod)
    return _gen['mod']


_auto = {'timer': None, 'busy': False, 'last': None, 'err': None}


def schedule_auto_regen(delay=2.5):
    """조닝이 저장되면 잠시 뒤 계획설계를 자동으로 다시 생성 (연속 저장은 한 번으로 묶음).
    실습: 계획설계를 처음 만드는 것은 Claude에게 요청하는 단계라, 계획설계 파일이 생긴 뒤부터만 자동으로 다시 만듭니다."""
    if not os.path.exists(PFILE):
        return
    if _auto['timer']:
        _auto['timer'].cancel()

    def run():
        _auto['busy'] = True
        try:
            with _regen_lock:
                regen()
            _auto['last'] = time.strftime('%H:%M:%S'); _auto['err'] = None
        except Exception as e:
            _auto['err'] = str(e)
        finally:
            _auto['busy'] = False
    _auto['timer'] = threading.Timer(delay, run)
    _auto['timer'].daemon = True
    _auto['timer'].start()


_sum = {'key': None, 'val': None}


def build_summary():
    """허브 페이지용 요약 (파일이 바뀔 때만 다시 계산)."""
    def mt(p):
        return os.path.getmtime(p) if os.path.exists(p) else 0
    LFILE = DOCS['/api/legal'][0]
    key = (mt(ZFILE), mt(PFILE), mt(LFILE), mt(RFILE))
    if _sum['key'] == key:
        return _sum['val']
    out = {'ok': True, 'zoning': None, 'plan': None, 'history': {}}
    try:
        with open(ZFILE, encoding='utf-8') as f:
            z = json.load(f)
        zones = [q for fl in z['floors'] for q in fl['zones']]
        out['zoning'] = {'updated': z.get('updated'), 'floors': len(z['floors']), 'zones': len(zones),
                         'units': sum(1 for q in zones if q['type'] == 'unit'),
                         'openComments': sum(1 for c in z.get('comments', []) if c.get('status') != 'done'),
                         'comments': len(z.get('comments', [])), 'bytes': os.path.getsize(ZFILE)}
    except Exception as e:
        out['zoning'] = {'error': str(e)}
    try:
        with open(PFILE, encoding='utf-8') as f:
            p = json.load(f)
        fl = p.get('floors', [])
        cnt = lambda k: sum(len(x.get(k, [])) for x in fl)
        ops = [o for x in fl for o in x.get('openings', [])]
        iss = p.get('issues', [])
        out['plan'] = {'generated': p.get('generated'), 'zoningUpdated': (p.get('source') or {}).get('zoningUpdated'),
                       'rooms': cnt('rooms'), 'walls': cnt('walls'), 'doors': sum(1 for o in ops if o['kind'] == 'door'),
                       'windows': sum(1 for o in ops if o['kind'] == 'window'), 'stairs': cnt('stairs'), 'elevators': cnt('elevators'),
                       'fixtures': cnt('fixtures'), 'stalls': cnt('stalls'), 'units': sum(len(x.get('units', [])) for x in fl),
                       'types': len(p.get('types', [])), 'typesEdited': sum(1 for t in p.get('types', []) if t.get('edited')),
                       'warn': sum(1 for i in iss if i.get('level') == 'warn' and i.get('discipline', 'arch') == 'arch'),
                       'info': sum(1 for i in iss if i.get('level') == 'info' and i.get('discipline', 'arch') == 'arch'),
                       'disciplines': {k: (v or {}).get('summary') for k, v in (p.get('disciplines') or {}).items()},
                       'conflicts': sum(1 for c in p.get('conflicts', []) if not c.get('acked')),
                       'openComments': sum(1 for c in p.get('comments', []) if c.get('status') != 'done'),
                       'comments': len(p.get('comments', [])), 'bytes': os.path.getsize(PFILE)}
    except Exception as e:
        out['plan'] = {'error': str(e)}
    try:
        with open(LFILE, encoding='utf-8') as f:
            out['legal'] = (json.load(f).get('summary') or {})
    except Exception:
        out['legal'] = None
    try:
        with open(RFILE, encoding='utf-8') as f:
            rq = json.load(f).get('requests', [])
        out['requests'] = {}
        for r in rq:
            if r.get('status') != 'done':
                out['requests'][r.get('page')] = out['requests'].get(r.get('page'), 0) + 1
    except Exception:
        out['requests'] = {}
    for k, d in (('zoning', HIST), ('plan', DOCS['/api/plan'][2])):
        files = sorted(glob.glob(os.path.join(d, '*.json')), key=os.path.getmtime)
        out['history'][k] = {'count': len(files), 'last': time.strftime('%Y-%m-%dT%H:%M:%S', time.localtime(os.path.getmtime(files[-1]))) if files else None}
    _sum.update(key=key, val=out)
    return out


def regen():
    r = subprocess.run([sys.executable, GEN], cwd=ROOT, capture_output=True, text=True, timeout=120)
    if r.returncode != 0:
        raise RuntimeError((r.stderr or r.stdout).strip().splitlines()[-1] if (r.stderr or r.stdout) else 'gen_plan 실패')
    return r.stdout.strip()


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *a, **kw):
        super().__init__(*a, directory=ROOT, **kw)

    def log_message(self, fmt, *args):
        sys.stderr.write('[zoning] ' + (fmt % args) + '\n')

    def _json(self, code, obj):
        body = json.dumps(obj, ensure_ascii=False).encode('utf-8')
        self.send_response(code)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Cache-Control', 'no-store')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def end_headers(self):
        if not self.path.startswith('/api/'):
            self.send_header('Cache-Control', 'no-store')
        super().end_headers()

    def do_GET(self):
        path = self.path.split('?')[0]
        if path == '/api/requests':
            if not os.path.exists(RFILE):
                return self._json(200, {'schema': 'design-requests/1', 'requests': []})
            with open(RFILE, encoding='utf-8') as f:
                return self._json(200, json.load(f))
        if path == '/api/summary':
            return self._json(200, build_summary())
        if path == '/api/status':
            st = {'ok': True, 'regenerating': _auto['busy'], 'lastAuto': _auto['last'], 'autoError': _auto['err']}
            try:
                with open(ZFILE, encoding='utf-8') as f:
                    st['zoningUpdated'] = json.load(f).get('updated')
                with open(PFILE, encoding='utf-8') as f:
                    p = json.load(f)
                st.update(planGenerated=p.get('generated'), planZoning=(p.get('source') or {}).get('zoningUpdated'),
                          conflicts=sum(1 for c in p.get('conflicts', []) if not c.get('acked')))
            except Exception as e:
                st['error'] = str(e)
            return self._json(200, st)
        if path in DOCS:
            fpath = DOCS[path][0]
            if not os.path.exists(fpath):
                return self._json(404, {'ok': False, 'error': 'not saved yet'})
            with open(fpath, 'rb') as f:
                body = f.read()
            self.send_response(200)
            self.send_header('Content-Type', 'application/json; charset=utf-8')
            self.send_header('Cache-Control', 'no-store')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        return super().do_GET()

    def do_POST(self):
        path = self.path.split('?')[0]
        if path == '/api/requests':
            return self.save_requests()
        if path == '/api/plan/ack':
            return self.plan_ack()
        if path == '/api/plan/preview':
            return self.plan_preview()
        if path == '/api/plan/comments':
            return self.plan_comments()
        if path in ('/api/plan/typeedit', '/api/plan/regen', '/api/plan/inputs'):
            return self.plan_action(path)
        if path not in DOCS:
            return self._json(404, {'ok': False, 'error': 'unknown endpoint'})
        fpath, schema, hist = DOCS[path]
        n = int(self.headers.get('Content-Length') or 0)
        if n <= 0 or n > MAX_BYTES:
            return self._json(413, {'ok': False, 'error': 'empty or too large'})
        try:
            doc = json.loads(self.rfile.read(n).decode('utf-8'))
            if doc.get('schema') != schema or (path in ('/api/zoning', '/api/plan') and not isinstance(doc.get('floors'), list)):
                raise ValueError('schema mismatch')
        except Exception as e:
            return self._json(400, {'ok': False, 'error': str(e)})
        os.makedirs(hist, exist_ok=True)
        tmp = fpath + '.tmp'
        compact = path in ('/api/plan',)
        with open(tmp, 'w', encoding='utf-8') as f:
            json.dump(doc, f, ensure_ascii=False, indent=None if compact else 1, separators=(',', ':') if compact else None)
        os.replace(tmp, fpath)
        now = time.time()
        if now - _last_hist.get(path, 0) > 60:  # 최대 1분에 한 번 버전 기록
            _last_hist[path] = now
            stamp = time.strftime('%Y%m%d_%H%M%S')
            base = path.rsplit('/', 1)[-1]
            with open(os.path.join(hist, f'{base}_{stamp}.json'), 'w', encoding='utf-8') as f:
                json.dump(doc, f, ensure_ascii=False, separators=(',', ':'))
            for old in sorted(glob.glob(os.path.join(hist, f'{base}_*.json')))[:-KEEP]:
                os.remove(old)
        if path == '/api/zoning':
            schedule_auto_regen()
        return self._json(200, {'ok': True, 'path': os.path.relpath(fpath, ROOT), 'saved': time.strftime('%H:%M:%S')})


    def save_requests(self):
        """공통 수정 요청: {op:'add', item} | {op:'update', id, patch} | {op:'delete', id}"""
        n = int(self.headers.get('Content-Length') or 0)
        try:
            body = json.loads(self.rfile.read(n).decode('utf-8'))
            with _regen_lock:
                doc = {'schema': 'design-requests/1', 'requests': []}
                if os.path.exists(RFILE):
                    with open(RFILE, encoding='utf-8') as f:
                        doc = json.load(f)
                R = doc['requests']; op = body.get('op')
                if op == 'add':
                    it = body['item']
                    if not isinstance(it.get('text'), str) or not it['text'].strip():
                        raise ValueError('text 없음')
                    it['n'] = max([r.get('n', 0) for r in R] + [0]) + 1
                    it.setdefault('status', 'open'); it.setdefault('created', time.strftime('%Y-%m-%dT%H:%M:%S'))
                    if not any(r.get('id') == it.get('id') for r in R):
                        R.append(it)
                elif op == 'update':
                    for r in R:
                        if r.get('id') == body.get('id'):
                            r.update({k: v for k, v in (body.get('patch') or {}).items() if k in ('status', 'text', 'reply', 'resolvedAt', 'reopened')})
                elif op == 'delete':
                    doc['requests'] = [r for r in R if r.get('id') != body.get('id')]
                else:
                    raise ValueError('op 오류')
                os.makedirs(os.path.dirname(RFILE), exist_ok=True)
                tmp = RFILE + '.tmp'
                with open(tmp, 'w', encoding='utf-8') as f:
                    json.dump(doc, f, ensure_ascii=False, indent=1)
                os.replace(tmp, RFILE)
            return self._json(200, {'ok': True, 'requests': doc['requests']})
        except Exception as e:
            return self._json(400, {'ok': False, 'error': str(e)})

    def plan_ack(self):
        n = int(self.headers.get('Content-Length') or 0)
        try:
            body = json.loads(self.rfile.read(n).decode('utf-8'))
            cid, on = body.get('id'), body.get('ack', True)
            with _regen_lock:
                with open(PFILE, encoding='utf-8') as f:
                    doc = json.load(f)
                ack = set(doc.get('conflictAck') or [])
                (ack.add if on else ack.discard)(cid)
                doc['conflictAck'] = sorted(ack)
                for c in doc.get('conflicts', []):
                    if c['id'] == cid:
                        c['acked'] = bool(on)
                for fl in doc.get('floors', []):
                    fl['issues'] = [i for i in fl['issues'] if not (on and i.get('cid') == cid)]
                tmp = PFILE + '.tmp'
                with open(tmp, 'w', encoding='utf-8') as f:
                    json.dump(doc, f, ensure_ascii=False, separators=(',', ':'))
                os.replace(tmp, PFILE)
            return self._json(200, {'ok': True})
        except Exception as e:
            return self._json(400, {'ok': False, 'error': str(e)})

    def plan_preview(self):
        """편집 중인 타입 배치로 대표 층 하나만 다시 계산해 벽·문·창 미리보기를 돌려줌 (저장 안 함)."""
        n = int(self.headers.get('Content-Length') or 0)
        try:
            body = json.loads(self.rfile.read(n).decode('utf-8'))
            key, rooms = body.get('key'), body.get('rooms')
            if not isinstance(key, str) or not isinstance(rooms, list) or not rooms:
                raise ValueError('key/rooms 형식 오류')
            with _regen_lock:
                with open(PFILE, encoding='utf-8') as f:
                    doc = json.load(f)
            T = next((t for t in doc.get('types', []) if t['key'] == key), None)
            if not T:
                raise ValueError('타입 없음')
            I = T['instances'][0]
            with open(ZFILE, encoding='utf-8') as f:
                zf = next(f2 for f2 in json.load(f)['floors'] if f2['id'] == I['floor'])
            m = gen_module()
            m.EDITS.clear(); m.EDITS.update(doc.get('typeEdits') or {}); m.EDITS[key] = {'rooms': rooms, 'doors': body.get('doors') or {}}; m.TYPES.clear(); m.CONFLICTS.clear()
            res = m.build_floor(zf, 0.0)
            ids = {r['id'] for r in res['rooms'] if r['zone'] == I['zone']}
            walls = [w for w in res['walls'] if w['rooms'][0] in ids or w['rooms'][1] in ids]
            wid = {w['id'] for w in walls}
            need = ids | {x for w in walls for x in w['rooms'] if x}
            names = {r['name'] for r in res['rooms'] if r['id'] in ids} | {I['name']}
            out = {'id': zf['id'], 'rooms': [r for r in res['rooms'] if r['id'] in need], 'walls': walls,
                   'openings': [o for o in res['openings'] if o['wall'] in wid],
                   'stairs': [x for x in res['stairs'] if x['room'] in ids], 'elevators': [x for x in res['elevators'] if x['room'] in ids],
                   'fixtures': [x for x in res.get('fixtures', []) if x['room'] in ids]}
            iss = [c['msg'] for c in m.CONFLICTS if c.get('unit') == I['name']] + [i['msg'][len(I['name']) + 1:] if i['msg'].startswith(I['name'] + ' ') else i['msg'] for i in res['issues'] if i.get('level') != 'info' and (i.get('ref') in names)]
            return self._json(200, {'ok': True, 'floor': out, 'issues': iss})
        except Exception as e:
            return self._json(400, {'ok': False, 'error': str(e)})

    def plan_comments(self):
        """수정 요청(comments)만 저장 — 화면이 오래된 계획을 들고 있어도 재생성 결과를 덮어쓰지 않음."""
        n = int(self.headers.get('Content-Length') or 0)
        try:
            body = json.loads(self.rfile.read(n).decode('utf-8'))
            cm = body.get('comments')
            if not isinstance(cm, list):
                raise ValueError('comments 형식 오류')
            with _regen_lock:
                with open(PFILE, encoding='utf-8') as f:
                    doc = json.load(f)
                old = {c.get('id'): c for c in doc.get('comments', [])}
                for c in cm:  # Claude가 단 답변·완료 표시는 오래된 화면이 덮어쓰지 않게 유지
                    o = old.get(c.get('id'))
                    if o and o.get('reply') and not c.get('reply'):
                        c['reply'] = o['reply']
                        if o.get('status') == 'done' and c.get('status') != 'done' and not c.get('reopened'):
                            c['status'] = 'done'; c['resolvedAt'] = o.get('resolvedAt')
                doc['comments'] = cm
                tmp = PFILE + '.tmp'
                with open(tmp, 'w', encoding='utf-8') as f:
                    json.dump(doc, f, ensure_ascii=False, separators=(',', ':'))
                os.replace(tmp, PFILE)
            return self._json(200, {'ok': True, 'path': os.path.relpath(PFILE, ROOT), 'generated': doc.get('generated')})
        except Exception as e:
            return self._json(400, {'ok': False, 'error': str(e)})

    def plan_action(self, path):
        """타입 편집 저장(typeEdits) 후 계획설계 재생성. body: {key, rooms|null}"""
        n = int(self.headers.get('Content-Length') or 0)
        try:
            body = json.loads(self.rfile.read(n).decode('utf-8')) if n else {}
            with _regen_lock:
                with open(PFILE, encoding='utf-8') as f:
                    doc = json.load(f)
                hist = DOCS['/api/plan'][2]
                os.makedirs(hist, exist_ok=True)
                stamp = time.strftime('%Y%m%d_%H%M%S')
                if path == '/api/plan/typeedit':
                    key = body.get('key')
                    rooms = body.get('rooms')
                    if not isinstance(key, str) or not key:
                        raise ValueError('key 없음')
                    if rooms is not None:
                        if not isinstance(rooms, list) or not rooms:
                            raise ValueError('rooms 형식 오류')
                        for r in rooms:
                            if not isinstance(r.get('name'), str) or not all(isinstance(r.get(k), (int, float)) for k in ('t0', 't1', 'd0', 'd1')):
                                raise ValueError('room 형식 오류')
                    with open(os.path.join(hist, f'plan_{stamp}_before_typeedit.json'), 'w', encoding='utf-8') as f:
                        json.dump(doc, f, ensure_ascii=False, separators=(',', ':'))
                    te = doc.get('typeEdits') or {}
                    if rooms is None:
                        te.pop(key, None)
                    else:
                        te[key] = {'rooms': rooms, 'at': time.strftime('%Y-%m-%dT%H:%M:%S'), 'by': '사용자'}
                        doors = body.get('doors')
                        if isinstance(doors, dict) and doors:
                            te[key]['doors'] = {str(k): {'pos': [float(v['pos'][0]), float(v['pos'][1])]} for k, v in doors.items()}
                    doc['typeEdits'] = te
                    tmp = PFILE + '.tmp'
                    with open(tmp, 'w', encoding='utf-8') as f:
                        json.dump(doc, f, ensure_ascii=False, separators=(',', ':'))
                    os.replace(tmp, PFILE)
                if path == '/api/plan/inputs':
                    disc = body.get('discipline'); inp = body.get('inputs')
                    if disc not in ('fire', 'mep', 'struct', 'cost') or not isinstance(inp, dict):
                        raise ValueError('discipline/inputs 형식 오류')
                    if not all(isinstance(v, (int, float, str, bool)) for v in inp.values()):
                        raise ValueError('inputs 값은 숫자·문자만')
                    with open(os.path.join(hist, f'plan_{stamp}_before_inputs.json'), 'w', encoding='utf-8') as f:
                        json.dump(doc, f, ensure_ascii=False, separators=(',', ':'))
                    di = doc.get('disciplineInputs') or {}
                    di[disc] = inp
                    doc['disciplineInputs'] = di
                    tmp = PFILE + '.tmp'
                    with open(tmp, 'w', encoding='utf-8') as f:
                        json.dump(doc, f, ensure_ascii=False, separators=(',', ':'))
                    os.replace(tmp, PFILE)
                out = regen()
            return self._json(200, {'ok': True, 'log': out})
        except Exception as e:
            return self._json(400, {'ok': False, 'error': str(e)})


if __name__ == '__main__':
    port = int(os.environ.get('PORT', '8770'))
    print(f'serving {ROOT} on http://127.0.0.1:{port}/', flush=True)
    ThreadingHTTPServer(('127.0.0.1', port), Handler).serve_forever()
