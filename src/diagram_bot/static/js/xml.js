// draw.io XML dialog: one chosen .drawio/.xml file imported as a snapshot (POST /api/xml/import), auto-aligned
// (POST /api/xml/align) or adopted as a company standard (POST /api/xml/adopt), and the company standards list
// (GET /api/standards, DELETE /api/standards/{name} for admins). Results and errors stay inside the modal dialog.
import {alignMetricNames} from './labels.js';
import {api, state} from './state.js';
import {$, bind, download, el, setControls} from './ui.js';
import {loadSnapshot, refreshSnapshots} from './document.js';
const MAX_XML=10*1024*1024;
let aligned=null;
const status=(text)=>{$('xml-status').textContent=text;};
const count=value=>Array.isArray(value)?value.length:Number(value||0);
const day=value=>value?new Date(value).toLocaleString('ko-KR',{dateStyle:'short',timeStyle:'short'}):'';
async function chosenXml() {
  const file=$('xml-file').files[0];
  if(!file)throw new Error('draw.io 파일(.drawio, .xml)을 먼저 고르세요.');
  if(file.size>MAX_XML)throw new Error('draw.io 파일은 최대 10 MiB입니다.');
  return {file,xml:await file.text()};
}
function showResult(...children){$('xml-result').replaceChildren(...children);$('xml-result').hidden=!children.length;}
// One action at a time; its failure is shown in the dialog (a toast would sit behind the modal backdrop).
async function run(message, task) {
  if(state.xmlBusy)return;
  state.xmlBusy=true;setControls();status(message);showResult();$('xml-download').hidden=true;
  try{await task();}catch(error){status(error.message);}
  finally{state.xmlBusy=false;setControls();}
}
function list(title, items){const box=el('div');box.append(el('b',title));const ul=el('ul');for(const item of items.slice(0,8))ul.append(el('li',item));if(items.length>8)ul.append(el('li',`외 ${items.length-8}개`));box.append(ul);return box;}
function importReport(report) {
  const pages=report.pages||[],unmapped=report.unmapped||[],synthesized=report.synthesized||[];
  const parts=[el('b',`페이지 ${pages.filter(p=>p.imported).length}/${pages.length}개 · 셀 ${Object.keys(report.cells||{}).length}개 대응 · 미대응 도형 ${unmapped.length}개 · 합성 ${synthesized.length}개`)];
  if(unmapped.length)parts.push(list('대응하지 않은 도형 (추측하지 않고 보고만 합니다)',unmapped.map(u=>`${u.page} · ${u.label||u.id}${u.kind?` (${u.kind})`:''}`)));
  if(synthesized.length)parts.push(list('그림에 없어 만든 항목',synthesized.map(String)));
  return parts;
}
export async function loadStandards() {
  state.standards=await api('/api/standards');renderStandards();return state.standards;
}
export function renderStandards() {
  const rows=state.standards.filter(s=>s.company);
  $('standards-rows').replaceChildren(...rows.map(standard=>{
    const item=el('li'),text=el('div'),name=el('strong',standard.title);
    if(standard.default)name.append(el('span','기본 규격','badge warn'));
    text.append(name,el('small',[standard.name,day(standard.created_at),standard.adopted_by].filter(Boolean).join(' · ')));
    const open=el('button','열기','text-button');open.type='button';open.disabled=!standard.snapshot_id;
    open.onclick=()=>run('표준 스냅샷을 여는 중…',async()=>{$('xml-dialog').close();await refreshSnapshots(standard.snapshot_id);await loadSnapshot(standard.snapshot_id);status('');});
    item.append(text,open);
    if(state.role==='admin'){
      const remove=el('button','삭제','text-button');remove.type='button';
      remove.onclick=()=>{if(!confirm(`사내 표준 ${standard.title}(${standard.name})을(를) 삭제할까요?${standard.default?' 서버 기본 규격도 기본값으로 돌아갑니다.':''}`))return;
        run('삭제하는 중…',async()=>{const result=await api(`/api/standards/${encodeURIComponent(standard.name)}`,{method:'DELETE'});await loadStandards();
          status(`사내 표준 ${standard.name}을(를) 삭제했습니다.${result.default_cleared?' 서버 기본 규격은 기본값으로 돌아갔습니다.':''}`);});};
      item.append(remove);
    }
    return item;
  }));
  $('standards-empty').hidden=rows.length>0;
}
bind('xml-button',async()=>{status('');showResult();$('xml-download').hidden=true;setControls();$('xml-dialog').showModal();await loadStandards().catch(error=>status(error.message));});
$('close-xml').onclick=()=>$('xml-dialog').close();
$('xml-form').onsubmit=event=>event.preventDefault();
$('xml-file').onchange=()=>{aligned=null;$('xml-download').hidden=true;showResult();status('');};
bind('xml-import',()=>run('가져오는 중…',async()=>{
  const {xml}=await chosenXml();
  const result=await api('/api/xml/import',{method:'POST',body:JSON.stringify({xml,save:true})});
  await refreshSnapshots(result.id);await loadSnapshot(result.id);
  status(`스냅샷으로 가져와 열었습니다 · ${result.snapshot.metadata.title}. 라벨에서 읽은 추론이므로 원본과 비교해 확인하세요.`);
  showResult(...importReport(result.snapshot.metadata.import||{}));
}));
bind('xml-align',()=>run('정렬하는 중…',async()=>{
  const {file,xml}=await chosenXml();
  const result=await api('/api/xml/align',{method:'POST',body:JSON.stringify({xml})});
  const table=el('table'),head=el('tr');head.append(el('th','항목'),el('th','정렬 전'),el('th','정렬 후'));table.append(head);
  for(const [key,label] of Object.entries(alignMetricNames)){const row=el('tr');row.append(el('td',label),el('td',String(count(result.before[key]))),el('td',String(count(result.after[key]))));table.append(row);}
  const pages=result.pages||[],reverted=pages.filter(p=>p.status==='reverted'),changed=pages.filter(p=>p.status==='aligned');
  const parts=[el('b',`페이지 ${pages.length}개 · 정렬 ${changed.length} · 원본 유지 ${pages.length-changed.length}`),table];
  // Quality guard: a page whose overlaps, out-of-bounds cells or edge collisions would grow keeps its original layout.
  if(reverted.length)parts.push(list('정렬하면 겹침·범위 이탈·선 충돌이 늘어나 원래 배치를 유지한 페이지',reverted.map(p=>p.name)));
  showResult(...parts);
  aligned={name:file.name.replace(/\.(drawio|xml)$/i,'')+'-aligned.drawio',xml:result.xml};$('xml-download').hidden=false;
  status(!changed.length?(reverted.length?'정렬 결과가 원본보다 나빠 입력을 그대로 두었습니다.':'정렬할 문제가 없어 입력을 그대로 두었습니다.'):'정렬했습니다. 정렬된 파일을 다운로드하세요.');
}));
bind('xml-download',()=>{if(aligned)download(aligned.name,aligned.xml,'application/xml');});
bind('xml-adopt',()=>run('규격을 추출하고 채택하는 중…',async()=>{
  const {xml}=await chosenXml();
  const name=$('adopt-name').value.trim();
  if(!/^[a-z0-9-]{1,40}$/.test(name))throw new Error('이름은 영문 소문자·숫자·하이픈 40자 이내로 쓰세요 (예: corp-web).');
  const makeDefault=$('adopt-default').checked;
  const result=await api('/api/xml/adopt',{method:'POST',body:JSON.stringify({xml,name,title:$('adopt-title').value.trim()||null,make_default:makeDefault,save:true})});
  await refreshSnapshots(result.id);await loadSnapshot(result.id);await loadStandards();
  status(result.default
    ?`사내 표준 ${result.name}을(를) 채택했습니다. 서버 기본 규격이 되었고, 다운로드의 규격 목록과 AI 어시스턴트(apply_standard_architecture ${result.name}, 예: "${result.name} 표준으로 설계해줘")에서 쓸 수 있습니다.`
    :`규격을 추출하고 정렬한 템플릿을 스냅샷으로 열었습니다. 이 채택은 이번 요청에만 적용되어 어시스턴트와 규격 목록에는 등록되지 않습니다(관리자가 기본 규격으로 지정하면 등록됩니다).`);
  const changed=Object.keys(alignMetricNames).filter(k=>count(result.before[k])!==count(result.after[k]));
  showResult(el('b',`추출한 규격 · 래퍼 ${result.spec.cell_wrapper} · 서브넷 라벨 ${result.spec.subnet_label_style} · Cloud ${result.spec.use_cloud_box?'○':'×'} · Region ${result.spec.use_region_box?'○':'×'} · AZ ${result.spec.use_az_box?'○':'×'}`),
    el('span',changed.length?`정렬로 바뀐 지표: ${changed.map(k=>`${alignMetricNames[k]} ${count(result.before[k])}→${count(result.after[k])}`).join(', ')}`:'템플릿 정렬 지표는 그대로입니다.'));
}));
