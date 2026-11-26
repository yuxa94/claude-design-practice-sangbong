# 3D 제안서 스타터

계획설계(plan.json)를 **인터랙티브 3D가 들어간 HTML 제안서 한 파일**로 만듭니다.
3D·표·차트는 그림이 아니라 내 계획 데이터에서 매번 새로 만들어지므로, 설계를 고치고 다시 빌드하면 제안서도 같이 바뀝니다.

## 만들기

```bash
python3 proposal/build.py
```
Windows: `py proposal\build.py`  · 파이썬만 있으면 됩니다 (추가 설치 없음)

결과: `proposal/<프로젝트>_제안서.html` — 더블클릭하면 브라우저에서 열립니다 (인터넷 없이도 열림).
Claude에게는 **“제안서 만들어줘 / 제안서 다시 빌드해줘”** 라고 하면 됩니다.

## 폴더

| 위치 | 내용 | 고칠 때 |
|---|---|---|
| `src/slides.html` | **장표 원본** — 여기만 고치면 됩니다 | 목차·문장·장 추가 |
| `src/extract.py` | 프로젝트 JSON → 제안서 데이터 | 새 수치를 쓰고 싶을 때 |
| `src/scene.js` | 3D 엔진 | 보기 방식·색·카메라 |
| `src/app.js` | 장표 이동·차트·표·편집·저장 | 차트·표 추가 |
| `src/comments.js` | 코멘트 기능 | |
| `src/base.css` `extra.css` | 디자인 | |
| `examples/상봉동_완성본_slides.html` | 문장까지 채운 완성 예시 (참고용) | 읽기만 |
| `vendor/` | three.js (MIT) · Pretendard 글꼴 (SIL OFL 1.1) | 그대로 |
| `assets/` | 장표에 넣을 이미지 (`<img src="asset:파일이름.jpg">`) | |
| `review_comments.json` | 받은 코멘트와 반영 기록 (있으면 제안서에 들어감) | |
| `build/` | 빌드 중간 파일 | 지워도 됨 |

## slides.html 쓰는 법

| 쓰는 법 | 결과 |
|---|---|
| `{{ units }}` | 285 |
| `{{ gfa \| n2 }}` | 24,512.46 (n0·n1·n2 = 소수 자리, `int` 정수) |
| `{{ basementIds \| join }}` | B1,B2,B3 |
| `{{#each resGroups}} {{ .label }} {{/each}}` | 주거층 묶음마다 반복 |
| `{{#if hasParking}} … {{else}} … {{/if}}` | 값이 있을 때만 |
| `<span class="todo">✎ …</span>` | **내가 채울 문장** — 노란 표시. 채우면 `class="todo"`를 지움 |
| `data-ch="units"` + `data-goto="units"` | 목차 버튼이 그 장으로 이동 |

3D 넣기 (장표 안에 한 줄):
```html
<div class="scene-host" data-scene='{"mode":"floor","floor":"4F","view":"plan"}' data-ui="floors:4F,11F|views:plan,floor|legend:units"></div>
```
- `mode` : `exterior` 외관 · `cinema` 해질녘 · `program` 실·세대 블록 · `floor` 한 층 평면 · `core` 코어 · `struct` 구조 · `basement` 지하 · `context` 대지 주변 · `unit` 세대 하나 · `parksim` 주차 시뮬레이션 · `flows` 동선
- 옵션 : `floor` · `unit` · `view`(iso·aerial·street·hero·low·north·side·top·plan·floor·core·basement·park·unit) · `explode` · `highlightFloors` · `auto` · `pan` · `light`(dusk·night)
- `data-ui` : `views:` 시점 버튼 · `floors:` 층 버튼 · `rail` 전체 층 · `explode` · `auto` · `cut` · `flows` · `legend:context|floor|units`

### 쓸 수 있는 값

| 이름 | 뜻 |
|---|---|
| `title` `site` `address` `zone` `use` | 프로젝트 이름·대지·주소·용도지역·용도 (project.json) |
| `siteArea` `limits.coverage` `limits.far` `limits.farBase` `limits.height` | 대지면적·법정 한도 |
| `districtPlan.*` | 지구단위계획 값 (unitMin·unitMax·nonResMin·nonResMax·communityMin·publicArea) |
| `floorsUp` `floorsDown` `scale` `height` `heightMargin` `topFloor` | 층수·높이 |
| `gfa` `gfaUp` `gfaDown` `far` `farCapArea` | 연면적·용적률 |
| `typFloor` `typArea` `coverageTyp` `coverageMax` `coverage1F` `buildingAreaFloor` | 기준층(세대가 가장 많은 층)·건폐율 |
| `units` `unitMix` `unitTypeCount` `planCount` `unitTypeList` `subTypes` | 세대 |
| `groups` `podiumGroups` `resGroups` `typGroupIds` | 층 묶음 (label·ids·btnIds·sub·mix·units·h·area) |
| `basementLabel` `basementIds` `firstBasement` `stalls` `stallReqAssumed` `stallMargin` `parkingIds` `hasParking` | 지하·주차 |
| `areas.*` `exclRatioPct` `supplyArea` | 전용·공용 |
| `stairs` `elevators` `emergencyElevators` `sprinklers` `maxTravel` `travelLimit` `columns` `transferFloor` `mep.*` `hasFire` | 안전·구조·설비 |
| `costTotalEok` `costPerPyeongMan` `costPerUnitEok` `costBenchPct` `hasCost` | 공사비 |
| `legalBad` `legalChk` `legalTotal` `hasLegal` | 법규 판정 (법규인허가 페이지를 한 번 열어야 생김) |

전체 목록은 빌드 후 `build/metrics.json` 에 있습니다.

## 고치는 순서 (수업)

1. **목차 먼저** — “내 프로젝트를 클라이언트에게 설명한다면 목차를 어떻게 짤지 제안해줘”
2. **첫 빌드** — “제안서 만들어줘” → 노란 ✎ 칸이 내가 채울 곳
3. **문장 채우기** — “6쪽 배치 설명을 내 조닝 근거로 써줘” (예시: `examples/`)
4. **장표 다듬기** — “세대 장표 3D를 벽 전체 높이로”, “11쪽에 2층 버튼 추가”
5. **검증** — 전체 장표를 넘겨 보며 가려짐·밝기·잘림·빈 ✎ 확인
6. **코멘트 반영** — 짝이 ‘코멘트 포함 HTML 저장’으로 돌려준 파일을 주고 “코멘트 확인해서 수정해줘”

## 지킬 것
- 프로젝트 원본(`plan/` `zoning/` `site/` `legal/`)은 읽기만 합니다. 수치가 틀리면 설계를 고치고 다시 빌드합니다.
- 장표에 수치를 직접 적지 말고 `{{ }}` 로 씁니다. 설계가 바뀌어도 맞게 유지됩니다.
- 연출(하늘·조명·가로수·차량)은 연출이라고 적고, 확인이 필요한 값은 숨기지 않습니다.
