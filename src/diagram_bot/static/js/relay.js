// Browser egress (serve --aws-egress browser): the server builds and signs each AWS request it makes for this user;
// this page sends it from the PC's address and returns the response (server: egress.py, /api/relay/*).
// The server never hands out keys, only per-request signatures. Framework-free: start it once after sign-in.

const AWS_API = /^https:\/\/[a-z0-9.-]+\.amazonaws\.com(\.cn)?\//;

function base64(bytes) {
  let text = '';
  for (let i = 0; i < bytes.length; i += 0x8000) text += String.fromCharCode(...bytes.subarray(i, i + 0x8000));
  return btoa(text);
}

// One signed request -> {status, headers, body (base64)}; status 0 with an error when the fetch itself fails (network, CORS).
export async function send(request) {
  if (!AWS_API.test(request.url)) return {status: 0, error: 'AWS API 주소가 아닙니다.'};
  try {
    const response = await fetch(request.url, {method: request.method, headers: request.headers,
      body: request.body ? Uint8Array.from(atob(request.body), c => c.charCodeAt(0)) : undefined,
      mode: 'cors', credentials: 'omit', cache: 'no-store', referrerPolicy: 'no-referrer'});
    const headers = {};
    response.headers.forEach((value, name) => { headers[name] = value; });
    return {status: response.status, headers, body: base64(new Uint8Array(await response.arrayBuffer()))};
  } catch (error) {
    return {status: 0, error: String(error)};
  }
}

// This tab's id: the server hands a request to one tab and, if that tab goes away, gives a read to another open tab.
const TAB = Array.from(crypto.getRandomValues(new Uint8Array(12)), b => b.toString(16).padStart(2, '0')).join('');

// Long-poll /api/relay/next and answer every request for as long as the page is open. Requests are answered in the
// background and the next poll starts at once: an open poll is this tab's heartbeat, so a slow call (Bedrock) never
// makes the tab look closed.
export async function startRelay(api, {onRequests = () => {}} = {}) {
  for (;;) {
    try {
      const {requests} = await api(`/api/relay/next?tab=${TAB}`);
      onRequests(requests.length);
      for (const r of requests) send(r).then(result => api(`/api/relay/${r.id}`, {method: 'POST', body: JSON.stringify(result)})).catch(() => {});
    } catch (error) {
      await new Promise(resolve => setTimeout(resolve, 3000));
    }
  }
}
