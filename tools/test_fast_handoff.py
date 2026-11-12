"""Fast-path contract: claim only what can be finished, hand the rest to the agent."""
import unittest

from core.fast_commands import match
from core.browser_interaction import parse_step
from core.execution_contract import requires_execution
from core.self_agent import needs_full_budget


class FastHandoffTests(unittest.TestCase):
    def test_compound_sentence_goes_to_agent_instead_of_half_done(self):
        parsed = match('打开浏览器，在输入框中输入 咕咕嘎嘎')
        self.assertFalse(parsed['matched'])
        # The handed-off sentence must still be recognised as work, not chat.
        self.assertTrue(requires_execution('打开浏览器，在输入框中输入 咕咕嘎嘎'))

    def test_second_action_after_comma_is_not_swallowed(self):
        self.assertFalse(match('打开微信，找到小军')['matched'])

    def test_filler_tail_still_uses_fast_path(self):
        parsed = match('打开记事本，好吗')
        self.assertTrue(parsed['matched'])
        self.assertEqual(parsed['action'], 'open_app')
        self.assertEqual(parsed['target'], '记事本')

    def test_browser_field_name_is_normalised_to_generic_input(self):
        step = parse_step('在浏览器的输入框中输入 咕咕嘎嘎')
        self.assertEqual(step['browser_action'], 'fill_named')
        self.assertEqual(step['target'], '输入框')
        self.assertEqual(step['text'], '咕咕嘎嘎')

    def test_explicit_field_name_is_preserved(self):
        step = parse_step('在用户名输入框中输入 咕嘎')
        self.assertEqual(step['target'], '用户名')

    def test_short_multi_step_sentence_gets_full_budget(self):
        self.assertTrue(needs_full_budget('打开浏览器，在输入框中输入 咕咕嘎嘎'))
        self.assertTrue(needs_full_budget('打开微信，找到小军'))
        self.assertTrue(needs_full_budget('打开微信，帮我发消息给小军'))
        self.assertFalse(needs_full_budget('帮我打开浏览器'))

    def test_window_qualified_search_field_is_normalised(self):
        step = parse_step('在浏览器的搜索框中输入 咕嘎')
        self.assertEqual(step['target'], '搜索')


if __name__ == '__main__':
    unittest.main()
