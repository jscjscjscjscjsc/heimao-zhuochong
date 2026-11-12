"""可执行工作流的录制/回放单测：全部用假桌面，不碰真窗口、不发真输入。"""
import json
import os
from pathlib import Path
import random
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core import action_workflow as aw


def element(name, id='', type='Button', class_name='', enabled=True, offscreen=False,
            value=None, top=0, left=0):
    item = {'ref': 'ref-' + name + '-' + str(top), 'name': name, 'id': id, 'type': type,
            'class_name': class_name, 'enabled': enabled, 'offscreen': offscreen,
            'rect': {'left': left, 'top': top, 'right': left + 100, 'bottom': top + 30}}
    if value is not None:
        item['value'] = value
    return item


class FakeClock:
    """每次读取前进 10ms，让耗时统计在不睡觉的情况下也有非零值。"""

    def __init__(self, step=0.01):
        self.now = 0.0
        self.step = step

    def __call__(self):
        self.now += self.step
        return self.now


class FakeDesktop:
    """与 RealDesktop 同接口的假适配器；只记录动作，不碰真系统。"""

    def __init__(self, elements=None, title='演示窗口 - 记事本'):
        self.windows = [dict(hwnd=100, title=title, process_name='notepad.exe',
                             window_class='Notepad', minimized=False)]
        self.elements = list(elements or [])
        self.actions = []
        self.on_act = None
        self._foreground = 100

    # -- 只读 --------------------------------------------------------- #
    def list_windows(self, query=''):
        return list(self.windows)

    def resolve_window(self, app_hint):
        hint = dict(app_hint or {})
        query = str(hint.get('query') or '')
        if query and query not in self.windows[0]['title']:
            raise aw.WorkflowError('没有找到目标窗口')
        return dict(self.windows[0])

    def foreground(self):
        return self._foreground

    def window_title(self, hwnd):
        return self.windows[0]['title']

    def app_hint_from_window(self, hwnd):
        return {'query': '演示窗口'}

    def snapshot(self, hwnd):
        return {'hwnd': int(hwnd), 'revision': 'rev', 'truncated': False,
                'elements': [dict(item) for item in self.elements]}

    def locate(self, hwnd, selector):
        found, tier = aw.resolve_selector(self.elements, selector)
        result = dict(found)
        result['_tier'] = list(tier)
        return result

    # -- 动作 --------------------------------------------------------- #
    def focus(self, hwnd):
        self.actions.append(('focus', int(hwnd)))
        self._foreground = int(hwnd)
        return {'focused': True}

    def act(self, hwnd, operation, selector, value=''):
        target = self.locate(hwnd, selector)  # 歧义/不存在 -> 动作前就抛
        self.actions.append(('act', operation, target.get('name'), value))
        if callable(self.on_act):
            return self.on_act(operation, target, value, self) or {'method': 'FakePattern'}
        return {'method': 'FakePattern'}

    def type_text(self, hwnd, selector, value):
        target = self.locate(hwnd, selector)
        if str(target.get('type')) not in ('Edit', 'ComboBox'):
            raise aw.WorkflowError('type 只允许写进输入框（Edit/ComboBox）')
        self.actions.append(('type', target.get('name'), value))
        for item in self.elements:
            if item.get('ref') == target.get('ref'):
                item['value'] = value
        return {'method': 'ValuePattern'}

    def scroll(self, hwnd, selector, times=1):
        target = self.locate(hwnd, selector)
        self.actions.append(('scroll', target.get('name'), int(times)))
        return {'method': 'ScrollPattern'}

    def key(self, keys):
        self.actions.append(('key', keys))
        return {'method': 'hotkey'}

    def launch(self, query):
        self.actions.append(('launch', query))
        return {'reused_existing': True}


def base_elements():
    return [
        element('演示窗口 - 记事本', type='Window', class_name='Notepad', top=0),
        element('搜索', id='view_1', type='Edit', class_name='Edit', value='', top=40),
        element('新标签页', type='Button', class_name='NewTabButton', top=80),
        element('新建', id='view_2', type='Button', top=120),
        element('文件列表', id='list', type='List', top=160),
    ]


