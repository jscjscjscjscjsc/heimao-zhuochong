# -*- coding: utf-8 -*-
"""语音链路健康自检与恢复 — 咕嘎企鹅版（A 语音工作流）

为什么需要这个模块：
    注册表 HKCU\\Software\\GugaPenguinPet\\GugaPenguin\\agent 的 silent=true 时，
    ui/tts_engine.py 的 TTSEngine.speak() 第一行就 return。整机朗读和碎碎念
    （core/proactive_conversation.py 走 panel._say_active_reply → speak）会一起变哑，
    但 UI 不报任何错误。本模块提供可编程的 check / probe_speak / ensure_audible，
    让 MCP、脚本和自检工具能定位"静默开关"并把声音恢复。

Qt 铁律：
    任何要创建 QApplication 的进程，必须先 import launcher; launcher._prepare_qt()，
    否则 Qt 平台插件初始化失败会以 0xC0000409 硬崩。本模块在 import PyQt5 之前执行。

公开接口（契约见 咕嘎×DSH闭环/契约.md 与 task-1）：
    check(engine=None)          -> {silent, edge_ok, doubao_ok, playback_ok,
                                    last_error, provider, voice, ok, detail}
    probe_speak(text='测试')    -> {ok, elapsed_ms, bytes, error, ...}
                                   真实合成 + 真实播放一次（会出声）
    ensure_audible(engine=None) -> {ok, was_silent, cleared, ...}
                                   若 agent/silent 为真则写回 false 并 sync

命令行自检：
    python -m core.voice_health
"""
import json
import os
import subprocess
import sys
import tempfile
import threading
import time

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

_ORG = 'GugaPenguinPet'
_APP = 'GugaPenguin'
_SILENT_KEY = 'agent/silent'

_MIN_PROBE_BYTES = 1000       # 低于这个字节数视为合成异常（"测试"约 10-17KB）
_PLAY_TIMEOUT_MS = 15000      # 单次探针播放最长等待
_PROBE_PREFIX = 'guga_tts_probe_'
_PROBE_TIMEOUT_S = 70         # 子进程探针总预算（edge 20s + 豆包 30s + 播放 15s 的余量）
_PROBE_SENTINEL = '@@VOICE_HEALTH_PROBE@@'

_QT_PREPARED = False
_QT_ERROR = ''
_ENGINE = None                # 本模块自建引擎（仅在找不到运行中的桌宠时使用）
_LAST_ERROR = ''              # 本模块层面的最近错误（Qt/引擎初始化等）


# ---------------------------------------------------------------- Qt 准备

def _prepare_qt():
    """在导入 PyQt5 之前调用 launcher._prepare_qt()；失败不抛异常。"""
    global _QT_PREPARED, _QT_ERROR
    if _QT_PREPARED:
        return True
    try:
        import launcher
        _QT_PREPARED = bool(launcher._prepare_qt())
        if not _QT_PREPARED:
            _QT_ERROR = 'launcher._prepare_qt() 未找到 Qt 插件目录'
    except Exception as e:  # 没装/路径不对：不硬崩，让调用方看到显式错误
        _QT_ERROR = 'launcher 导入失败: %s' % e
    return _QT_PREPARED


_prepare_qt()


def _ensure_app():
    """返回（必要时创建）QApplication。未准备 Qt 时拒绝创建，避免 0xC0000409。"""
    if threading.current_thread() is not threading.main_thread():
        raise RuntimeError('QApplication 只能在主线程创建')
    from PyQt5.QtWidgets import QApplication
    app = QApplication.instance()
    if app is not None:
        return app
    if not _QT_PREPARED:
        raise RuntimeError('Qt 未准备（launcher._prepare_qt() 失败）: %s'
                           % (_QT_ERROR or '未知原因'))
    return QApplication([])


# ---------------------------------------------------------------- 静默开关

def _settings():
    from PyQt5.QtCore import QSettings
    return QSettings(_ORG, _APP)


def _read_silent():
    global _LAST_ERROR
    try:
        return bool(_settings().value(_SILENT_KEY, False, type=bool))
    except Exception as e:
        _LAST_ERROR = 'QSettings 读取失败: %s' % e
        return False


