# -*- coding: utf-8 -*-
"""咕嘎的编程智能体通道：把"改一个项目"这件事交给本机 DSH 跑，咕嘎负责拉起、看住、收结果。

为什么不自造：本机 DSH 就是一个成熟 harness（工具/计划/子智能体/验收都在）。
咕嘎缺的不是"再写一个 agent 循环"，而是**一条能真的把项目任务交出去并拿回证据的通道**。

纪律：
- 拉起后必须**轮询到终态**才算有结果，不能"已受理"就报成功；
- 超时返回 `timeout`，不假装完成；
- 结果里带 session_id，人能回 DSH 里自己看。
"""
import json
import time
from pathlib import Path

DEFAULT_TIMEOUT = 900
DEFAULT_POLL = 5
RECEIPT_DIR = Path(__file__).resolve().parents[1] / 'runtime'
RECEIPT_FILE = RECEIPT_DIR / 'code-receipts.jsonl'


def write_receipt(report, path=None):
    """把一次编码任务的结果追加进台账。人可以拿去核对，不用信任何人的嘴。"""
    path = Path(path or RECEIPT_FILE)
    path.parent.mkdir(parents=True, exist_ok=True)
    row = dict(
        ts=time.strftime('%Y-%m-%dT%H:%M:%S'),
        task=str(report.get('task') or '')[:200],
        cwd=str(report.get('cwd') or ''),
        ok=bool(report.get('ok')),
        stopped=str(report.get('stopped') or ''),
        check_cmd=str(report.get('check_cmd') or ''),
        check_exit=report.get('check_exit'),
        session_id=str(report.get('session_id') or ''),
        elapsed=report.get('elapsed'),
        changed_files=list(report.get('changed_files') or [])[:20],
        summary=str(report.get('answer') or report.get('error') or '')[:300],
    )
    with path.open('a', encoding='utf-8') as handle:
        handle.write(json.dumps(row, ensure_ascii=False) + '\n')
    return row


def _status_of(view):
    """从会话视图里读出 (running, answer, seq)。

    ``running`` 在这个 DSH 版本里不可信（做完也一直是 true），所以额外返回
    ``as_of_seq``：它才是"会话还在推进吗"的真实信号。
    """
    if not isinstance(view, dict):
        return True, '', None
    running = view.get('running')
    if running is None:
        state = str(view.get('state') or view.get('phase') or '')
        running = state not in ('finished', 'idle', 'done', 'ended')
    answer = ''
    for key in ('last_answer', 'answer', 'lastAnswer', 'summary'):
        value = view.get(key)
        if isinstance(value, str) and value.strip():
            answer = value.strip()
            break
    seq = view.get('as_of_seq')
    if seq is None:
        seq = view.get('asOfSeq') or view.get('seq')
    try:
        seq = int(seq) if seq is not None else None
    except (TypeError, ValueError):
        seq = None
    return bool(running), answer, seq


