# -*- coding: utf-8 -*-
"""验证桌宠帧率档：原生 24fps 片段在空闲时也必须按 30fps 跑，而不是掉到 10fps。

用法：python -X utf8 tools/probe_pet_framerate.py
（离屏跑，不会弹窗；只读资源、不写任何文件）
"""
import os
import sys
from pathlib import Path

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main():
    from PyQt5.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    from ui.sprite_animator import SpriteAnimator

    # 只验"帧率档"，把重型片段（舞蹈 1046×1504 等）和表情包跳掉，否则装载要好几分钟
    original_loader = SpriteAnimator._load_action_frames

    def limited_loader(self, name, frames_dir, fps=15.0, lazy=False, reference_width=None):
        if not str(name).startswith('idle:'):
            return
        return original_loader(self, name, frames_dir, fps, lazy, reference_width)

    SpriteAnimator._load_action_frames = limited_loader
    SpriteAnimator._load_stickers = lambda self, folder=None: None
    try:
        anim = SpriteAnimator(str(ROOT / 'resources'), display_width=260)
    finally:
        SpriteAnimator._load_action_frames = original_loader
    failures = []

    print('画布=%dx%d  扩边=%s' % (anim.width, anim.height, anim._resident_pad))
    print('活跃档=%dms (%.0ffps)  空闲档=%dms (%.0ffps)' % (
        anim._active_interval_ms, 1000.0 / anim._active_interval_ms,
        anim._idle_interval_ms, 1000.0 / anim._idle_interval_ms))

    frames_clips = {name: clip for name, clip in anim.clips.items() if clip.get('type') == 'frames'}
    print('原生序列帧片段 %d 个：' % len(frames_clips))
    for name, clip in sorted(frames_clips.items()):
        print('   %-22s frames=%-4s fps=%s' % (name, len(clip['frames']), clip.get('fps')))

    idle_names = [name for name, _ in anim.idle_motion_clips]
    print('常驻 idle 片段=%s' % idle_names)
    if not idle_names:
        failures.append('没有装上常驻 idle 原生片段（会退回 6fps 程序化呼吸）')

    # 1) 空闲 + 程序化呼吸轨 -> 允许 10fps 省电
    anim.play_clip('idle')
    anim._tick()
    idle_ms = anim._timer.interval()
    print('空闲呼吸轨 interval=%dms' % idle_ms)
    if idle_ms != anim._idle_interval_ms:
        failures.append('纯呼吸轨不应跑高帧率（现在 %dms）' % idle_ms)

    # 2) 空闲 + 原生 24fps 片段 -> 必须 30fps 档
    if idle_names:
        anim.play_clip('idle:' + idle_names[0], loop=True)
        anim._tick()
        native_ms = anim._timer.interval()
        print('原生 %s interval=%dms (%.0ffps)' % (
            idle_names[0], native_ms, 1000.0 / max(1, native_ms)))
        if native_ms != anim._active_interval_ms:
            failures.append('原生 24fps 片段在空闲时被降到 %dms' % native_ms)

        # 3) 帧推进是否按 fps 走（模拟 1 秒 tick）
        anim.play_clip('idle:' + idle_names[0], loop=True)
        anim._clip_frame = 0.0
        advanced = []
        for _ in range(10):
            anim._tick()
            advanced.append(anim._clip_frame)
        print('10 次 tick 后 clip_frame=%.2f（若固定 30fps 档应≈10/3）' % anim._clip_frame)
        if anim._clip_frame <= 0:
            failures.append('tick 没有推进帧')

    print()
    if failures:
        print('FAIL')
        for item in failures:
            print('  - ' + item)
        return 2
    print('PASS 原生片段按 30fps 跑，程序化呼吸轨才降到 10fps')
    return 0


if __name__ == '__main__':
    sys.exit(main())
