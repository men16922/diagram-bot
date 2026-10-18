---
title: 위키 변경 기록
summary: 위키 페이지를 추가·삭제·크게 고친 기록(최신이 위). 코드 변경 이력은 CHANGELOG.md.
sources: [CHANGELOG.md]
related: [index, SCHEMA]
updated: 2026-10-18
---
# 위키 변경 기록

> 페이지 단위의 변경만 한 줄씩 남긴다. 형식: `날짜 — 무엇을 (왜)`.

- 2026-10-18 — [[drawio-format-and-alignment]] 추가 및 [[porting-guide]]·[[layout-and-drawio]]·[[http-api]]·[[cli]] 보강: 회사별 draw.io XML 포맷·스타일 규격(`DrawioSpec`, `spec_from_xml`), 기존 회사 `drawio.xml` 인플레이스 자동 정렬·직교 배선(`align_drawio_xml`), 스냅샷 역변환(`import_drawio_xml`), 경량 모델(GPT-6 Luna·Claude 5.5 Haiku)용 Level 0~4 모듈 이식 플레이북 추가.
- 2026-10-18 — [[porting-guide]] 보강: 표준 `drawio.xml` 직접 정렬 방식 vs 스냅샷·배치 파이프라인 비교, 사용 라이브러리 전체 명세, 경량 모델(GPT-6 Luna·Claude 5.5 Haiku)용 4단계(Level 1~4) 모듈별 복사·연결 레시피 및 추가 제안.
- 2026-10-18 — 위키 시작: 작성 규칙(SCHEMA), 색인·`llms.txt` 생성기(`scripts/wiki_index.py`), 24개 페이지(구조·포팅 가이드·용어, 데이터와 모델 6,
  분석과 출력 4, AWS 접근과 인증 3, 앱과 API 5, 작업 방법 3). 브라우저 egress 도입과 서버 분리 리팩터링(`store.py`, `aws_access.py`, `routes/`,
  `api.py`, `static/js/`)에 맞춰 코드 기준으로 작성. 작성 중 발견해 고친 것: 관계 표시 이름 4개 누락(`labels.js`), `verify_browser_egress.py` 설명의 없는 옵션.
