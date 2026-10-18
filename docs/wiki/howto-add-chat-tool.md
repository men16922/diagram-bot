---
title: 채팅 도구 추가하기
summary: 설계 단계(Design 메서드)와 그 단계를 부르는 Strands 도구를 추가하는 절차. add_fsx_lustre·add_subnet_tier를 본보기로 DesignError 메시지, record(), @tool docstring, 도구 수 단언, SYSTEM_PROMPT, CloudFormation 범위, 테스트를 다룬다.
sources: [src/diagram_bot/designer.py, src/diagram_bot/agent.py, src/diagram_bot/samples/, src/diagram_bot/iac/, src/diagram_bot/graph.py, tests/test_agent.py, tests/test_iac.py]
related: [chat-agent, http-api, storage, cloudformation, graph, snapshot-model, views]
updated: 2026-10-18
---
# 채팅 도구 추가하기

> 도구 하나는 두 층이다. `designer.Design`의 메서드가 API 모양 `raw`에 리소스를 쓰고 단계를 기록한다. `agent.build_tools` 안의 `@tool` 함수는 그 메서드를 부르고 새 설계 스냅샷을 저장한다. 모델은 도구를 고르기만 하고 리소스는 코드가 만든다([[chat-agent]]).

## 핵심
- 본보기: `Design.add_fsx_lustre`(새 리소스 하나를 기존 서브넷에 두는 단계)와 `Design.add_subnet_tier`(네트워크 구조를 바꾸는 단계), 대응 도구 `add_fsx_lustre`·`add_subnet_tier`.
- 바꿀 파일: `designer.py`, `agent.py`(도구와 `SYSTEM_PROMPT`), `tests/test_agent.py`(도구 수 29 → 30), 필요하면 `samples/`·`iac/`와 `tests/test_iac.py`.
- 실제 AWS는 호출하지 않는다. 설계는 제안이고 `Sample` 헬퍼로 `raw`만 쓴다.

## 1. Design 메서드
`designer.Design`에 메서드를 추가한다. 규칙:
- 입력 VPC는 `self.vpc(name)`으로 찾는다. 없으면 `VPC '<name>'가 설계에 없습니다. 먼저 add_vpc로 만드세요. (현재: …)`라는 `DesignError`가 이미 나온다.
- 서브넷은 `self.tier(v, "app")`처럼 계층으로 고른다. 계층이 없으면 `add_subnet_tier`를 안내하는 `DesignError`가 나온다.
- 이름은 `slug(name)`으로 정규화한다. 로드 밸런서·대상 그룹처럼 중복을 막아야 하면 `self.unique("lb"|"tg", name)`, 그 밖의 이름·ID 중복은 `self.absent(name, collection, field, *values)`를 쓴다.
- 단계 메서드에는 `@atomic`을 붙인다. 실패하면 설계 상태 전체를 되돌리고 StopIteration·IndexError·KeyError·ValueError를 `DesignError`로 바꾼다. 주소가 필요하면 무엇이든 바꾸기 전에 `self.capacity(subnets, per_subnet)`로 빈 주소를 먼저 확보한다.
- 라우트를 쓰는 단계는 `self.routing_tables(v)`를 **아무것도 추가하기 전에** 부른다. 라우트 테이블이 수집되지 않은 brownfield VPC면 여기서 거부된다(`add_vpc_endpoints`가 이 순서를 지킨다).
- 보안 그룹은 `self.security_group(v, name, [(port, source)])`, 리소스는 `self.sample.<helper>(...)`(`samples.Sample`)로 만든다. `add_fsx_lustre`는 `self.sample.fsx_lustre(key, slug(name), v["id"], subnet, deployment, capacity, throughput)`을 부른다.
- 입력 검사는 리소스를 만들기 전에 끝낸다. 실패하면 부분 변경이 남지 않는다.
- 마지막에 `self.record("<step>", **params)`로 단계를 남긴다. `None`·빈 문자열·빈 목록은 자동으로 빠지고, 이 목록이 스냅샷 `metadata.steps`가 된다.
- 반환값은 모델이 다음 단계를 정할 수 있는 작은 dict다(예: `add_fsx_lustre`는 `{file_system, subnet, az, next_to_training}`).