def _write_silent(value):
    """写 agent/silent 并 sync 到注册表；返回写后读回值。"""
    s = _settings()
    s.setValue(_SILENT_KEY, bool(value))
    s.sync()
    return bool(s.value(_SILENT_KEY, not bool(value), type=bool))


# ---------------------------------------------------------------- 对象定位

def _find_live_pet():
    """在同一个进程里找运行中的桌宠窗口（有 _tts 且能 set_silent）。"""
    try:
        from PyQt5.QtWidgets import QApplication
        app = QApplication.instance()
        if app is None:
            return None
        seen = []
        try:
            seen.extend(app.topLevelWidgets())
        except Exception:
            pass
        try:
            for widget in app.allWidgets():
                if widget not in seen:
                    seen.append(widget)
        except Exception:
            pass
        for widget in seen:
            if hasattr(widget, '_tts') and hasattr(widget, 'set_silent'):
                return widget
    except Exception:
        pass
    return None


def _engine(create=True):
    """优先复用运行中桌宠的引擎；没有才自建一个（脚本/探针场景）。"""
    global _ENGINE, _LAST_ERROR
    live = _find_live_pet()
    if live is not None:
        engine = getattr(live, '_tts', None)
        if engine is not None:
            return engine
    if _ENGINE is not None:
        return _ENGINE
    if not create:
        return None
    try:
        _ensure_app()
        from ui.tts_engine import TTSEngine
        # 自建引擎时不要跑启动清理：那会删掉运行中桌宠正在播放的临时 mp3
        original = TTSEngine._cleanup_stale_tmp
        TTSEngine._cleanup_stale_tmp = lambda self: None
        try:
            _ENGINE = TTSEngine()
        finally:
            TTSEngine._cleanup_stale_tmp = original
        return _ENGINE
    except Exception as e:
        _LAST_ERROR = 'TTSEngine 初始化失败: %s' % e
        return None


# ---------------------------------------------------------------- 能力探测

def _edge_available():
    """edge-tts 可导入且传输端点已指向可达主机（不做真实合成，那是 probe 的事）。"""
    global _LAST_ERROR
    try:
        import edge_tts  # noqa: F401
        from ui.tts_engine import configure_edge_transport
        configure_edge_transport()
        return True
    except Exception as e:
        _LAST_ERROR = 'edge-tts 不可用: %s' % e
        return False


def _doubao_available(cfg=None):
    try:
        if cfg is None:
            from core.model_settings import read_raw
            cfg = read_raw()
        if not str(cfg.get('doubao_tts_api_key') or '').strip():
            return False
        import requests  # noqa: F401
        return True
    except Exception:
        return False


def _playback_available():
    global _LAST_ERROR
    try:
        _ensure_app()
        from PyQt5.QtMultimedia import QMediaPlayer
        return bool(QMediaPlayer().isAvailable())
    except Exception as e:
        _LAST_ERROR = '播放后端不可用: %s' % e
        return False


def _read_cfg():
    try:
        from core.model_settings import read_raw
        return read_raw()
    except Exception:
        return {}


# ---------------------------------------------------------------- check

