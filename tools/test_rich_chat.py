# -*- coding: utf-8 -*-
"""富文本聊天回归：渲染 / 展示与朗读分离 / 联网档位 / 复制锚点。

纯离线：不请求真实 API、不读灵枢、不碰剪贴板（复制用 mock 校验）。
布局类断言只看结构（<pre>、表格、锚点），不依赖具体字体渲染结果。
"""
import json
import os
import sys
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from launcher import _prepare_qt

_prepare_qt()

from PyQt5.QtCore import QUrl
from PyQt5.QtWidgets import QApplication

from core import chat_render
from core.chat_answer import AnswerWorker
from core.companion_dialogue import display_answer, needs_web_search, plain_answer, speech_answer

APP = QApplication.instance() or QApplication([])

SAMPLE = """## 最小生成树的思路

先记住一句话：**按价格挑路，挑够 n-1 条就收工**。

1. 把边按成本排序
2. 已经连通的两个点跳过
3. 挑够 n-1 条就停

| 算法 | 适合的图 | 复杂度 |
|---|---|---|
| Kruskal | 稀疏图 | O(E log E) |
| Prim | 稠密图 | O(V²) |

```python
def kruskal(n, edges):
    edges = sorted(edges)
    picked = total = 0
    for cost, u, v in edges:
        total += cost
        picked += 1
        if picked == n - 1:
            break
    return total
```

<script>alert(1)</script>
"""


class RendererTests(unittest.TestCase):
    def test_markdown_becomes_rich_qt_html(self):
        codes = []
        html = chat_render.render_markdown(SAMPLE, lambda src: (codes.append(src) or 'guga-copy:m1:%d' % (len(codes) - 1)))
        self.assertIn('最小生成树的思路', html)
        self.assertIn('font-size:16px', html)
        self.assertIn('<b>按价格挑路', html)
        self.assertIn('<ol', html)
        self.assertIn('border-collapse:collapse', html)
        self.assertIn('<pre style=', html)
        self.assertIn('python', html)
        self.assertIn('guga-copy:m1:0', html)
        self.assertEqual(len(codes), 1)
        self.assertIn('def kruskal', codes[0])
        self.assertTrue(any(color in html for color in ('#007020', '#06287e', '#40a070', '#60a0b0')))

    def test_raw_html_is_escaped_and_no_copy_without_callback(self):
        html = chat_render.render_markdown('<script>alert(1)</script>')
        self.assertNotIn('<script>', html)
        self.assertIn('&lt;script&gt;', html)
        self.assertNotIn('guga-copy', chat_render.render_markdown('```js\nx=1\n```'))

    def test_partial_stream_with_unclosed_fence_still_renders(self):
        html = chat_render.render_markdown('思路如下：\n\n```python\ndef kruskal(n):\n    pass')
        self.assertIn('<pre', html)
        self.assertIn('kruskal', html)

    def test_tables_links_and_blockquote(self):
        html = chat_render.render_markdown('> 一句话\n\n[文档](https://example.com)\n\n| a | b |\n|---|---|\n| 1 | 2 |')
        self.assertIn('border-left:3px solid', html)
        self.assertIn('href="https://example.com"', html)
        self.assertIn('border-collapse:collapse', html)


class DisplaySpeechTests(unittest.TestCase):
    def test_display_keeps_markdown_while_speech_strips_it(self):
        raw = '<think>PRIVATE</think>|happy|none|## 建议\n**先喝水**。\n```python\nprint("hi")\n```'
        from core.ai_chat import parse_reply
        shown = parse_reply(display_answer(raw))[2]
        self.assertIn('## 建议', shown)
        self.assertIn('**先喝水**', shown)
        self.assertIn('```python', shown)
        self.assertNotIn('PRIVATE', shown)
        spoken = speech_answer(shown)
        self.assertNotIn('##', spoken)
        self.assertNotIn('**', spoken)
        self.assertNotIn('```', spoken)
        self.assertNotIn('|', spoken)

    def test_plain_answer_stays_the_speech_filter(self):
        self.assertEqual(plain_answer('<think>X</think>## 标题\n**要点**'), '标题\n要点')

    def test_search_modes_are_explicit(self):
        self.assertTrue(needs_web_search('你好', mode='always'))
        self.assertFalse(needs_web_search('今天上海天气', mode='never'))
        self.assertTrue(needs_web_search('今天上海的天气如何？'))
        self.assertFalse(needs_web_search('我喜欢喝茶'))
        self.assertTrue(needs_web_search('这个库最新版本是多少？'))
        self.assertTrue(needs_web_search('iPhone 现在售价多少'))