```python
def add_thing(self, vpc, name, size=2):
    v = self.vpc(vpc)
    if not 1 <= int(size) <= 10:
        raise DesignError("size는 1~10입니다.")          # 무엇을 고치면 되는지 말한다
    subnets = self.tier(v, "data")
    ...                                                  # self.sample.<helper>(...)로 raw에 쓴다
    self.record("add_thing", vpc=vpc, name=name, size=int(size))
    return {"thing": slug(name), "subnets": len(subnets)}
```

### DesignError 메시지
- `DesignError(ValueError)`의 메시지는 Strands가 오류 도구 결과로 모델에 돌려주고, 모델이 그대로 사용자에게 전하기도 한다. 한국어로 **무엇이 틀렸고 어떤 도구·값으로 고치면 되는지**를 쓴다. 예: `EKS 클러스터가 설계에 없습니다: <이름>. add_eks_cluster로 먼저 만드세요.`, `deployment는 PERSISTENT_2(권장), PERSISTENT_1, SCRATCH_2, SCRATCH_1 중 하나입니다.`
- 허용 값이 정해져 있으면 메시지에 모두 나열한다. 모델은 그 목록을 보고 다시 호출한다.

## 2. 새 리소스 종류라면 (samples·graph·화면)
기존 `raw` 키(예: `fsx_file_systems`)를 쓰면 이 단계는 필요 없다. 새 키라면 FSx의 경로를 따라간다.
- `samples.Sample.__init__`의 `self.raw` 키 목록과 헬퍼 메서드. 같은 키를 `Sample.snapshot()`의 `coverage` 목록에도 넣을지 확인한다.
- `graph.build_graph`의 노드·관계 생성(`raw.get("fsx_file_systems")` → `node("fsx_file_system", ...)`).
- 배치·표시: `views.SUBNET_KINDS` 같은 분류, `layout.py`의 라벨·아이콘 분기, `drawio.py`의 아이콘 표, 프런트엔드 `static/js/labels.js`의 `names`.
- 검토 규칙이 필요하면 `findings.py`.
- 수집기에서도 읽으려면 `collector.py`의 호출 목록과 `readonly-policy.json`까지 바뀐다. 이 페이지 범위 밖이다.

## 3. Strands 도구 (`agent.build_tools`)
- `build_tools(workspace)` 안에 `@tool` 함수를 추가하고 **반환 목록에도 넣는다**. 목록에 없으면 모델이 볼 수 없다.
- 설계를 바꾸는 도구는 `change("<메서드 이름>", **kwargs)`를 쓴다. `w.lock` 안에서 `Design` 메서드를 부르고 `publish_design`이 새 스냅샷을 저장해 `snapshot_id`를 결과에 붙인다. 진행 중이면 `snapshot` 이벤트가 바로 나간다.
- 시그니처: 타입 힌트가 입력 스키마가 된다. 기본값 없는 인자만 `required`다(`test_tools_have_schemas_and_descriptions`가 `add_vpc`의 `required == ["name"]`를 확인). 선택 문자열은 `None` 대신 `""` 기본값을 쓰고 `change()`에 넘길 때 `x or None`으로 바꾼다(`add_ec2_service`의 `load_balancer` 참고). 목록 인자는 `list[str] | None = None`.
- docstring이 모델이 읽는 도구 설명이다. 규칙:
  - 한국어로 무엇을 만드는지 한 문장, 그다음에 인자 값의 선택지와 권장값(예: `deployment는 PERSISTENT_2 권장(SCRATCH는 복제 없음)`).
  - 배치 규칙처럼 모델이 알아야 할 결과를 적는다(예: "학습 노드 그룹이 있으면 그 전용 서브넷(같은 AZ)에 둡니다").
  - 다른 도구가 먼저 필요하면 이름을 적는다.
  - 빈 docstring은 테스트가 막는다.

```python
@tool
def add_thing(vpc: str, name: str, size: int = 2) -> dict:
    """data 계층에 <리소스>를 추가합니다. size는 1~10(기본 2). VPC가 없으면 add_vpc로 먼저 만드세요."""
    return change("add_thing", vpc=vpc, name=name, size=size)
```