class CodingAgent:
    """交给 DSH 的编码任务。gateway/sleep 可注入，便于测试。"""

    def __init__(self, gateway=None, *, sleep=None, timeout=DEFAULT_TIMEOUT,
                 poll_seconds=DEFAULT_POLL, clock=None, idle_polls=3, stall_polls=24,
                 seq_polls=6):
        self._gateway = gateway
        self._sleep = sleep or time.sleep
        self.timeout = int(timeout)
        self.poll_seconds = float(poll_seconds)
        self.clock = clock or time.monotonic
        self.idle_polls = max(1, int(idle_polls))
        # 文件长时间不动、会话还自称 running、检查也不过 → 判定停滞，不再干等到超时
        self.stall_polls = max(4, int(stall_polls))
        self.seq_polls = max(2, int(seq_polls))

    def gateway(self):
        if self._gateway is None:
            from core.super_agent.dsh_gateway import DshGateway
            self._gateway = DshGateway()
        return self._gateway

    def instruction(self, task, cwd):
        """给 DSH 的完整指令：限定工作目录 + 要求自证 + 结束时落一个完成文件。"""
        return (
            '工作目录：%s\n'
            '只在这个目录里读写，不要碰其他项目。\n'
            '要求：\n'
            '1. 先看清楚现状再动手，改动要小。\n'
            '2. 改完必须运行该项目自己的检查（测试/构建/运行一次），并把真实输出贴出来。\n'
            '3. 没跑通就说没跑通，不要用"应该可以"代替。\n'
            '4. **结束时必须在工作目录写下 DONE.json**，内容形如：\n'
            '   {"summary": "一句话说改了什么", "checks": [{"cmd": "跑的什么命令", "exit": 0}],'
            ' "ok": true}\n'
            '   如果做不成，改写成 FAILED.json，内容 {"reason": "卡在哪", "tried": ["试过什么"]}。\n'
            '   这两个文件是咕嘎判断你有没有结束的唯一依据，不写就等于没结束。\n'
            '\n用户任务：\n%s' % (cwd, str(task or '').strip()))

    def _completion(self, cwd, stale=None):
        """读完成信号：DONE.json / FAILED.json。上一轮留下的旧文件要忽略。

        ``stale``：{文件名: mtime}，本轮开始时就已存在的完成文件视为旧文件。
        """
        folder = Path(cwd)
        stale = stale or {}
        for name, stopped in (('DONE.json', 'finished'), ('FAILED.json', 'failed')):
            path = folder / name
            if not path.is_file():
                continue
            try:
                if name in stale and abs(path.stat().st_mtime - stale[name]) < 1e-6:
                    continue      # 上一轮留下的，不算这一轮的完成信号
                payload = json.loads(path.read_text(encoding='utf-8'))
            except (OSError, ValueError):
                continue  # 写了一半，下一轮再看
            if not isinstance(payload, dict):
                continue
            return dict(stopped=stopped, signal=name, payload=payload)
        return None

    def _stale_signals(self, cwd):
        """记下本轮开始时就已存在的完成文件（防止上一轮的 DONE.json 骗过这一轮）。"""
        folder = Path(cwd)
        marks = {}
        for name in ('DONE.json', 'FAILED.json'):
            path = folder / name
            try:
                if path.is_file():
                    marks[name] = path.stat().st_mtime
            except OSError:
                pass
        return marks

    def _snapshot(self, cwd):
        """项目里**真实源码文件**的名字+修改时间。缓存目录不算，免得跑个测试就以为改过代码。"""
        folder = Path(cwd)
        skip_dirs = {'__pycache__', '.git', 'node_modules', '.pytest_cache', '.mypy_cache', '.venv'}
        skip_files = {'DONE.json', 'FAILED.json'}
        items = []
        try:
            for path in folder.rglob('*'):
                if not path.is_file():
                    continue
                if any(part in skip_dirs for part in path.relative_to(folder).parts):
                    continue
                if path.name in skip_files or path.suffix == '.pyc':
                    continue
                items.append((str(path.relative_to(folder)), path.stat().st_mtime))
        except OSError:
            return None
        return sorted(items)

    def _run_check(self, check_cmd, cwd):
        """咕嘎自己跑一次项目检查。**不采信 DSH 的自述**，只认退出码。"""
        import subprocess
        import sys as _sys
        parts = str(check_cmd).split()
        if not parts:
            return dict(check_ok=None, check_cmd=check_cmd, check_error='检查命令为空')
        head = parts[0].lower()
        if head.endswith('.py'):
            argv = [_sys.executable, '-X', 'utf8'] + parts      # 直接跑脚本
        elif head in ('pytest', 'unittest', 'pip', 'mypy', 'ruff', 'flake8', 'black'):
            argv = [_sys.executable, '-X', 'utf8', '-m'] + parts  # python 模块
        else:
            argv = str(check_cmd)                                # 其它（npm test 等）交给 shell
        try:
            proc = subprocess.run(argv, cwd=str(cwd), capture_output=True, text=True,
                                  timeout=300, shell=not isinstance(argv, list))
        except (OSError, ValueError, subprocess.SubprocessError) as exc:
            return dict(check_ok=None, check_cmd=check_cmd, check_error=str(exc)[:200])
        tail = ((proc.stdout or '') + (proc.stderr or '')).strip().splitlines()[-3:]
        return dict(check_ok=proc.returncode == 0, check_cmd=check_cmd,
                    check_exit=proc.returncode, check_tail=' | '.join(tail)[:300])

    def run(self, task, cwd, check_cmd=None, cancel=None, session_id=None, retries=1):
        """跑一轮；卡住（stalled）时自动换一个新会话重试，最多 retries 次。"""
        attempts = 0
        report = self._run_round(task, cwd, check_cmd=check_cmd, cancel=cancel,
                                 session_id=session_id)
        attempts += 1
        while (report.get('stopped') == 'stalled' and attempts <= max(0, int(retries))
               and not (cancel is not None and getattr(cancel, 'is_set', lambda: False)())):
            # 卡住的会话不用了，换一个新的再试一次
            report = self._run_round(task, cwd, check_cmd=check_cmd, cancel=cancel,
                                     session_id=None)
            attempts += 1
        report['reused'] = bool(str(session_id or '').strip()) and attempts == 1
        report['attempts'] = attempts
        return report

    def _run_round(self, task, cwd, check_cmd=None, cancel=None, session_id=None):
        """一轮的实际实现。"""
        task = str(task or '').strip()
        cwd = str(cwd or '').strip()
        if not task:
            raise ValueError('编码任务不能为空')
        if not cwd:
            raise ValueError('必须给一个工作目录，咕嘎不能在家目录里乱改')
        client = self.gateway()
        started = self.clock()
        reused = bool(str(session_id or '').strip())
        if reused:
            session_id = str(session_id).strip()
        else:
            created = client.create_session()
            session_id = str((created or {}).get('session_id') or '')
            if not session_id:
                raise RuntimeError('DSH 没有返回 session_id，任务没有拉起')
        client.prompt(session_id, self.instruction(task, cwd))
        polls = 0
        answer = ''
        first_snapshot = self._snapshot(cwd)
        last_snapshot = first_snapshot
        stable_polls = 0
        last_seq = None
        seq_stable = 0
        stale_signals = self._stale_signals(cwd)   # 上一轮留下的 DONE/FAILED 不算数
        saw_progress = False   # 会话到底有没有推进过（没推进就不能判"完成"）
        while True:
            elapsed = self.clock() - started
            if elapsed > self.timeout:
                evidence = self._run_check(check_cmd, cwd) if check_cmd else {}
                verified = evidence.get('check_ok') is True
                return dict(ok=verified,
                            stopped='timeout_but_verified' if verified else 'timeout',
                            session_id=session_id, cwd=cwd, polls=polls,
                            reused=reused,
                            elapsed=round(elapsed, 1), answer=answer,
                            error=('超过 %d 秒仍未收到完成信号；'
                                   + ('但咕嘎自己跑的检查通过了，按通过算' if verified
                                      else '且咕嘎自己跑的检查也没通过，按未完成算'))
                            % self.timeout, **evidence)
            self._sleep(self.poll_seconds)
            polls += 1
            if cancel is not None and getattr(cancel, 'is_set', lambda: False)():
                return dict(ok=False, stopped='cancelled', session_id=session_id, cwd=cwd,
                            polls=polls, elapsed=round(self.clock() - started, 1),
                            answer=answer,
                            error='用户中止了这轮；DSH 会话还留着，里面的改动不会自动回滚')
            followup = self._completion(cwd, stale_signals)
            if followup is not None:
                payload = followup['payload']
                evidence = self._run_check(check_cmd, cwd) if check_cmd else {}
                # 完成文件说"做完了"，但咕嘎自己跑检查没过 → 以检查为准，并明确记下来
                if check_cmd and evidence.get('check_ok') is not True:
                    return dict(ok=False, stopped='check_failed_after_done',
                                signal=followup['signal'], session_id=session_id, cwd=cwd,
                                polls=polls, elapsed=round(self.clock() - started, 1),
                                answer=str(payload.get('summary') or answer)[:2000],
                                checks=payload.get('checks') or [],
                                error='完成文件说做完了，但咕嘎自己跑的 %s 没通过（exit=%s），'
                                      '按没做完算' % (check_cmd, evidence.get('check_exit')),
                                **evidence)
                ok = followup['stopped'] == 'finished'
                return dict(ok=ok,
                            stopped=followup['stopped'], signal=followup['signal'],
                            session_id=session_id, cwd=cwd, polls=polls,
                            elapsed=round(self.clock() - started, 1),
                            answer=str(payload.get('summary') or answer)[:2000],
                            checks=payload.get('checks') or [],
                            reason=str(payload.get('reason') or '')[:400],
                            **evidence)
            running, latest, seq = _status_of(client.session_view(session_id))
            if latest:
                answer = latest
            snapshot = self._snapshot(cwd)
            files_changed = snapshot is not None and snapshot != first_snapshot
            # as_of_seq 涨过 / 文件变过，才算"这轮真的动过"
            if files_changed:
                saw_progress = True
            if seq is not None:
                if last_seq is not None and seq != last_seq:
                    seq_stable = 0
                    saw_progress = True      # 序号涨了 = 它确实在干活
                else:
                    seq_stable += 1
                last_seq = seq
            if not running:
                evidence = self._run_check(check_cmd, cwd) if check_cmd else {}
                ok = bool(evidence.get('check_ok')) if check_cmd else True
                return dict(ok=ok, stopped='finished' if ok else 'finished_check_failed',
                            session_id=session_id, cwd=cwd, polls=polls,
                            elapsed=round(self.clock() - started, 1),
                            answer=answer, **evidence)
            # 会话状态不可靠（做完了也常显示 running）：
            # 项目文件连续几轮没变 + 咕嘎自己的检查通过 → 提前收工，不白等到超时
            if snapshot is not None and snapshot == last_snapshot:
                stable_polls += 1
            else:
                stable_polls = 0
                last_snapshot = snapshot
            if check_cmd and stable_polls >= self.idle_polls and files_changed:
                evidence = self._run_check(check_cmd, cwd)
                if evidence.get('check_ok') is True:
                    return dict(ok=True, stopped='finished_by_check', session_id=session_id,
                                cwd=cwd, polls=polls,
                                elapsed=round(self.clock() - started, 1), answer=answer,
                                **evidence)
            # 会话序号不再涨（≈不再推进）：比等文件不动更快，也能覆盖"不改文件"的任务
            # 但**改代码的任务必须见到文件改动**：否则就是"拿老测试跑绿了"，不能报成功
            if seq is not None and seq_stable >= self.seq_polls and saw_progress:
                evidence = self._run_check(check_cmd, cwd) if check_cmd else {}
                if check_cmd and evidence.get('check_ok') is True and files_changed:
                    return dict(ok=True, stopped='finished_by_check', session_id=session_id,
                                cwd=cwd, polls=polls,
                                elapsed=round(self.clock() - started, 1), answer=answer,
                                **evidence)
                if check_cmd and evidence.get('check_ok') is True and not files_changed:
                    return dict(ok=False, stopped='no_change', session_id=session_id, cwd=cwd,
                                polls=polls, elapsed=round(self.clock() - started, 1),
                                answer=answer,
                                error='检查通过了，但这一轮**没有任何文件改动**——'
                                      '很可能是拿老测试跑绿的，不算做完。检查结果：%s'
                                      % (evidence.get('check_tail') or ''),
                                **evidence)
                if check_cmd:
                    return dict(ok=False, stopped='finished_check_failed',
                                session_id=session_id, cwd=cwd, polls=polls,
                                elapsed=round(self.clock() - started, 1), answer=answer,
                                **evidence)
                return dict(ok=False, stopped='idle_no_check', session_id=session_id, cwd=cwd,
                            polls=polls, elapsed=round(self.clock() - started, 1),
                            answer=answer,
                            error='会话已经不再推进，但这次没给 check_cmd，无法证明改对了')
            # 文件很久没动、会话还自称在跑：可能排队或卡死。明确报停滞，别让人干等
            if stable_polls >= self.stall_polls:
                evidence = self._run_check(check_cmd, cwd) if check_cmd else {}
                return dict(ok=False, stopped='stalled', session_id=session_id, cwd=cwd,
                            polls=polls, elapsed=round(self.clock() - started, 1),
                            answer=answer,
                            error='项目文件已经 %d 轮没变化（约 %d 秒），会话仍显示在运行；'
                                  '可能是排队或卡住。可以去 DSH 里那个会话看看。'
                                  % (stable_polls, int(stable_polls * self.poll_seconds)),
                            **evidence)
