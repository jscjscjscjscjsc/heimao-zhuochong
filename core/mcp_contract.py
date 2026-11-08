"""Shared self-MCP schema and pipe identity, without importing UI or credentials."""
import getpass
import hashlib
import os,re
from pathlib import Path


def pipe_name():
    override=os.environ.get('GUGA_MCP_PIPE','')
    if override and re.fullmatch(r'guga-self-[a-zA-Z0-9-]{1,60}',override):return override
    source = str(Path(__file__).resolve().parents[1]).lower() + getpass.getuser()
    return 'guga-self-' + hashlib.sha256(source.encode('utf-8')).hexdigest()[:20]


def self_tools():
    from core.self_agent import TOOLS
    result=[dict(name=t['function']['name'], description=t['function']['description'],
                 inputSchema=t['function']['parameters'])
            for t in TOOLS if t['function']['name'] in ('app_state', 'pet_control', 'workspace', 'applications', 'computer', 'wechat')]
    result.append(dict(name='voice_session',description='控制咕嘎自己的实时语音会话；start受当前免费额度保护约束，status查看真实状态，stop结束通话；不自动重试。',
        inputSchema=dict(type='object',properties=dict(action=dict(type='string',enum=['start','status','stop'])),
                         required=['action'],additionalProperties=False)))
    result.append(dict(name='screen_share',description='长语音通话默认启用的辅助屏幕上下文。start仅用于当前长通话，2—5帧/秒本地采集，status/context/snapshot/stop控制；不显示预览、不保存录制。',
        inputSchema=dict(type='object',properties=dict(action=dict(type='string',enum=['start','status','stop','context','assist','snapshot']),
            fps=dict(type='integer'),max_age=dict(type='integer')),required=['action'],additionalProperties=False)))
    result.append(dict(name='pet_say',description='让桌宠在气泡里直接显示一段文字，不调用模型、不写入对话历史、不发声。用于外部系统（如 DSH 插件）把一句话推上桌面。',
        inputSchema=dict(type='object',properties=dict(text=dict(type='string',description='要显示的文字，最多1200字')),
                         required=['text'],additionalProperties=False)))
    return result


def dsh_tools():
    from core.self_agent import TOOLS
    allowed={'app_state','pet_control','workspace','applications','computer','local_computer','screen_share',
             'browser','wechat','web_search','task_plan','music_performance'}
    result=[dict(name=t['function']['name'],description=t['function']['description'],inputSchema=t['function']['parameters'])
            for t in TOOLS if t['function']['name'] in allowed]
    result.extend([
        dict(name='voice_session',description='控制咕嘎自己的实时语音会话，不用电脑UI。start只在当前配置的免费额度保护通过时连接当前模型；status查看真实状态，stop结束通话。最多一段会话，不自动重试。',
             inputSchema=dict(type='object',properties=dict(action=dict(type='string',enum=['start','status','stop'])),
                              required=['action'],additionalProperties=False)),
        dict(name='media_workflow',description='本地视频处理：检查、抽帧、中文字幕转写、烧录底部进度条和字幕、生成投稿清单。原文件不覆盖，开始处理不等于完成，wait有限等待；没有发布功能。',
             inputSchema=dict(type='object',properties=dict(action=dict(type='string',enum=['inspect','extract_frames','transcribe','render','status','wait','stop','submission']),
                 path=dict(type='string'),subtitles=dict(type='string'),job_id=dict(type='string'),seconds=dict(type='integer'),
                 title=dict(type='string'),description=dict(type='string')),required=['action'],additionalProperties=False)),
        dict(name='personal_memory',description='用户的持久记忆。只保存明确偏好和可核对事实，不存凭据、隐私或推测。经验候选不自动启用。',
             inputSchema=dict(type='object',properties=dict(action=dict(type='string',enum=['recall','list','remember','forget','propose_lesson','lessons']),
                 query=dict(type='string'),content=dict(type='string'),id=dict(type='integer')),required=['action'],additionalProperties=False)),
        dict(name='task_report',description='回报真实进展或交付；ready只是待验收，不代表独立验证成功。',
             inputSchema=dict(type='object',properties=dict(state=dict(type='string',enum=['progress','ready','blocked','failed','needs_input']),
                 summary=dict(type='string'),milestone=dict(type='boolean')),required=['state','summary'],additionalProperties=False))])
    return result
