import {names, relationNames, routeClasses, statuses, viewNames} from './js/labels.js';
import {createClient} from './js/http.js';
import {startRelay} from './js/relay.js';
const $ = id => document.getElementById(id);
const state = {token:null,snapshot:null,snapshotId:null,view:'overview',vpc:null,focus:null,references:false,routes:null,revision:0,xml:null,history:[],visible:null,counts:null,ready:false,loading:true,dirty:false,busy:false,mode:'diagram',selected:null};
const {api, streamApi} = createClient(() => state.token);
window.diagramBot = {state};  // for browser checks (scripts/verify_demo.py) and debugging
const pending = new Map();
let editorTimer, toastTimer, pngWaiter = null;
const emptyDetail=document.querySelector('#detail-body .empty-detail');
function el(tag, text, className) { const element=document.createElement(tag); if(text!==undefined)element.textContent=text; if(className)element.className=className; return element; }
// Notices describe the current diagram; a new snapshot or view drops them (errors stay until they time out).
function clearNotice() { if($('toast').className!=='error'){$('toast').hidden=true;clearTimeout(toastTimer);} }
function toast(message, error=false) { $('toast').textContent=message; $('toast').className=error?'error':''; $('toast').hidden=false; clearTimeout(toastTimer); toastTimer=setTimeout(()=>$('toast').hidden=true,error?8500:4000); }
function post(message) { $('editor').contentWindow.postMessage(JSON.stringify(message),location.origin); }
const node = id => state.snapshot.graph.nodes.find(n=>n.id===id);
const vpcNode = resourceId => state.snapshot.graph.nodes.find(n=>n.kind==='vpc'&&n.resource_id===resourceId);
function documentUrl(suffix='', reset=false) {
  const query=new URLSearchParams({view:state.view,references:String(state.references)});
  if(state.view==='detail')query.set('vpc',state.vpc);
  if(state.view==='detail'&&state.routes)query.set('routes',state.routes);
  if(state.view==='focus')query.set('resource',state.focus);
  if(reset)query.set('reset','true');
  return `/api/snapshots/${state.snapshotId}/document${suffix}?${query}`;
}
function setControls() {
  for(const id of ['save-button','export-button','note-button','reset-button'])$(id).disabled=!state.ready||state.loading||state.busy;
  $('restore-button').disabled=!state.ready||state.loading||state.busy||!state.history.length;
  for(const id of ['snapshot-select','references','route-filter','import-button'])$(id).disabled=state.busy||(state.ready&&state.loading);
  // Team mode roles: viewers read only, editors edit and import, admins also collect from AWS.
  if(state.role==='viewer')for(const id of ['save-button','note-button','reset-button','restore-button','import-button','combine-button'])$(id).disabled=true;
  $('collection-button').disabled=state.role!=='admin';$('signin-button').disabled=state.role!=='admin';$('chat-button').disabled=state.role==='viewer';
  const locked=state.busy||(state.ready&&state.loading);
  $('view-overview').disabled=locked;$('view-detail').disabled=locked||!state.vpc;$('view-focus').disabled=locked||!state.selected;
  $('save-state').textContent=state.loading?'불러오는 중':state.busy?'처리 중':state.dirty?'저장하지 않은 편집':state.revision?`저장됨 · v${state.revision}`:'자동 생성 구성도';
}
function sendDocument() {
  // draw.io focuses itself when a document loads and the browser would scroll the page to the iframe; keep the user's place.
  state.scrollY=window.scrollY;state.loading=true;setControls();$('editor-overlay').hidden=false;
  post({action:'load',xml:state.xml,autosave:1,title:'Diagram Bot',saveAndExit:0,noSaveBtn:1,noExitBtn:1,fit:1,maxFitScale:1,border:24});
  clearTimeout(editorTimer);
  editorTimer=setTimeout(()=>{state.loading=false;state.ready=false;setControls();$('editor-message').textContent='편집기 응답이 지연되고 있습니다. 새로고침하거나 setup-editor 설치를 확인하세요.';},30000);
}
function requestDocument() {
  if(!state.ready||state.loading)return Promise.reject(new Error('구성도를 불러온 뒤 다시 시도하세요.'));
  const requestId=crypto.randomUUID();
  return new Promise((resolve,reject)=>{
    const timer=setTimeout(()=>{pending.delete(requestId);reject(new Error('편집본을 읽지 못했습니다. 다시 시도하세요.'));},15000);
    pending.set(requestId,{resolve,reject,timer});
    post({action:'diagramBotDocument',requestId});
  });
}
window.addEventListener('message', event=>{
  if(event.origin!==location.origin||event.source!==$('editor').contentWindow)return;
  let message;try{message=typeof event.data==='string'?JSON.parse(event.data):event.data;}catch{return;}
  if(!message)return;
  if(message.event==='configure')post({action:'configure',config:{defaultFonts:['Arial','Helvetica','sans-serif'],defaultLibraries:'general',defaultCompressed:false}});
  if(message.event==='init'){state.ready=true;window.scrollTo(0,0);if(state.xml)sendDocument();}
  if(message.event==='load'){
    clearTimeout(editorTimer);state.loading=false;state.dirty=false;$('editor-overlay').hidden=true;setControls();
    requestAnimationFrame(()=>window.scrollTo(0,state.scrollY||0));
    post({action:'diagramBotOverview'});
  }
  if(message.event==='autosave'&&!state.loading){state.dirty=true;setControls();}
  if(message.event==='save'&&!state.loading)saveDocument().catch(e=>toast(e.message,true));
  if(message.event==='diagramBotDocument'||message.event==='diagramBotError'){
    const task=pending.get(message.requestId);if(task){clearTimeout(task.timer);pending.delete(message.requestId);message.error?task.reject(new Error(message.error)):task.resolve(message.xml);}
    else if(message.error)toast(message.error,true);
  }
  if(message.event==='diagramBotSelection'){
    if(message.resourceId)showResource(message.resourceId,false);
    else if(message.relationId||message.relationIndex!=null)showRelation(message.relationId,message.relationIndex);
    else if(message.label){$('detail-body').replaceChildren(el('div','DIAGRAM ELEMENT','detail-kind'),el('h3',message.label,'detail-name'),el('p','표현용 그룹, 요약 또는 사용자 주석입니다. 수집한 AWS 속성은 변경되지 않습니다.','small-note'));}
  }
  if(message.event==='diagramBotNotVisible')toast('현재 보기에서 숨겨진 리소스입니다. 속성 패널에서 내용을 확인하거나 선택 리소스 보기를 여세요.');
  if(message.event==='diagramBotHighlighted')toast(message.count?`직접 관계 ${message.count}개를 구성도에서 강조했습니다.`:'이 보기에 그려진 직접 관계가 없습니다. 선택 리소스 보기에서 확인하세요.');
  if(message.event==='diagramBotNoteAdded')toast('메모를 추가했습니다. 더블클릭해 내용을 수정하세요.');
  if(message.event==='export'&&pngWaiter){
    const task=pngWaiter;pngWaiter=null;clearTimeout(task.timer);
    if(message.data?.startsWith('data:image/png;base64,'))task.resolve(message.data);else task.reject(new Error('PNG를 생성하지 못했습니다. 해상도를 낮춰 다시 시도하세요.'));
  }
});
function snapshotLabel(item){const m=item.metadata;if(m.combined&&!m.sample)return `통합 · ${m.title} · ${m.finished_at.slice(5,16).replace('T',' ')}`;if(m.design)return `설계 · ${m.title} · ${m.finished_at.slice(5,16).replace('T',' ')}`;return m.title?`샘플 · ${m.title}`:`${m.demo?'샘플':'AWS'} · ${m.account_id} · ${m.region} · ${m.finished_at.slice(5,16).replace('T',' ')}`;}
async function refreshSnapshots(preferred) {
  const items=await api('/api/snapshots');state.snapshots=items;$('snapshot-select').replaceChildren();
  for(const item of items)$('snapshot-select').append(new Option(snapshotLabel(item),item.id));
  $('snapshot-select').value=preferred||items[0]?.id;
  return $('snapshot-select').value;
}
async function loadSnapshot(id) {
  if(state.dirty)await saveDocument(false);
  clearNotice();
  state.snapshot=await api(`/api/snapshots/${id}`);state.snapshotId=id;
  state.review=await api(`/api/snapshots/${id}/findings`);renderReview();
  // The overview shows how VPCs connect; with one VPC and no TGW, peering or VPN it is a single card, so open its detail.
  const graphNodes=state.snapshot.graph.nodes,vpcs=graphNodes.filter(n=>n.kind==='vpc');
  const linked=graphNodes.some(n=>['transit_gateway','vpc_peering','vpn_gateway','customer_gateway','dx_gateway'].includes(n.kind));
  state.view=vpcs.length===1&&!linked?'detail':'overview';state.vpc=vpcs[0]?.resource_id||null;state.focus=null;state.selected=null;state.routes=null;
  $('snapshot-select').value=id;
  // A selection from the previous snapshot must not linger in the inspector.
  $('detail-body').replaceChildren(emptyDetail.cloneNode(true));
  renderSummary();await loadView();
  if(state.mode==='changes')await renderChanges();
}
function renderSummary() {
  const {metadata:m,graph}=state.snapshot;
  $('region-label').textContent=m.region;$('crumb-account').textContent=m.design?'설계안':m.combined?m.account_id:`계정 ${m.account_id}`;$('account-summary').textContent=m.design?'DESIGN · 실제 계정 아님':`ACCOUNT ${m.account_id}`;
  $('demo-badge').hidden=!m.demo;$('demo-badge').textContent=m.design?'설계안':'샘플 데이터';
  $('scope-description').textContent=m.design?`${m.region} · 채팅으로 만든 표준 아키텍처 설계안 (실제 계정 아님)`:`${m.account_id} · ${m.region} · 수집 설정을 바탕으로 생성한 구성도`;
  $('collected-at').textContent=`수집 ${new Date(m.finished_at).toLocaleString('ko-KR')} · ${m.demo?'합성 샘플':'AWS API 조회'}`;
  $('coverage-button').textContent=(m.status==='complete'?'● ':'◐ ')+(statuses[m.status]||m.status);
  $('coverage-button').classList.toggle('complete',m.status==='complete');
  $('partial-chip').hidden=m.status==='complete';
  const count=kind=>graph.nodes.filter(n=>n.kind===kind&&!n.reference_only).length;
  $('metrics').replaceChildren();
  for(const [label,value,icon,unit] of [['VPC ENVIRONMENTS',count('vpc'),'▦','개 환경'],['SUBNETS',count('subnet'),'⊞','개 서브넷'],['COMPUTE',count('ec2')+count('ecs_task'),'▣',count('ecs_task')?'개 인스턴스·태스크':'개 인스턴스'],['LOAD BALANCERS',count('load_balancer'),'⇄','개 로드밸런서']]){
    const box=el('div',undefined,'metric');box.append(el('div',icon,'metric-icon'));const content=el('div');content.append(el('div',label,'metric-label'));const number=el('div',String(value),'metric-number');number.append(el('small',unit));content.append(number);box.append(content);$('metrics').append(box);
  }
  $('environments').replaceChildren();const vpcs=graph.nodes.filter(n=>n.kind==='vpc');$('environment-count').textContent=vpcs.length;
  for(const vpc of vpcs){const button=el('button',undefined,'environment');button.dataset.vpc=vpc.resource_id;const label=el('div',vpc.name);label.append(el('small',vpc.attributes.CidrBlock||vpc.resource_id));button.append(el('span','◇'),label);button.onclick=()=>switchView('detail',{vpc:vpc.resource_id});$('environments').append(button);}
  $('kind-filter').replaceChildren(new Option('모든 리소스','all'));
  for(const kind of [...new Set(graph.nodes.map(n=>n.kind))])$('kind-filter').append(new Option(names[kind]||kind,kind));
  $('total-count').textContent=graph.nodes.length;
  $('coverage-rows').replaceChildren();
  for(const row of state.snapshot.coverage){const container=el('div',undefined,'coverage-row');const text=el('div',row.operation);text.append(el('small',`${row.items??'—'}개 · ${row.pages??'—'} 페이지${row.error_code?' · '+row.error_code:''}${row.status!=='complete'&&row.permission?' · 필요 권한 '+row.permission:''}`));container.append(text,el('span',statuses[row.status]||row.status,'badge'+(row.status!=='complete'?' warn':'')));$('coverage-rows').append(container);}
  $('limitations').replaceChildren(...[...(m.practices||[]).map(s=>'반영한 권장 사항: '+s),...(m.limitations||[])].map(s=>el('li',s)));
}
function applyDocument(document) {
  state.xml=document.xml;state.revision=document.revision;state.history=document.history||[];state.visible=new Set(document.visible||[]);state.counts=document.counts;
  const c=document.counts;
  $('view-scope').textContent=c?`표시 ${c.visible}개 / 수집 ${c.collected}개 · 참조 ${c.references}개 · 이 보기에서 숨김 ${c.hidden}개`+(c.filtered?` (경로 필터 ${c.filtered}개 포함)`:''):'';
}
// Detail views can be narrowed to one load balancer's or target group's routes; the options are this VPC's.
function renderRouteFilter() {
  const vpc=state.view==='detail'?vpcNode(state.vpc):null;
  const inVpc=new Set(vpc?state.snapshot.graph.edges.filter(e=>e.relation==='in_vpc'&&e.target===vpc.id).map(e=>e.source):[]);
  const options=kind=>state.snapshot.graph.nodes.filter(n=>n.kind===kind&&inVpc.has(n.id)).sort((a,b)=>a.name.localeCompare(b.name));
  const lbs=options('load_balancer'),groups=options('target_group');
  $('route-filter-label').hidden=!lbs.length&&!groups.length;
  $('route-filter').replaceChildren(new Option('모든 경로',''));
  for(const [label,items] of [['로드 밸런서',lbs],['대상 그룹',groups]]){if(!items.length)continue;const group=el('optgroup');group.label=label;for(const n of items)group.append(new Option(n.name,n.id));$('route-filter').append(group);}
  $('route-filter').value=state.routes||'';
}
async function loadView() {
  state.loading=true;setControls();
  try{
    applyDocument(await api(documentUrl()));state.dirty=false;
    const title=state.view==='detail'?vpcNode(state.vpc)?.name:state.view==='focus'?node(state.focus)?.name:'AWS 전체 구성';
    $('environment-title').textContent=title||'AWS 구성도';$('crumb-view').textContent={overview:'전체 구성',detail:'상세 구성',focus:'선택 리소스'}[state.view];
    documentActive();if(state.selected&&node(state.selected))showResource(state.selected,false);
    if(state.ready)sendDocument();else{state.loading=false;setControls();}
  }catch(error){state.loading=false;setControls();throw error;}
}
function documentActive() {
  $('all-environments').classList.toggle('active',state.view==='overview');
  document.querySelectorAll('[data-vpc]').forEach(b=>b.classList.toggle('active',state.view==='detail'&&b.dataset.vpc===state.vpc));
  for(const view of Object.keys(viewNames))$('view-'+view).classList.toggle('active',state.view===view);
  renderRouteFilter();setControls();renderInventory();
}
async function switchView(view, options={}) {
  if(state.loading||state.busy)return;
  clearNotice();
  try{
    if(state.dirty)await saveDocument(false);
    // A route filter belongs to one VPC's detail view.
    if(view!=='detail'||(options.vpc&&options.vpc!==state.vpc))state.routes=null;
    state.view=view;if(options.vpc)state.vpc=options.vpc;if(options.focus)state.focus=options.focus;
    await loadView();
  }catch(error){toast(error.message,true);}
}
async function saveDocument(notify=true) {
  if(state.role==='viewer'){state.dirty=false;return;}
  if(state.busy)throw new Error('이전 작업을 완료한 뒤 다시 시도하세요.');
  state.busy=true;setControls();
  try{
    const xml=await requestDocument();
    const saved=await api(documentUrl(),{method:'PUT',body:JSON.stringify({xml,revision:state.revision})});
    applyDocument(saved);state.dirty=false;if(notify)toast('편집본을 로컬에 저장했습니다.');
    return xml;
  }finally{state.busy=false;setControls();}
}
async function replaceDocument(suffix, body, message) {
  if(state.dirty)await saveDocument(false);
  state.busy=true;setControls();
  try{applyDocument(await api(documentUrl(suffix),{method:'POST',body:JSON.stringify({revision:state.revision,...body})}));}
  finally{state.busy=false;}
  sendDocument();toast(message);
}
function matchingResources() {
  if(!state.snapshot)return[];
  const search=$('search').value.toLowerCase(),kind=$('kind-filter').value;
  return state.snapshot.graph.nodes.filter(n=>(state.view==='overview'||state.visible?.has(n.id))&&(!n.reference_only||state.references||state.visible?.has(n.id))&&(kind==='all'||n.kind===kind)&&JSON.stringify([n.name,n.resource_id,n.attributes]).toLowerCase().includes(search));
}
function renderInventory() {
  const matches=matchingResources();$('inventory-rows').replaceChildren();
  for(const item of matches){const row=el('tr');row.tabIndex=0;row.append(el('td',item.name),el('td',names[item.kind]),el('td',item.resource_id),el('td',item.reference_only?'참조만 확인':item.source));row.onclick=()=>showResource(item.id);row.onkeydown=e=>{if(e.key==='Enter')showResource(item.id);};$('inventory-rows').append(row);}
  $('inventory-empty').hidden=matches.length>0;
  const active=Boolean($('search').value)||$('kind-filter').value!=='all';$('search-results').hidden=!active||state.mode==='inventory';$('search-results').replaceChildren();
  if(active){$('search-results').append(el('span',`${viewNames[state.view]} 범위 · ${matches.length}개`,'search-scope'));for(const item of matches.slice(0,30)){const button=el('button',item.name);button.onclick=()=>showResource(item.id);$('search-results').append(button);}if(!matches.length)$('search-results').append(el('span','검색 결과가 없습니다.'));}
}
const severityNames={high:'높음',medium:'중간',low:'낮음'},pillarNames={reliability:'안정성',security:'보안',operations:'운영',ai:'AI/ML',cost:'비용'};
function renderReview() {
  const {summary,findings}=state.review||{summary:{},findings:[]};
  $('finding-count').textContent=summary.high?`${summary.high} 높음`:findings.length;
  $('review-summary').replaceChildren(...['high','medium','low'].map(s=>el('span',`${severityNames[s]} ${summary[s]||0}`,'sev-'+s)));
  const filter=$('severity-filter').value,rows=findings.filter(f=>filter==='all'||f.severity===filter);
  $('review-rows').replaceChildren(...rows.map(f=>{const item=el('li'),button=el('button',undefined,'finding'),top=el('div',undefined,'finding-top');
    top.append(el('span',`${severityNames[f.severity]} · ${pillarNames[f.pillar]}`,'chip sev-'+f.severity),el('span',`${f.resource_name} — ${f.title}`),el('code',f.rule));
    button.append(top,el('small','근거: '+f.evidence),el('em','권장: '+f.recommendation));button.dataset.finding=f.id;
    button.onclick=()=>{if(f.resource&&node(f.resource))showResource(f.resource);};item.append(button);return item;}));
  $('review-empty').hidden=rows.length>0;
}
async function renderChanges() {
  const others=state.snapshots.filter(s=>s.id!==state.snapshotId),select=$('compare-select'),previous=select.value;
  select.replaceChildren(...others.map(s=>new Option(snapshotLabel(s),s.id)));
  // Default: the latest earlier snapshot of the same account and region, else any of the same scope, else the latest.
  const scope=others.filter(s=>s.metadata.account_id===state.snapshot.metadata.account_id&&s.metadata.region===state.snapshot.metadata.region);
  const sameScope=scope.find(s=>s.metadata.finished_at<state.snapshot.metadata.finished_at)||scope[0];
  select.value=others.some(s=>s.id===previous)?previous:(sameScope||others[0])?.id||'';
  if(!select.value){$('changes-rows').replaceChildren();$('changes-summary').replaceChildren();$('changes-empty').hidden=false;return;}
  const result=await api(`/api/snapshots/${state.snapshotId}/diff/${select.value}`),s=result.summary;
  $('changes-summary').replaceChildren(el('span',`추가 ${s.added}`,'sev-low'),el('span',`삭제 ${s.removed}`,'sev-high'),el('span',`설정 변경 ${s.changed}`,'sev-medium'),el('span',`관계 +${s.relations_added} / −${s.relations_removed}`,'sev-low'));
  $('changes-note').textContent=(result.comparable?'':'계정 또는 리전이 달라 모든 리소스가 추가·삭제로 보입니다. ')+(result.before.status!=='complete'||result.after.status!=='complete'?'부분 조회 스냅샷이 있어 삭제로 보이는 항목은 권한 문제일 수 있습니다. ':'')+'상태 값(헬스, 실행 수 등)은 비교하지 않습니다.';
  const row=(chip,cls,title,detail,id)=>{const item=el('li'),button=el('button',undefined,'finding'),top=el('div',undefined,'finding-top');top.append(el('span',chip,'chip '+cls),el('span',title));button.append(top);if(detail)button.append(el('small',detail));button.onclick=()=>{if(id&&node(id))showResource(id);};item.append(button);return item;};
  $('changes-rows').replaceChildren(
    ...result.added.map(n=>row('추가','sev-low',`${names[n.kind]||n.kind} · ${n.name}`,n.resource_id,n.id)),
    ...result.removed.map(n=>row('삭제','sev-high',`${names[n.kind]||n.kind} · ${n.name}`,n.resource_id)),
    ...result.changed.map(n=>row('변경','sev-medium',`${names[n.kind]||n.kind} · ${n.name}`,n.fields.map(f=>`${f.field}: ${f.before} → ${f.after}`).join('  ·  '),n.id)),
    ...result.relations_added.map(r=>row('관계 +','sev-low',`${r.source} — ${relationNames[r.relation]||r.relation} → ${r.target}`,r.evidence)),
    ...result.relations_removed.map(r=>row('관계 −','sev-high',`${r.source} — ${relationNames[r.relation]||r.relation} → ${r.target}`,r.evidence)));
  $('changes-empty').hidden=Object.values(s).some(Boolean);
}
function relationsOf(id) {return state.snapshot.graph.edges.filter(e=>(e.source===id||e.target===id)&&e.relation!=='in_vpc');}
function actionButton(label, handler) {const button=el('button',label,'button');button.onclick=()=>Promise.resolve().then(handler).catch(error=>toast(error.message,true));return button;}
function showResource(id, select=true) {
  const item=node(id);if(!item)return;state.selected=id;setControls();
  const body=$('detail-body');body.replaceChildren(el('div',names[item.kind]||item.kind,'detail-kind'),el('h3',item.name,'detail-name'),el('div',item.resource_id,'detail-id'));
  body.append(el('p',item.reference_only?'참조만 확인 · 상세 미조회':'API 응답에서 조회됨','small-note'));
  const actions=el('div',undefined,'inspector-actions');
  if(item.kind==='vpc'&&!(state.view==='detail'&&state.vpc===item.resource_id))actions.append(actionButton('상세 구성 보기',()=>switchView('detail',{vpc:item.resource_id})));
  if(item.kind!=='vpc'&&!(state.view==='focus'&&state.focus===id))actions.append(actionButton('선택 리소스 보기',()=>switchView('focus',{focus:id})));
  if(state.view==='focus')actions.append(actionButton('상세 구성으로 돌아가기',()=>switchView('detail',{vpc:vpcOf(state.focus)||state.vpc})));
  if(state.view==='detail'&&['load_balancer','target_group'].includes(item.kind)&&state.routes!==id&&vpcOf(id)===state.vpc)
    actions.append(actionButton('이 경로만 보기',async()=>{if(state.loading||state.busy)return;if(state.dirty)await saveDocument(false);state.routes=id;await loadView();}));
  if(state.view==='detail'&&state.routes)actions.append(actionButton('모든 경로 보기',async()=>{if(state.loading||state.busy)return;if(state.dirty)await saveDocument(false);state.routes=null;await loadView();}));
  const related=relationsOf(id).map(e=>e.id);
  if(related.length&&state.view!=='overview')actions.append(actionButton('직접 관계 강조',()=>post({action:'diagramBotSelect',resourceId:id,relations:related})));
  if(actions.children.length)body.append(actions);
  const fields=[['계정',item.account],['리전',item.region],['인스턴스 유형',item.attributes.InstanceType],['EFA',(item.attributes.NetworkInterfaces||[]).some(i=>i.InterfaceType==='efa')?'EFA 인터페이스 있음':undefined],['배치 그룹',item.attributes.Placement?.GroupName],['용량',item.attributes.InstanceLifecycle==='capacity-block'?'Capacity Block':item.attributes.CapacityReservationId?`ODCR ${item.attributes.CapacityReservationId}`:undefined],['배치 전략',item.kind==='placement_group'?item.attributes.Strategy:undefined],['CIDR',item.attributes.CidrBlock],['AZ',item.attributes.AvailabilityZone||item.attributes.Placement?.AvailabilityZone],['상태',item.attributes.State?.Name||item.attributes.State?.Code],['프로토콜',item.attributes.Protocol],['포트',item.attributes.Port],['VPC',item.attributes.VpcId],['서브넷',item.attributes.SubnetId],['스킴',item.attributes.Scheme],['라우팅',routeClasses[item.route_class]],['엔진',item.attributes.Engine],['역할',item.db_role||item.attributes.CurrentRole],['노드 유형',item.attributes.CacheNodeType||item.attributes.InstanceType],['버전',item.attributes.version||item.attributes.KafkaVersion||item.attributes.EngineVersion],['런타임',item.attributes.Runtime],['서비스',item.attributes.ServiceName],['엔드포인트 유형',item.attributes.VpcEndpointType],['DB 클래스',item.attributes.DBInstanceClass],['Multi-AZ',item.attributes.MultiAZ],['용량',item.attributes.DesiredCapacity!==undefined?`${item.attributes.MinSize}–${item.attributes.MaxSize} (원하는 ${item.attributes.DesiredCapacity})`:undefined],['읽기 복제본 원본',item.attributes.ReadReplicaSourceDBInstanceIdentifier],['읽기 복제본',(item.attributes.ReadReplicaDBInstanceIdentifiers||[]).join(', ')],['샤드',item.attributes.Shard],['자동 장애 조치',item.attributes.AutomaticFailover],['노드 (AZ)',item.kind==='search_node'?`data ${item.attributes.Data??'?'} · master ${item.attributes.Master??'?'}${item.attributes.Standby?' · standby':''}${item.attributes.Estimated?' (추정)':''}`:undefined],['Multi-AZ with Standby',item.attributes.MultiAZWithStandby],['브로커',(item.attributes.BrokerIds||[]).join(', ')],['태스크',item.attributes.desiredCount!==undefined?`실행 ${item.attributes.runningCount} / 원하는 ${item.attributes.desiredCount}`:undefined],['시작 유형',item.attributes.LaunchSummary||item.attributes.launchType],['IP',item.attributes.PrivateIp||item.attributes.IpAddress],['EFS 클래스',item.kind==='efs_mount_target'?(item.attributes.OneZone?'One Zone':'Regional'):undefined],['처리량 모드',item.attributes.ThroughputMode],['Redshift 역할',item.attributes.Role],['노드',item.attributes.NodesHere!==undefined?`${item.attributes.NodeType} × ${item.attributes.NodesHere}`:undefined],['피어링',item.kind==='vpc_peering'?`${item.attributes.RequesterVpcInfo?.CidrBlock} ↔ ${item.attributes.AccepterVpcInfo?.CidrBlock} (${item.attributes.Status?.Code})`:undefined],['인바운드 규칙',item.kind==='security_group'?(item.attributes.IpPermissions||[]).map(r=>`${r.IpProtocol==='-1'?'모든 트래픽':`${r.IpProtocol} ${r.FromPort===r.ToPort?r.FromPort:r.FromPort+'-'+r.ToPort}`} ← ${[...(r.IpRanges||[]).map(x=>x.CidrIp),...(r.Ipv6Ranges||[]).map(x=>x.CidrIpv6),...(r.UserIdGroupPairs||[]).map(x=>x.GroupId),...(r.PrefixListIds||[]).map(x=>x.PrefixListId)].join(', ')}`).join(' · ')||'없음':undefined],['VPN 터널',(item.attributes.VpnTunnels||[]).map(t=>t+' UP').join(', ')],['BGP ASN',item.attributes.BgpAsn||item.attributes.AmazonSideAsn],['Elastic IP',(item.elastic_ips||[]).join(', ')],...(item.properties||[])];
  const list=el('dl');for(const [key,value]of fields)if(value!==undefined&&value!==null&&value!==''){const field=el('div',undefined,'detail-field');field.append(el('dt',key),el('dd',String(value)));list.append(field);}body.append(list);
  if(item.attributes.Tags?.length){body.append(el('div','TAGS','detail-section'));for(const tag of item.attributes.Tags)body.append(el('p',`${tag.Key} = ${tag.Value}`,'small-note'));}
  body.append(el('div',`API에서 확인한 관계 · ${related.length}개`,'detail-section'));
  for(const edge of relationsOf(id)){
    const other=node(edge.source===id?edge.target:edge.source);
    const qualifier=[edge.qualifiers?.port&&`:${edge.qualifiers.port}`,edge.qualifiers?.availability_zone,edge.qualifiers?.destination].filter(Boolean).join(' · ');
    const button=el('button',`${relationNames[edge.relation]||edge.relation} · ${other.name}${qualifier?' · '+qualifier:''}`,'relation-button');button.append(el('small',edge.evidence));
    button.onclick=()=>{showRelation(edge.id);if(state.ready&&!state.loading)post({action:'diagramBotSelectRelation',relationId:edge.id});};body.append(button);
  }
  const raw=el('details');raw.append(el('summary','원본 속성 보기'),el('pre',JSON.stringify(item.attributes,null,2)));body.append(raw);
  if(select&&state.ready&&!state.loading)post({action:'diagramBotSelect',resourceId:id});
}
function vpcOf(id) {const edge=state.snapshot.graph.edges.find(e=>e.source===id&&e.relation==='in_vpc');return edge?node(edge.target)?.resource_id:null;}
function showRelation(id, index) {
  const edges=state.snapshot.graph.edges;
  const edge=id?edges.find(e=>e.id===id):edges[Number(index)];if(!edge)return;
  const body=$('detail-body');
  body.replaceChildren(el('div','CONFIGURATION RELATION','detail-kind'),el('h3',relationNames[edge.relation]||edge.relation,'detail-name'),el('p','AWS API에서 확인한 설정 관계입니다. 실제 통신 여부를 의미하지 않습니다.','small-note'));
  for(const key of ['source','target']){const other=node(edge[key]);if(other){const button=el('button',`${key==='source'?'출발':'대상'} · ${names[other.kind]||other.kind} · ${other.name}`,'relation-button');button.onclick=()=>showResource(other.id);body.append(button);}}
  body.append(el('pre',JSON.stringify(edge,null,2)));
}
function download(name, content, type) {
  const url=URL.createObjectURL(new Blob([content],{type}));const link=el('a');link.href=url;link.download=name;link.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
}
function filename(extension) {const scope=state.view==='detail'?state.vpc+(state.routes?`-routes-${node(state.routes)?.name}`:''):state.view==='focus'?`focus-${node(state.focus)?.resource_id.split('/').pop()}`:'overview';return `diagram-bot-${state.snapshot.metadata.account_id}-${scope}-v${state.revision}.${extension}`;}
async function downloadDiagram() {
  const xml=await saveDocument(false);download(filename('drawio'),xml,'application/xml');toast('편집 가능한 draw.io 파일을 다운로드했습니다.');
}
async function downloadPng() {
  const xml=await saveDocument(false);state.busy=true;setControls();$('download-png').disabled=true;
  try{
    const uri=await new Promise((resolve,reject)=>{
      const timer=setTimeout(()=>{pngWaiter=null;reject(new Error('PNG 생성 시간이 초과됐습니다. 해상도를 낮춰 다시 시도하세요.'));},45000);
      pngWaiter={resolve,reject,timer};
      post({action:'export',format:'png',xml,scale:Number($('png-scale').value),border:24,grid:false,transparent:$('png-transparent').checked,background:$('png-transparent').checked?null:'#FFFFFF',size:'diagram',currentPage:true});
    });
    const bytes=Uint8Array.from(atob(uri.split(',')[1]),c=>c.charCodeAt(0));
    if(bytes.length<1000)throw new Error('PNG가 비어 있습니다. 다시 시도하세요.');
    download(filename('png'),bytes,'image/png');toast('PNG 이미지를 다운로드했습니다.');
  }finally{state.busy=false;setControls();$('download-png').disabled=false;}
}
function csvCell(value){let text=typeof value==='object'?JSON.stringify(value):String(value??'');if(/^\s*[=+\-@]/.test(text))text="'"+text;return '"'+text.replaceAll('"','""')+'"';}
function downloadCsv(){const fields=['account','region','kind','resource_id','name','reference_only','source','attributes'];const rows=[fields,...state.snapshot.graph.nodes.map(n=>fields.map(f=>n[f]))];download('inventory.csv','﻿'+rows.map(r=>r.map(csvCell).join(',')).join('\r\n'),'text/csv;charset=utf-8');}
// CloudFormation draft from the stored snapshot (not the edited diagram); a combined snapshot gives one template per account × region.
async function downloadCloudFormation(){
  const parts=state.snapshot.metadata.parts||[null];let unsupported=0;
  for(const [index,part] of parts.entries()){
    const response=await fetch(`/api/snapshots/${state.snapshotId}/cloudformation${part?`?part=${index}`:''}`,{headers:{'X-Diagram-Bot':state.token}});
    const text=await response.text();
    if(!response.ok)throw new Error(JSON.parse(text).detail||'템플릿을 만들지 못했습니다.');
    unsupported+=JSON.parse(text).Metadata.DiagramBot.Unsupported.length;
    download(response.headers.get('Content-Disposition')?.match(/filename="([^"]+)"/)?.[1]||'cloudformation.json',text,'application/json');
  }
  toast(`CloudFormation 템플릿 초안 ${parts.length}개를 다운로드했습니다. 매개변수와 Metadata.DiagramBot(미지원 ${unsupported}개·가정)을 검토하세요.`);
}
function bind(id, handler) {$(id).onclick=()=>Promise.resolve().then(handler).catch(error=>toast(error.message,true));}
bind('all-environments',()=>switchView('overview'));
bind('view-overview',()=>switchView('overview'));
bind('view-detail',()=>switchView('detail',{vpc:state.vpc}));
bind('view-focus',()=>switchView('focus',{focus:state.selected}));
bind('save-button',()=>saveDocument());
bind('export-button',()=>{$('export-dialog').showModal();});
bind('coverage-button',()=>{$('coverage-dialog').showModal();});
// Profile menu (top right): AWS sign-in (SSO or local credentials) and collection with the signed-in identity.
function closeProfile(){$('profile-menu').hidden=true;$('profile-button').setAttribute('aria-expanded','false');}
$('profile-button').onclick=event=>{event.stopPropagation();const open=$('profile-menu').hidden;$('profile-menu').hidden=!open;$('profile-button').setAttribute('aria-expanded',String(open));};
document.addEventListener('click',event=>{if(!$('profile-menu').hidden&&!event.target.closest('.profile'))closeProfile();});
function identityText(){
  const id=state.identity;
  if(!id)return {title:'AWS에 로그인하지 않음',detail:'SSO 또는 로컬 자격 증명으로 로그인하면 계정을 읽기 전용으로 수집할 수 있습니다. 로그인하지 않으면 기본 SDK 인증 체인을 씁니다.',label:'로그인'};
  if(id.via==='sso')return {title:`${id.role} @ ${id.account}`,detail:`IAM Identity Center(SSO) · ${id.accountName||id.account}`,label:`${id.role} @ ${id.account} (SSO)`};
  const name=id.arn.split('/').pop()||id.arn;
  return {title:`${name} @ ${id.account}`,detail:`${id.profile?`프로파일 ${id.profile}`:'기본 SDK 인증 체인'} · ${id.arn}`,label:`${name} @ ${id.account}`};
}
function renderProfile(){
  const text=identityText();$('profile-label').textContent=text.label;$('profile-title').textContent=text.title;$('profile-detail').textContent=text.detail;
  $('profile-signout').hidden=!state.identity;$('signin-button').textContent=state.identity?'다른 자격 증명으로 로그인':'AWS 로그인 (SSO · 프로파일)';
  document.querySelector('.profile').classList.toggle('signed-in',!!state.identity);
  if(!state.team)$('user-avatar').textContent=state.identity?text.title.slice(0,2).toUpperCase():'DB';
}
bind('signin-button',async()=>{closeProfile();$('signin-dialog').showModal();
  if(!state.ssoDefaults){state.ssoDefaults=await api('/api/sso/defaults').catch(()=>[]);
    $('sso-start-urls').replaceChildren(...state.ssoDefaults.map(d=>new Option(d.name,d.start_url)));
    if(state.ssoDefaults[0]&&!$('sso-start-url').value){$('sso-start-url').value=state.ssoDefaults[0].start_url;$('sso-region').value=state.ssoDefaults[0].region;}}});
