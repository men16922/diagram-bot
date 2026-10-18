# T2 Level 1: 설계·검토·CloudFormation 연결

## 담당·예산
- GPT-6 Luna, effort low. 상한 $3, 세션당 30턴, 세션 ≤2. 컨텍스트 추정 ~35K / 상한 150K.

## 목표
- 회사의 손으로 짠 다이어그램 생성을 `Design` + `generate_document`로 바꾸고 검토·CFN을 노출한다.

## 읽을 것
- `docs/porting/RULES.md` (~0.4K)
- `<회사:다이어그램 생성 호출부 파일>`의 해당 함수만
- 아래 치트시트. 모듈 본문은 열지 않는다.

```python
from diagram_bot.designer import Design, DesignError, STANDARDS  # STANDARDS: 이름 -> 설명
from diagram_bot.drawio import generate_document, carry_annotations, validate_document
from diagram_bot.findings import review, summary
from diagram_bot.iac import template, dumps
from diagram_bot.diff import diff
d = Design(title="…", region="ap-northeast-2"); d.apply_standard("three-tier")
d.add_cache(vpc=d.describe()["vpcs"][0]["name"], name="session-redis")  # add_vpc/add_load_balancer/add_database/…; 실패 DesignError
snap = d.snapshot(); d.review()  # {"summary", "findings"}
vpc_id = snap["raw"]["vpcs"][0]["VpcId"]
xml = generate_document(snap, vpc_id, view="detail", spec="clean")  # 위치 인자: snapshot, vpc_id
xml = carry_annotations(saved_xml, xml)  # 사용자가 추가한 메모·도형 유지
cfn_text = dumps(template(snap)); changes = diff(before, after)["summary"]
```

## 하지 말 것
- 좌표 계산·XML 조립을 회사 코드에 남기기.
- 검토 규칙·CFN 생성 로직 복제.

## 단계
1. 호출부의 XML 조립을 `Design` 단계 + `generate_document`로 바꾼다. 회사 규격은 T1의 `spec`.
2. 저장된 편집본이 있으면 `carry_annotations(saved, new)`.
3. 검토: `review(d.graph(), snap.get("coverage", []))` 또는 `d.review()`.
4. CFN: `dumps(template(snap))`.
5. 테스트 1개: 표준 하나 → 리소스 하나 추가 → XML이 `validate_document` 통과, `template(snap)["Resources"]` 비어 있지 않음.

## 완료 조건
- `cd <회사:패키지 루트> && python -m diagram_bot.port_check --level 1`
- `<회사:대상 테스트 명령> 2>&1 | tail -20`

## 멈춤 조건
- RULES.md. 추가: 회사 리소스가 `Design` 메서드에 없음(제품 결정).

## 보고
- RULES.md 형식. 다음: T3.