def check(engine=None):
    """快速自检：不发声、不联网，读配置 + 探测库/播放后端。

    silent 为"下一次 speak 会不会被拦"的真实判断：
    运行中的桌宠以它的内存 _silent 为准（注册表只在启动时读取），
    独立进程则以注册表为准。
    """
    global _LAST_ERROR
    if engine is None:
        engine = _engine(create=True)
    cfg = _read_cfg()
    registry_silent = _read_silent()
    pet = _find_live_pet()

    if pet is not None:
        silent = bool(getattr(pet, '_silent', registry_silent))
    else:
        silent = bool(registry_silent or getattr(engine, 'silent', False))

    detail = {
        'registry_silent': registry_silent,
        'live_pet_found': pet is not None,
        'engine_available': engine is not None,
        'doubao_key_configured': bool(str(cfg.get('doubao_tts_api_key') or '').strip()),
    }

    if engine is not None:
        snap = engine.self_check()
        edge_ok = bool(snap.get('edge_ok'))
        doubao_ok = bool(snap.get('doubao_ok'))
        playback_ok = bool(snap.get('playback_ok'))
        last_error = str(snap.get('last_error') or '')
        provider = str(snap.get('provider') or 'edge')
        voice = str(snap.get('voice') or '')
        detail.update({
            'edge_host': snap.get('edge_host') or '',
            'edge_error': snap.get('edge_error') or '',
            'playback_error': snap.get('playback_error') or '',
            'last_provider': snap.get('last_provider') or '',
            'last_bytes': snap.get('last_bytes') or 0,
            'is_speaking': snap.get('is_speaking'),
        })
    else:
        provider = str(cfg.get('tts_provider') or 'edge')
        edge_ok = _edge_available()
        doubao_ok = _doubao_available(cfg)
        playback_ok = _playback_available()
        last_error = _LAST_ERROR
        voice = ''

    if not voice:
        voice = (cfg.get('edge_voice') or 'zh-CN-XiaoyiNeural') \
            if provider != 'doubao' else 'zh_female_mizai_saturn_bigtts'
    if silent and not last_error:
        last_error = 'silent=true（agent/silent 开启，本机朗读被静默）'

    ok = (not silent) and playback_ok and (edge_ok or doubao_ok)
    return {
        'ok': bool(ok),
        'silent': bool(silent),
        'edge_ok': bool(edge_ok),
        'doubao_ok': bool(doubao_ok),
        'playback_ok': bool(playback_ok),
        'last_error': last_error,
        'provider': provider,
        'voice': voice,
        'detail': detail,
    }


# ---------------------------------------------------------------- ensure_audible

def ensure_audible(engine=None):
    """若 agent/silent 为真则写回 false 并 sync；同时尽力同步运行中的桌宠。

    注册表只在桌宠启动时被读取（ui/pet_window.py），所以对**已经在运行**的进程
    还必须调用它的 set_silent(False) 才能立刻出声；跨进程时只能等重启。
    """
    was_silent = _read_silent()
    cleared = False
    errors = []
    if was_silent:
        try:
            _write_silent(False)
        except Exception as e:
            errors.append('写 QSettings 失败: %s' % e)
        cleared = not _read_silent()

    pet = _find_live_pet()
    if engine is None and pet is not None:
        engine = getattr(pet, '_tts', None)

    if pet is not None and bool(getattr(pet, '_silent', False)):
        try:
            pet.set_silent(False)  # 内部会写 QSettings、更新菜单并提示"声音回来了"
        except Exception as e:
            errors.append('同步桌宠失败: %s' % e)
    if engine is not None and bool(getattr(engine, 'silent', False)):
        try:
            engine.set_silent(False)
        except Exception as e:
            errors.append('同步 TTSEngine 失败: %s' % e)

    silent_now = _read_silent()
    engine_silent = bool(getattr(engine, 'silent', False)) if engine is not None else False
    return {
        'ok': bool((not silent_now) and (not engine_silent)),
        'was_silent': bool(was_silent),
        'cleared': bool(cleared),
        'silent': bool(silent_now),
        'engine_silent': engine_silent,
        'live_pet_found': pet is not None,
        'error': '; '.join(errors),
    }


# ---------------------------------------------------------------- probe_speak