class WorkflowValidationTests(unittest.TestCase):
    def step(self, **kwargs):
        raw = {'op': 'verify', 'selector': {'name': '搜索', 'control_type': 'Edit'},
               'expect': {'kind': 'exists'}}
        raw.update(kwargs)
        return raw

    def test_valid_workflow_normalized(self):
        workflow = aw.validate_workflow({
            'name': '演示流程', 'trigger_phrases': ['演示一下'],
            'app_hint': {'query': '记事本'}, 'steps': [self.step()],
            'verify_recipe': {'kind': 'window_title_contains', 'value': '演示'},
        })
        self.assertEqual(workflow['schema'], 1)
        self.assertEqual(workflow['slug'], aw._slugify('演示流程'))
        self.assertEqual(workflow['success_count'], 0)
        self.assertIsNone(workflow['last_ok_at'])
        self.assertEqual(workflow['steps'][0]['id'], 1)
        self.assertEqual(workflow['steps'][0]['timeout_ms'], 5000)

    def test_missing_expect_rejected(self):
        raw = self.step()
        raw.pop('expect')
        with self.assertRaises(aw.WorkflowError):
            aw.validate_workflow({'name': 'x', 'steps': [raw]})

    def test_unknown_op_and_selector_field_rejected(self):
        with self.assertRaises(aw.WorkflowError):
            aw.validate_workflow({'name': 'x', 'steps': [{'op': 'send', 'expect': 'exists'}]})
        with self.assertRaises(aw.WorkflowError):
            aw.validate_workflow({'name': 'x', 'steps': [self.step(
                selector={'name': '搜索', 'xpath': '//'})]})

    def test_dangerous_controls_rejected(self):
        for name, class_name in (('发送', ''), ('删除文件', ''), ('确认支付', ''),
                                 ('卸载', ''), ('关闭', 'WindowsCaptionButton')):
            raw = self.step(selector={'name': name, 'class_name': class_name})
            with self.assertRaises(aw.WorkflowError, msg=name):
                aw.validate_workflow({'name': 'x', 'steps': [raw]})

    def test_dangerous_hotkey_rejected(self):
        with self.assertRaises(aw.WorkflowError):
            aw.validate_workflow({'name': 'x', 'steps': [
                {'op': 'key', 'keys': 'alt+f4', 'expect': {'kind': 'foreground'}}]})

    def test_type_requires_selector_and_value(self):
        with self.assertRaises(aw.WorkflowError):
            aw.validate_workflow({'name': 'x', 'steps': [
                {'op': 'type', 'value': 'hi', 'expect': 'exists'}]})
        with self.assertRaises(aw.WorkflowError):
            aw.validate_workflow({'name': 'x', 'steps': [
                {'op': 'type', 'selector': {'name': '搜索', 'control_type': 'Edit'},
                 'value': '', 'expect': 'exists'}]})


class SelectorFallbackTests(unittest.TestCase):
    def setUp(self):
        self.elements = base_elements()

    def test_automation_id_is_first_tier(self):
        tiers = aw.selector_tiers({'automation_id': 'view_1', 'name': '搜索',
                                   'control_type': 'Edit'})
        self.assertIn('automation_id', tiers[0])

    def test_falls_back_when_automation_id_missing(self):
        found, tier = aw.resolve_selector(
            self.elements, {'automation_id': 'view_missing', 'name': '搜索',
                            'control_type': 'Edit'})
        self.assertEqual(found['name'], '搜索')
        self.assertIn('name', tier)
        self.assertNotIn('automation_id', tier)

    def test_name_contains_and_type_fallback(self):
        found, _ = aw.resolve_selector(
            self.elements, {'name_contains': '新标签', 'control_type': 'Button'})
        self.assertEqual(found['name'], '新标签页')

    def test_ordinal_only_with_control_type(self):
        found, tier = aw.resolve_selector(self.elements,
                                          {'control_type': 'Button', 'ordinal': 1})
        self.assertEqual(tier, {'control_type': 'Button'})
        buttons = [item for item in self.elements if item['type'] == 'Button']
        buttons.sort(key=aw._element_order)
        self.assertEqual(found['name'], buttons[1]['name'])

    def test_ordinal_out_of_range_stops(self):
        with self.assertRaises(aw.SelectorNotFound):
            aw.resolve_selector(self.elements, {'control_type': 'Button', 'ordinal': 9})

    def test_ambiguous_selector_stops(self):
        elements = self.elements + [element('选项', type='ListItem', top=200),
                                    element('选项', type='ListItem', top=240)]
        with self.assertRaises(aw.SelectorAmbiguous):
            aw.resolve_selector(elements, {'name': '选项'})

    def test_offscreen_elements_skipped(self):
        elements = [element('隐藏按钮', type='Button', offscreen=True)]
        with self.assertRaises(aw.SelectorNotFound):
            aw.resolve_selector(elements, {'name': '隐藏按钮'})


class WorkflowPlayTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix='aw-test-')
        self.store = aw.WorkflowStore(Path(self.tmp))
        self.desktop = FakeDesktop(base_elements())
        self.clock = FakeClock()

    def workflow(self, steps, recipe=None):
        return aw.validate_workflow({
            'name': '演示流程', 'slug': 'demo-flow',
            'trigger_phrases': ['演示流程'], 'app_hint': {'query': '演示'},
            'steps': steps, 'verify_recipe': recipe,
        })

    def test_play_verifies_each_step_and_updates_stats(self):
        def on_act(operation, target, value, desktop):
            if target['name'] == '新建':
                desktop.elements.append(element('未命名 - 记事本', type='Window'))
            return {'method': 'InvokePattern'}

        self.desktop.on_act = on_act
        self.store.save(self.workflow([
            {'op': 'focus', 'expect': {'kind': 'foreground'}},
            {'op': 'act', 'operation': 'invoke',
             'selector': {'name': '新建', 'control_type': 'Button'},
             'expect': {'kind': 'exists',
                        'selector': {'name': '未命名 - 记事本', 'control_type': 'Window'}}},
            {'op': 'type', 'selector': {'name': '搜索', 'control_type': 'Edit'},
             'value': '你好咕嘎', 'expect': {'kind': 'value_equals', 'value': '你好咕嘎'}},
            {'op': 'verify', 'selector': {'name': '搜索', 'control_type': 'Edit'},
             'expect': {'kind': 'value_contains', 'value': '咕嘎'}},
            {'op': 'key', 'keys': 'ctrl+a', 'expect': {'kind': 'foreground'}},
        ], recipe={'kind': 'window_title_contains', 'value': '演示窗口'}))
        report = aw.Player(store=self.store, desktop=self.desktop,
                           clock=self.clock).play('demo-flow')
        self.assertTrue(report['ok'], report)
        self.assertEqual(report['verified_steps'], 5)
        self.assertEqual(report['success_rate'], 1.0)
        self.assertGreater(report['total_ms'], 0)
        self.assertEqual(report['steps'][0]['op'], 'focus')
        saved = self.store.load('demo-flow')
        self.assertEqual(saved['success_count'], 1)
        self.assertTrue(saved['last_ok_at'])
        self.assertEqual(saved['last_run']['verified_steps'], 5)

    def test_play_stops_at_failed_expect(self):
        self.store.save(self.workflow([
            {'op': 'focus', 'expect': {'kind': 'foreground'}},
            {'op': 'act', 'operation': 'invoke',
             'selector': {'name': '新建', 'control_type': 'Button'},
             'expect': {'kind': 'exists',
                        'selector': {'name': '永远不会出现的窗口', 'control_type': 'Window'},
                        'timeout_ms': 120}},
            {'op': 'key', 'keys': 'ctrl+a', 'expect': {'kind': 'foreground'}},
        ]))
        report = aw.Player(store=self.store, desktop=self.desktop,
                           clock=self.clock).play('demo-flow')
        self.assertFalse(report['ok'])
        self.assertEqual(report['stuck_at'], 2)
        self.assertEqual(len(report['steps']), 2)
        self.assertIn('核验未通过', report['error'])
        self.assertNotIn(('key', 'ctrl+a'), self.desktop.actions)
        saved = self.store.load('demo-flow')
        self.assertEqual(saved['failure_count'], 1)
        self.assertIn('核验', saved['last_error'])

    def test_ambiguous_selector_stops_without_action(self):
        self.desktop.elements += [element('选项', type='ListItem', top=200),
                                  element('选项', type='ListItem', top=240)]
        self.store.save(self.workflow([
            {'op': 'focus', 'expect': {'kind': 'foreground'}},
            {'op': 'act', 'operation': 'invoke', 'selector': {'name': '选项'},
             'expect': {'kind': 'exists'}},
        ]))
        report = aw.Player(store=self.store, desktop=self.desktop,
                           clock=self.clock).play('demo-flow')
        self.assertFalse(report['ok'])
        self.assertEqual(report['stuck_at'], 2)
        # 预检门在聚焦之前就停住：动作列表必须完全为空
        self.assertEqual(report.get('stopped'), 'preflight_failed')
        self.assertEqual(self.desktop.actions, [])
        self.assertIn('预检没过', report['error'])
        self.assertIn('不唯一', report['preflight_failed'][0]['error'])

    def test_ambiguous_selector_still_stops_when_preflight_off(self):
        self.desktop.elements += [element('选项', type='ListItem', top=200),
                                  element('选项', type='ListItem', top=240)]
        self.store.save(self.workflow([
            {'op': 'focus', 'expect': {'kind': 'foreground'}},
            {'op': 'act', 'operation': 'invoke', 'selector': {'name': '选项'},
             'expect': {'kind': 'exists'}},
        ]))
        report = aw.Player(store=self.store, desktop=self.desktop,
                           clock=self.clock).play('demo-flow', preflight=False)
        self.assertFalse(report['ok'])
        self.assertEqual(report['stuck_at'], 2)
        self.assertIn('不唯一', report['error'])
        self.assertEqual(self.desktop.actions, [('focus', 100)])  # 只聚焦，没发出动作

    def test_type_into_non_edit_refused(self):
        self.store.save(self.workflow([
            {'op': 'focus', 'expect': {'kind': 'foreground'}},
            {'op': 'type', 'selector': {'name': '新建', 'control_type': 'Button'},
             'value': '危险输入', 'expect': {'kind': 'value_equals', 'value': '危险输入'}},
        ]))
        report = aw.Player(store=self.store, desktop=self.desktop,
                           clock=self.clock).play('demo-flow')
        self.assertFalse(report['ok'])
        self.assertIn('输入框', report['error'])
        self.assertEqual(self.desktop.actions, [('focus', 100)])

    def test_verify_recipe_failure_marks_run_failed(self):
        self.store.save(self.workflow([
            {'op': 'focus', 'expect': {'kind': 'foreground'}},
        ], recipe={'kind': 'window_title_contains', 'value': '不存在的标题'}))
        report = aw.Player(store=self.store, desktop=self.desktop,
                           clock=self.clock).play('demo-flow')
        self.assertFalse(report['ok'])
        self.assertIn('结束核验未通过', report['error'])
        self.assertFalse(self.store.load('demo-flow')['success_count'])

    def test_dry_run_resolves_without_actions(self):
        self.store.save(self.workflow([
            {'op': 'focus', 'expect': {'kind': 'foreground'}},
            {'op': 'act', 'operation': 'invoke',
             'selector': {'name': '新建', 'control_type': 'Button'},
             'expect': {'kind': 'exists'}},
        ]))
        report = aw.Player(store=self.store, desktop=self.desktop).play(
            'demo-flow', dry_run=True)
        self.assertTrue(report['ok'], report)
        self.assertEqual(self.desktop.actions, [])
        self.assertEqual(report['steps'][1]['control']['name'], '新建')

    def test_preflight_blocks_before_any_input_when_ui_state_changed(self):
        """录制时的界面状态变了，必须在动第一下手之前停住。"""
        self.store.save(self.workflow([
            {'op': 'focus', 'expect': {'kind': 'foreground'}},
            {'op': 'act', 'operation': 'invoke',
             'selector': {'name': '预检时不存在的东西', 'control_type': 'Button'},
             'expect': {'kind': 'exists'}},
        ]))
        report = aw.Player(store=self.store, desktop=self.desktop,
                           clock=self.clock).play('demo-flow')
        self.assertFalse(report['ok'])
        self.assertEqual(report.get('stopped'), 'preflight_failed')
        self.assertEqual(report['executed_steps'], 0)
        self.assertEqual(self.desktop.actions, [], '预检没过却已经动了界面')
        self.assertIn('预检没过', report['error'])
        self.assertEqual(report['preflight_failed'][0]['index'], 2)

    def test_preflight_can_be_turned_off_for_calibration(self):
        self.store.save(self.workflow([
            {'op': 'focus', 'expect': {'kind': 'foreground'}},
            {'op': 'act', 'operation': 'invoke',
             'selector': {'name': '预检时不存在的东西', 'control_type': 'Button'},
             'expect': {'kind': 'exists'}},
        ]))
        report = aw.Player(store=self.store, desktop=self.desktop,
                           clock=self.clock).play('demo-flow', preflight=False)
        self.assertFalse(report['ok'])
        self.assertEqual(report['stuck_at'], 2)
        self.assertGreaterEqual(report['executed_steps'], 1)
        self.assertTrue(self.desktop.actions)


