"""模型请求的稳定性：一次网络抖动不该把整个任务判死。"""
import unittest
from unittest.mock import patch

import requests

from core.self_agent import SelfAgent


class _Response:
    status_code = 200

    def json(self):
        return {'choices': [{'message': {'role': 'assistant', 'content': 'ok'}}]}

    def close(self):
        pass


class _AgentShim:
    """只带 _request 需要的字段，避开 QObject 初始化。"""

    config = dict(base_url='https://api.deepseek.com', api_key='test', model='test-model')
    _routine_request = False


class ModelRetryTests(unittest.TestCase):
    def _request(self, session_id):
        return SelfAgent._request(_AgentShim(), [{'role': 'user', 'content': 'x'}],
                                  session_id, tools=False)

    def test_read_timeout_is_retried_once(self):
        calls = []

        def post(*args, **kwargs):
            calls.append(1)
            if len(calls) == 1:
                raise requests.Timeout('read timeout')
            return _Response()

        with patch('core.self_agent.requests.post', side_effect=post), \
                patch('core.self_agent.time.sleep'):
            message = self._request('session-1')

        self.assertEqual(message['content'], 'ok')
        self.assertEqual(len(calls), 2)

    def test_two_timeouts_still_fail(self):
        calls = []

        def post(*args, **kwargs):
            calls.append(1)
            raise requests.Timeout('read timeout')

        with patch('core.self_agent.requests.post', side_effect=post), \
                patch('core.self_agent.time.sleep'):
            with self.assertRaises(requests.Timeout):
                self._request('session-2')

        self.assertEqual(len(calls), 2)

    def test_http_error_is_not_retried(self):
        calls = []

        class BadResponse(_Response):
            status_code = 500

        def post(*args, **kwargs):
            calls.append(1)
            return BadResponse()

        with patch('core.self_agent.requests.post', side_effect=post), \
                patch('core.self_agent.time.sleep'):
            with self.assertRaises(requests.HTTPError):
                self._request('session-3')

        self.assertEqual(len(calls), 1)


if __name__ == '__main__':
    unittest.main()