def _play_file(path, volume=80, player=None):
    """真实播放一个 mp3 文件，等到 EndOfMedia/Stopped/错误/超时。

    返回 {played, ended, position_ms, player_error, error}。
    Playing(1) 且 position 推进才算真的过了一遍播放管线。

    注意：同一进程里不要同时存在两个 QMediaPlayer —— 引擎的播放器 + 新建的
    第二个播放器会让 Qt5 的 DirectShow 后端死锁（连 QTimer 都不触发）。
    所以探针优先复用引擎自带的 _player。
    """
    out = {'played': False, 'ended': False, 'position_ms': 0,
           'player_error': 0, 'error': ''}
    _ensure_app()
    from PyQt5.QtCore import QEventLoop, QTimer, QUrl
    from PyQt5.QtMultimedia import QMediaContent, QMediaPlayer

    if player is None:
        player = QMediaPlayer()
        player.setVolume(int(volume))
    loop = QEventLoop()
    timer = QTimer()
    timer.setSingleShot(True)
    timer.timeout.connect(loop.quit)

    def on_state(state):
        if state == QMediaPlayer.PlayingState:
            out['played'] = True
        elif state == QMediaPlayer.StoppedState and out['played']:
            loop.quit()

    def on_position(position):
        if position > out['position_ms']:
            out['position_ms'] = int(position)

    def on_media(status):
        if status == QMediaPlayer.EndOfMedia:
            out['ended'] = True
            loop.quit()
        elif status == QMediaPlayer.InvalidMedia:
            out['error'] = '媒体无效（Qt 无法解码该 mp3）'
            loop.quit()

    def on_error(error):
        code = int(error)
        if code:
            out['player_error'] = code
            loop.quit()

    player.stateChanged.connect(on_state)
    player.positionChanged.connect(on_position)
    player.mediaStatusChanged.connect(on_media)
    player.error.connect(on_error)
    try:
        player.setMedia(QMediaContent(QUrl.fromLocalFile(path)))
        timer.start(_PLAY_TIMEOUT_MS)
        player.play()
        loop.exec_()
    finally:
        timer.stop()
        try:
            player.stop()
        except Exception:
            pass
        for signal, slot in ((player.stateChanged, on_state),
                             (player.positionChanged, on_position),
                             (player.mediaStatusChanged, on_media),
                             (player.error, on_error)):
            try:
                signal.disconnect(slot)
            except Exception:
                pass

    if not out['error']:
        if not out['played']:
            out['error'] = '播放未进入 Playing 状态'
        elif out['player_error']:
            out['error'] = '播放器错误码 %s' % out['player_error']
        elif not (out['ended'] or out['position_ms'] > 0):
            out['error'] = '播放没有推进（position=0）'
    return out


def probe_speak(text='测试'):
    """真实合成 + 真实播放一次，返回 {ok, elapsed_ms, bytes, error, ...}。

    为什么用子进程：Qt5 的 DirectShow 后端在个别状态下会硬卡在 C++ 层
    （连 QTimer 都不触发，Python 侧无法自救）。把真实合成+播放放进带超时的
    子进程后，探针一定能返回；父进程最多等 _PROBE_TIMEOUT_S 秒。
    """
    started = time.perf_counter()
    child = _run_probe_child(text)
    if child is None:  # 冻结包/无法起子进程：退化为进程内探针
        result = _probe_inprocess(text)
        result['mode'] = 'inprocess'
        return result
    child['elapsed_ms'] = int((time.perf_counter() - started) * 1000)
    child['mode'] = 'subprocess'
    child['silent'] = _read_silent()
    if child['silent']:
        child['warning'] = ('agent/silent=true，链路正常也听不到；'
                            '调用 ensure_audible() 恢复')
    return child


def _probe_child_exe():
    """子进程用 python.exe：pythonw.exe 的 stdio 在管道下不可靠。"""
    exe = sys.executable or ''
    if exe.lower().endswith('pythonw.exe'):
        candidate = exe[:-len('pythonw.exe')] + 'python.exe'
        if os.path.exists(candidate):
            return candidate
    return exe


def _run_probe_child(text):
    """拉起子进程做真实合成+播放；超时或起不来时返回 None（调用方退化）。"""
    if getattr(sys, 'frozen', False):
        return None
    exe = _probe_child_exe()
    if not exe:
        return None
    cmd = [exe, '-u', '-m', 'core.voice_health', '--probe-child',
           str(text or '测试')]
    try:
        proc = subprocess.run(cmd, cwd=_ROOT, capture_output=True, text=True,
                              encoding='utf-8', errors='replace',
                              timeout=_PROBE_TIMEOUT_S)
    except subprocess.TimeoutExpired:
        return {
            'ok': False, 'bytes': 0, 'provider': '', 'played': False,
            'ended': False, 'position_ms': 0, 'player_error': 0,
            'error': '探针子进程超时（%ss）已终止，Qt 多媒体后端无响应'
                     % _PROBE_TIMEOUT_S,
        }
    except Exception:
        return None
    payload = None
    for line in (proc.stdout or '').splitlines():
        line = line.strip()
        if line.startswith(_PROBE_SENTINEL):
            try:
                payload = json.loads(line[len(_PROBE_SENTINEL):])
            except ValueError:
                payload = None
    if payload is None:
        stderr_lines = (proc.stderr or '').strip().splitlines()
        return {
            'ok': False, 'bytes': 0, 'provider': '', 'played': False,
            'ended': False, 'position_ms': 0, 'player_error': 0,
            'error': '探针子进程无结果（rc=%s）%s' % (
                proc.returncode,
                (': ' + stderr_lines[-1][:200]) if stderr_lines else ''),
        }
    return payload