class WorkflowToolSurfaceTests(unittest.TestCase):
    """`workflow` 必须是模型看得见的工具动作，且"查不到就报错"，不许假装成功。"""

    def test_tool_surfaces_expose_workflow_actions(self):
        from core.self_agent import TOOLS
        from core.mcp_contract import self_tools
        own = next(t for t in TOOLS if t['function']['name'] == 'workflow')
        actions = own['function']['parameters']['properties']['action']['enum']
        for action in ('list', 'play', 'compile'):
            self.assertIn(action, actions)
        mcp = next(t for t in self_tools() if t['name'] == 'workflow')
        for action in ('list', 'play', 'compile'):
            self.assertIn(action, mcp['inputSchema']['properties']['action']['enum'])

    def test_play_unknown_slug_raises_instead_of_faking_success(self):
        tmp = tempfile.mkdtemp(prefix='aw-missing-')
        store = aw.WorkflowStore(Path(tmp))
        with self.assertRaises(aw.WorkflowError):
            aw.play_workflow('not-a-real-workflow', store=store)

    def test_store_lists_saved_workflows(self):
        tmp = tempfile.mkdtemp(prefix='aw-list-')
        store = aw.WorkflowStore(Path(tmp))
        store.save(aw.validate_workflow({
            'name': '名单流程', 'slug': 'list-demo',
            'trigger_phrases': ['名单流程'], 'app_hint': {'query': '演示'},
            'steps': [{'op': 'focus', 'expect': {'kind': 'foreground'}}],
        }))
        names = [path.stem for path in sorted(store.root.glob('*.json'))]
        self.assertIn('list-demo', names)


class RecorderTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix='aw-rec-')
        self.store = aw.WorkflowStore(Path(self.tmp))
        self.desktop = FakeDesktop(base_elements())
        self.clock = FakeClock()
        self.recorder = aw.Recorder(store=self.store, desktop=self.desktop, clock=self.clock)

    def test_capture_executes_verifies_and_finishes(self):
        def on_act(operation, target, value, desktop):
            if target['name'] == '新建':
                desktop.elements.append(element('未命名 - 记事本', type='Window'))
            return {'method': 'InvokePattern'}

        self.desktop.on_act = on_act
        self.recorder.start('演示录制', ['演示录制'], slug='demo-record',
                            app_hint={'query': '演示'})
        self.recorder.capture({'op': 'focus', 'expect': {'kind': 'foreground'}})
        self.recorder.capture({'op': 'act', 'operation': 'invoke',
                               'selector': {'name': '新建', 'control_type': 'Button'},
                               'expect': {'kind': 'exists',
                                          'selector': {'name': '未命名 - 记事本',
                                                       'control_type': 'Window'}}})
        self.recorder.capture({'op': 'type',
                               'selector': {'name': '搜索', 'control_type': 'Edit'},
                               'value': '咕嘎',
                               'expect': {'kind': 'value_equals', 'value': '咕嘎'}})
        self.assertTrue(self.recorder.status()['recording'])
        draft = self.store.draft_path('demo-record')
        self.assertTrue(draft.is_file())
        saved = self.recorder.finish()
        self.assertEqual(saved['slug'], 'demo-record')
        self.assertEqual(len(saved['steps']), 3)
        self.assertEqual(saved['source'], 'recorder')
        self.assertEqual(saved['success_count'], 0)
        self.assertTrue(saved['baseline']['recorded_total_ms'] > 0)
        self.assertFalse(self.store.draft_path('demo-record').exists())
        on_disk = json.loads(self.store.path('demo-record').read_text(encoding='utf-8'))
        self.assertEqual(on_disk['schema'], 1)
        self.assertEqual(len(on_disk['trigger_phrases']), 1)

    def test_capture_refuses_failed_step(self):
        self.recorder.start('失败录制', ['失败录制'], slug='demo-fail',
                            app_hint={'query': '演示'})
        with self.assertRaises(aw.StepFailed):
            self.recorder.capture({'op': 'act', 'operation': 'invoke',
                                   'selector': {'name': '新建', 'control_type': 'Button'},
                                   'expect': {'kind': 'exists',
                                              'selector': {'name': '不会出现',
                                                           'control_type': 'Window'},
                                              'timeout_ms': 100}})
        self.assertEqual(self.recorder.status()['steps'], 0)
        self.recorder.abort()

    def test_observe_rejects_coordinate_and_observation_actions(self):
        self.recorder.start('接线录制', ['接线录制'], slug='demo-wire')
        with self.assertRaises(aw.WorkflowError):
            self.recorder.observe({'action': 'click', 'x': 10, 'y': 20})
        with self.assertRaises(aw.WorkflowError):
            self.recorder.observe({'action': 'observe', 'hwnd': 100})
        step = self.recorder.observe({'action': 'hotkey', 'keys': 'enter'})
        self.assertEqual(step['op'], 'key')
        self.assertEqual(step['keys'], 'enter')
        saved = self.recorder.finish()
        self.assertEqual(saved['steps'][0]['op'], 'key')

    def test_observe_uia_action_maps_selector(self):
        self.recorder.start('UIA录制', ['UIA录制'], slug='demo-uia')
        step = self.recorder.observe(
            {'action': 'uia', 'uia_operation': 'invoke', 'uia_name': '新建',
             'uia_type': 'Button'},
            result={'control': {'name': '新建', 'id': 'view_2', 'type': 'Button'}})
        self.assertEqual(step['op'], 'act')
        self.assertEqual(step['selector']['automation_id'], 'view_2')
        self.assertEqual(step['selector']['name'], '新建')
        self.assertEqual(step['selector']['control_type'], 'Button')
        self.recorder.abort()

    def test_observe_derives_app_hint_when_recording_without_one(self):
        self.recorder.start('接线录制2', ['接线录制2'], slug='demo-hint')
        self.recorder.observe({'action': 'hotkey', 'keys': 'enter'})
        saved = self.recorder.finish()
        self.assertEqual(saved['app_hint'], {'query': '演示窗口'})

    def test_finish_requires_at_least_one_step(self):
        self.recorder.start('空录制', ['空录制'], slug='demo-empty')
        with self.assertRaises(aw.WorkflowError):
            self.recorder.finish()
        self.recorder.abort()


class CompileAndLookupTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix='aw-compile-')
        self.store = aw.WorkflowStore(Path(self.tmp))

    def reply(self):
        return json.dumps({
            'name': '打开空白页',
            'description': '打开新标签页并输入 about:blank',
            'trigger_phrases': ['打开空白页', '开个空白页'],
            'app_hint': {'query': '豆包浏览器'},
            'steps': [
                {'op': 'focus', 'title': '聚焦窗口', 'expect': {'kind': 'foreground'}},
                {'op': 'act', 'operation': 'invoke',
                 'selector': {'name': '新标签页', 'control_type': 'Button'},
                 'expect': {'kind': 'exists',
                            'selector': {'name': '新标签页', 'control_type': 'TabItem'}}},
                {'op': 'type', 'selector': {'automation_id': 'view_1012'},
                 'value': 'about:blank',
                 'expect': {'kind': 'value_equals', 'value': 'about:blank'}},
            ],
            'verify_recipe': {'kind': 'window_title_contains', 'value': '豆包浏览器'},
        }, ensure_ascii=False)

    def test_compile_uses_exactly_one_model_call_and_saves(self):
        calls = []

        def complete(messages):
            calls.append(messages)
            return self.reply()

        workflow = aw.compile_from_text('帮我在豆包浏览器开个空白页', slug='compiled-blank',
                                        store=self.store, complete=complete)
        self.assertEqual(len(calls), 1)
        self.assertIn('桌面工作流编译器', calls[0][0]['content'])
        self.assertEqual(workflow['source'], 'model-compiled')
        self.assertEqual(len(workflow['steps']), 3)
        self.assertTrue(self.store.path('compiled-blank').is_file())

    def test_compile_fails_honestly_when_model_unavailable(self):
        def broken(messages):
            raise RuntimeError('no credential')

        with self.assertRaises(aw.WorkflowError):
            aw.compile_from_text('做一件长任务', store=self.store, complete=broken)
        self.assertEqual(self.store.list(), [])

    def test_compile_sanitizes_harmless_model_deviations(self):
        reply = json.dumps({
            'name': '翻页', 'app_hint': {'name': '记事本'},
            'trigger_phrases': '翻下一页',
            'steps': [
                {'op': 'focus', 'expect': {'kind': 'foreground'}, 'wait_seconds': 2},
                {'op': 'scroll', 'selector': {'type': 'Document', 'text': '正文'},
                 'times': 3, 'expect': {'kind': 'exists'}},
            ],
            'verify_recipe': {'kind': 'unknown_recipe', 'value': 'x'},
            'extra_top': 1,
        }, ensure_ascii=False)
        workflow = aw.compile_from_text(
            '把记事本往下翻三格', slug='sanitized', store=self.store,
            complete=lambda messages: reply)
        self.assertEqual(workflow['app_hint'], {'query': '记事本'})
        self.assertEqual(workflow['trigger_phrases'], ['翻下一页'])
        self.assertEqual(workflow['steps'][0]['timeout_ms'], 2000)
        self.assertEqual(workflow['steps'][1]['selector'],
                         {'control_type': 'Document', 'name': '正文'})
        self.assertIsNone(workflow['verify_recipe'])
        self.assertIn('编译时规范化', workflow['description'])

    def test_verify_step_may_put_selector_inside_expect(self):
        workflow = aw.validate_workflow({'name': 'x', 'steps': [
            {'op': 'wait', 'expect': {'kind': 'exists',
                                      'selector': {'name': '搜索', 'control_type': 'Edit'}}}]})
        self.assertIsNone(workflow['steps'][0]['selector'])
        self.assertEqual(workflow['steps'][0]['expect']['selector']['name'], '搜索')

    def test_compile_rejects_invalid_model_json(self):
        with self.assertRaises(aw.WorkflowError):
            aw.compile_from_text('做一件长任务', store=self.store,
                                 complete=lambda messages: '不是 JSON')
        with self.assertRaises(aw.WorkflowError):
            aw.compile_from_text(
                '做一件长任务', store=self.store,
                complete=lambda messages: json.dumps({'name': 'x', 'steps': [
                    {'op': 'send', 'expect': 'exists'}]}))
        with self.assertRaises(aw.WorkflowError):
            aw.compile_from_text(
                '做一件长任务', store=self.store,
                complete=lambda messages: json.dumps({'name': 'x', 'steps': [
                    {'op': 'act', 'selector': {'name': '删除'}, 'expect': 'exists'}]}))
        self.assertEqual(self.store.list(), [])

    def test_find_workflow_by_trigger_phrase(self):
        self.store.save(aw.validate_workflow({
            'name': '开空白页', 'slug': 'blank-page',
            'trigger_phrases': ['打开空白页', '开个空白页'],
            'app_hint': {'query': '豆包浏览器'},
            'steps': [{'op': 'focus', 'expect': {'kind': 'foreground'}}],
        }))
        self.assertEqual(aw.find_workflow('帮我打开空白页，快点', store=self.store)['slug'],
                         'blank-page')
        self.assertIsNone(aw.find_workflow('今天天气怎么样', store=self.store))


