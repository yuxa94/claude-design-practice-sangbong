#!/usr/bin/env python3
"""대지 경계 표시 — site/site_geometry.json 의 대지 경계·건축한계선을 조닝 파일에 넣습니다.

사용
  python3 tools/site_boundary.py            # 대지 경계 + (층이 없으면) 1층 외곽선 시작안
  python3 tools/site_boundary.py --no-floor # 대지 경계만

- 조닝 파일(zoning/<SLUG>_zoning.json)이 이미 있으면 층·구역·요청은 그대로 두고 대지 정보만 바꿉니다.
- 1층 외곽선 시작안은 건축한계선 안쪽, 대지 안에 들어가는 건폐율 60% 이하 직사각형입니다.
- 서버가 켜져 있으면 조닝 편집기를 새로고침하면 바로 보입니다.
"""
import datetime
import json
import os
import shutil
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'tools'))
from project_conf import SLUG, CONF  # noqa: E402

GEO = os.path.join(ROOT, 'site', 'site_geometry.json')
ZFILE = os.path.join(ROOT, 'zoning', SLUG + '_zoning.json')
HIST = os.path.join(ROOT, 'zoning', 'history')


def area(poly):
    s = 0.0
    for i in range(len(poly)):
        x1, y1 = poly[i]
        x2, y2 = poly[(i + 1) % len(poly)]
        s += x1 * y2 - x2 * y1
    return abs(s) / 2


def main():
    g = json.load(open(GEO, encoding='utf-8'))
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    if os.path.exists(ZFILE):
        doc = json.load(open(ZFILE, encoding='utf-8'))
        os.makedirs(HIST, exist_ok=True)
        shutil.copy(ZFILE, os.path.join(HIST, datetime.datetime.now().strftime('%Y%m%d-%H%M%S') + '_before_site.json'))
    else:
        doc = {'schema': 'sangbong-zoning/1', 'floors': [], 'comments': []}
    for k in ('project', 'frame', 'site', 'params', 'context'):
        doc[k] = g[k]
    doc['updated'] = now
    prog = CONF.get('program', {})
    if '--no-floor' not in sys.argv:
        if not doc.get('floors'):
            doc['floors'] = [{'id': '1F', 'name': '1층', 'level': 1, 'height': prog.get('groundFloorHeight', 4.8), 'outline': [], 'zones': []}]
        for f in doc['floors']:   # 외곽선이 비어 있는 층(편집기가 처음 만든 빈 1층 등)에 시작안
            if not f.get('outline'):
                f['outline'] = [list(p) for p in g['suggestedOutline']['uv']]
    os.makedirs(os.path.dirname(ZFILE), exist_ok=True)
    json.dump(doc, open(ZFILE, 'w', encoding='utf-8'), ensure_ascii=False)

    site_a = area(g['site']['uv'])
    off = CONF.get('siteAreaOfficial') or site_a
    print('대지 경계: 꼭짓점 %d개 · 도면 면적 %.1f㎡ (공부 %.1f㎡)' % (len(g['site']['uv']), site_a, off))
    print('건축한계선: %s 쪽 %sm (조닝 좌표 v=0)' % (CONF.get('frontRoad', {}).get('name', '정면 도로'), CONF.get('frontRoad', {}).get('setback', '?')))
    for f in doc['floors']:
        a = area(f['outline']) if f.get('outline') else 0
        print('%s 외곽선 %.1f㎡ · 건폐율 %.1f%% (한도 %s%%) · 구역 %d개' % (f['id'], a, a / off * 100, CONF.get('limits', {}).get('coverage', 60), len(f.get('zones', []))))
    print('저장: %s' % os.path.relpath(ZFILE, ROOT))


if __name__ == '__main__':
    main()
