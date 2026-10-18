---
title: 용어
summary: 스냅샷·커버리지·참조 노드·보기·경로 클래스·설계안·브라운필드·브라우저 egress 등 이 저장소에서 쓰는 용어의 정확한 뜻.
sources: [src/diagram_bot/graph.py, src/diagram_bot/views.py, src/diagram_bot/collector.py, src/diagram_bot/designer.py, src/diagram_bot/egress.py]
related: [architecture, snapshot-model, graph, views]
updated: 2026-10-18
---
# 용어

> 코드와 화면에서 같은 말이 같은 뜻으로 쓰이도록 정리한 목록. 자세한 내용은 각 링크 페이지.

- **스냅샷(snapshot)** — 한 계정 × 리전의 수집 결과 JSON: `metadata`, `coverage`, `raw`(API 응답 원본), `graph`. 저장 후 바뀌지 않는다. [[snapshot-model]]
- **커버리지(coverage)** — 호출마다 한 줄: `complete` / `partial`(일부 페이지 후 실패) / `unavailable`(거부·오류) / `not_discovered`(앞 단계가 없어 호출 안 함). [[collection]]
- **노드·관계(node, edge)** — 리소스와 설정 근거 관계. 관계 ID는 출발·관계·도착과 포트·AZ 한정자로 정해져 API 순서와 무관하다. [[graph]]
- **참조 노드(reference_only)** — 다른 리소스가 가리키지만 이 스냅샷에서 직접 조회되지 않은 대상(미조회 인스턴스, 다른 계정 리소스 등). [[graph]]
- **경로 클래스(route_class)** — 서브넷 라우팅 테이블의 `0.0.0.0/0` 대상으로 정한 구분: `public`(IGW), `nat`, `tgw`, `vgw`, `isolated`. [[graph]]
- **보기(view)** — `overview`(VPC 카드와 네트워크), `detail`(VPC 하나), `focus`(리소스 하나와 직접 관계). 개수는 visible / collected / reference / hidden. [[views]]
- **경로 필터(routes)** — 상세 보기를 로드 밸런서·대상 그룹 하나의 경로로 좁히기. [[views]]
- **편집본(document)** — 스냅샷 × 보기 범위의 draw.io XML. 리비전으로 동시 저장을 막고 이전 20개를 보관. [[storage]]
- **설계안(design)** — 채팅·설계 도구가 만든 스냅샷(`metadata.design: true`). 실제 계정 데이터가 아니다. [[chat-agent]]
- **브라운필드(brownfield) 설계** — 수집·열린 스냅샷에서 시작한 설계안(`metadata.based_on`). [[chat-agent]]
- **표준 아키텍처(standard)** — `designer.STANDARDS`의 불러올 수 있는 표본 설계(샘플과 같은 생성기). [[samples]]
- **통합 스냅샷(combined)** — 여러 계정 × 리전을 합친 스냅샷(`parts`). [[multi-account]]
- **검토 결과(finding)** — 규칙 ID·심각도·영역·근거·권장 조치. [[review-rules]]
- **egress** — AWS API 호출이 나가는 경로. `server`(서버가 직접) 또는 `browser`(서버 서명, 브라우저 전송). [[browser-egress]]
- **중계(relay)** — 브라우저 egress에서 서명된 요청을 브라우저에 넘기고 응답을 받는 서버-브라우저 통로(`/api/relay/*`). [[browser-egress]]
- **페이지 토큰** — 앱 프로세스마다 새로 만드는 값. 모든 쓰기 요청이 `X-Diagram-Bot` 헤더로 보내야 한다. [[team-mode-security]]
- **팀 모드** — 루프백 밖으로 열 때 필요한 계정·역할·TLS·감사 로그 구성. [[team-mode-security]]