$('close-signin').onclick=()=>$('signin-dialog').close();
bind('sso-use',()=>{
  if(!$('sso-role').value)throw new Error('SSO 역할을 선택하세요. 이 계정에 할당된 역할이 없다면 Identity Center 관리자에게 요청하세요.');
  const option=$('sso-account').selectedOptions[0];
  state.identity={via:'sso',account:$('sso-account').value,role:$('sso-role').value,accountName:option?.textContent};
  api(`/api/sso/login/${state.sso.id}/use`,{method:'POST',body:JSON.stringify({account_id:state.identity.account,role:state.identity.role})}).catch(error=>toast(error.message,true));
  $('aws-profile').value='';renderProfile();$('signin-dialog').close();toast(`SSO로 로그인했습니다 · ${state.identity.role} @ ${state.identity.account}`);
});
bind('use-profile',async()=>{
  $('signin-status').textContent='자격 증명을 확인하고 있습니다…';
  try{const profile=$('aws-profile').value;const who=await api(`/api/identity${profile?`?profile=${encodeURIComponent(profile)}`:''}`);
    if(state.sso)await api(`/api/sso/login/${state.sso.id}/logout`,{method:'POST'}).catch(()=>{});state.sso=null;ssoView('form');
    state.identity={via:'profile',profile,account:who.account,arn:who.arn};$('signin-status').textContent='';renderProfile();$('signin-dialog').close();
    toast(`로그인했습니다 · ${identityText().title}`);
  }catch(error){$('signin-status').textContent=error.message;}
});
bind('profile-signout',async()=>{
  // Sign out locally at once; the server then discards the SSO token and invalidates it with sso:Logout.
  closeProfile();const flow=state.sso;state.sso=null;state.identity=null;$('aws-account').value='';ssoView('form');renderProfile();
  if(flow)await api(`/api/sso/login/${flow.id}/logout`,{method:'POST'}).catch(error=>toast(`SSO 토큰 폐기에 실패했습니다: ${error.message}`,true));
  toast('AWS 자격 증명에서 로그아웃했습니다.');});
