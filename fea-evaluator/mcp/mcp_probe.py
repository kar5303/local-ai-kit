# -*- coding: utf-8 -*-
"""
直接連線 PrePoMax Automation Server（MCP，streamable HTTP），不經過 AI 模型。
1. initialize  2. tools/list  3. 呼叫 get_guide
結果存到本資料夾：tools.json、guide.md、probe_log.txt
token：放在同資料夾的 prepomax_token.txt（只有一行），或環境變數 PREPOMAX_TOKEN
只用 Python 標準函式庫。
"""
import json, os, sys, urllib.request, urllib.error

HERE = os.path.dirname(os.path.abspath(__file__))
URL = os.environ.get('PREPOMAX_MCP_URL', 'http://127.0.0.1:8756/mcp')
LOG = []

def log(s):
    print(s); LOG.append(s)

def token():
    t = os.environ.get('PREPOMAX_TOKEN')
    p = os.path.join(HERE, 'prepomax_token.txt')
    if not t and os.path.exists(p):
        t = open(p, encoding='utf-8').read().strip()
    if not t:
        log('[ERROR] 找不到 token：請在 mcp 資料夾建立 prepomax_token.txt，貼上 PrePoMax 的存取權杖')
        sys.exit(1)
    return t

class Client:
    def __init__(self, url, tok):
        self.url, self.tok, self.sid, self.n = url, tok, None, 0

    def _post(self, payload):
        data = json.dumps(payload).encode('utf-8')
        req = urllib.request.Request(self.url, data=data, method='POST')
        req.add_header('Content-Type', 'application/json')
        req.add_header('Accept', 'application/json, text/event-stream')
        req.add_header('Authorization', 'Bearer ' + self.tok)
        if self.sid:
            req.add_header('Mcp-Session-Id', self.sid)
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                sid = r.headers.get('Mcp-Session-Id')
                if sid:
                    self.sid = sid
                body = r.read().decode('utf-8', errors='replace')
                ctype = r.headers.get('Content-Type', '')
        except urllib.error.HTTPError as e:
            log(f'[HTTP {e.code}] {e.read().decode("utf-8", errors="replace")[:500]}')
            raise
        if not body.strip():
            return None
        if 'text/event-stream' in ctype:
            msgs = []
            for line in body.splitlines():
                if line.startswith('data:'):
                    msgs.append(json.loads(line[5:].strip()))
            # 取與請求 id 相同的回應
            for m in msgs:
                if m.get('id') == payload.get('id'):
                    return m
            return msgs[-1] if msgs else None
        return json.loads(body)

    def request(self, method, params=None):
        self.n += 1
        return self._post({'jsonrpc': '2.0', 'id': self.n, 'method': method, 'params': params or {}})

    def notify(self, method, params=None):
        return self._post({'jsonrpc': '2.0', 'method': method, 'params': params or {}})

def main():
    c = Client(URL, token())
    log(f'連線：{URL}')
    try:
        r = c.request('initialize', {
            'protocolVersion': '2025-06-18',
            'capabilities': {},
            'clientInfo': {'name': 'fea-ai-probe', 'version': '0.1'},
        })
    except urllib.error.URLError as e:
        log(f'[ERROR] 連不到 PrePoMax：{e}。請確認 PrePoMax 已開啟，且 Automation Server 已啟動')
        save(); sys.exit(1)
    log('initialize 回應：' + json.dumps(r.get('result', r), ensure_ascii=False)[:800])
    c.notify('notifications/initialized')

    r = c.request('tools/list')
    tools = r.get('result', {}).get('tools', [])
    log(f'工具數量：{len(tools)}')
    for t in tools:
        log(f"  - {t.get('name')}: {(t.get('description') or '').splitlines()[0][:120] if t.get('description') else ''}")
    with open(os.path.join(HERE, 'tools.json'), 'w', encoding='utf-8') as f:
        json.dump(tools, f, ensure_ascii=False, indent=2)

    r = c.request('tools/call', {'name': 'get_guide', 'arguments': {}})
    res = r.get('result', {})
    texts = [x.get('text', '') for x in res.get('content', []) if x.get('type') == 'text']
    with open(os.path.join(HERE, 'guide.md'), 'w', encoding='utf-8') as f:
        f.write('\n\n'.join(texts) if texts else json.dumps(r, ensure_ascii=False, indent=2))
    log(f'get_guide：{"成功" if texts else "沒有文字內容，原始回應已存檔"}，長度 {sum(len(t) for t in texts)} 字元')
    save()

def save():
    with open(os.path.join(HERE, 'probe_log.txt'), 'w', encoding='utf-8') as f:
        f.write('\n'.join(LOG) + '\n')

if __name__ == '__main__':
    main()
