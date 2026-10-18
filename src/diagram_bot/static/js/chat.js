// Design chat: a Strands agent on Bedrock calls drawing and read-only collection tools; each change is a snapshot.
// Model replies use a little Markdown; render headings, lists, **bold** and `code` as DOM nodes (never innerHTML).
import {api, state, streamApi} from './state.js';
import {$, bind, el, toast} from './ui.js';
import {flushEdits, loadSnapshot, refreshSnapshots} from './document.js';
function inline(text) {
  const parts=[];for(const piece of text.split(/(\*\*[^*]+\*\*|`[^`]+`)/)){if(!piece)continue;
    if(piece.startsWith('**')&&piece.endsWith('**'))parts.push(el('strong',piece.slice(2,-2)));
    else if(piece.startsWith('`')&&piece.endsWith('`'))parts.push(el('code',piece.slice(1,-1)));
    else parts.push(document.createTextNode(piece));}
  return parts;
}
function markdown(text) {
  const box=el('div',undefined,'chat-text rich');let list=null;
  for(const raw of text.split('\n')){const line=raw.trimEnd();
    const heading=line.match(/^#{1,6}\s+(.*)$/),item=line.match(/^\s*(?:[-*]|\d+\.)\s+(.*)$/);
    if(item){if(!list){list=el('ul');box.append(list);}const li=el('li');li.append(...inline(item[1]));list.append(li);continue;}
    list=null;
    if(heading){const h=el('div',undefined,'chat-heading');h.append(...inline(heading[1]));box.append(h);}
    else if(/^\s*(?:---|>\s*)/.test(line)&&line.replace(/[->\s]/g,'')===''){continue;}
    else if(line.trim()){const p=el('p');p.append(...inline(line.replace(/^>\s*/,'')));box.append(p);}}
  return box;
}
function chatMessage(role, text, meta) {
  const item=el('div',undefined,`chat-message ${role}`);item.append(role==='assistant'?markdown(text):el('div',text,'chat-text'));
  if(meta)item.append(el('small',meta,'chat-meta'));$('chat-log').append(item);$('chat-log').scrollTop=$('chat-log').scrollHeight;return item;
}
function setChat(open){$('chat-panel').hidden=!open;document.body.classList.toggle('chat-open',open);$('chat-button').setAttribute('aria-expanded',String(open));}
async function openChat() {
  if(!$('chat-panel').hidden){setChat(false);return;}
  setChat(true);$('chat-input').focus();
  if(state.chatReady!==undefined)return;
  const info=await api('/api/agent');state.chatReady=info.ready;
  $('chat-model').replaceChildren(...info.models.map(m=>new Option(m.label+(m.key===info.default?' (기본)':''),m.key)));$('chat-model').value=info.default;
  if(!info.ready)chatMessage('system',"채팅 에이전트가 설치되지 않았습니다. pip install -e '.[agent]' 후 다시 시작하세요.");
}
$('chat-form').onsubmit=async event=>{
  event.preventDefault();const message=$('chat-input').value.trim();if(!message||state.chatBusy)return;
  state.chatBusy=true;$('chat-send').disabled=true;
  let loading=null;
  try{
    await flushEdits();
    $('chat-input').value='';chatMessage('user',message);
    const waiting=chatMessage('system','설계하는 중…'),status=waiting.querySelector('.chat-text');
    let reply=null,text='',shown=null,pending=null,result;
    // Each design step the agent saves opens on the canvas as it arrives; only the newest waits while one loads.
    // A step that fails to open is reported and the loop moves on, so later steps still open.
    const show=id=>{pending=id;if(!loading)loading=(async()=>{while(pending&&pending!==shown){const next=pending;shown=next;
      try{await refreshSnapshots(next);await loadSnapshot(next);}catch(error){toast(error.message,true);}}loading=null;})();};
    try{
      result=await streamApi('/api/agent/chat/stream',{message,model:$('chat-model').value,snapshot:state.snapshotId},event=>{
        if(event.type==='tool')status.textContent=event.status==='start'?`도구 실행 중: ${event.name}`:`도구 ${event.status==='error'?'오류':'완료'}: ${event.name}`;
        else if(event.type==='model'&&event.fallback)status.textContent=`${event.model_label}(대체 모델)로 응답하는 중…`;
        else if(event.type==='snapshot')show(event.id);
        else if(event.type==='text'){text+=event.text;if(!reply){reply=chatMessage('assistant','');}
          reply.querySelector('.chat-text').replaceWith(Object.assign(el('div',undefined,'chat-text'),{textContent:text}));$('chat-log').scrollTop=$('chat-log').scrollHeight;}
      });
    }catch(error){waiting.remove();reply?.remove();if(text)chatMessage('assistant',text);chatMessage('system',error.message);return;}
    waiting.remove();reply?.remove();
    const meta=`${result.model_label}${result.fallback?' · 대체 모델(기본 모델 사용 불가)':''} · 도구 ${result.tools_used.length}회`+(result.snapshots.length?` · 구성도 ${result.snapshots.length}회 갱신`:'');
    chatMessage('assistant',result.reply||'(응답 없음)',meta);
    const last=result.snapshots.at(-1);if(last)show(last);
  }catch(error){toast(error.message,true);}
  finally{if(loading)await loading;state.chatBusy=false;$('chat-send').disabled=false;$('chat-input').focus();}
};
$('chat-input').onkeydown=event=>{if(event.key==='Enter'&&!event.shiftKey&&!event.isComposing){event.preventDefault();$('chat-form').requestSubmit();}};
bind('chat-button',openChat);bind('chat-close',()=>setChat(false));
bind('chat-reset',async()=>{await api('/api/agent/reset',{method:'POST'});$('chat-log').replaceChildren();chatMessage('system','새 대화를 시작합니다.');});
for(const button of document.querySelectorAll('.chat-suggestion'))button.onclick=()=>{$('chat-input').value=button.textContent;$('chat-input').focus();};
