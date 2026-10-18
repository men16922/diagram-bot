# T1 Level 0: 기존 XML 정렬을 라이브러리 호출로

## 담당·예산
- GPT-6 Luna, effort low. 상한 $3, 세션당 30턴, 세션 ≤2. 컨텍스트 추정 ~30K / 상한 150K.

## 목표
- 회사 코드의 `drawio.xml` 직접 정렬 호출을 Level 0 함수 호출로 바꾼다.

## 읽을 것
- `docs/porting/RULES.md` (~0.4K)
- `<회사:정렬 호출부 파일>`의 해당 함수만
- 아래 치트시트. 모듈 본문은 열지 않는다.

```python
from diagram_bot.xml_align import align_drawio_xml, align_report, inspect_drawio_xml, import_drawio_xml
from diagram_bot.drawio_spec import spec_from_xml
from diagram_bot.errors import InputError  # 잘못된 XML·규격, 메시지는 사용자용
rep = inspect_drawio_xml(xml)  # clean, node_count, overlaps, out_of_bounds, unaligned_tiers, unrouted_edges, edge_collisions (목록), pages
out = align_drawio_xml(xml, mode="preserve", spec=None)  # str; 셀 id·라벨·속성 유지
res = align_report(xml, mode="preserve")  # {"xml", "pages": [{"name", "status": clean|aligned|reverted}]}
spec = spec_from_xml(sample_xml)  # DrawioSpec, spec= 에 그대로
snap = import_drawio_xml(xml, title=None, region="ap-northeast-2")  # Snapshot dict
# api.align_xml / inspect_xml / spec_from_xml 는 Level 2(api.py)부터. 같은 함수.
```

## 하지 말 것
- 정렬 로직을 회사 코드에 다시 구현·유지.
- `mode="rebuild"`를 기본값으로(제품 결정).
- 요청 값을 파일 경로로 규격에 넘기기.

## 단계
1. 호출부의 기존 정렬 호출을 `align_report`(또는 `align_drawio_xml`)로 바꾼다.
2. 회사 규격 샘플이 있으면 `spec_from_xml(<회사:규격 샘플 XML>)`을 한 번 만들어 `spec=`로.
3. `InputError` → 회사 오류 형식(예: 422).
4. 테스트 1개: 회사 샘플 XML → `align_report` → `reverted` 없음, `inspect_drawio_xml(out)["overlaps"]` 개수가 늘지 않음.
5. 옛 정렬 코드 제거.

## 완료 조건
- `cd <회사:패키지 루트> && python -m diagram_bot.port_check --level 0`
- `<회사:대상 테스트 명령> 2>&1 | tail -20`

## 멈춤 조건
- RULES.md. 추가: 호출부를 못 찾음, 회사 XML이 `InputError`.

## 보고
- RULES.md 형식. 다음: T2(목표 Level 0이면 끝).
