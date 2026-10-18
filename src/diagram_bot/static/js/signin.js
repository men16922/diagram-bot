// Profile menu (top right): AWS sign-in (SSO or local credentials) and collection with the signed-in identity.
import {api, state} from './state.js';
import {$, bind, el, guard, toast} from './ui.js';
import {loadSnapshot, refreshSnapshots} from './document.js';
function closeProfile(){$('profile-menu').hidden=true;$('profile-button').setAttribute('aria-expanded','false');}
$('profile-button').onclick=event=>{event.stopPropagation();const open=$('profile-menu').hidden;$('profile-menu').hidden=!open;$('profile-button').setAttribute('aria-expanded',String(open));};
document.addEventListener('click',event=>{if(!$('profile-menu').hidden&&!event.target.closest('.profile'))closeProfile();});
function identityText(){
  const id=state.identity;
  if(!id)return {title:'AWS에 로그인하지 않음',detail:'SSO 또는 로컬 자격 증명으로 로그인하면 계정을 읽기 전용으로 수집할 수 있습니다. 로그인하지 않으면 기본 SDK 인증 체인을 씁니다.',label:'로그인'};
  if(id.via==='sso')return {title:`${id.role} @ ${id.account}`,detail:`IAM Identity Center(SSO) · ${id.accountName||id.account}`,label:`${id.role} @ ${id.account} (SSO)`};
  const name=id.arn.split('/').pop()||id.arn;
  return {title:`${name} @ ${id.account}`,detail:`${id.profile?`프로파일 ${id.profile}`:'기본 SDK 인증 체인'} · ${id.arn}`,label:`${name} @ ${id.account}`};
}
export function renderProfile(){
  const text=identityText();$('profile-label').textContent=text.label;$('profile-title').textContent=text.title;$('profile-detail').textContent=text.detail;
  $('profile-signout').hidden=!state.identity;$('signin-button').textContent=state.identity?'다른 자격 증명으로 로그인':'AWS 로그인 (SSO · 프로파일)';
  document.querySelector('.profile').classList.toggle('signed-in',!!state.identity);
  if(!state.team)$('user-avatar').textContent=state.identity?text.title.slice(0,2).toUpperCase():'DB';
}
bind('signin-button',async()=>{closeProfile();$('signin-dialog').showModal();
  if(!state.ssoDefaults){state.ssoDefaults=await api('/api/sso/defaults').catch(()=>[]);
    $('sso-start-urls').replaceChildren(...state.ssoDefaults.map(d=>new Option(d.name,d.start_url)));
    if(state.ssoDefaults[0]&&!$('sso-start-url').value){$('sso-start-url').value=state.ssoDefaults[0].start_url;$('sso-region').value=state.ssoDefaults[0].region;}}});
$('close-signin').onclick=()=>$('signin-dialog').close();
bind('sso-use',async()=>{
  const status=$('sso-use-status');status.textContent='';
  if(!$('sso-role').value){status.textContent='SSO 역할을 선택하세요. 이 계정에 할당된 역할이 없다면 Identity Center 관리자에게 요청하세요.';return;}
  const option=$('sso-account').selectedOptions[0],identity={via:'sso',account:$('sso-account').value,role:$('sso-role').value,accountName:option?.textContent};
  // The server binds the role to this sign-in (the chat agent's collections use it); only a confirmed role signs in.
  $('sso-use').disabled=true;
  try{await api(`/api/sso/login/${state.sso.id}/use`,{method:'POST',body:JSON.stringify({account_id:identity.account,role:identity.role})});}
  catch(error){status.textContent=`역할로 로그인하지 못했습니다: ${error.message}`;return;}
  finally{$('sso-use').disabled=false;}
  state.identity=identity;$('aws-profile').value='';renderProfile();$('signin-dialog').close();toast(`SSO로 로그인했습니다 · ${identity.role} @ ${identity.account}`);
});
bind('use-profile',async()=>{
  $('signin-status').textContent='자격 증명을 확인하고 있습니다…';
  try{const profile=$('aws-profile').value;const who=await api(`/api/identity${profile?`?profile=${encodeURIComponent(profile)}`:''}`);
    if(state.sso)await api(`/api/sso/login/${state.sso.id}/logout`,{method:'POST'}).catch(()=>{});ssoStop();ssoView('form');
    state.identity={via:'profile',profile,account:who.account,arn:who.arn};$('signin-status').textContent='';renderProfile();$('signin-dialog').close();
    toast(`로그인했습니다 · ${identityText().title}`);
  }catch(error){$('signin-status').textContent=error.message;}
});
bind('profile-signout',async()=>{
  // Sign out locally at once; the server then discards the SSO token and invalidates it with sso:Logout.
  closeProfile();const flow=state.sso;ssoStop();state.identity=null;$('aws-account').value='';ssoView('form');renderProfile();
  if(flow)await api(`/api/sso/login/${flow.id}/logout`,{method:'POST'}).catch(error=>toast(`SSO 토큰 폐기에 실패했습니다: ${error.message}`,true));
  toast('AWS 자격 증명에서 로그아웃했습니다.');});