bind('collection-button',()=>{closeProfile();
  const text=identityText(),box=$('collection-identity');box.replaceChildren(el('b',state.identity?text.title:'기본 SDK 인증 체인 (로그인하지 않음)'),el('span',state.identity?text.detail:'이 기기의 환경 변수·기본 프로파일을 씁니다.'));
  const change=el('button','로그인 변경','text-button');change.type='button';change.onclick=()=>{$('collection-dialog').close();$('signin-button').click();};box.append(el('br'),change);
  if(state.identity&&!$('aws-account').value)$('aws-account').value=state.identity.account;
  $('collection-dialog').showModal();});
// IAM Identity Center sign-in with the device code grant: the server keeps every secret, the page shows the code and link.
function ssoView(mode){$('sso-form').hidden=mode!=='form';$('sso-device').hidden=mode!=='device';$('sso-select').hidden=mode!=='select';
  $('aws-profile').disabled=mode==='select';$('aws-account').readOnly=mode==='select';}
async function ssoRoles(){
  const account=$('sso-account').value;$('aws-account').value=account;$('sso-role').replaceChildren();
  const {roles}=await api(`/api/sso/login/${state.sso.id}/roles?account_id=${encodeURIComponent(account)}`);
  $('sso-role').append(...roles.map(r=>new Option(r,r)));
  const readOnly=roles.find(r=>/read.?only/i.test(r));if(readOnly)$('sso-role').value=readOnly;
}
async function ssoPoll(){
  if(!state.sso||state.sso.status!=='pending')return;
  try{const view=await api(`/api/sso/login/${state.sso.id}`);state.sso={...state.sso,...view};
    if(view.status==='pending'){$('sso-status').textContent='승인을 기다리는 중…';state.ssoTimer=setTimeout(ssoPoll,view.interval*1000);return;}
    if(view.status==='authorized'){
      // Roles load before the selection is shown, so a collection never starts without a role.
      $('sso-status').textContent='계정·역할 목록을 불러오는 중…';
      $('sso-account').replaceChildren(...view.accounts.map(a=>new Option(`${a.name} (${a.id})`,a.id)));
      if(view.accounts.length)await ssoRoles();
      $('sso-signed-in').textContent=view.accounts.length?`SSO 로그인됨 · 계정 ${view.accounts.length}개`:'SSO 로그인됨 · 할당된 계정이 없습니다';
      ssoView('select');return;}
    $('sso-status').textContent={expired:'코드가 만료됐습니다. 다시 로그인하세요.',denied:'승인이 거부됐습니다.',failed:'SSO 로그인에 실패했습니다.'}[view.status]||view.status;
    setTimeout(()=>ssoView('form'),2500);
  }catch(error){$('sso-status').textContent=error.message;setTimeout(()=>ssoView('form'),2500);}
}
bind('sso-login',async()=>{
  clearTimeout(state.ssoTimer);
  const flow=await api('/api/sso/login',{method:'POST',body:JSON.stringify({start_url:$('sso-start-url').value.trim(),region:$('sso-region').value.trim()})});
  state.sso=flow;$('sso-code').textContent=flow.user_code;$('sso-link').href=flow.verification_uri_complete;$('sso-status').textContent='승인을 기다리는 중…';
  ssoView('device');state.ssoTimer=setTimeout(ssoPoll,flow.interval*1000);
});
$('sso-account').onchange=()=>ssoRoles().catch(error=>toast(error.message,true));
bind('sso-logout',async()=>{if(state.sso)await api(`/api/sso/login/${state.sso.id}/logout`,{method:'POST'});state.sso=null;$('aws-account').value='';ssoView('form');
  if(state.identity?.via==='sso'){state.identity=null;renderProfile();}});
