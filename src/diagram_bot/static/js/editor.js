// The draw.io embed protocol: standard messages (configure, init, load, autosave, save, export) and the diagramBot*
// messages of editor-bridge.js. Both sides check the origin and the sending window.
import {state} from './state.js';
import {$, guard, setControls, toast} from './ui.js';
import {saveDocument} from './document.js';
import {showElement, showRelation, showResource} from './inspector.js';
const pending = new Map();
let editorTimer, pngWaiter = null;
export function post(message) { $('editor').contentWindow.postMessage(JSON.stringify(message),location.origin); }
export function sendDocument() {
  // draw.io focuses itself when a document loads and the browser would scroll the page to the iframe; keep the user's place.
  state.scrollY=window.scrollY;state.loading=true;setControls();$('editor-overlay').hidden=false;
  post({action:'load',xml:state.xml,autosave:1,title:'Diagram Bot',saveAndExit:0,noSaveBtn:1,noExitBtn:1,fit:1,maxFitScale:1,border:24});
  clearTimeout(editorTimer);
  editorTimer=setTimeout(()=>{state.loading=false;state.ready=false;setControls();$('editor-message').textContent='편집기 응답이 지연되고 있습니다. 새로고침하거나 setup-editor 설치를 확인하세요.';},30000);
}
export function requestDocument() {
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
  if(message.event==='save'&&!state.loading)guard(saveDocument)();
  if(message.event==='diagramBotDocument'||message.event==='diagramBotError'){
    const task=pending.get(message.requestId);if(task){clearTimeout(task.timer);pending.delete(message.requestId);message.error?task.reject(new Error(message.error)):task.resolve(message.xml);}
    else if(message.error)toast(message.error,true);
  }
  if(message.event==='diagramBotSelection'){
    if(message.resourceId)showResource(message.resourceId,false);
    else if(message.relationId||message.relationIndex!=null)showRelation(message.relationId,message.relationIndex);
    else if(message.label)showElement(message.label);
  }
  if(message.event==='diagramBotNotVisible')toast('현재 보기에서 숨겨진 리소스입니다. 속성 패널에서 내용을 확인하거나 선택 리소스 보기를 여세요.');
  if(message.event==='diagramBotHighlighted')toast(message.count?`직접 관계 ${message.count}개를 구성도에서 강조했습니다.`:'이 보기에 그려진 직접 관계가 없습니다. 선택 리소스 보기에서 확인하세요.');
  if(message.event==='diagramBotNoteAdded')toast('메모를 추가했습니다. 더블클릭해 내용을 수정하세요.');
  if(message.event==='export'&&pngWaiter){
    const task=pngWaiter;pngWaiter=null;clearTimeout(task.timer);
    if(message.data?.startsWith('data:image/png;base64,'))task.resolve(message.data);else task.reject(new Error('PNG를 생성하지 못했습니다. 해상도를 낮춰 다시 시도하세요.'));
  }
});
// PNG of the given document through draw.io's own exporter (the editor answers with an export event).
export function exportPng(xml, {scale, transparent}) {
  return new Promise((resolve,reject)=>{
    const timer=setTimeout(()=>{pngWaiter=null;reject(new Error('PNG 생성 시간이 초과됐습니다. 해상도를 낮춰 다시 시도하세요.'));},45000);
    pngWaiter={resolve,reject,timer};
    post({action:'export',format:'png',xml,scale,border:24,grid:false,transparent,background:transparent?null:'#FFFFFF',size:'diagram',currentPage:true});
  });
}
export function openEditor() {
  $('editor').src='/editor/index.html?embed=1&proto=json&configure=1&spin=1&ui=min&libraries=1&noSaveBtn=1&noExitBtn=1&saveAndExit=0&offline=1&local=1&stealth=1&lang=ko';
  editorTimer=setTimeout(()=>{$('editor-message').textContent='편집기 로딩이 지연되고 있습니다. 페이지를 새로고침하세요.';},30000);
}
