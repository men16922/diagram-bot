// Entry module of app.html: wires the shell (views, tabs, toolbar, import, combine) and starts the app. The screen logic
// lives in js/: state, ui, editor (draw.io protocol), document (load/save flow), workspace, inspector, review, exports,
// signin, chat and xml (draw.io XML import, alignment and company standards); exports.js, chat.js and xml.js are imported for
// the handlers they bind.
import {startRelay} from './js/relay.js';
import {api, state} from './js/state.js';
import {$, bind, clearNotice, el, guard, setControls, toast} from './js/ui.js';
import {openEditor, post} from './js/editor.js';
import {flushEdits, loadSnapshot, loadView, refreshSnapshots, replaceDocument, saveDocument, snapshotLabel, switchView} from './js/document.js';
import {renderInventory} from './js/workspace.js';
import {renderChanges} from './js/review.js';
import {renderProfile} from './js/signin.js';
import './js/exports.js';
import './js/chat.js';
import './js/xml.js';
window.diagramBot = {state};  // for browser checks (scripts/verify_demo.py) and debugging
bind('all-environments',()=>switchView('overview'));
bind('view-overview',()=>switchView('overview'));
bind('view-detail',()=>switchView('detail',{vpc:state.vpc}));
bind('view-focus',()=>switchView('focus',{focus:state.selected}));
bind('save-button',()=>saveDocument());
bind('coverage-button',()=>{$('coverage-dialog').showModal();});
bind('note-button',()=>post({action:'diagramBotNote'}));
bind('reset-button',()=>replaceDocument('/relayout',{},'자동 배치를 다시 실행했습니다. 이전 편집본은 보관되고 메모는 유지됩니다.'));
bind('restore-button',()=>{const last=state.history.at(-1);return replaceDocument('/restore',{from_revision:last.revision},`보관된 v${last.revision} 편집본을 복원했습니다.`);});
bind('import-button',()=>$('import-file').click());
const modes=['diagram','inventory','review','changes'];
for(const mode of modes)bind(mode+'-tab',async()=>{state.mode=mode;$('canvas-area').hidden=mode!=='diagram';for(const panel of modes.slice(1))$(panel).hidden=mode!==panel;for(const tab of modes){$(tab+'-tab').classList.toggle('active',mode===tab);$(tab+'-tab').setAttribute('aria-selected',String(mode===tab));}renderInventory();if(mode==='changes')await renderChanges();});
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
$('snapshot-select').onchange=guard(()=>loadSnapshot($('snapshot-select').value));
$('references').onchange=async()=>{try{await flushEdits();state.references=$('references').checked;await loadView();}catch(e){toast(e.message,true);$('references').checked=state.references;}};
$('route-filter').onchange=async()=>{const previous=state.routes;try{await flushEdits();state.routes=$('route-filter').value||null;clearNotice();await loadView();}catch(e){state.routes=previous;$('route-filter').value=previous||'';toast(e.message,true);}};
$('import-file').onchange=async()=>{
  try{const file=$('import-file').files[0];if(!file)return;if(file.size>15*1024*1024)throw new Error('스냅샷은 최대 15 MiB입니다.');const data=JSON.parse(await file.text());const result=await api('/api/snapshots',{method:'POST',body:JSON.stringify(data)});await refreshSnapshots(result.id);await loadSnapshot(result.id);toast('스냅샷을 가져왔습니다.');}catch(error){toast(error.message,true);}finally{$('import-file').value='';}
};
window.addEventListener('beforeunload',event=>{if(state.dirty){event.preventDefault();event.returnValue='';}});
async function start() {
  const boot=await api('/api/bootstrap');state.token=boot.token;state.role=boot.user.role;
  state.team=boot.team;
  if(boot.egress==='browser'){startRelay(api);$('egress-note').hidden=false;}
  if(boot.team){const name=boot.user.name;$('user-avatar').textContent=name.slice(0,2).toUpperCase();$('user-avatar').title=`${name} · ${boot.user.role}`;
    $('user-role').textContent=`${name} · ${{viewer:'보기 전용',editor:'편집',admin:'관리자'}[boot.user.role]}`;$('user-role').hidden=false;$('logout-button').hidden=false;}
  bind('logout-button',async()=>{await api('/api/logout',{method:'POST'});location.href='/login';});
  for(const profile of boot.profiles)$('aws-profile').append(new Option(profile,profile));
  renderProfile();
  await refreshSnapshots('demo');await loadSnapshot('demo');
  if(!boot.editor_ready){state.loading=false;setControls();$('editor-message').textContent='터미널에서 diagram-bot setup-editor를 실행한 뒤 앱을 다시 시작하세요.';document.querySelector('.loader').hidden=true;return;}
  openEditor();
}
start().catch(error=>{toast(error.message,true);$('editor-message').textContent=error.message;});
