// The review (findings) and change comparison tabs.
import {names, pillarNames, relationNames, severityNames} from './labels.js';
import {api, node, state} from './state.js';
import {$, el, guard} from './ui.js';
import {snapshotLabel} from './document.js';
import {showResource} from './inspector.js';
export function renderReview() {
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
export async function renderChanges() {
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
$('compare-select').onchange=guard(renderChanges);
$('severity-filter').onchange=renderReview;
