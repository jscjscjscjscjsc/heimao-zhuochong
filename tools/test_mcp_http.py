# -*- coding: utf-8 -*-
"""StreamableHTTPMCP 的真实传输测试：起一个本地桩 MCP 端点，不依赖任何第三方插件。

覆盖：initialize 协商、会话号回带、tools/list、tools/call、SSE 分支、
HTTP 401、服务端 error、MCPHost 的传输选择。
"""
import json
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core.mcp_host import MCPHost, StreamableHTTPMCP  # noqa: E402

PROTOCOL = '2025-11-25'


class _Stub(BaseHTTPRequestHandler):
    mode = 'json'          # json | sse | unauthorized | server_error
    seen = []
    session_id = 'stub-session-1'

    def log_message(self, *args):  # 静音
        pass

    def _reply_json(self, payload, status=200):
        body = json.dumps(payload, ensure_ascii=False).encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Mcp-Session-Id', self.session_id)
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        length = int(self.headers.get('Content-Length') or 0)
        request = json.loads(self.rfile.read(length) or b'{}')
        _Stub.seen.append(dict(method=request.get('method'), headers=dict(self.headers)))
        if _Stub.mode == 'unauthorized':
            self.send_response(401)
            self.end_headers()
            return
        if _Stub.mode == 'server_error':
            self.send_response(500)
            self.end_headers()
            return
        method = request.get('method')
        if 'id' not in request:                      # 通知
            self.send_response(202)
            self.end_headers()
            return
        ident = request['id']
        if method == 'initialize':
            payload = dict(jsonrpc='2.0', id=ident, result=dict(
                protocolVersion=PROTOCOL, capabilities=dict(tools={}),
                serverInfo=dict(name='stub', version='1')))
        elif method == 'tools/list':
            payload = dict(jsonrpc='2.0', id=ident, result=dict(tools=[
                dict(name='dsh_get_status', description='状态', inputSchema=dict(type='object', properties={})),
                dict(name='dsh_inject_alert', description='插话', inputSchema=dict(type='object', properties={})),
            ]))
        elif method == 'tools/call':
            name = (request.get('params') or {}).get('name')
            if name == 'dsh_get_status':
                payload = dict(jsonrpc='2.0', id=ident, result=dict(
                    content=[dict(type='text', text='running')], isError=False))
            else:
                payload = dict(jsonrpc='2.0', id=ident, error=dict(code=-32602, message='工具不存在'))
        else:
            payload = dict(jsonrpc='2.0', id=ident, error=dict(code=-32601, message='Method not found'))

        if _Stub.mode == 'sse':
            body = ('event: message\ndata: ' + json.dumps(payload, ensure_ascii=False) + '\n\n').encode('utf-8')
            self.send_response(200)
            self.send_header('Content-Type', 'text/event-stream')
            self.send_header('Mcp-Session-Id', self.session_id)
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        self._reply_json(payload)


class _FakeSSEResponse:
    """按字节喂 SSE：模拟服务端把中文 UTF-8 原样吐出来。

    requests 的 iter_lines(decode_unicode=True) 会先按 latin-1 解码再用 str.splitlines()
    切行，中文 UTF-8 字节里 0x85 之类的字符会被当成换行，把一行 JSON 切成两半。
    这里用真实字节复现，防止回归。
    """

    def __init__(self, raw, status_code=200, content_type='text/event-stream'):
        self._raw = raw
        self.status_code = status_code
        self.headers = {'Content-Type': content_type}
        self.raw = _ByteStream(raw)
        self.content = raw

    def close(self):
        pass


class _ByteStream:
    def __init__(self, raw):
        self._raw = raw
        self._at = 0

    def read(self, size=-1, decode_content=True):
        if self._at >= len(self._raw):
            return b''
        chunk = self._raw[self._at:self._at + size] if size and size > 0 else self._raw[self._at:]
        self._at += len(chunk)
        return chunk


class SSEByteLevelTests(unittest.TestCase):
    def test_chinese_utf8_is_not_split_by_latin1_splitlines(self):
        # U+0085(NEL) 与 U+000B 都在 str.splitlines() 的切分集合里
        payload = json.dumps(dict(result=dict(tools=[
            dict(name='dsh_get_status', description='读取 DSH 当前实时状态\u0085含 turn\u000b等'),
        ]), id=7), ensure_ascii=False)
        raw = ('event: message\ndata: ' + payload + '\n\n').encode('utf-8')
        session = StreamableHTTPMCP(dict(url='http://127.0.0.1:1/mcp'))
        message = session._read_sse(_FakeSSEResponse(raw), 7, __import__('time').monotonic() + 5)
        self.assertEqual(message['id'], 7)
        tools = message['result']['tools']
        self.assertEqual(tools[0]['name'], 'dsh_get_status')
        self.assertIn('读取 DSH', tools[0]['description'])

    def test_other_ids_are_skipped(self):
        raw = (b'event: message\ndata: {"jsonrpc":"2.0","id":1,"result":{"x":1}}\n\n'
               b'event: message\ndata: {"jsonrpc":"2.0","id":2,"result":{"x":2}}\n\n')
        session = StreamableHTTPMCP(dict(url='http://127.0.0.1:1/mcp'))
        message = session._read_sse(_FakeSSEResponse(raw), 2, __import__('time').monotonic() + 5)
        self.assertEqual(message['result']['x'], 2)


class StreamableHTTPTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(('127.0.0.1', 0), _Stub)
        cls.port = cls.server.server_address[1]
        cls.url = 'http://127.0.0.1:%d/mcp' % cls.port
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def setUp(self):
        _Stub.mode = 'json'
        _Stub.seen = []

    def test_initialize_negotiates_protocol_and_keeps_session(self):
        client = StreamableHTTPMCP(dict(url=self.url))
        result = client.initialize()
        self.assertEqual(result['protocolVersion'], PROTOCOL)
        self.assertEqual(client.session_id, _Stub.session_id)
        # 第二次请求必须回带会话号
        client.request('tools/list', {})
        last = [row for row in _Stub.seen if row['method'] == 'tools/list'][-1]
        header = {k.lower(): v for k, v in last['headers'].items()}
        self.assertEqual(header.get('mcp-session-id'), _Stub.session_id)

    def test_tools_list_and_call(self):
        client = StreamableHTTPMCP(dict(url=self.url))
        client.initialize()
        tools = client.request('tools/list', {})['tools']
        self.assertEqual([tool['name'] for tool in tools], ['dsh_get_status', 'dsh_inject_alert'])
        result = client.request('tools/call', dict(name='dsh_get_status', arguments={}))
        self.assertEqual(result['content'][0]['text'], 'running')

    def test_sse_response_is_parsed(self):
        _Stub.mode = 'sse'
        client = StreamableHTTPMCP(dict(url=self.url))
        self.assertEqual(client.initialize()['protocolVersion'], PROTOCOL)
        self.assertEqual(len(client.request('tools/list', {})['tools']), 2)

    def test_unauthorized_is_reported_honestly(self):
        _Stub.mode = 'unauthorized'
        client = StreamableHTTPMCP(dict(url=self.url))
        with self.assertRaisesRegex(RuntimeError, '拒绝访问'):
            client.initialize()

    def test_server_error_is_reported(self):
        _Stub.mode = 'server_error'
        client = StreamableHTTPMCP(dict(url=self.url))
        with self.assertRaisesRegex(RuntimeError, 'HTTP 500'):
            client.initialize()

    def test_jsonrpc_error_is_reported_with_message(self):
        client = StreamableHTTPMCP(dict(url=self.url))
        client.initialize()
        with self.assertRaisesRegex(RuntimeError, '工具不存在'):
            client.request('tools/call', dict(name='nope', arguments={}))

    def test_bad_url_is_rejected(self):
        with self.assertRaisesRegex(ValueError, 'http'):
            StreamableHTTPMCP(dict(url='127.0.0.1:51630'))

    def test_mcp_host_picks_http_transport_from_config(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'mcp_servers.json'
            path.write_text(json.dumps({'mcpServers': {'dsh-bridge': {
                'url': self.url,
                'enabled': True,
                'allowed_tools': ['dsh_get_status'],
                'routine_tools': ['dsh_get_status'],
            }}}, ensure_ascii=False), encoding='utf-8')
            host = MCPHost(path)
            listed = host.execute(dict(action='list_servers'))
            self.assertEqual(listed['servers'][0]['transport'], 'http')
            tools = host.execute(dict(action='list_tools', server='dsh-bridge'))
            self.assertEqual(len(tools['tools']), 2)
            result = host.execute(dict(action='call', server='dsh-bridge',
                                       tool='dsh_get_status', arguments={}))
            self.assertEqual(result['content'][0]['text'], 'running')
            # 未授权的工具必须被本地白名单拦住
            with self.assertRaisesRegex(ValueError, 'allowed_tools'):
                host.execute(dict(action='call', server='dsh-bridge',
                                  tool='dsh_inject_alert', arguments={}))
            # 未启用的软件不许访问
            with self.assertRaisesRegex(ValueError, '未配置或尚未授权'):
                host.execute(dict(action='list_tools', server='unknown'))


if __name__ == '__main__':
    unittest.main()