bind('close-collection',()=>{$('collection-dialog').close();});
bind('note-button',()=>post({action:'diagramBotNote'}));
bind('reset-button',()=>replaceDocument('/relayout',{},'자동 배치를 다시 실행했습니다. 이전 편집본은 보관되고 메모는 유지됩니다.'));
bind('restore-button',()=>{const last=state.history.at(-1);return replaceDocument('/restore',{from_revision:last.revision},`보관된 v${last.revision} 편집본을 복원했습니다.`);});
bind('download-drawio',downloadDiagram);bind('download-png',downloadPng);
bind('download-json',()=>download('snapshot.json',JSON.stringify(state.snapshot,null,2),'application/json'));bind('download-csv',downloadCsv);bind('download-cfn',downloadCloudFormation);
bind('download-findings',()=>{const fields=['severity','pillar','rule','resource_name','resource','title','evidence','recommendation'];const rows=[fields,...(state.review?.findings||[]).map(f=>fields.map(k=>k==='severity'?severityNames[f[k]]:k==='pillar'?pillarNames[f[k]]:f[k]))];download(`diagram-bot-review-${state.snapshot.metadata.account_id}-${state.snapshot.metadata.region}.csv`,'\ufeff'+rows.map(r=>r.map(csvCell).join(',')).join('\r\n'),'text/csv;charset=utf-8');});
bind('import-button',()=>$('import-file').click());
const modes=['diagram','inventory','review','changes'];
for(const mode of modes)bind(mode+'-tab',async()=>{state.mode=mode;$('canvas-area').hidden=mode!=='diagram';for(const panel of modes.slice(1))$(panel).hidden=mode!==panel;for(const tab of modes){$(tab+'-tab').classList.toggle('active',mode===tab);$(tab+'-tab').setAttribute('aria-selected',String(mode===tab));}renderInventory();if(mode==='changes')await renderChanges();});
$('compare-select').onchange=()=>renderChanges().catch(e=>toast(e.message,true));
$('severity-filter').onchange=renderReview;
$('search').oninput=renderInventory;$('kind-filter').onchange=renderInventory;
// Multi-account: pick account × region collections (not samples, designs or combined views) and merge them.
$('combine-button').onclick=()=>{
  const rows=$('combine-rows');rows.replaceChildren();
  for(const item of state.snapshots.filter(i=>!i.metadata.combined&&!i.metadata.design)){
    const label=el('label'),box=el('input');box.type='checkbox';box.value=item.id;label.append(box,document.createTextNode(`${item.metadata.account_id} · ${item.metadata.region} — ${snapshotLabel(item)}`));rows.append(label);
  }
  $('combine-dialog').showModal();
};
$('close-combine').onclick=()=>$('combine-dialog').close();
$('combine-form').onsubmit=async event=>{
  event.preventDefault();
  try{
    const ids=[...$('combine-rows').querySelectorAll('input:checked')].map(b=>b.value);
    const result=await api('/api/snapshots/combine',{method:'POST',body:JSON.stringify({ids,title:$('combine-title').value||null})});
    $('combine-dialog').close();await refreshSnapshots(result.id);await loadSnapshot(result.id);toast('계정 통합 보기를 만들었습니다.');
  }catch(error){toast(error.message,true);}
};
$('snapshot-select').onchange=()=>loadSnapshot($('snapshot-select').value).catch(e=>toast(e.message,true));
$('references').onchange=async()=>{try{if(state.dirty)await saveDocument(false);state.references=$('references').checked;await loadView();}catch(e){toast(e.message,true);$('references').checked=state.references;}};
$('route-filter').onchange=async()=>{const previous=state.routes;try{if(state.dirty)await saveDocument(false);state.routes=$('route-filter').value||null;clearNotice();await loadView();}catch(e){state.routes=previous;$('route-filter').value=previous||'';toast(e.message,true);}};
$('import-file').onchange=async()=>{
  try{const file=$('import-file').files[0];if(!file)return;if(file.size>15*1024*1024)throw new Error('스냅샷은 최대 15 MiB입니다.');const data=JSON.parse(await file.text());const result=await api('/api/snapshots',{method:'POST',body:JSON.stringify(data)});await refreshSnapshots(result.id);await loadSnapshot(result.id);toast('스냅샷을 가져왔습니다.');}catch(error){toast(error.message,true);}finally{$('import-file').value='';}
};
$('collection-form').onsubmit=async event=>{
  event.preventDefault();$('start-collection').disabled=true;$('collection-progress').textContent='AWS 인증과 계정을 확인하고 있습니다…';
  try{
    const sso=state.identity?.via==='sso'&&state.sso?.status==='authorized'?{sso_login:state.sso.id,sso_role:state.identity.role}:{};
    if(sso.sso_login&&!sso.sso_role)throw new Error('SSO 역할을 선택하세요. 이 계정에 할당된 역할이 없다면 Identity Center 관리자에게 요청하세요.');
    let job=await api('/api/collections',{method:'POST',body:JSON.stringify({profile:sso.sso_login?null:(state.identity?.via==='profile'?state.identity.profile||null:null),region:$('aws-region').value,expected_account:$('aws-account').value,role_arn:$('aws-role').value||null,external_id:$('aws-external-id').value||null,...sso})});
    while(['queued','running'].includes(job.status)){$('collection-progress').textContent='AWS 설정을 수집하고 있습니다. 잠시 기다려 주세요.';await new Promise(resolve=>setTimeout(resolve,1500));job=await api(`/api/collections/${job.id}`);}
    if(job.status==='failed')throw new Error(job.error);
    await refreshSnapshots(job.snapshot_id);await loadSnapshot(job.snapshot_id);$('collection-dialog').close();toast(job.status==='partial'?'부분 수집을 완료했습니다. 조회 상태를 확인하세요.':'AWS 수집을 완료했습니다.');
  }catch(error){$('collection-progress').textContent=error.message;}finally{$('start-collection').disabled=false;}
};
// Design chat: a Strands agent on Bedrock calls drawing and read-only collection tools; each change is a snapshot.
// Model replies use a little Markdown; render headings, lists, **bold** and `code` as DOM nodes (never innerHTML).
function inline(text) {
  const parts=[];for(const piece of text.split(/(\*\*[^*]+\*\*|`[^`]+`)/)){if(!piece)continue;
    if(piece.startsWith('**')&&piece.endsWith('**'))parts.push(el('strong',piece.slice(2,-2)));
    else if(piece.startsWith('`')&&piece.endsWith('`'))parts.push(el('code',piece.slice(1,-1)));
    else parts.push(document.createTextNode(piece));}
  return parts;
}
function markdown(text) {
  const box=el('div',undefined,'chat-text rich');let list=null;
  for(const raw of text.split('\n')){const line=raw.trimEnd();
    const heading=line.match(/^#{1,6}\s+(.*)$/),item=line.match(/^\s*(?:[-*]|\d+\.)\s+(.*)$/);
    if(item){if(!list){list=el('ul');box.append(list);}const li=el('li');li.append(...inline(item[1]));list.append(li);continue;}
    list=null;
    if(heading){const h=el('div',undefined,'chat-heading');h.append(...inline(heading[1]));box.append(h);}
    else if(/^\s*(?:---|>\s*)/.test(line)&&line.replace(/[->\s]/g,'')===''){continue;}
    else if(line.trim()){const p=el('p');p.append(...inline(line.replace(/^>\s*/,'')));box.append(p);}}
  return box;
}
function chatMessage(role, text, meta) {
  const item=el('div',undefined,`chat-message ${role}`);item.append(role==='assistant'?markdown(text):el('div',text,'chat-text'));
  if(meta)item.append(el('small',meta,'chat-meta'));$('chat-log').append(item);$('chat-log').scrollTop=$('chat-log').scrollHeight;return item;
}
function setChat(open){$('chat-panel').hidden=!open;document.body.classList.toggle('chat-open',open);$('chat-button').setAttribute('aria-expanded',String(open));}
async function openChat() {
  if(!$('chat-panel').hidden){setChat(false);return;}
  setChat(true);$('chat-input').focus();
  if(state.chatReady!==undefined)return;
  const info=await api('/api/agent');state.chatReady=info.ready;
  $('chat-model').replaceChildren(...info.models.map(m=>new Option(m.label+(m.key===info.default?' (기본)':''),m.key)));$('chat-model').value=info.default;
  if(!info.ready)chatMessage('system',"채팅 에이전트가 설치되지 않았습니다. pip install -e '.[agent]' 후 다시 시작하세요.");
}
$('chat-form').onsubmit=async event=>{
  event.preventDefault();const message=$('chat-input').value.trim();if(!message||state.chatBusy)return;
  if(state.dirty)await saveDocument(false);
  state.chatBusy=true;$('chat-send').disabled=true;$('chat-input').value='';chatMessage('user',message);
  const waiting=chatMessage('system','설계하는 중…'),status=waiting.querySelector('.chat-text');
  let reply=null,text='',shown=null,pending=null,loading=null;
  // Each design step the agent saves opens on the canvas as it arrives; only the newest waits while one loads.
  const show=id=>{pending=id;if(!loading)loading=(async()=>{while(pending&&pending!==shown){const next=pending;shown=next;
    await refreshSnapshots(next);await loadSnapshot(next);}loading=null;})();};
  try{
    const result=await streamApi('/api/agent/chat/stream',{message,model:$('chat-model').value,snapshot:state.snapshotId},event=>{
      if(event.type==='tool')status.textContent=event.status==='start'?`도구 실행 중: ${event.name}`:`도구 ${event.status==='error'?'오류':'완료'}: ${event.name}`;
      else if(event.type==='model'&&event.fallback)status.textContent=`${event.model_label}(대체 모델)로 응답하는 중…`;
      else if(event.type==='snapshot')show(event.id);
      else if(event.type==='text'){text+=event.text;if(!reply){reply=chatMessage('assistant','');}
        reply.querySelector('.chat-text').replaceWith(Object.assign(el('div',undefined,'chat-text'),{textContent:text}));$('chat-log').scrollTop=$('chat-log').scrollHeight;}
    });
    waiting.remove();reply?.remove();
    const meta=`${result.model_label}${result.fallback?' · 대체 모델(기본 모델 사용 불가)':''} · 도구 ${result.tools_used.length}회`+(result.snapshots.length?` · 구성도 ${result.snapshots.length}회 갱신`:'');
    chatMessage('assistant',result.reply||'(응답 없음)',meta);
    const last=result.snapshots.at(-1);if(last)show(last);if(loading)await loading;
  }catch(error){waiting.remove();reply?.remove();if(text)chatMessage('assistant',text);chatMessage('system',error.message);}
  finally{state.chatBusy=false;$('chat-send').disabled=false;$('chat-input').focus();}
};
$('chat-input').onkeydown=event=>{if(event.key==='Enter'&&!event.shiftKey&&!event.isComposing){event.preventDefault();$('chat-form').requestSubmit();}};
bind('chat-button',openChat);bind('chat-close',()=>setChat(false));
bind('chat-reset',async()=>{await api('/api/agent/reset',{method:'POST'});$('chat-log').replaceChildren();chatMessage('system','새 대화를 시작합니다.');});
for(const button of document.querySelectorAll('.chat-suggestion'))button.onclick=()=>{$('chat-input').value=button.textContent;$('chat-input').focus();};
window.addEventListener('beforeunload',event=>{if(state.dirty){event.preventDefault();event.returnValue='';}});
async function start() {
  const boot=await api('/api/bootstrap');state.token=boot.token;state.role=boot.user.role;
  state.team=boot.team;state.egress=boot.egress;
  if(boot.egress==='browser'){startRelay(api,{onRequests:n=>{state.relayed=(state.relayed||0)+n;}});$('egress-note').hidden=false;}
  if(boot.team){const name=boot.user.name;$('user-avatar').textContent=name.slice(0,2).toUpperCase();$('user-avatar').title=`${name} · ${boot.user.role}`;
    $('user-role').textContent=`${name} · ${{viewer:'보기 전용',editor:'편집',admin:'관리자'}[boot.user.role]}`;$('user-role').hidden=false;$('logout-button').hidden=false;}
  bind('logout-button',async()=>{await api('/api/logout',{method:'POST'});location.href='/login';});
  for(const profile of boot.profiles)$('aws-profile').append(new Option(profile,profile));
  renderProfile();
  await refreshSnapshots('demo');await loadSnapshot('demo');
  if(!boot.editor_ready){state.loading=false;setControls();$('editor-message').textContent='터미널에서 diagram-bot setup-editor를 실행한 뒤 앱을 다시 시작하세요.';document.querySelector('.loader').hidden=true;return;}
  $('editor').src='/editor/index.html?embed=1&proto=json&configure=1&spin=1&ui=min&libraries=1&noSaveBtn=1&noExitBtn=1&saveAndExit=0&offline=1&local=1&stealth=1&lang=ko';
  editorTimer=setTimeout(()=>{$('editor-message').textContent='편집기 로딩이 지연되고 있습니다. 페이지를 새로고침하세요.';},30000);
}
start().catch(error=>{toast(error.message,true);$('editor-message').textContent=error.message;});