class WiringTests(unittest.TestCase):
    """模块级接线函数（Lead 接 core/self_agent.py computer 工具时用）。"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix='aw-wire-')
        self.store = aw.WorkflowStore(Path(self.tmp))
        self.desktop = FakeDesktop(base_elements())

    def tearDown(self):
        aw.abort_recording()

    def test_module_level_recording_helpers(self):
        state = aw.start_recording('接线测试', ['接线测试'], slug='wired-flow',
                                   app_hint={'query': '演示'},
                                   store=self.store, desktop=self.desktop)
        self.assertTrue(state['recording'])
        step = aw.record_computer_action({'action': 'hotkey', 'keys': 'enter'})
        self.assertEqual(step['op'], 'key')
        saved = aw.finish_recording()
        self.assertEqual(saved['slug'], 'wired-flow')
        self.assertFalse(aw.active_recording()['recording'])
        self.assertTrue(self.store.path('wired-flow').is_file())

    def test_foreign_actions_do_not_break_recording(self):
        aw.start_recording('接线测试2', ['接线测试2'], slug='wired-two',
                           app_hint={'query': '演示'},
                           store=self.store, desktop=self.desktop)
        self.assertIsNone(aw.record_computer_action({'action': 'click', 'x': 1, 'y': 2}))
        self.assertIsNone(aw.record_computer_action({'action': 'observe'}))
        aw.record_computer_action({'action': 'hotkey', 'keys': 'esc'})
        saved = aw.finish_recording()
        self.assertEqual(len(saved['steps']), 1)


if __name__ == '__main__':
    unittest.main()
