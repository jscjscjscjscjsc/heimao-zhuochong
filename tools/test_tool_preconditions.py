# -*- coding: utf-8 -*-
"""工具前置条件：每个"选错工具"的教训都要在描述里留一句，否则模型还会再犯。"""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core.self_agent import TOOLS  # noqa: E402


def description(name):
    for item in TOOLS:
        fn = item['function']
        if fn['name'] == name:
            return fn['description']
    raise KeyError(name)


class ToolPreconditionTests(unittest.TestCase):
    def test_applications_says_websites_are_not_apps(self):
        text = description('applications')
        self.assertIn('网站', text)
        self.assertIn('browser', text)
        self.assertIn('未找到名为', text)

    def test_computer_discourages_coordinate_clicks(self):
        text = description('computer')
        self.assertIn('不要在能用 uia', text)
        self.assertIn('坐标', text)

    def test_browser_says_start_session_for_login_tasks(self):
        text = description('browser')
        self.assertIn('session_start', text)
        self.assertIn('登录', text)
        self.assertIn('同时操作', text)

    def test_code_task_requires_cwd_and_status(self):
        text = description('code_task')
        self.assertIn('status', text)
        self.assertIn('终态', text)
        self.assertIn('绝对路径', text)

    def test_no_tool_description_is_empty(self):
        for item in TOOLS:
            fn = item['function']
            self.assertTrue(str(fn.get('description', '')).strip(), fn['name'])


if __name__ == '__main__':
    unittest.main()
