# 실습 작업 규칙 (Claude가 읽는 문서)

상봉동 116-49 신축 기획 실습 폴더입니다. 교육생이 단계별로 요청하면 아래 규칙대로 진행하세요.
교육생은 AI를 처음 쓰는 설계자일 수 있습니다. 답은 짧은 한국어로, 무엇을 바꿨고 화면에서 무엇을 확인하면 되는지 알려 주세요.

## 폴더
| 위치 | 내용 | 규칙 |
|---|---|---|
| `site/` | 대지 정보 (조사 정리 md, 원자료 zip, `raw/` 지적·건물·용도지역, `site_geometry.json` 대지 경계·좌표, `*_context.json` 주변) | **읽기만** |
| `reference/` | 법규 검토서 (소방 중점) | **읽기만** |
| `details/` | 벽·문·창 디테일 라이브러리 (`walls.json` `doors.json` `windows.json`) | 요청이 있을 때만 수정 |
| `zoning/상봉동_116-49_zoning.json` | 조닝 원본 (층·외곽선·구역·요청) | 조닝 편집기·API로 저장 |
| `plan/상봉동_116-49_plan.json` | 계획설계 결과 (생성기가 씀) + `typeEdits` + `comments` | 생성기로만 다시 만듦 |
| `legal/상봉동_116-49_legal.json` | 법규 가정값·사람 판정·요약(`summary.items` = 부적합·확인 필요 항목) | 페이지·API로 저장 |
| `tools/` | `zoning_server.py`(서버) · `site_boundary.py`(대지 경계) · `gen_plan.py`(계획설계 생성기) · 분야 모듈 | 규칙 변경 요청 때만 수정 |
| `*/history/` | 자동 버전 기록 | 지우지 않음 |

## 서버
- 실행: `.claude/launch.json` 의 `zoning` (포트 8770) 또는 `PORT=8770 python3 tools/zoning_server.py`
- 허브 http://localhost:8770/ · 조닝 편집기 · 계획설계 · 법규인허가 페이지
- 교육생이 “로컬호스트 띄워줘 / 다시 띄워줘”라고 하면 서버를 켜고 주소를 알려 주세요.

## 단계별로 할 일
1. **대지 경계 표시** (“대지 경계 표시해줘”) → `python3 tools/site_boundary.py` 실행. 대지 면적·건축한계선·1층 외곽선 시작안(건폐율 60% 이하)을 보고하고 “조닝 편집기를 새로고침하세요”라고 안내. 교육생이 편집기의 ⌖ 대지 경계 도구로 직접 그렸으면(`site.source: "manual"`) 덮어쓰지 말고, 바꿀지 먼저 묻기. 직접 그린 경계 면적과 공부 면적 차이가 크면 알려 주기.
2. **조닝** — 교육생이 편집기에서 층을 늘리고(+위층/+지하층) 구역을 그립니다. “요청 반영해줘”라고 하면
   - `GET /api/zoning` 으로 읽고 `comments` 중 `status`가 `done`이 아닌 것을 처리 (대상은 `targets[].floor/zoneId`)
   - 수정한 문서 전체를 `POST /api/zoning` 으로 저장 (서버가 history에 버전을 남김). 처리한 요청은 `status:'done'`, `reply`(무엇을 바꿨는지 한두 문장), `resolvedAt` 기록
   - 건폐율 60%·용적률 960%·최고높이 80m·주차·비주거 10~20%·모든 세대 창 확보를 지키고, 어긋나면 이유를 답글에 씀
3. **계획설계 만들기** (“계획설계 만들어줘”) → 서버가 켜져 있으면 `POST /api/plan/regen`, 아니면 `python3 tools/gen_plan.py`. 처음 생성은 이 요청으로만 합니다(그 뒤로는 조닝 저장 시 자동 재생성). 실·벽·문·창 개수, 세대 수, 이슈 수를 보고.
4. **실 디테일** — 교육생이 계획설계 페이지에서 벽·문·창·실을 고르고 요청을 남깁니다. “계획설계 요청 반영해줘”라고 하면
   - `plan.json` 의 `comments` 중 열린 것을 처리. 세대 안 실 배치·문 위치는 같은 타입 전체에 반영되도록 `typeEdits` 로 (`POST /api/plan/typeedit`), 모든 세대에 공통인 크기·사양은 `details/*.json` 을 고친 뒤 다시 생성, 생성 규칙 자체는 `tools/gen_plan.py`
   - 처리 결과는 `POST /api/plan/comments` 로 `status:'done'`·`reply` 저장 (파일을 직접 덮어쓰지 않음). `typeEdits` 를 절대 지우지 않음
5. **법규·인허가** — 교육생이 법규인허가 페이지를 열면 판정이 자동 계산되어 `legal.json` 의 `summary` 에 저장됩니다. “법규 검토 정리해줘”라고 하면 `summary.items`(부적합·확인 필요·절차)와 `reference/` 검토서를 읽고 `legal/상봉동_116-49_검토메모.md` 에 항목별로 “현재 값 / 기준 / 고칠 방법(조닝·계획설계·협의 중 어디서)”을 정리. 판정 수정은 하지 말고 사람이 정하도록 남김.

## 늦은 교육생 따라잡기
“완성본 step-N으로 맞춰줘”라고 하면:
1. `git status` 로 지금 작업이 있는지 확인하고, 있으면 `git add -A && git commit -m "내 작업 (step-N 전)"` 으로 먼저 남김 (지우지 않음)
2. 완성본 원격이 없으면 `git remote add answer https://github.com/yuxa94/claude-design-practice-sangbong.git` (같은 폴더 옆에 완성본이 있으면 `../practice-sangbong` 도 됨)
3. `git fetch answer --tags` → `git checkout -B my-stepN step-N`
4. 서버가 켜져 있으면 페이지를 새로고침하라고 안내

## 지킬 것
- `site/`·`reference/` 원본과 `history/` 는 고치거나 지우지 않음
- 부적합 판정을 임의로 “적합”으로 바꾸지 않음 — 설계로 고치거나 사람 판단으로 남김
- git 커밋은 교육생이 “커밋해줘”라고 할 때만. 메시지는 `step-N: 한 일` 형식 (예: `step-2: 조닝 — 3~16층 세대 배치`)
- 단계가 끝나면 교육생이 직접 확인할 조작 순서를 2~3줄로 알려 주기