class MarkdownAnswerTests(unittest.TestCase):
    def pump(self, predicate, seconds=5):
        until = time.monotonic() + seconds
        while not predicate() and time.monotonic() < until:
            APP.processEvents()
            time.sleep(0.005)
        self.assertTrue(predicate())

    def run_worker(self, prompt='列表和元组有什么区别？', receipt=None, search_mode='auto'):
        worker = AnswerWorker(config=dict(api_key='offline', base_url='https://example.com/v1'))
        results, drafts = [], []
        worker.completed.connect(results.append)
        worker.partial.connect(lambda _i, _g, text: drafts.append(text))
        response = MagicMock(status_code=200)
        response.iter_lines.return_value = [
            ('data: ' + json.dumps(dict(choices=[dict(delta=x)]))).encode() for x in
            (dict(reasoning_content='PRIVATE'), dict(content='<think>PRIVATE'),
             dict(content='</think>|happy|none|## 简短解释\n**可以这样理解。**[1]'))] + [b'data: [DONE]']
        with patch('core.chat_memory.recall_context', return_value=dict(state='empty', items=[])), \
                patch('core.search_results.search', return_value=receipt or dict(ok=False, results=[])) as search, \
                patch('core.chat_answer.requests.post', return_value=response) as post:
            worker.start('qa', [], prompt, memory_context='用户喜欢乌龙茶', search_mode=search_mode)
            self.pump(lambda: bool(results))
        return results[0], post.call_args.kwargs['json'], drafts, search

    def test_answer_keeps_markdown_and_payload_has_no_tools(self):
        result, payload, drafts, _search = self.run_worker()
        self.assertTrue(result['ok'])
        self.assertIn('## 简短解释', result['text'])
        self.assertIn('**可以这样理解。**', result['text'])
        self.assertNotIn('PRIVATE', json.dumps(result, ensure_ascii=False) + ''.join(drafts))
        self.assertNotIn('tools', payload)
        self.assertEqual(payload['thinking']['type'], 'disabled')
        self.assertGreaterEqual(payload['max_tokens'], 2600)

    def test_sources_become_clickable_markdown_links(self):
        result, _payload, _drafts, search = self.run_worker(
            receipt=dict(ok=True, results=[dict(title='文档', url='https://example.com/docs', snippet='摘要')]),
            search_mode='always')
        self.assertEqual(result['search_state'], 'ok')
        self.assertIn('[文档](https://example.com/docs)', result['text'])
        self.assertIn('参考来源：', result['text'])
        self.assertTrue(search.called)
        self.assertNotIn('乌龙茶', search.call_args.args[0])
        self.assertNotIn('example.com', speech_answer(result['text']))

    def test_never_mode_skips_search_for_time_sensitive_question(self):
        result, _payload, _drafts, search = self.run_worker('今天上海的天气如何？', search_mode='never')
        self.assertFalse(search.called)
        self.assertEqual(result['search_state'], 'not_needed')


class CopyAnchorTests(unittest.TestCase):
    def test_code_copy_link_puts_exact_code_on_clipboard(self):
        from ui.agent_chat import AgentChatWindow
        panel = type('P', (), {})()
        panel._copy_store = {'m1': dict(codes=['def k(n):\n    return n'], text='完整回答')}
        panel.status = MagicMock()
        clipboard = MagicMock()
        with patch('ui.agent_chat.QApplication.clipboard', return_value=clipboard):
            AgentChatWindow._log_link(panel, QUrl('guga-copy:m1:0'))
            AgentChatWindow._log_link(panel, QUrl('guga-copy-msg:m1'))
        self.assertEqual(clipboard.setText.call_args_list[0].args[0], 'def k(n):\n    return n')
        self.assertEqual(clipboard.setText.call_args_list[1].args[0], '完整回答')
        panel.status.setText.assert_called()


if __name__ == '__main__':
    unittest.main()
