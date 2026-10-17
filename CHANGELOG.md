# Changelog

형식은 [Keep a Changelog](https://keepachangelog.com/ko/1.1.0/)를, 버전은 [Semantic Versioning](https://semver.org/lang/ko/)을 따릅니다.

## [0.5.0] - 2026-10-17

### Added
- **팀 모드:** `serve --host 0.0.0.0 --users users.json --certfile … --keyfile … --allowed-host …`. 루프백이 아닌 주소는 사용자 파일·TLS·허용 호스트가
  모두 있어야 열립니다. 로그인(PBKDF2-SHA256 600,000회, 실패 5회/15분 제한), HttpOnly·Secure·SameSite=Strict 세션 쿠키(8시간), HSTS,
  역할(viewer 읽기 / editor 편집·가져오기 / admin 수집·감사 로그), `diagram-bot user add|remove|list`.
- **SSO:** `--oidc-config`로 OpenID Connect 로그인(Authorization Code + PKCE, state·nonce·issuer·aud·만료 확인, 그룹→역할 매핑).
- **감사 로그:** 로그인·로그아웃·모든 변경 요청·수집 결과를 `audit.log`(JSON Lines)에 남기고 admin은 `GET /api/audit`로 봅니다. 로컬 모드도 기록합니다.
- AWS API 에뮬레이터(moto) 통합 테스트: 실제 boto3 직렬화로 20여 종 리소스를 만들고 병렬 수집·그래프·검토·보고서까지 확인합니다.
- `scripts/verify_team.py`: TLS 서버·로그인·역할·감사 로그 브라우저 검증(CI 포함).
- Apache License 2.0(`LICENSE`, `NOTICE`, 패키지 메타데이터).

### Fixed
- 한 API가 예상하지 못한 예외를 내도 그 API만 `조회 불가`로 기록하고 나머지 서비스 수집을 계속합니다.

## [0.4.0] - 2026-10-17

### Added
- **아키텍처 검토:** 수집한 설정만으로 안정성·보안 항목을 판정합니다(단일 AZ 로드밸런서·대상·Auto Scaling·RDS, reader 없는 DB 클러스터,
  ElastiCache 자동 장애 조치, 다른 AZ NAT 의존, OpenSearch 영역 인식·전용 마스터, MSK·Redshift·EFS 가용성, VPN 터널, ECS 실행 수,
  퍼블릭 DB, 저장·전송 암호화, IMDSv1, 퍼블릭 IP, 인터넷에 열린 관리·데이터 포트, 공개 EKS 엔드포인트, 수집 누락).
  앱의 `검토 결과` 탭과 검토 결과 CSV 내보내기, `review.md`, `diagram-bot review --fail-on <심각도>`(CI 게이트, 종료 코드 3).
- **변경 비교:** 두 스냅샷 사이의 리소스 추가·삭제, 설정 변경, 관계 변경. 상태 값(헬스·실행 수 등)은 제외합니다.
  앱의 `변경 비교` 탭, `GET /api/snapshots/{after}/diff/{before}`, `diagram-bot diff --fail-on-change`(드리프트 감지, 종료 코드 4).
- **교차 계정 수집:** `--role-arn`·`--external-id`(앱 수집 대화상자에도 있음). 맡은 역할 세션은 ReadOnlyAccess 세션 정책으로 제한됩니다.
  여러 `--role-arn`과 여러 리전(`--region a,b`)을 한 번에 수집해 `<계정>-<리전>` 디렉터리로 저장합니다.
- 보안 그룹 규칙 수집(`ec2:DescribeSecurityGroups`). 속성 패널에 인바운드 규칙을 보여 줍니다.
- 구성 요소: OpenSearch, ECS 서비스·태스크, EFS, Redshift, DocumentDB·Neptune, VPC 피어링, Site-to-Site VPN, 리소스별 고가용성 역할 표시.
- `deploy/readonly-role.cfn.json`: 멤버 계정용 읽기 전용 수집 역할 CloudFormation 템플릿(StackSet 배포용).
- `diagram-bot --version`, `/api/bootstrap`의 `version`.

### Changed
- 서비스별 병렬 수집과 adaptive 재시도로 대규모 계정 수집 시간을 줄였습니다. 같은 서비스의 호출은 순서대로 실행됩니다.
- 스냅샷 목록과 본문을 파일 수정 시각 기준으로 캐시합니다.
- IP 대상은 서브넷 CIDR과 AZ가 하나로 일치할 때 그 서브넷에 배치합니다.
- `make check`에 ruff 린트를 추가했고 CI도 린트·검토·비교 CLI를 실행합니다.

### Security
- 맡은 역할 세션에 ReadOnlyAccess 세션 정책을 적용합니다. 수집 정책 예시와 배포 템플릿은 테스트로 수집기 호출 목록과 일치하는지 확인합니다.

## [0.3.0] - 2026-10-17

- 인프라 아키텍처 표기(IGW·NAT·라우팅·RDS·Auto Scaling·엔드포인트·Transit Gateway·EKS·MSK·ElastiCache·Lambda),
  전체/상세/선택 보기, 결정적 배치 엔진, 편집 이력·복원, 브라우저 인수 검증.