// Audit log (team mode, admin): GET /api/audit, newest first.
bind('audit-button',async()=>{closeProfile();
  const entries=await api('/api/audit?limit=200');
  $('audit-rows').replaceChildren(...entries.map(entry=>{
    const row=el('tr'),result=String(entry.result??''),failed=/^(denied|failed|throttled|[45]\d\d)$/.test(result);
    const cell=el('td');cell.append(el('span',result,failed?'badge warn':'badge sample'));
    row.append(el('td',entry.time?new Date(entry.time).toLocaleString('ko-KR'):''),el('td',entry.user??'—'),el('td',entry.action),
      el('td',[entry.target,entry.snapshot&&`snapshot ${entry.snapshot}`,entry.client].filter(Boolean).join(' · ')),cell);
    return row;}));
  $('audit-empty').hidden=entries.length>0;$('audit-dialog').showModal();});
bind('collection-button',()=>{closeProfile();
  const text=identityText(),box=$('collection-identity');box.replaceChildren(el('b',state.identity?text.title:'기본 SDK 인증 체인 (로그인하지 않음)'),el('span',state.identity?text.detail:'이 기기의 환경 변수·기본 프로파일을 씁니다.'));
  const change=el('button','로그인 변경','text-button');change.type='button';change.onclick=()=>{$('collection-dialog').close();$('signin-button').click();};box.append(el('br'),change);
  if(state.identity&&!$('aws-account').value)$('aws-account').value=state.identity.account;
  $('collection-dialog').showModal();});
// IAM Identity Center sign-in with the device code grant: the server keeps every secret, the page shows the code and link.
function ssoView(mode){$('sso-form').hidden=mode!=='form';$('sso-device').hidden=mode!=='device';$('sso-select').hidden=mode!=='select';
  $('aws-profile').disabled=mode==='select';$('aws-account').readOnly=mode==='select';$('sso-use-status').textContent='';}
