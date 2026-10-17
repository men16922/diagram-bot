# Diagram Bot 시연 가이드

AWS 인증 없이 실행할 수 있는 시연 버전입니다. 합성 샘플은 계정 `111122223333`, 서울 리전에 VPC 세 개(하나는 빈 환경), 두 AZ, 서브넷 다섯 개, EC2 네 개, ALB·NLB, 대상 그룹 세 개를 포함합니다. 같은 EC2의 두 포트 등록, EC2와 IP가 같은 IP 대상, 조회되지 않은 대상, 긴 한글 이름, `DescribeInstances` 부분 실패를 의도적으로 넣었습니다.

## 준비

README의 설치 후 아래처럼 **새로운 데이터 디렉터리**로 실행합니다. 기존 저장본에 영향을 주지 않고 시연을 반복할 수 있습니다.

```bash
diagram-bot setup-editor
diagram-bot serve --data-dir reports/presentation-01
```

http://127.0.0.1:8765 를 데스크톱 Chrome/Chromium에서 열고 창을 1440×900 이상으로 설정합니다. 전체 화면을 넓게 보려면 1600×1100을 권장합니다. 앱 설치·편집기 설치는 시연 전에 완료하세요.

## 5분 시연

1. **전체 구성:** 처음 화면은 `전체 구성`입니다. 계정·리전 경계 안의 VPC 요약 카드(파란 테두리, 실제 리소스 도형과 구분)와 VPC를 잇는 Transit Gateway·피어링·VPN(온프레미스 고객 게이트웨이까지) 연결선, `표시 n개 / 수집 n개 · 숨김 n개`, `부분 조회` 표시를 보여줍니다. `주문 서비스 운영 네트워크` 카드를 선택하고 오른쪽 `상세 구성 보기`를 누릅니다. 왼쪽 VPC 목록도 같은 상세 보기로 이동합니다.
2. **ALB 관계와 근거:** `demo-alb`를 선택합니다. 대상 그룹 연결과 두 서브넷 배치(`ap-northeast-2a/2c`)가 API 필드 근거와 함께 표시됩니다. `직접 관계 강조`로 구성도의 연결선을 강조합니다. `demo-web`을 선택하면 `web-a`가 `:443`과 `:8443` 두 등록으로 구분됩니다.
3. **EC2 배치:** `web-c`를 선택해 AZ·서브넷 위치와 `DescribeInstances.SubnetId` 근거를 확인합니다. `선택 리소스 보기`로 직접 관계와 필요한 상위 경계만 그린 보기로 이동한 뒤 `상세 구성으로 돌아가기`를 누릅니다.
4. **부분 조회·참조:** `수집 상태`에서 `UnauthorizedOperation`과 필요한 권한 `ec2:DescribeInstances`를 보여줍니다. `배치 미확인` 영역의 점선 `i-demo-missing`(대상 그룹에는 등록됐지만 조회되지 않은 EC2)과 IP 대상 `10.20.11.25:9000`(같은 IP의 EC2와 합치지 않음)을 설명합니다. `SG·ENI·EBS 참조 표시`를 켜면 참조 리소스가 점선 연결로 추가됩니다.
5. **편집:** 참조 표시를 끄고 도형을 드래그(또는 선택 후 Shift+방향키)해 위치를 조정하고, `＋ 메모`를 더블클릭해 검토 메모를 씁니다. `편집본 저장` 후 새로고침해 유지되는지 확인합니다.
6. **출력:** `다운로드`에서 `.drawio`와 PNG(1×/2×/3×, 투명 배경)를 받습니다. 두 파일은 같은 편집본에서 생성되므로 이동한 위치와 메모가 일치합니다. JSON·CSV는 같은 스냅샷의 전체 수집 데이터이며 메모를 포함하지 않습니다.

VPC가 하나이고 TGW·피어링·VPN이 없는 스냅샷은 카드 한 장짜리 전체 구성 대신 상세 구성부터 엽니다(`전체 구성` 버튼은 그대로).

CLI의 `topology.drawio`는 전체 구성 페이지와 VPC별 상세 페이지를 담은 다중 페이지 문서입니다.

실제 AWS를 보여줄 때만 먼저 `aws sso login --profile 프로파일명`을 실행한 뒤, 앱에서 예상 계정 ID와 리전을 지정해 수집합니다. 실제 수집 시연은 해당 환경의 권한·접속을 사전에 확인해야 합니다.

