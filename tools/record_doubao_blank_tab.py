# -*- coding: utf-8 -*-
"""真机录制“豆包浏览器：开空白页再关掉”的可执行工作流。

只做完全可逆的界面操作：
  聚焦已授权的豆包浏览器 → 读回窗口标题 → 读回地址栏 → 点“新标签页”
  → 地址栏输入 about:blank → 回车 → 读回新标签页/文档 → 滚动 → ctrl+w 关掉自己开的标签页
  → 再读回窗口标题，最后把窗口恢复成原来的最小化状态。

每一步都由 core.action_workflow.Recorder.capture 真机执行 + 读回核验，核验通过才记入工作流。
用法（项目根目录）：
  python -X utf8 tools/record_doubao_blank_tab.py
"""
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core.action_workflow import (RealDesktop, Recorder, StepFailed, WorkflowError,
                                  WorkflowStore, play_workflow)

SLUG = 'doubao-browser-blank-tab'
APP_HINT = {'query': '豆包浏览器'}
TRIGGERS = ['在豆包浏览器开个空白页', '豆包浏览器开个空白标签页再关掉',
            '开个空白页看看再关掉', '用豆包浏览器开新标签页']
OMNIBOX = {'automation_id': 'view_1012', 'name': '地址和搜索栏', 'control_type': 'Edit'}
WINDOW = {'name_contains': '豆包浏览器', 'control_type': 'Window'}
PAGE_DOC = {'automation_id': 'RootWebArea', 'control_type': 'Document'}
ABOUT_TAB = {'name_contains': 'about:blank', 'control_type': 'TabItem'}

STEPS = [
    {'op': 'focus', 'title': '聚焦豆包浏览器窗口',
     'expect': {'kind': 'foreground'}, 'timeout_ms': 8000},
    {'op': 'verify', 'title': '读回窗口标题，确认是豆包浏览器',
     'selector': WINDOW,
     'expect': {'kind': 'name_contains', 'value': '豆包浏览器'}},
    {'op': 'verify', 'title': '读回地址栏，确认可操作',
     'selector': OMNIBOX, 'expect': {'kind': 'enabled'}},
    {'op': 'verify', 'title': '读回当前网页文档（UIA 文档节点）',
     'selector': PAGE_DOC, 'expect': {'kind': 'exists'}},
    {'op': 'scroll', 'times': 2, 'title': '在当前网页上滚动两格',
     'selector': PAGE_DOC, 'expect': {'kind': 'exists', 'selector': PAGE_DOC}},
    {'op': 'key', 'keys': 'home', 'title': '按 Home 把网页滚回顶部',
     'expect': {'kind': 'foreground'}},
    {'op': 'act', 'operation': 'invoke', 'title': '点“新标签页”开一个新标签页',
     'selector': {'name': '新标签页', 'control_type': 'Button',
                  'class_name': 'NewTabButton'},
     # 新标签页打开后地址栏应为空 —— 用读回地址栏的值来核验标签页真的开了。
     'expect': {'kind': 'value_equals', 'value': '', 'selector': OMNIBOX,
                'timeout_ms': 5000}},
    {'op': 'act', 'operation': 'focus', 'title': '把键盘焦点放进地址栏',
     'selector': OMNIBOX, 'expect': {'kind': 'focused', 'timeout_ms': 3000}},
    {'op': 'act', 'operation': 'set_value', 'value': 'about:blank',
     'title': '地址栏输入 about:blank（回车前先读回）',
     'selector': OMNIBOX,
     'expect': {'kind': 'value_equals', 'value': 'about:blank'}},
    {'op': 'key', 'keys': 'enter', 'title': '回车导航到 about:blank',
     'expect': {'kind': 'exists', 'selector': ABOUT_TAB, 'timeout_ms': 8000}},
    {'op': 'verify', 'title': '读回窗口标题，确认已停在 about:blank',
     'selector': WINDOW,
     'expect': {'kind': 'name_contains', 'value': 'about:blank'}},
    {'op': 'verify', 'title': '读回地址栏 URL，确认导航完成',
     'selector': OMNIBOX,
     'expect': {'kind': 'value_contains', 'value': 'about:blank'}},
    {'op': 'key', 'keys': 'ctrl+w', 'title': '关掉自己开的 about:blank 标签页',
     'expect': {'kind': 'absent', 'selector': ABOUT_TAB, 'timeout_ms': 6000}},
    {'op': 'verify', 'title': '再读回窗口标题，确认浏览器还在',
     'selector': WINDOW,
     'expect': {'kind': 'name_contains', 'value': '豆包浏览器'}},
]


