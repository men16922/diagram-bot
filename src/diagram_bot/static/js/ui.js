// DOM helpers shared by every module: lookups, element creation, notices, click guards, downloads and the button states.
import {state} from './state.js';
export const $ = id => document.getElementById(id);
let toastTimer;
export function el(tag, text, className) { const element=document.createElement(tag); if(text!==undefined)element.textContent=text; if(className)element.className=className; return element; }
// Notices describe the current diagram; a new snapshot or view drops them (errors stay until they time out).
export function clearNotice() { if($('toast').className!=='error'){$('toast').hidden=true;clearTimeout(toastTimer);} }
// Runs a click handler and shows its failure; every button handler goes through it.
export const guard = handler => () => Promise.resolve().then(handler).catch(error=>toast(error.message,true));
export function toast(message, error=false) { $('toast').textContent=message; $('toast').className=error?'error':''; $('toast').hidden=false; clearTimeout(toastTimer); toastTimer=setTimeout(()=>$('toast').hidden=true,error?8500:4000); }
export function setControls() {
  for(const id of ['save-button','export-button','note-button','reset-button'])$(id).disabled=!state.ready||state.loading||state.busy;
  $('restore-button').disabled=!state.ready||state.loading||state.busy||!state.history.length;
  for(const id of ['snapshot-select','references','route-filter','import-button'])$(id).disabled=state.busy||(state.ready&&state.loading);
  // Team mode roles: viewers read only, editors edit and import, admins also collect from AWS.
  if(state.role==='viewer')for(const id of ['save-button','note-button','reset-button','restore-button','import-button','combine-button'])$(id).disabled=true;
  $('collection-button').disabled=state.role!=='admin';$('signin-button').disabled=state.role!=='admin';$('chat-button').disabled=state.role==='viewer';
  const locked=state.busy||(state.ready&&state.loading);
  $('view-overview').disabled=locked;$('view-detail').disabled=locked||!state.vpc;$('view-focus').disabled=locked||!state.selected;
  $('save-state').textContent=state.loading?'불러오는 중':state.busy?'처리 중':state.dirty?'저장하지 않은 편집':state.revision?`저장됨 · v${state.revision}`:'자동 생성 구성도';
}
export function download(name, content, type) {
  const url=URL.createObjectURL(new Blob([content],{type}));const link=el('a');link.href=url;link.download=name;link.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
}
export function bind(id, handler) {$(id).onclick=guard(handler);}
