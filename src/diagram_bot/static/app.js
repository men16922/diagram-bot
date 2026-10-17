'use strict';
const $ = id => document.getElementById(id);
const names = {vpc:'VPC',subnet:'Subnet',ec2:'EC2',load_balancer:'Load balancer',target_group:'Target group',registered_target:'등록 대상',security_group:'Security group',network_interface:'Network interface',volume:'EBS volume'};
const relationNames = {in_vpc:'VPC 소속',in_subnet:'서브넷 배치',associated_load_balancer:'로드밸런서 연결',registered_target:'대상 등록',uses_security_group:'보안 그룹 연결',attached_interface:'ENI 장착',attached_volume:'EBS 장착'};
const statuses = {complete:'조회 완료',partial:'부분 조회',unavailable:'조회 불가',not_discovered:'미발견 범위'};
const state = {token:null,snapshot:null,snapshotId:null,vpc:null,references:false,revision:0,xml:null,ready:false,loading:true,dirty:false,busy:false,mode:'diagram',selected:null};
const pending = new Map();
let editorTimer, toastTimer, pngWaiter = null;
function el(tag, text, className) { const element=document.createElement(tag); if(text!==undefined)element.textContent=text; if(className)element.className=className; return element; }
function toast(message, error=false) { $('toast').textContent=message; $('toast').className=error?'error':''; $('toast').hidden=false; clearTimeout(toastTimer); toastTimer=setTimeout(()=>$('toast').hidden=true,error?8500:4000); }
async function api(path, options={}) {
  const response=await fetch(path,{...options,headers:{'Content-Type':'application/json','X-Diagram-Bot':state.token,...options.headers}});
  const data=await response.json();
  if(!response.ok)throw new Error(typeof data.detail==='string'?data.detail:'요청 값을 확인하세요.');
  return data;
}
function post(message) { $('editor').contentWindow.postMessage(JSON.stringify(message),location.origin); }
function documentUrl(reset=false) {
  const query=new URLSearchParams({references:String(state.references)});
  if(state.vpc)query.set('vpc',state.vpc);
  if(reset)query.set('reset','true');
  return `/api/snapshots/${state.snapshotId}/document?${query}`;
}
function setControls() {
  for(const id of ['save-button','export-button','note-button','reset-button'])$(id).disabled=!state.ready||state.loading||state.busy;
  for(const id of ['snapshot-select','references','import-button'])$(id).disabled=state.busy||(state.ready&&state.loading);
  $('save-state').textContent=state.loading?'불러오는 중':state.busy?'처리 중':state.dirty?'저장하지 않은 편집':state.revision?`저장됨 · v${state.revision}`:'자동 생성 구성도';
}
function sendDocument() {
  state.loading=true;setControls();$('editor-overlay').hidden=false;
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
  if(message.event==='init'){state.ready=true;if(state.xml)sendDocument();}
  if(message.event==='load'){
    clearTimeout(editorTimer);state.loading=false;state.dirty=false;$('editor-overlay').hidden=true;setControls();
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
    else if(message.relationIndex!=null)showRelation(Number(message.relationIndex));
    else if(message.label){$('detail-body').replaceChildren(el('div','DIAGRAM ELEMENT','detail-kind'),el('h3',message.label,'detail-name'),el('p','표현용 그룹 또는 사용자 주석입니다. 수집한 AWS 속성은 변경되지 않습니다.','small-note'));}
  }
  if(message.event==='diagramBotNotVisible')toast('현재 구성도에서 숨겨진 리소스입니다. 속성 패널에서 내용을 확인하세요.');
  if(message.event==='diagramBotNoteAdded')toast('메모를 추가했습니다. 더블클릭해 내용을 수정하세요.');
  if(message.event==='export'&&pngWaiter){
    const task=pngWaiter;pngWaiter=null;clearTimeout(task.timer);
    if(message.data?.startsWith('data:image/png;base64,'))task.resolve(message.data);else task.reject(new Error('PNG를 생성하지 못했습니다. 해상도를 낮춰 다시 시도하세요.'));
  }
});
async function refreshSnapshots(preferred) {
  const items=await api('/api/snapshots');$('snapshot-select').replaceChildren();
  for(const item of items){const m=item.metadata;const label=`${m.demo?'샘플':'AWS'} · ${m.region} · ${m.finished_at.slice(5,16).replace('T',' ')}`;const option=new Option(label,item.id);$('snapshot-select').append(option);}
  $('snapshot-select').value=preferred||items[0]?.id;
  return $('snapshot-select').value;
}
async function loadSnapshot(id) {
  if(state.dirty)await saveDocument(false);
  state.snapshot=await api(`/api/snapshots/${id}`);state.snapshotId=id;state.vpc=state.snapshot.graph.nodes.find(n=>n.kind==='vpc')?.resource_id||null;state.selected=null;
  $('snapshot-select').value=id;
  renderSummary();renderInventory();await loadView();
}
function renderSummary() {
  const {metadata:m,graph}=state.snapshot;
  $('region-label').textContent=m.region;$('account-summary').textContent=`ACCOUNT ${m.account_id}`;
  $('demo-badge').hidden=!m.demo;$('scope-description').textContent=`${m.account_id} · ${m.region} · 수집 설정을 바탕으로 생성한 구성도`;
  $('collected-at').textContent=`수집 ${new Date(m.finished_at).toLocaleString('ko-KR')} · ${m.demo?'합성 샘플':'AWS API 조회'}`;
  $('coverage-button').textContent=(m.status==='complete'?'● ':'◐ ')+(statuses[m.status]||m.status);
  $('coverage-button').classList.toggle('complete',m.status==='complete');
  const count=kind=>graph.nodes.filter(n=>n.kind===kind&&!n.reference_only).length;
  $('metrics').replaceChildren();
  for(const [label,value,icon,unit] of [['VPC ENVIRONMENTS',count('vpc'),'▦','개 환경'],['SUBNETS',count('subnet'),'⊞','개 서브넷'],['COMPUTE',count('ec2'),'▣','개 인스턴스'],['LOAD BALANCERS',count('load_balancer'),'⇄','개 로드밸런서']]){
    const box=el('div',undefined,'metric');box.append(el('div',icon,'metric-icon'));const content=el('div');content.append(el('div',label,'metric-label'));const number=el('div',String(value),'metric-number');number.append(el('small',unit));content.append(number);box.append(content);$('metrics').append(box);
  }
  $('environments').replaceChildren();const vpcs=graph.nodes.filter(n=>n.kind==='vpc');$('environment-count').textContent=vpcs.length;
  for(const node of vpcs){const button=el('button',undefined,'environment');button.dataset.vpc=node.resource_id;const label=el('div',node.name);label.append(el('small',node.attributes.CidrBlock||node.resource_id));button.append(el('span','◇'),label);button.onclick=()=>switchView(node.resource_id);$('environments').append(button);}
  $('kind-filter').replaceChildren(new Option('모든 리소스','all'));
  for(const kind of [...new Set(graph.nodes.map(n=>n.kind))])$('kind-filter').append(new Option(names[kind]||kind,kind));
  $('total-count').textContent=graph.nodes.length;
  $('coverage-rows').replaceChildren();
  for(const row of state.snapshot.coverage){const container=el('div',undefined,'coverage-row');const text=el('div',row.operation);text.append(el('small',`${row.items??'—'}개 · ${row.pages??'—'} 페이지${row.error_code?' · '+row.error_code:''}`));container.append(text,el('span',statuses[row.status]||row.status,'badge'+(row.status!=='complete'?' warn':'')));$('coverage-rows').append(container);}
  $('limitations').replaceChildren(...(m.limitations||[]).map(s=>el('li',s)));
}
async function loadView(reset=false) {
  state.loading=true;setControls();
  try{
    const document=await api(documentUrl(reset));state.xml=document.xml;state.revision=document.revision;state.dirty=false;
    $('environment-title').textContent=state.vpc?state.snapshot.graph.nodes.find(n=>n.resource_id===state.vpc)?.name:'AWS 구성도';
    documentActive();if(state.ready)sendDocument();
  }catch(error){state.loading=false;setControls();throw error;}
}
function documentActive() {
  $('all-environments').classList.toggle('active',!state.vpc);
  document.querySelectorAll('[data-vpc]').forEach(b=>b.classList.toggle('active',b.dataset.vpc===state.vpc));
  renderInventory();
}
async function switchView(vpc) {
  if(state.loading||state.busy)return;
  try{if(state.dirty)await saveDocument(false);state.vpc=vpc;await loadView();}catch(error){toast(error.message,true);}
}
async function saveDocument(notify=true) {
  if(state.busy)throw new Error('이전 작업을 완료한 뒤 다시 시도하세요.');
  state.busy=true;setControls();
  try{
    const xml=await requestDocument();
    const saved=await api(documentUrl(),{method:'PUT',body:JSON.stringify({xml,revision:state.revision})});
    state.xml=xml;state.revision=saved.revision;state.dirty=false;if(notify)toast('편집본을 로컬에 저장했습니다.');
    return xml;
  }finally{state.busy=false;setControls();}
}
function matchingResources() {
  if(!state.snapshot)return[];
  const search=$('search').value.toLowerCase(),kind=$('kind-filter').value;
  const nodes=state.snapshot.graph.nodes;
  const vpcNode=nodes.find(n=>n.resource_id===state.vpc);
  const contained=new Set(state.snapshot.graph.edges.filter(e=>e.relation==='in_vpc'&&e.target===vpcNode?.id).map(e=>e.source));
  if(vpcNode)contained.add(vpcNode.id);
  for(const edge of state.snapshot.graph.edges)if(edge.relation==='registered_target'&&contained.has(edge.source))contained.add(edge.target);
  if(state.references){for(const edge of state.snapshot.graph.edges)if(contained.has(edge.source))contained.add(edge.target);}
  return nodes.filter(n=>(!state.vpc||contained.has(n.id))&&(!n.reference_only||state.references)&&(kind==='all'||n.kind===kind)&&JSON.stringify([n.name,n.resource_id,n.attributes]).toLowerCase().includes(search));
}
function renderInventory() {
  const matches=matchingResources();$('inventory-rows').replaceChildren();
  for(const node of matches){const row=el('tr');row.tabIndex=0;row.append(el('td',node.name),el('td',names[node.kind]),el('td',node.resource_id),el('td',node.reference_only?'참조만 확인':node.source));row.onclick=()=>showResource(node.id);row.onkeydown=e=>{if(e.key==='Enter')showResource(node.id);};$('inventory-rows').append(row);}
  $('inventory-empty').hidden=matches.length>0;
  const active=Boolean($('search').value)||$('kind-filter').value!=='all';$('search-results').hidden=!active||state.mode==='inventory';$('search-results').replaceChildren();
  if(active){for(const node of matches.slice(0,30)){const button=el('button',node.name);button.onclick=()=>showResource(node.id);$('search-results').append(button);}if(!matches.length)$('search-results').append(el('span','검색 결과가 없습니다.'));}
}
function showResource(id, select=true) {
  const node=state.snapshot.graph.nodes.find(n=>n.id===id);if(!node)return;state.selected=id;
  const body=$('detail-body');body.replaceChildren(el('div',names[node.kind]||node.kind,'detail-kind'),el('h3',node.name,'detail-name'),el('div',node.resource_id,'detail-id'));
  const badge=el('p',node.reference_only?'참조만 확인 · 상세 미조회':'API 응답에서 조회됨','small-note');body.append(badge);
  const fields=[['계정',node.account],['리전',node.region],['인스턴스 유형',node.attributes.InstanceType],['CIDR',node.attributes.CidrBlock],['AZ',node.attributes.AvailabilityZone||node.attributes.Placement?.AvailabilityZone],['상태',node.attributes.State?.Name||node.attributes.State?.Code],['프로토콜',node.attributes.Protocol],['포트',node.attributes.Port],['VPC',node.attributes.VpcId],['서브넷',node.attributes.SubnetId],['스킴',node.attributes.Scheme]];
  const list=el('dl');for(const [key,value]of fields)if(value!==undefined){const item=el('div',undefined,'detail-field');item.append(el('dt',key),el('dd',String(value)));list.append(item);}body.append(list);
  if(node.attributes.Tags?.length){body.append(el('div','TAGS','detail-section'));for(const tag of node.attributes.Tags)body.append(el('p',`${tag.Key} = ${tag.Value}`,'small-note'));}
  body.append(el('div','API에서 확인한 관계','detail-section'));
  for(const edge of state.snapshot.graph.edges.filter(e=>e.source===id||e.target===id)){
    const other=state.snapshot.graph.nodes.find(n=>n.id===(edge.source===id?edge.target:edge.source));
    const button=el('button',`${relationNames[edge.relation]||edge.relation} · ${other.name}`,'relation-button');button.append(el('small',edge.evidence));button.onclick=()=>showResource(other.id);body.append(button);
  }
  const raw=el('details');raw.append(el('summary','원본 속성 보기'),el('pre',JSON.stringify(node.attributes,null,2)));body.append(raw);
  if(select&&state.ready&&!state.loading)post({action:'diagramBotSelect',resourceId:id});
}
function showRelation(index) {
  const edge=state.snapshot.graph.edges[index];if(!edge)return;
  $('detail-body').replaceChildren(el('div','CONFIGURATION RELATION','detail-kind'),el('h3',relationNames[edge.relation]||edge.relation,'detail-name'),el('p','AWS API에서 확인한 설정 관계입니다. 실제 통신 여부를 의미하지 않습니다.','small-note'),el('pre',JSON.stringify(edge,null,2)));
}
function download(name, content, type) {
  const url=URL.createObjectURL(new Blob([content],{type}));const link=el('a');link.href=url;link.download=name;link.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
}
function filename(extension) {return `diagram-bot-${state.snapshot.metadata.account_id}-${state.vpc||'all'}-v${state.revision}.${extension}`;}
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
function downloadCsv(){const fields=['account','region','kind','resource_id','name','reference_only','source','attributes'];const rows=[fields,...state.snapshot.graph.nodes.map(n=>fields.map(f=>n[f]))];download('inventory.csv','\ufeff'+rows.map(r=>r.map(csvCell).join(',')).join('\r\n'),'text/csv;charset=utf-8');}
function bind(id, handler) {$(id).onclick=()=>Promise.resolve().then(handler).catch(error=>toast(error.message,true));}
bind('all-environments',()=>switchView(null));
bind('save-button',()=>saveDocument());
bind('export-button',()=>{$('export-dialog').showModal();});
bind('coverage-button',()=>{$('coverage-dialog').showModal();});
bind('collection-button',()=>{$('collection-dialog').showModal();});
bind('close-collection',()=>{$('collection-dialog').close();});
bind('note-button',()=>post({action:'diagramBotNote'}));
bind('reset-button',async()=>{if(state.dirty)await saveDocument(false);await loadView(true);state.dirty=true;setControls();toast('자동 생성 배치를 불러왔습니다. 저장하면 기존 편집본에 적용됩니다.');});
bind('download-drawio',downloadDiagram);bind('download-png',downloadPng);
bind('download-json',()=>download('snapshot.json',JSON.stringify(state.snapshot,null,2),'application/json'));bind('download-csv',downloadCsv);
bind('import-button',()=>$('import-file').click());
for(const mode of ['diagram','inventory'])bind(mode+'-tab',()=>{state.mode=mode;$('canvas-area').hidden=mode!=='diagram';$('inventory').hidden=mode!=='inventory';$('diagram-tab').classList.toggle('active',mode==='diagram');$('inventory-tab').classList.toggle('active',mode==='inventory');renderInventory();});
$('search').oninput=renderInventory;$('kind-filter').onchange=renderInventory;
$('snapshot-select').onchange=()=>loadSnapshot($('snapshot-select').value).catch(e=>toast(e.message,true));
$('references').onchange=async()=>{try{if(state.dirty)await saveDocument(false);state.references=$('references').checked;await loadView();}catch(e){toast(e.message,true);$('references').checked=state.references;}};
$('import-file').onchange=async()=>{
  try{const file=$('import-file').files[0];if(!file)return;if(file.size>15*1024*1024)throw new Error('스냅샷은 최대 15 MiB입니다.');const data=JSON.parse(await file.text());const result=await api('/api/snapshots',{method:'POST',body:JSON.stringify(data)});await refreshSnapshots(result.id);await loadSnapshot(result.id);toast('스냅샷을 가져왔습니다.');}catch(error){toast(error.message,true);}finally{$('import-file').value='';}
};
$('collection-form').onsubmit=async event=>{
  event.preventDefault();$('start-collection').disabled=true;$('collection-progress').textContent='AWS 인증과 계정을 확인하고 있습니다…';
  try{
    let job=await api('/api/collections',{method:'POST',body:JSON.stringify({profile:$('aws-profile').value||null,region:$('aws-region').value,expected_account:$('aws-account').value})});
    while(['queued','running'].includes(job.status)){$('collection-progress').textContent='AWS 설정을 수집하고 있습니다. 잠시 기다려 주세요.';await new Promise(resolve=>setTimeout(resolve,1500));job=await api(`/api/collections/${job.id}`);}
    if(job.status==='failed')throw new Error(job.error);
    await refreshSnapshots(job.snapshot_id);await loadSnapshot(job.snapshot_id);$('collection-dialog').close();toast(job.status==='partial'?'부분 수집을 완료했습니다. 조회 상태를 확인하세요.':'AWS 수집을 완료했습니다.');
  }catch(error){$('collection-progress').textContent=error.message;}finally{$('start-collection').disabled=false;}
};
window.addEventListener('beforeunload',event=>{if(state.dirty){event.preventDefault();event.returnValue='';}});
async function start() {
  const boot=await api('/api/bootstrap');state.token=boot.token;
  for(const profile of boot.profiles)$('aws-profile').append(new Option(profile,profile));
  await refreshSnapshots('demo');await loadSnapshot('demo');
  if(!boot.editor_ready){state.loading=false;setControls();$('editor-message').textContent='터미널에서 diagram-bot setup-editor를 실행한 뒤 앱을 다시 시작하세요.';document.querySelector('.loader').hidden=true;return;}
  $('editor').src='/editor/index.html?embed=1&proto=json&configure=1&spin=1&ui=min&libraries=1&noSaveBtn=1&noExitBtn=1&saveAndExit=0&offline=1&local=1&stealth=1&lang=ko';
  editorTimer=setTimeout(()=>{$('editor-message').textContent='편집기 로딩이 지연되고 있습니다. 페이지를 새로고침하세요.';},30000);
}
start().catch(error=>{toast(error.message,true);$('editor-message').textContent=error.message;});
