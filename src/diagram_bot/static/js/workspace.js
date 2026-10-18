// The workspace around the canvas: snapshot summary and metrics, VPC navigation, route filter and the resource inventory.
import {names, statuses, viewNames} from './labels.js';
import {state, vpcNode} from './state.js';
import {$, el, setControls} from './ui.js';
import {switchView} from './document.js';
import {showResource} from './inspector.js';
export function renderSummary() {
  const {metadata:m,graph}=state.snapshot;
  $('region-label').textContent=m.region;$('crumb-account').textContent=m.design?'설계안':m.combined?m.account_id:`계정 ${m.account_id}`;$('account-summary').textContent=m.design?'DESIGN · 실제 계정 아님':`ACCOUNT ${m.account_id}`;
  $('demo-badge').hidden=!m.demo;$('demo-badge').textContent=m.design?'설계안':m.imported?'XML 가져옴 · 라벨 추정':'샘플 데이터';
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
export function documentActive() {
  $('all-environments').classList.toggle('active',state.view==='overview');
  document.querySelectorAll('[data-vpc]').forEach(b=>b.classList.toggle('active',state.view==='detail'&&b.dataset.vpc===state.vpc));
  for(const view of Object.keys(viewNames))$('view-'+view).classList.toggle('active',state.view===view);
  renderRouteFilter();setControls();renderInventory();
}
function matchingResources() {
  if(!state.snapshot)return[];
  const search=$('search').value.toLowerCase(),kind=$('kind-filter').value;
  return state.snapshot.graph.nodes.filter(n=>(state.view==='overview'||state.visible?.has(n.id))&&(!n.reference_only||state.references||state.visible?.has(n.id))&&(kind==='all'||n.kind===kind)&&JSON.stringify([n.name,n.resource_id,n.attributes]).toLowerCase().includes(search));
}
export function renderInventory() {
  const matches=matchingResources();$('inventory-rows').replaceChildren();
  for(const item of matches){const row=el('tr');row.tabIndex=0;row.append(el('td',item.name),el('td',names[item.kind]||item.kind),el('td',item.resource_id),el('td',item.reference_only?'참조만 확인':item.source));row.onclick=()=>showResource(item.id);row.onkeydown=e=>{if(e.key==='Enter')showResource(item.id);};$('inventory-rows').append(row);}
  $('inventory-empty').hidden=matches.length>0;
  const active=Boolean($('search').value)||$('kind-filter').value!=='all';$('search-results').hidden=!active||state.mode==='inventory';$('search-results').replaceChildren();
  if(active){$('search-results').append(el('span',`${viewNames[state.view]} 범위 · ${matches.length}개`,'search-scope'));for(const item of matches.slice(0,30)){const button=el('button',item.name);button.onclick=()=>showResource(item.id);$('search-results').append(button);}if(!matches.length)$('search-results').append(el('span','검색 결과가 없습니다.'));}
}