## 4. SYSTEM_PROMPT
- 일반 단계는 docstring만으로 충분하다. 모델이 **언제** 써야 하는지가 중요한 도구(순서, 대체 관계, Well-Architected 기본값)는 `SYSTEM_PROMPT`의 해당 원칙 줄에 이름을 넣는다. 예: AI/GPU 원칙의 `add_gpu_node_group training=true`, `add_fsx_lustre`.
- 프롬프트에는 도구에 없는 동작(실제 계정 변경 등)을 약속하지 않는다.

## 5. CloudFormation 범위 (`iac/`)
- 설계 스냅샷은 `GET /api/snapshots/{id}/cloudformation`과 `export-cfn`으로 템플릿 초안이 된다. `iac.Template.build`가 단계별 메서드(`network`, …, `file_systems`, …, `not_expressible`)를 차례로 부른다. 메서드는 영역별 믹스인 파일(`iac/network.py`, `balancing.py`, `compute.py`, `data.py`)에 있다.
- 새 `raw` 키는 해당 단계 메서드에서 리소스로 만들거나(`self.add(...)`, 서브넷은 `self.subnet_refs`), 정확히 표현할 수 없으면 `self.skip(kind, id, reason, name)`으로 `Metadata.DiagramBot.Unsupported`에 남긴다. 지어내지 않는다(모듈 docstring). 데이터를 가진 리소스 유형이면 `STATEFUL`에 삭제 정책을 더한다.
- `tests/test_iac.py`의 `test_design_snapshot_exports_every_designer_resource`는 모든 설계 단계를 한 번씩 부르고 나온 CloudFormation 유형을 확인한다. `Unsupported`는 정확히 `{"LambdaFunction", "SageMakerEndpoint"}`여야 한다. 새 단계를 이 테스트에 넣고 기대 유형이나 `Unsupported` 집합을 고친다.

## 6. 테스트 (`tests/test_agent.py`)
- 도구 수: `test_start_from_current_uses_the_open_snapshot_then_the_last_collection`의 `len(tools) == 29`를 새 개수로 바꾼다. 이름 존재 단언은 `test_ai_designer_steps_place_lustre_and_sagemaker_by_the_lenses`처럼 `{t.tool_spec["name"] for t in build_tools(...)}`로 한다.
- 단계 동작: `Design()`에 `add_vpc` 후 새 메서드를 부르고 `d.graph()`의 노드·관계, `d.sample.raw`, `d.snapshot()["metadata"]["steps"]`를 확인한다. `test_a_missing_tier_takes_free_space_and_the_right_routes`가 본보기다.
- 오류 메시지: `test_design_errors_explain_what_to_fix`의 매개변수 목록에 `(lambda d: ..., "메시지 일부")`를 더한다.
- 표준 위에서 이어지는지: `test_every_standard_adopts_its_vpcs_and_takes_another_step`을 참고해, 표준이나 brownfield(`start_from_snapshot`)로 들여온 VPC에도 새 단계가 적용되는지 확인한다.
- 모델 없이 대화 전체를 시험하려면 파일 위쪽의 `Scripted` 모델로 `(도구 이름, 입력)` 순서를 재생한다.
- 오프라인 게이트(`make check`)로 전체를 돌린다.

## 바꿀 때 주의
- `Design` 메서드 이름과 `change()`에 넘기는 문자열이 같아야 한다(`getattr(d(), method)`).
- `record()`에 넘기는 키는 스냅샷 메타데이터로 남으므로 비밀이나 큰 값을 넣지 않는다.
- 단계가 brownfield VPC에서도 돌아야 한다. `vpc["private_tables"]`가 비어 있거나 `main_table`이 `None`일 수 있다는 점을 고려하고, 라우트가 필요하면 `routing_tables()`의 거부를 그대로 쓴다.

## 관련
- [[chat-agent]] — 도구 목록 전체, Workspace, 한 턴의 흐름.
- [[http-api]] — 도구가 만든 스냅샷이 나가는 스트림 이벤트와 CloudFormation 엔드포인트.
- [[storage]] — `publish_design`이 저장하는 스냅샷.
- [[cloudformation]] — `iac.Template` 단계와 `Unsupported` 규칙.
- [[graph]] — 새 `raw` 키를 노드·관계로 만드는 곳.
- [[snapshot-model]] — `raw` 키 목록과 `metadata.steps`.
- [[views]] — 새 종류를 서브넷 안에 그릴지 정하는 분류.
