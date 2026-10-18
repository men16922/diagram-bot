---
title: 위키 변경 기록
summary: 위키 페이지를 추가·삭제·크게 고친 기록(최신이 위). 코드 변경 이력은 CHANGELOG.md.
sources: [CHANGELOG.md]
related: [index, SCHEMA]
updated: 2026-10-18
---
# 위키 변경 기록

> 페이지 단위의 변경만 한 줄씩 남긴다. 형식: `날짜 — 무엇을 (왜)`.

- 2026-10-18 — 위키 시작: 작성 규칙(SCHEMA), 색인·`llms.txt` 생성기(`scripts/wiki_index.py`), 24개 페이지(구조·포팅 가이드·용어, 데이터와 모델 6,
  분석과 출력 4, AWS 접근과 인증 3, 앱과 API 5, 작업 방법 3). 브라우저 egress 도입과 서버 분리 리팩터링(`store.py`, `aws_access.py`, `routes/`,
  `api.py`, `static/js/`)에 맞춰 코드 기준으로 작성. 작성 중 발견해 고친 것: 관계 표시 이름 4개 누락(`labels.js`), `verify_browser_egress.py` 설명의 없는 옵션.
