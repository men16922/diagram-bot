// The app's HTTP client. Every mutation carries the per-process page token (X-Diagram-Bot); a 401 means the team-mode
// session ended. Framework-free: a React app can call createClient(() => token) and use the same two functions.

export function createClient(getToken, {onUnauthorized = () => { location.href = '/login'; }} = {}) {
  async function api(path, options = {}) {
    const response = await fetch(path, {...options, headers: {'Content-Type': 'application/json', 'X-Diagram-Bot': getToken(), ...options.headers}});
    if (response.status === 401) { onUnauthorized(); throw new Error('로그인이 필요합니다.'); }
    const data = await response.json();
    if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : '요청 값을 확인하세요.');
    return data;
  }

  // POST that answers with NDJSON progress events (the chat turn); the last line is {type:'done', ...result} or
  // {type:'error', detail}. onEvent sees every line as it arrives.
  async function streamApi(path, body, onEvent) {
    const response = await fetch(path, {method: 'POST', body: JSON.stringify(body), headers: {'Content-Type': 'application/json', 'X-Diagram-Bot': getToken()}});
    if (response.status === 401) { onUnauthorized(); throw new Error('로그인이 필요합니다.'); }
    if (!response.ok) { const data = await response.json().catch(() => ({})); throw new Error(typeof data.detail === 'string' ? data.detail : '요청 값을 확인하세요.'); }
    const reader = response.body.getReader(), decoder = new TextDecoder();
    let buffer = '', last = null;
    for (;;) {
      const {value, done} = await reader.read();
      buffer += decoder.decode(value || new Uint8Array(), {stream: !done});
      const lines = buffer.split('\n');
      buffer = lines.pop();
      for (const line of lines) if (line.trim()) { last = JSON.parse(line); onEvent(last); }
      if (done) break;
    }
    if (!last || last.type === 'error') throw new Error(last ? last.detail : '응답이 중간에 끊겼습니다.');
    return last;
  }

  return {api, streamApi};
}
