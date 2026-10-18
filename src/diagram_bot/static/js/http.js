// The app's HTTP client. Every mutation carries the per-process page token (X-Diagram-Bot); a 401 means the team-mode
// session ended. Framework-free: a React app can call createClient(() => token) and use the same functions.

// A failed response's message: FastAPI's string detail, a 422 validation list as "field: message" lines, or the status
// when the body is not JSON (a plain-text 500, a proxy's HTML page).
export async function errorMessage(response) {
  const text = await response.text().catch(() => '');
  let detail;
  try { detail = JSON.parse(text).detail; } catch { return `요청에 실패했습니다 (HTTP ${response.status}).`; }
  if (typeof detail === 'string') return detail;
  if (Array.isArray(detail) && detail.length)
    return detail.map(d => [d.loc?.filter(part => !['body', 'query', 'path'].includes(part)).join('.'), d.msg].filter(Boolean).join(': ')).join(' · ');
  return '요청 값을 확인하세요.';
}

export function createClient(getToken, {onUnauthorized = () => { location.href = '/login'; }} = {}) {
  // The checked Response, for callers that read the body themselves (file downloads).
  async function request(path, options = {}) {
    const response = await fetch(path, {...options, headers: {'Content-Type': 'application/json', 'X-Diagram-Bot': getToken(), ...options.headers}});
    if (response.status === 401) { onUnauthorized(); throw new Error('로그인이 필요합니다.'); }
    if (!response.ok) throw new Error(await errorMessage(response));
    return response;
  }

  async function api(path, options = {}) {
    const response = await request(path, options);
    try { return await response.json(); } catch { throw new Error('서버 응답을 읽지 못했습니다. 다시 시도하세요.'); }
  }

  // POST that answers with NDJSON progress events (the chat turn); the last line is {type:'done', ...result} or
  // {type:'error', detail}. onEvent sees every line as it arrives.
  async function streamApi(path, body, onEvent) {
    const response = await request(path, {method: 'POST', body: JSON.stringify(body)});
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

  return {api, request, streamApi};
}
