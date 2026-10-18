// The document flow: snapshot and view loading, saving, relayout/restore. The server keeps one document per view
// (GET/PUT /api/snapshots/{id}/document) with a revision that every save must name.
import {viewNames} from './labels.js';
import {api, node, state, vpcNode} from './state.js';
import {$, clearNotice, setControls, toast} from './ui.js';
import {requestDocument, sendDocument} from './editor.js';
import {documentActive, renderSummary} from './workspace.js';
import {clearInspector, showResource} from './inspector.js';
import {renderChanges, renderReview} from './review.js';
function documentUrl(suffix='', reset=false) {
  const query=new URLSearchParams({view:state.view,references:String(state.references)});
  if(state.view==='detail')query.set('vpc',state.vpc);
  if(state.view==='detail'&&state.routes)query.set('routes',state.routes);
  if(state.view==='focus')query.set('resource',state.focus);
  if(reset)query.set('reset','true');
  return `/api/snapshots/${state.snapshotId}/document${suffix}?${query}`;
}
export function snapshotLabel(item){const m=item.metadata;if(m.combined&&!m.sample)return `통합 · ${m.title} · ${m.finished_at.slice(5,16).replace('T',' ')}`;if(m.design)return `설계 · ${m.title} · ${m.finished_at.slice(5,16).replace('T',' ')}`;if(m.imported)return `가져옴 · ${m.title} · ${m.finished_at.slice(5,16).replace('T',' ')}`;return m.title?`샘플 · ${m.title}`:`${m.demo?'샘플':'AWS'} · ${m.account_id} · ${m.region} · ${m.finished_at.slice(5,16).replace('T',' ')}`;}
export async function refreshSnapshots(preferred) {
  const items=await api('/api/snapshots');state.snapshots=items;$('snapshot-select').replaceChildren();
  for(const item of items)$('snapshot-select').append(new Option(snapshotLabel(item),item.id));
  $('snapshot-select').value=preferred||items[0]?.id;
  return $('snapshot-select').value;
}
// Loads can overlap (a chat step arriving while the user picks another snapshot); only the newest one applies its result.
let snapshotSeq=0,viewSeq=0;
export async function loadSnapshot(id) {
  const seq=++snapshotSeq;
  await flushEdits();
  clearNotice();
  const [snapshot,review]=await Promise.all([api(`/api/snapshots/${id}`),api(`/api/snapshots/${id}/findings`)]);
  if(seq!==snapshotSeq)return;
  state.snapshot=snapshot;state.snapshotId=id;state.review=review;renderReview();
  // The overview shows how VPCs connect; with one VPC and no TGW, peering or VPN it is a single card, so open its detail.
  const graphNodes=state.snapshot.graph.nodes,vpcs=graphNodes.filter(n=>n.kind==='vpc');
  const linked=graphNodes.some(n=>['transit_gateway','vpc_peering','vpn_gateway','customer_gateway','dx_gateway'].includes(n.kind));
  state.view=vpcs.length===1&&!linked?'detail':'overview';state.vpc=vpcs[0]?.resource_id||null;state.focus=null;state.selected=null;state.routes=null;
  $('snapshot-select').value=id;
  // A selection from the previous snapshot must not linger in the inspector.
  clearInspector();
  renderSummary();await loadView();
  if(seq===snapshotSeq&&state.mode==='changes')await renderChanges();
}
function applyDocument(document) {
  state.xml=document.xml;state.revision=document.revision;state.history=document.history||[];state.visible=new Set(document.visible||[]);
  const c=document.counts;
  $('view-scope').textContent=c?`표시 ${c.visible}개 / 수집 ${c.collected}개 · 참조 ${c.references}개 · 이 보기에서 숨김 ${c.hidden}개`+(c.filtered?` (경로 필터 ${c.filtered}개 포함)`:''):'';
}
export async function loadView() {
  const seq=++viewSeq;state.loading=true;setControls();
  try{
    const loaded=await api(documentUrl());if(seq!==viewSeq)return;
    applyDocument(loaded);state.dirty=false;
    const title=state.view==='detail'?vpcNode(state.vpc)?.name:state.view==='focus'?node(state.focus)?.name:'AWS 전체 구성';
    $('environment-title').textContent=title||'AWS 구성도';$('crumb-view').textContent=viewNames[state.view];
    documentActive();if(state.selected&&node(state.selected))showResource(state.selected,false);
    if(state.ready)sendDocument();else{state.loading=false;setControls();}
  }catch(error){if(seq!==viewSeq)return;state.loading=false;setControls();throw error;}
}
export async function switchView(view, options={}) {
  if(state.loading||state.busy)return;
  clearNotice();
  try{
    await flushEdits();
    // A route filter belongs to one VPC's detail view.
    if(view!=='detail'||(options.vpc&&options.vpc!==state.vpc))state.routes=null;
    state.view=view;if(options.vpc)state.vpc=options.vpc;if(options.focus)state.focus=options.focus;
    await loadView();
  }catch(error){toast(error.message,true);}
}
export async function flushEdits() {if(state.dirty)await saveDocument(false);}
export async function saveDocument(notify=true) {
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
export async function replaceDocument(suffix, body, message) {
  await flushEdits();
  state.busy=true;setControls();
  try{applyDocument(await api(documentUrl(suffix),{method:'POST',body:JSON.stringify({revision:state.revision,...body})}));}
  finally{state.busy=false;setControls();}
  sendDocument();toast(message);
}