## 저장, 재배치와 복원

- 보기(전체/상세/선택 리소스), VPC, 선택 리소스, 참조 표시 조합마다 편집본이 따로 저장됩니다. 보기를 바꾸기 전 편집 중인 내용은 자동 저장됩니다. 보기 전환만으로 편집본을 덮어쓰지 않습니다.
- `자동 배치 다시 실행`은 현재 편집본을 이력에 보관하고 새 배치를 만든 뒤 사용자 메모를 옮겨 새 버전으로 저장합니다. `이전 편집본 복원`은 마지막으로 보관된 편집본을 새 버전으로 되살립니다. 이력은 보기마다 최근 20개입니다.
- 새로 수집하거나 JSON을 가져오면 새 스냅샷이 생깁니다. 기존 스냅샷과 편집본은 보존됩니다.
- 저장 충돌 메시지가 나오면 다른 창에서 저장한 상태를 새로고침해 불러옵니다.
- 수집 진행 상태는 메모리에 있으므로 실행 중 서버를 종료하면 진행 상태가 복원되지 않습니다. 완료된 스냅샷은 파일로 남습니다.

## 재현 가능한 브라우저 검증

```bash
pip install -e '.[test,browser]'
python -m playwright install chromium
diagram-bot serve --data-dir reports/browser-check-01
# 다른 터미널
python scripts/verify_demo.py   # 설치된 Chromium을 쓰려면 --chromium /path/to/chrome
```

새 데이터 디렉터리를 사용해야 합니다. 검증 스크립트가 메모와 가져온 스냅샷을 추가하므로 실제 업무 데이터에 실행하지 마세요. 결과는 `artifacts/browser/verification.json`, 화면 캡처, draw.io 파일, 흰색/투명 PNG로 남습니다. 저장소에는 합성 샘플만 포함합니다.

검증된 항목: 전체 구성 요약·집계·부분 조회 표시, VPC 카드 → 상세 보기, ALB 관계·근거·관계 강조, 포트별 등록, EC2 배치, 선택 리소스 보기와 복귀, 조회 범위·필요 권한, 참조만 확인된 대상, 검색·종류 필터, VPC 전환, SG·ENI·EBS 참조, 키보드 이동·네이티브 메모 편집, 저장·다시 열기, 이동 좌표가 반영된 `.drawio`, PNG 2× 흰색/1× 투명/3×, JSON·CSV, 자동 재배치 후 메모 유지, 이전 편집본 복원, JSON 가져오기. 편집기에서 외부 HTTP 요청이나 JavaScript 예외가 발생하면 검증을 실패 처리합니다.

## 문제가 생기면

| 증상 | 확인 사항 |
| --- | --- |
| 편집기 설치 안내 | `diagram-bot setup-editor` 후 서버 재시작 |
| 포트 사용 중 | `diagram-bot serve --port 8766` 후 해당 주소 열기 |
| SSO 만료·권한 오류 | 터미널에서 SSO 로그인, 프로파일/계정/읽기 권한 확인 |
| PNG 생성 지연 | 1×로 낮추고 한 VPC만 선택해 출력 |
| 오래된 편집본 표시 | `자동 배치 다시 실행`(이전 편집본은 보관) 또는 새로운 데이터 디렉터리 사용 |

## 확인한 산출물

- 앱 화면: [전체 구성](images/overview.png) · [상세 구성](images/workspace.png) · [선택 리소스](images/focus.png) · [데이터 플랫폼 샘플](images/data-platform.png) · [AI 플랫폼 샘플](images/ai-platform.png) · [네트워크 연결도(전체 구성)](images/network-overview.png) · [검토 결과](images/review.png) · [변경 비교](images/changes.png)
- [편집한 구성도 PNG](../examples/demo/edited-topology.png) · [동일 편집본 draw.io](../examples/demo/edited-topology.drawio)
- [CLI 다중 페이지 draw.io](../examples/demo/topology.drawio) · [브라우저 검증 결과](../examples/demo/verification.json)
- [배치 엔진 평가](engineering/LAYOUT_EVALUATION.md)

실제 AWS 계정 조회는 한 계정·리전(기본 VPC·Lambda 위주의 테스트 계정)에서 검증했습니다. 수집기의 페이지네이션·권한 오류·계정 불일치 등은 botocore Stubber로 검증합니다.
