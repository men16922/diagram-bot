# T5 프런트: React·Vite 연결

## 담당·예산
- GPT-6.1 Sol, effort medium. 상한 $12, 세션당 30턴, 세션 ≤3. 컨텍스트 추정 ~100K / 상한 150K.

## 목표
- 복사된 `http.js`·`relay.js`·`labels.js`와 draw.io iframe 브리지를 회사 React 앱에 연결한다.

## 읽을 것
- `docs/porting/RULES.md` (~0.4K)
- `docs/wiki/web-app.md` §draw.io 임베드 규약, §React로 옮길 때 (~0.8K)
- `<회사:React 엔트리 파일>`, `<회사:vite.config>`, `<회사:API 클라이언트 파일>`
- 전제: T0을 `--level 4 --static-dest <회사:웹 src>/diagram-bot`으로 실행. 편집기 설치는 오케스트레이터 S 단계.

```js
import { createClient } from './diagram-bot/js/http.js';  // {api, request, streamApi}; 헤더 X-Diagram-Bot
import { startRelay } from './diagram-bot/js/relay.js';
import { names, viewNames } from './diagram-bot/js/labels.js';
const { api } = createClient(() => token, { onUnauthorized: () => navigate('/login') });
useEffect(() => { if (boot.egress === 'browser') startRelay(api); }, [boot.egress]);  // 로그인 뒤 한 번
// vite.config: server.proxy = {'/api': '<회사:백엔드>', '/editor': '<회사:백엔드>'}
// iframe src (static/js/editor.js와 같게):
// /editor/index.html?embed=1&proto=json&configure=1&spin=1&ui=min&libraries=1&noSaveBtn=1&noExitBtn=1&saveAndExit=0&offline=1&local=1&stealth=1&lang=ko
```

## 하지 말 것
- 복사된 JS 수정. `fetch` 직접 호출(AWS 요청은 relay.js만).
- 화면 전체 재작성(대화상자 7개 등): 범위 밖, 필요하면 새 카드로 분할.
- 규격 미리보기(`?spec=`) XML을 PUT.

## 단계
1. API 모듈: `createClient`. 토큰·CSRF 헤더는 T4 보고대로.
2. 앱 최상단에서 `startRelay` 한 번.
3. 표시 이름은 `labels.js`.
4. 편집기 컴포넌트: 같은 출처 iframe, `message`에서 `event.origin`과 `event.source` 확인, JSON 문자열. `init` → `load`(xml), `autosave` → dirty, `save` → PUT(revision). 409면 새로고침 안내.
5. 브리지 메시지는 §draw.io 임베드 규약 그대로(`diagramBot*`). `app.min.js` 뒤에 `editor-bridge.js`를 붙여 내보내는 경로는 백엔드 정적 설정.
6. 컴포넌트 테스트 1개(`<회사:프런트 테스트 도구>`): `load` 전송, `save` → PUT 본문에 `revision`.

## 완료 조건
- `cd <회사:패키지 루트> && python -m diagram_bot.port_check --level 4`
- `<회사:프런트 대상 테스트 명령> 2>&1 | tail -20` (빌드는 오케스트레이터)

## 멈춤 조건
- RULES.md. 추가: 상태 관리 방식 결정, 편집기 배포 경로 결정.

## 보고
- RULES.md 형식. 넘길 것: 컴포넌트 파일, 빌드 명령. 다음: T6.