def tab_titles(desktop, hwnd):
    snapshot = desktop.snapshot(hwnd)
    return [str(item.get('name') or '') for item in snapshot.get('elements') or []
            if item.get('type') == 'TabItem']


def ensure_focus(desktop, hwnd, attempts=8):
    """Windows 偶尔会临时拒绝前台切换（用户刚点过别的窗口）；重试有界，不无限抢焦点。"""
    for attempt in range(1, attempts + 1):
        try:
            desktop.focus(hwnd)
            return True
        except (WorkflowError, RuntimeError):
            if attempt >= attempts:
                return False
            time.sleep(0.6)
    return False


def cleanup(desktop, hwnd, original_tabs):
    """只关掉本次录制新开出来的空白标签页；不碰用户原有的任何标签页。"""
    try:
        current = tab_titles(desktop, hwnd)  # 快照不需要前台，先看完再决定要不要抢焦点
        new_tabs = [name for name in current if name not in original_tabs]
        blank = [name for name in new_tabs
                 if 'about:blank' in name or '新标签页' in name]
        if len(blank) == 1:
            if desktop.foreground() != hwnd and not ensure_focus(desktop, hwnd):
                print('CLEANUP skipped: 无法把焦点交给浏览器窗口')
                return
            desktop.key('ctrl+w')
            print('CLEANUP closed tab:', blank[0])
        elif new_tabs:
            print('CLEANUP unexpected new tabs (left untouched):',
                  json.dumps(new_tabs, ensure_ascii=False))
    except Exception as exc:  # 清理失败不掩盖主结果
        print('CLEANUP failed:', type(exc).__name__, str(exc)[:200])


def main():
    desktop = RealDesktop()
    windows = desktop.list_windows('豆包浏览器')
    if not windows:
        print('ABORT 没有在运行的豆包浏览器窗口')
        return 2
    window = windows[0]
    hwnd = int(window['hwnd'])
    was_minimized = bool(window.get('minimized'))
    original_tabs = tab_titles(desktop, hwnd)
    print('WINDOW', json.dumps({k: window.get(k) for k in
                                ('hwnd', 'title', 'process_name', 'minimized')},
                               ensure_ascii=False))
    print('ORIGINAL_TABS', json.dumps(original_tabs, ensure_ascii=False))

    store = WorkflowStore()
    recorder = Recorder(store=store, desktop=desktop)
    recorder.start('豆包浏览器：开空白页再关掉', TRIGGERS, slug=SLUG,
                   app_hint=APP_HINT, window_hwnd=hwnd,
                   verify_recipe={'kind': 'window_title_contains', 'value': '豆包浏览器'})
    if not ensure_focus(desktop, hwnd):
        recorder.abort()
        print('ABORT Windows 连续 8 次拒绝切到浏览器窗口，本次不训练（不硬来）')
        if was_minimized:
            _minimize(hwnd)
        return 4
    started_all = time.monotonic()
    try:
        for index, step in enumerate(STEPS, 1):
            result = recorder.capture(step)
            print('STEP %02d ok=%s %5.0fms %s | %s' % (
                index, result['ok'], result['elapsed_ms'], result.get('method'),
                step['title']))
        saved = recorder.finish()
    except Exception as exc:
        try:
            recorder.abort()
        except Exception:
            pass
        try:
            import win32gui
            foreground = win32gui.GetForegroundWindow()
            print('FOREGROUND_AT_FAILURE', foreground,
                  repr(win32gui.GetWindowText(foreground)))
        except Exception:
            pass
        cleanup(desktop, hwnd, original_tabs)
        if was_minimized:
            _minimize(hwnd)
        print('RECORD_FAILED', type(exc).__name__, str(exc)[:500])
        return 3
    finally:
        # 无论成功失败，恢复窗口原有最小化状态，避免打扰用户。
        if was_minimized:
            _minimize(hwnd)

    total_ms = round((time.monotonic() - started_all) * 1000, 1)
    print('SAVED', store.path(saved['slug']))
    print('BASELINE total_ms=%.1f steps=%d' % (
        (saved.get('baseline') or {}).get('recorded_total_ms') or 0,
        len(saved['steps'])))
    print('BASELINE_STEPS', json.dumps(
        [{k: item.get(k) for k in ('index', 'elapsed_ms', 'op')}
         for item in (saved.get('baseline') or {}).get('steps') or []],
        ensure_ascii=False))
    print('RECORD_WALL_MS', total_ms)
    return 0


def _minimize(hwnd):
    import win32gui
    win32gui.ShowWindow(int(hwnd), 6)


if __name__ == '__main__':
    sys.exit(main())
