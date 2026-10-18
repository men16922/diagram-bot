# Diagram Bot — 열린 제품 제안

Last Updated: 2026-10-19

> 2026-10-17의 최초 제품 계획(목표, 화면안, 단계 M0~M5, 데이터 계약, 검증 기준)과 그 구현 이력은
> [docs/archive/PLAN-2026-10-17.md](archive/PLAN-2026-10-17.md)에 보관했다. 이 문서에는 그 계획 가운데 **아직 구현하지 않은 제안**만 남긴다.
> 지금 동작하는 기능과 근거는 [STATUS.md](STATUS.md), 승인된 작업은 [NEXT_PLAN.md](NEXT_PLAN.md)에 있다.
> 이 목록은 무인 실행(overnight)의 작업 승인이 아니다.

## 화면과 저장

- **React + TypeScript(Vite) 화면:** 지금은 빌드 없는 ES 모듈 화면이다. 회사 스택으로 옮기는 순서는 [wiki porting-guide](wiki/porting-guide.md)와 [wiki web-app](wiki/web-app.md)에 있다.
- **SQLite 색인:** source / job / snapshot / view / document 색인. 지금은 데이터 디렉터리의 JSON 파일이다. 바꿀 때 구현할 메서드는 [wiki storage](wiki/storage.md).
- **저장된 보기:** 환경 범위, 종류·태그 필터, 연결선 옵션, 접힌 그룹, 위치 설정을 이름 붙여 저장(최초 계획의 `GET/POST /api/views`). 지금은 보기 조합마다 편집본만 저장한다.
- **그룹 접기:** VPC·AZ·서브넷을 요약하고 숨긴 리소스 개수를 표시.
- **전체 내보내기:** 여러 VPC를 한 번에 내보낼 때 PNG를 페이지별 이미지 ZIP으로. `.drawio`의 다중 페이지는 CLI `topology.drawio`에만 있다.
- **동시 편집 병합:** 지금은 리비전 충돌(409)을 알리고 새로고침으로 이어 간다.
- **채팅 대화 기록 저장과 재개.**

## 수집과 운영

- **예약 수집과 스냅샷 보관 정책:** 실행 환경에 유효한 인증이 있어야 하고, SSO 재로그인이 필요하면 중단 상태를 보인다. 오래된 스냅샷을 무제한 쌓지 않도록 보관·삭제 설정을 둔다. 지금은 cron·CI에서 `collect`·`review`·`diff` 종료 코드를 쓴다([OPERATIONS.md](OPERATIONS.md)).
- **수집 작업의 재시작 복구:** 작업 상태를 저장해 서버 재시작 뒤 중단 상태로 복구. 지금은 진행 상태가 메모리에만 있다.
- **수집 범위 후보:** ELBv2 Listener·Rule(forward·redirect·fixed-response 구분), NACL, 그리고 TA 사용 빈도에 따라 S3·DynamoDB·Route 53. EKS 내부 워크로드는 AWS EKS API만으로 부족해 Kubernetes API 접근을 별도 과제로 다룬다.

## 성능 목표 (측정 전)

- 한 화면 300개 노드·600개 엣지: 초기 배치·표시 3초 이내, 선택 패널 응답 200 ms 이내. 기준 장비와 fixture를 고정하고 브라우저에서 계측한다.
  지금 자동 테스트가 재는 것은 서버 배치 시간뿐이다([wiki layout-and-drawio](wiki/layout-and-drawio.md)의 "규모").
- 리소스 1,000개 이상은 환경 분리·그룹 접기·목록 가상화를 적용하고 일괄 전개를 기본값으로 하지 않는다.
