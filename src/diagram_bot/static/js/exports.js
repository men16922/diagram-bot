// Downloads: the diagram (.drawio, PNG) from the editor, and the stored snapshot as JSON, CSV, review CSV and CloudFormation.
import {pillarNames, severityNames} from './labels.js';
import {node, request, state} from './state.js';
import {$, bind, download, setControls, toast} from './ui.js';
import {exportPng, requestDocument} from './editor.js';
import {saveDocument} from './document.js';
function filename(extension) {const scope=state.view==='detail'?state.vpc+(state.routes?`-routes-${node(state.routes)?.name}`:''):state.view==='focus'?`focus-${node(state.focus)?.resource_id.split('/').pop()}`:'overview';return `diagram-bot-${state.snapshot.metadata.account_id}-${scope}-v${state.revision}.${extension}`;}
// Editors save first so the file matches the stored revision; viewers cannot save and export what the editor shows.
const exportXml=()=>state.role==='viewer'?requestDocument():saveDocument(false);
async function downloadDiagram() {
  const xml=await exportXml();download(filename('drawio'),xml,'application/xml');toast('편집 가능한 draw.io 파일을 다운로드했습니다.');
}
async function downloadPng() {
  const xml=await exportXml();state.busy=true;setControls();$('download-png').disabled=true;
  try{
    const uri=await exportPng(xml,{scale:Number($('png-scale').value),transparent:$('png-transparent').checked});
    const bytes=Uint8Array.from(atob(uri.split(',')[1]),c=>c.charCodeAt(0));
    if(bytes.length<1000)throw new Error('PNG가 비어 있습니다. 다시 시도하세요.');
    download(filename('png'),bytes,'image/png');toast('PNG 이미지를 다운로드했습니다.');
  }finally{state.busy=false;setControls();$('download-png').disabled=false;}
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
bind('export-button',()=>{$('export-dialog').showModal();});
bind('download-drawio',downloadDiagram);bind('download-png',downloadPng);
bind('download-json',()=>download('snapshot.json',JSON.stringify(state.snapshot,null,2),'application/json'));bind('download-csv',downloadInventory);bind('download-cfn',downloadCloudFormation);
bind('download-findings',downloadFindings);