async function ssoRoles(){
  const account=$('sso-account').value;$('aws-account').value=account;$('sso-role').replaceChildren();
  const {roles}=await api(`/api/sso/login/${state.sso.id}/roles?account_id=${encodeURIComponent(account)}`);
  $('sso-role').append(...roles.map(r=>new Option(r,r)));
  const readOnly=roles.find(r=>/read.?only/i.test(r));if(readOnly)$('sso-role').value=readOnly;
}
// Polls one device-code flow; a newer login, an SSO sign-out or a profile sign-in replaces state.sso and stops it.
function ssoStop(){clearTimeout(state.ssoTimer);state.sso=null;}
async function ssoPoll(){
  const flow=state.sso,current=()=>state.sso?.id===flow?.id;
  if(!flow||flow.status!=='pending')return;
  try{const view=await api(`/api/sso/login/${flow.id}`);if(!current())return;state.sso={...flow,...view};
    if(view.status==='pending'){$('sso-status').textContent='승인을 기다리는 중…';state.ssoTimer=setTimeout(ssoPoll,view.interval*1000);return;}
    if(view.status==='authorized'){
      // Roles load before the selection is shown, so a collection never starts without a role.
      $('sso-status').textContent='계정·역할 목록을 불러오는 중…';
      $('sso-account').replaceChildren(...view.accounts.map(a=>new Option(`${a.name} (${a.id})`,a.id)));
      if(view.accounts.length)await ssoRoles();
      if(!current())return;
      $('sso-signed-in').textContent=view.accounts.length?`SSO 로그인됨 · 계정 ${view.accounts.length}개`:'SSO 로그인됨 · 할당된 계정이 없습니다';
      ssoView('select');return;}
    $('sso-status').textContent={expired:'코드가 만료됐습니다. 다시 로그인하세요.',denied:'승인이 거부됐습니다.',failed:'SSO 로그인에 실패했습니다.'}[view.status]||view.status;
    setTimeout(()=>{if(current())ssoView('form');},2500);
  }catch(error){if(!current())return;$('sso-status').textContent=error.message;setTimeout(()=>{if(current())ssoView('form');},2500);}
}
bind('sso-login',async()=>{
  ssoStop();
  const flow=await api('/api/sso/login',{method:'POST',body:JSON.stringify({start_url:$('sso-start-url').value.trim(),region:$('sso-region').value.trim()})});
  state.sso=flow;$('sso-code').textContent=flow.user_code;$('sso-link').href=flow.verification_uri_complete;$('sso-status').textContent='승인을 기다리는 중…';
  ssoView('device');state.ssoTimer=setTimeout(ssoPoll,flow.interval*1000);
});
$('sso-account').onchange=guard(ssoRoles);
bind('sso-logout',async()=>{if(state.sso)await api(`/api/sso/login/${state.sso.id}/logout`,{method:'POST'});ssoStop();$('aws-account').value='';ssoView('form');
  if(state.identity?.via==='sso'){state.identity=null;renderProfile();}});
bind('close-collection',()=>{$('collection-dialog').close();});
let collectionRun=0;
$('collection-form').onsubmit=async event=>{
  event.preventDefault();const run=++collectionRun;$('start-collection').disabled=true;$('collection-progress').textContent='AWS 인증과 계정을 확인하고 있습니다…';
  try{
    const sso=state.identity?.via==='sso'&&state.sso?.status==='authorized'?{sso_login:state.sso.id,sso_role:state.identity.role}:{};
    if(sso.sso_login&&!sso.sso_role)throw new Error('SSO 역할을 선택하세요. 이 계정에 할당된 역할이 없다면 Identity Center 관리자에게 요청하세요.');
    let job=await api('/api/collections',{method:'POST',body:JSON.stringify({profile:sso.sso_login?null:(state.identity?.via==='profile'?state.identity.profile||null:null),region:$('aws-region').value,expected_account:$('aws-account').value,role_arn:$('aws-role').value||null,external_id:$('aws-external-id').value||null,...sso})});
    // A newer collection supersedes this poll; its job keeps running on the server and lands in 수집 이력.
    while(['queued','running'].includes(job.status)){$('collection-progress').textContent='AWS 설정을 수집하고 있습니다. 잠시 기다려 주세요.';await new Promise(resolve=>setTimeout(resolve,1500));if(run!==collectionRun)return;job=await api(`/api/collections/${job.id}`);}
    if(job.status==='failed')throw new Error(job.error);
    await refreshSnapshots(job.snapshot_id);await loadSnapshot(job.snapshot_id);$('collection-dialog').close();toast(job.status==='partial'?'부분 수집을 완료했습니다. 조회 상태를 확인하세요.':'AWS 수집을 완료했습니다.');
  }catch(error){if(run===collectionRun)$('collection-progress').textContent=error.message;}finally{if(run===collectionRun)$('start-collection').disabled=false;}
};
