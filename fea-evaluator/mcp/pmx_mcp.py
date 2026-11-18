# -*- coding: utf-8 -*-
"""PrePoMax Automation Server（MCP streamable HTTP）的 Python 用戶端。只用標準函式庫。"""
import json, os, time, urllib.request, urllib.error

HERE = os.path.dirname(os.path.abspath(__file__))

def load_token():
    t = os.environ.get('PREPOMAX_TOKEN')
    p = os.path.join(HERE, 'prepomax_token.txt')
    if not t and os.path.exists(p):
        t = open(p, encoding='utf-8').read().strip()
    if not t:
        raise SystemExit('[ERROR] 找不到 token：mcp\\prepomax_token.txt')
    return t

class PrePoMax:
    def __init__(self, url=None, token=None, http_timeout=660):
        self.url = url or os.environ.get('PREPOMAX_MCP_URL', 'http://127.0.0.1:8756/mcp')
        self.tok = token or load_token()
        self.sid = None
        self.n = 0
        self.http_timeout = http_timeout
        self._init()

    def _post(self, payload):
        req = urllib.request.Request(self.url, data=json.dumps(payload).encode('utf-8'), method='POST')
        req.add_header('Content-Type', 'application/json')
        req.add_header('Accept', 'application/json, text/event-stream')
        req.add_header('Authorization', 'Bearer ' + self.tok)
        if self.sid:
            req.add_header('Mcp-Session-Id', self.sid)
        with urllib.request.urlopen(req, timeout=self.http_timeout) as r:
            if r.headers.get('Mcp-Session-Id'):
                self.sid = r.headers['Mcp-Session-Id']
            body = r.read().decode('utf-8', errors='replace')
            ctype = r.headers.get('Content-Type', '')
        if not body.strip():
            return None
        if 'text/event-stream' in ctype:
            msgs = [json.loads(l[5:].strip()) for l in body.splitlines() if l.startswith('data:')]
            for m in msgs:
                if m.get('id') == payload.get('id'):
                    return m
            return msgs[-1] if msgs else None
        return json.loads(body)

    def _rpc(self, method, params=None):
        self.n += 1
        r = self._post({'jsonrpc': '2.0', 'id': self.n, 'method': method, 'params': params or {}})
        if r and 'error' in r:
            raise RuntimeError(f"MCP error: {r['error']}")
        return (r or {}).get('result', {})

    def _init(self):
        self._rpc('initialize', {'protocolVersion': '2025-06-18', 'capabilities': {},
                                 'clientInfo': {'name': 'fea-ai', 'version': '0.1'}})
        self._post({'jsonrpc': '2.0', 'method': 'notifications/initialized', 'params': {}})

    def call(self, tool, **args):
        """呼叫工具，回傳 (文字內容, 原始 result)。若回應為 running，會自動以 get_call 等待。"""
        res = self._rpc('tools/call', {'name': tool, 'arguments': args})
        text = '\n'.join(c.get('text', '') for c in res.get('content', []) if c.get('type') == 'text')
        data = None
        try:
            data = json.loads(text)
        except Exception:
            pass
        while isinstance(data, dict) and data.get('status') == 'running' and data.get('callId') is not None:
            time.sleep(2)
            text, res = self.call('get_call', call_id=data['callId'], timeout=60)
            try:
                data = json.loads(text)
            except Exception:
                data = None
        return text, res