def _probe_inprocess(text='测试'):
    """进程内真实合成 + 真实播放（子进程模式的实现体，也作兜底）。

    - 合成走 TTSEngine 的真实方法：provider=edge 时先 edge，失败自动降级豆包。
    - 播放走真实 QMediaPlayer（与生产同一个 Qt 多媒体后端），会真的出声。
    - 本探针刻意绕过 speak() 的 silent 闸门（否则静默时测不了链路），
      silent 状态在结果里单独报告。
    """
    started = time.perf_counter()
    result = {
        'ok': False, 'elapsed_ms': 0, 'bytes': 0, 'error': '',
        'provider': '', 'played': False, 'ended': False,
        'position_ms': 0, 'player_error': 0,
        'silent': _read_silent(), 'warning': '',
    }
    path = ''
    engine = _engine(create=True)
    try:
        if engine is None:
            result['error'] = _LAST_ERROR or 'TTSEngine 初始化失败（Qt/依赖不可用）'
        else:
            try:
                engine.reload_settings()
            except Exception:
                pass
            from ui.tts_engine import strip_punct
            spoken = strip_punct(str(text or '').strip()) or '测试'

            handle = tempfile.NamedTemporaryFile(
                prefix=_PROBE_PREFIX, suffix='.mp3', delete=False)
            path = handle.name
            handle.close()

            provider = getattr(engine, 'provider', 'edge') or 'edge'
            used = ''
            synth_ok = False
            if getattr(engine, '_use_edge', False) and provider != 'doubao':
                synth_ok = bool(engine._synth_edge(spoken, path))
                if synth_ok:
                    used = 'edge'
            if not synth_ok and getattr(engine, '_use_doubao', False):
                synth_ok = bool(engine._synth_doubao(spoken, path))
                if synth_ok:
                    used = 'doubao'
            result['provider'] = used
            size = os.path.getsize(path) if os.path.exists(path) else 0
            result['bytes'] = size

            if not synth_ok:
                result['error'] = (getattr(engine, 'last_error', '')
                                   or '合成失败（edge 与豆包均不可用）')
            elif size < _MIN_PROBE_BYTES:
                result['error'] = '合成音频过短: %s 字节' % size
            else:
                # 复用引擎的播放器：进程里只保留一个 QMediaPlayer，避免 DirectShow 死锁
                play = _play_file(path, player=getattr(engine, '_player', None))
                for key in ('played', 'ended', 'position_ms', 'player_error'):
                    result[key] = play[key]
                result['error'] = play['error']
                result['ok'] = not play['error']
    except Exception as e:
        result['error'] = '%s: %s' % (type(e).__name__, e)
    finally:
        if path:
            try:
                if os.path.exists(path):
                    os.remove(path)
            except Exception:
                pass
        result['elapsed_ms'] = int((time.perf_counter() - started) * 1000)

    if result['silent']:
        result['warning'] = ('agent/silent=true，链路正常也听不到；'
                             '调用 ensure_audible() 恢复')
    return result


# ---------------------------------------------------------------- 命令行自检

if __name__ == '__main__':
    if '--probe-child' in sys.argv:
        # 子进程模式：只做真实合成+播放，把结果用哨兵前缀打给父进程
        index = sys.argv.index('--probe-child')
        child_text = sys.argv[index + 1] if len(sys.argv) > index + 1 else '测试'
        child_result = _probe_inprocess(child_text)
        print(_PROBE_SENTINEL + json.dumps(child_result, ensure_ascii=False),
              flush=True)
        sys.exit(0 if child_result.get('ok') else 1)
    report = {
        'check_before': check(),
        'ensure_audible': ensure_audible(),
        'check_after': check(),
        'probe_speak': probe_speak(),
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    ok = report['probe_speak'].get('ok') and not report['check_after'].get('silent')
    sys.exit(0 if ok else 1)
