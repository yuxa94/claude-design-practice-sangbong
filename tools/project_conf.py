"""프로젝트 설정 읽기 — 생성기·서버가 파일 이름 접두어(SLUG)·정면 도로 이름 등을 여기서 가져옵니다.
값은 프로젝트 폴더의 project.json (new_project.py가 만듦)."""
import json
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONF = json.load(open(os.path.join(ROOT, 'project.json'), encoding='utf-8'))
SLUG = CONF['slug']
SITE = CONF.get('site') or SLUG.replace('_', ' ')
ROAD = (CONF.get('frontRoad') or {}).get('name') or '전면도로'
