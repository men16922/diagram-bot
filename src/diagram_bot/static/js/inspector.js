// The inspector panel: one resource's fields, actions and API relations, or one relation's endpoints and evidence.
import {names, relationNames, routeClasses} from './labels.js';
import {node, relationsOf, state, vpcOf} from './state.js';
import {$, el, guard, setControls} from './ui.js';
import {post} from './editor.js';
import {flushEdits, loadView, switchView} from './document.js';
const emptyDetail=document.querySelector('#detail-body .empty-detail');
function actionButton(label, handler) {const button=el('button',label,'button');button.onclick=guard(handler);return button;}
// Inspector rows: [label, value of (attributes, node)]; rows whose value is empty are skipped.
const FIELDS=[
  ['계정',(a,item)=>item.account],
  ['리전',(a,item)=>item.region],
  ['인스턴스 유형',(a,item)=>a.InstanceType],
  ['EFA',(a,item)=>(a.NetworkInterfaces||[]).some(i=>i.InterfaceType==='efa')?'EFA 인터페이스 있음':undefined],
  ['배치 그룹',(a,item)=>a.Placement?.GroupName],
  ['용량',(a,item)=>a.InstanceLifecycle==='capacity-block'?'Capacity Block':a.CapacityReservationId?`ODCR ${a.CapacityReservationId}`:undefined],
  ['배치 전략',(a,item)=>item.kind==='placement_group'?a.Strategy:undefined],
  ['CIDR',(a,item)=>a.CidrBlock],
  ['AZ',(a,item)=>a.AvailabilityZone||a.Placement?.AvailabilityZone],
  ['상태',(a,item)=>a.State?.Name||a.State?.Code],
  ['프로토콜',(a,item)=>a.Protocol],
  ['포트',(a,item)=>a.Port],
  ['VPC',(a,item)=>a.VpcId],
  ['서브넷',(a,item)=>a.SubnetId],
  ['스킴',(a,item)=>a.Scheme],
  ['라우팅',(a,item)=>routeClasses[item.route_class]],
  ['엔진',(a,item)=>a.Engine],
  ['역할',(a,item)=>item.db_role||a.CurrentRole],
  ['노드 유형',(a,item)=>a.CacheNodeType||a.InstanceType],
  ['버전',(a,item)=>a.version||a.KafkaVersion||a.EngineVersion],
  ['런타임',(a,item)=>a.Runtime],
  ['서비스',(a,item)=>a.ServiceName],
  ['엔드포인트 유형',(a,item)=>a.VpcEndpointType],
  ['DB 클래스',(a,item)=>a.DBInstanceClass],
  ['Multi-AZ',(a,item)=>a.MultiAZ],
  ['용량',(a,item)=>a.DesiredCapacity!==undefined?`${a.MinSize}–${a.MaxSize} (원하는 ${a.DesiredCapacity})`:undefined],
  ['읽기 복제본 원본',(a,item)=>a.ReadReplicaSourceDBInstanceIdentifier],
  ['읽기 복제본',(a,item)=>(a.ReadReplicaDBInstanceIdentifiers||[]).join(', ')],
  ['샤드',(a,item)=>a.Shard],
  ['자동 장애 조치',(a,item)=>a.AutomaticFailover],
  ['노드 (AZ)',(a,item)=>item.kind==='search_node'?`data ${a.Data??'?'} · master ${a.Master??'?'}${a.Standby?' · standby':''}${a.Estimated?' (추정)':''}`:undefined],
  ['Multi-AZ with Standby',(a,item)=>a.MultiAZWithStandby],
  ['브로커',(a,item)=>(a.BrokerIds||[]).join(', ')],
  ['태스크',(a,item)=>a.desiredCount!==undefined?`실행 ${a.runningCount} / 원하는 ${a.desiredCount}`:undefined],
  ['시작 유형',(a,item)=>a.LaunchSummary||a.launchType],
  ['IP',(a,item)=>a.PrivateIp||a.IpAddress],
  ['EFS 클래스',(a,item)=>item.kind==='efs_mount_target'?(a.OneZone?'One Zone':'Regional'):undefined],
  ['처리량 모드',(a,item)=>a.ThroughputMode],
  ['Redshift 역할',(a,item)=>a.Role],
  ['노드',(a,item)=>a.NodesHere!==undefined?`${a.NodeType} × ${a.NodesHere}`:undefined],
  ['피어링',(a,item)=>item.kind==='vpc_peering'?`${a.RequesterVpcInfo?.CidrBlock} ↔ ${a.AccepterVpcInfo?.CidrBlock} (${a.Status?.Code})`:undefined],
  ['인바운드 규칙',(a,item)=>item.kind==='security_group'?(a.IpPermissions||[]).map(r=>`${r.IpProtocol==='-1'?'모든 트래픽':`${r.IpProtocol} ${r.FromPort===r.ToPort?r.FromPort:r.FromPort+'-'+r.ToPort}`} ← ${[...(r.IpRanges||[]).map(x=>x.CidrIp),...(r.Ipv6Ranges||[]).map(x=>x.CidrIpv6),...(r.UserIdGroupPairs||[]).map(x=>x.GroupId),...(r.PrefixListIds||[]).map(x=>x.PrefixListId)].join(', ')}`).join(' · ')||'없음':undefined],
  ['VPN 터널',(a,item)=>(a.VpnTunnels||[]).map(t=>t+' UP').join(', ')],
  ['BGP ASN',(a,item)=>a.BgpAsn||a.AmazonSideAsn],
  ['Elastic IP',(a,item)=>(item.elastic_ips||[]).join(', ')],
];
export function showResource(id, select=true) {
  const item=node(id);if(!item)return;state.selected=id;setControls();
  const body=$('detail-body');body.replaceChildren(el('div',names[item.kind]||item.kind,'detail-kind'),el('h3',item.name,'detail-name'),el('div',item.resource_id,'detail-id'));
  body.append(el('p',item.reference_only?'참조만 확인 · 상세 미조회':'API 응답에서 조회됨','small-note'));
  const actions=el('div',undefined,'inspector-actions');
  if(item.kind==='vpc'&&!(state.view==='detail'&&state.vpc===item.resource_id))actions.append(actionButton('상세 구성 보기',()=>switchView('detail',{vpc:item.resource_id})));
  if(item.kind!=='vpc'&&!(state.view==='focus'&&state.focus===id))actions.append(actionButton('선택 리소스 보기',()=>switchView('focus',{focus:id})));
  if(state.view==='focus')actions.append(actionButton('상세 구성으로 돌아가기',()=>switchView('detail',{vpc:vpcOf(state.focus)||state.vpc})));
  if(state.view==='detail'&&['load_balancer','target_group'].includes(item.kind)&&state.routes!==id&&vpcOf(id)===state.vpc)
    actions.append(actionButton('이 경로만 보기',async()=>{if(state.loading||state.busy)return;await flushEdits();state.routes=id;await loadView();}));
  if(state.view==='detail'&&state.routes)actions.append(actionButton('모든 경로 보기',async()=>{if(state.loading||state.busy)return;await flushEdits();state.routes=null;await loadView();}));
  const related=relationsOf(id).map(e=>e.id);
  if(related.length&&state.view!=='overview')actions.append(actionButton('직접 관계 강조',()=>post({action:'diagramBotSelect',resourceId:id,relations:related})));
  if(actions.children.length)body.append(actions);
  const a=item.attributes,fields=[...FIELDS.map(([label,value])=>[label,value(a,item)]),...(item.properties||[])];
  const list=el('dl');for(const [key,value]of fields)if(value!==undefined&&value!==null&&value!==''){const field=el('div',undefined,'detail-field');field.append(el('dt',key),el('dd',String(value)));list.append(field);}body.append(list);
  if(item.attributes.Tags?.length){body.append(el('div','TAGS','detail-section'));for(const tag of item.attributes.Tags)body.append(el('p',`${tag.Key} = ${tag.Value}`,'small-note'));}
  body.append(el('div',`API에서 확인한 관계 · ${related.length}개`,'detail-section'));
  for(const edge of relationsOf(id)){
    // An endpoint can be missing from the graph (an imported or hand-edited snapshot); show its ID instead of failing.
    const otherId=edge.source===id?edge.target:edge.source,other=node(otherId);
    const qualifier=[edge.qualifiers?.port&&`:${edge.qualifiers.port}`,edge.qualifiers?.availability_zone,edge.qualifiers?.destination].filter(Boolean).join(' · ');
    const button=el('button',`${relationNames[edge.relation]||edge.relation} · ${other?.name||otherId}${qualifier?' · '+qualifier:''}`,'relation-button');button.append(el('small',edge.evidence));
    button.onclick=()=>{showRelation(edge.id);if(state.ready&&!state.loading)post({action:'diagramBotSelectRelation',relationId:edge.id});};body.append(button);
  }
  const raw=el('details');raw.append(el('summary','원본 속성 보기'),el('pre',JSON.stringify(item.attributes,null,2)));body.append(raw);
  if(select&&state.ready&&!state.loading)post({action:'diagramBotSelect',resourceId:id});
}
export function showRelation(id, index) {
  const edges=state.snapshot.graph.edges;
  const edge=id?edges.find(e=>e.id===id):edges[Number(index)];if(!edge)return;
  const body=$('detail-body');
  body.replaceChildren(el('div','CONFIGURATION RELATION','detail-kind'),el('h3',relationNames[edge.relation]||edge.relation,'detail-name'),el('p','AWS API에서 확인한 설정 관계입니다. 실제 통신 여부를 의미하지 않습니다.','small-note'));
  for(const key of ['source','target']){const other=node(edge[key]);if(other){const button=el('button',`${key==='source'?'출발':'대상'} · ${names[other.kind]||other.kind} · ${other.name}`,'relation-button');button.onclick=()=>showResource(other.id);body.append(button);}}
  body.append(el('pre',JSON.stringify(edge,null,2)));
}
// A selected diagram-only cell (group, summary, user note) has no AWS resource behind it.
export function showElement(label) {
  $('detail-body').replaceChildren(el('div','DIAGRAM ELEMENT','detail-kind'),el('h3',label,'detail-name'),el('p','표현용 그룹, 요약 또는 사용자 주석입니다. 수집한 AWS 속성은 변경되지 않습니다.','small-note'));
}
export function clearInspector() {$('detail-body').replaceChildren(emptyDetail.cloneNode(true));}
