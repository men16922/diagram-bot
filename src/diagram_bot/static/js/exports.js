// Downloads: the diagram (.drawio, PNG) from the editor or as a fresh rendering in a chosen spec, and the stored snapshot as
// JSON, CSV, review CSV and CloudFormation.
import {pillarNames, severityNames} from './labels.js';
import {api, node, request, state} from './state.js';
import {$, bind, download, el, setControls, toast} from './ui.js';
import {exportPng, requestDocument, sendDocument} from './editor.js';
import {documentUrl, flushEdits, saveDocument} from './document.js';
import {loadStandards} from './xml.js';
const chosenSpec=()=>$('export-spec').value;
function filename(extension, spec) {const scope=state.view==='detail'?state.vpc+(state.routes?`-routes-${node(state.routes)?.name}`:''):state.view==='focus'?`focus-${node(state.focus)?.resource_id.split('/').pop()}`:'overview';return `diagram-bot-${state.snapshot.metadata.account_id}-${scope}-${spec?`spec-${spec}`:`v${state.revision}`}.${extension}`;}
// Editors save first so the file matches the stored revision; viewers cannot save and export what the editor shows.
const exportXml=()=>state.role==='viewer'?requestDocument():saveDocument(false);
// A spec export renders this view afresh in that preset or company standard (GET document?spec=, revision null): it holds
// no manual edits and is never saved over the edited document.
const specXml=async spec=>{const preview=await api(documentUrl('',false,spec));if(preview.revision!==null)throw new Error('규격 미리보기가 아닙니다.');return preview.xml;};
async function downloadDiagram() {
  const spec=chosenSpec();
  if(spec){download(filename('drawio',spec),await specXml(spec),'application/xml');toast(`${spec} 규격으로 새로 그린 draw.io 파일을 다운로드했습니다. 수동 편집은 들어 있지 않습니다.`);return;}
  const xml=await exportXml();download(filename('drawio'),xml,'application/xml');toast('편집 가능한 draw.io 파일을 다운로드했습니다.');
}
async function downloadPng() {
  // draw.io's PNG export loads the XML it is given into the editor, so a spec PNG puts the shown document back afterwards;
  // it saves nothing new itself (pending edits are flushed as before any load).
  const spec=chosenSpec(),shown=!spec?await exportXml():state.role==='viewer'?await requestDocument():(await flushEdits(),state.xml);
  const xml=spec?await specXml(spec):shown;
  state.busy=true;setControls();$('download-png').disabled=true;
  try{
    const uri=await exportPng(xml,{scale:Number($('png-scale').value),transparent:$('png-transparent').checked});
    const bytes=Uint8Array.from(atob(uri.split(',')[1]),c=>c.charCodeAt(0));
    if(bytes.length<1000)throw new Error('PNG가 비어 있습니다. 다시 시도하세요.');
    download(filename('png',spec),bytes,'image/png');toast(spec?`${spec} 규격으로 새로 그린 PNG를 다운로드했습니다. 수동 편집은 들어 있지 않습니다.`:'PNG 이미지를 다운로드했습니다.');
  }finally{state.busy=false;setControls();$('download-png').disabled=false;if(spec){state.xml=shown;sendDocument();}}
}
function csvCell(value){let text=typeof value==='object'?JSON.stringify(value):String(value??'');if(/^\s*[=+\-@]/.test(text))text="'"+text;return '"'+text.replaceAll('"','""')+'"';}
// Excel opens UTF-8 CSV correctly only with a BOM.
function downloadCsv(name, rows){download(name,'\ufeff'+rows.map(r=>r.map(csvCell).join(',')).join('\r\n'),'text/csv;charset=utf-8');}
function downloadInventory(){const fields=['account','region','kind','resource_id','name','reference_only','source','attributes'];downloadCsv('inventory.csv',[fields,...state.snapshot.graph.nodes.map(n=>fields.map(f=>n[f]))]);}
function downloadFindings(){const fields=['severity','pillar','rule','resource_name','resource','title','evidence','recommendation'];
  downloadCsv(`diagram-bot-review-${state.snapshot.metadata.account_id}-${state.snapshot.metadata.region}.csv`,[fields,...(state.review?.findings||[]).map(f=>fields.map(k=>k==='severity'?severityNames[f[k]]:k==='pillar'?pillarNames[f[k]]:f[k]))]);}
// CloudFormation draft from the stored snapshot (not the edited diagram); a combined snapshot gives one template per account × region.
async function downloadCloudFormation(){
  const parts=state.snapshot.metadata.parts||[null];let unsupported=0;
  for(const [index,part] of parts.entries()){
    const response=await request(`/api/snapshots/${state.snapshotId}/cloudformation${part?`?part=${index}`:''}`);
    const text=await response.text();
    unsupported+=JSON.parse(text).Metadata.DiagramBot.Unsupported.length;
    download(response.headers.get('Content-Disposition')?.match(/filename="([^"]+)"/)?.[1]||'cloudformation.json',text,'application/json');
  }
  toast(`CloudFormation 템플릿 초안 ${parts.length}개를 다운로드했습니다. 매개변수와 Metadata.DiagramBot(미지원 ${unsupported}개·가정)을 검토하세요.`);
}
// 규격: 기본 (the edited document), the presets and the company standards that carry a spec.
async function renderSpecs() {
  const select=$('export-spec'),current=select.value,standards=(await loadStandards().catch(()=>state.standards)).filter(s=>s.company&&s.spec);
  const group=(label,options)=>{const g=el('optgroup');g.label=label;g.append(...options);return g;};
  select.replaceChildren(new Option('기본 · 현재 편집본',''),group('프리셋 (새로 그림)',state.presets.map(p=>new Option(p,p))),
    ...(standards.length?[group('사내 표준 (새로 그림)',standards.map(s=>new Option(`${s.title} (${s.name})${s.default?' · 기본 규격':''}`,s.name)))]:[]));
  select.value=[...select.options].some(o=>o.value===current)?current:'';$('export-spec-note').hidden=!select.value;
}
$('export-spec').onchange=()=>{$('export-spec-note').hidden=!chosenSpec();};
bind('export-button',async()=>{$('export-dialog').showModal();await renderSpecs();});
bind('download-drawio',downloadDiagram);bind('download-png',downloadPng);
bind('download-json',()=>download('snapshot.json',JSON.stringify(state.snapshot,null,2),'application/json'));bind('download-csv',downloadInventory);bind('download-cfn',downloadCloudFormation);
bind('download-findings',downloadFindings);
