"""Shared conversation policy; no desktop actions or model-private reasoning."""
import re
from datetime import datetime
from difflib import SequenceMatcher

ANSWER_STYLE = '''你的定位是有个性的管家和陪伴型智能体。像熟悉用户的朋友一样自然说中文，先回答最有用的一点，再按需要解释。
优先用大白话和生活例子解释，让没有专业背景的人也能听懂。必要的专业词第一次出现时随口解释含义，不堆术语，不把简单问题讲复杂。
日常关注用户的习惯、计划和感受，按用户需要协助生活和电脑事务；代码改造只是按需调用的能力，不要把聊天导向编程。
记忆资料有明确范围：用户长期信息跨对话、项目记忆限本项目、对话记忆限当前对话。日常安排与打卡以实时计划为准，不能把旧聊天、一次性状态或你的推测当成长期事实。
闲聊就用短句和自然段，不机械列提纲，不反复称呼或自我介绍，不用舞台说明。凡是知识讲解、操作步骤、方案对比、代码这类内容，把结构排清楚：小标题、要点、表格、代码块，让人一眼看懂。
代码必须能直接复制运行：写全 import 和用到的函数定义，不要省略号、不要伪代码、不要“略”；每段代码前用一句大白话说清它解决什么，后面说怎么运行或验证。代码块必须带语言标签（如 python），面向初学者的代码用中文注释。
问答等待时可偶尔说“让本鹅想一想”或“这道问题倒是有点意思”，不要每次重复；执行任务时不套这类问答口头禅。
记忆只用用户明确说过的信息；自然结合喜好和职业背景，不生硬地复述档案。
联网资料只作参考，明确区分搜索摘要、确认的事实和自己的建议；没有搜索结果不能声称查到了。
后台执行与本轮闲聊独立。用户聊天时正常回应，不把闲聊追加成执行要求，不承诺后台已经完成。
只给结论、简明依据与可理解的下一步，不输出私有思维链。'''


def current_time_instructions(clock=None):
    """Give every dialogue path the actual local date instead of model memory."""
    now=(clock() if callable(clock) else datetime.now().astimezone())
    weekdays='一二三四五六日'
    offset=now.strftime('%z')
    zone=f'UTC{offset[:3]}:{offset[3:]}' if offset else '本地时区'
    return ('\n【当前现实时间】%d年%d月%d日，星期%s，%02d:%02d（%s）。'
            '回答日期、星期和“今天/明天/最新”问题时以此为准；模型训练时间不能替代当前时间。') % (
        now.year,now.month,now.day,weekdays[now.weekday()],now.hour,now.minute,zone)


def is_repetitive(text,recent,threshold=.62):
    """Cheap local repeat guard for autonomous conversation topics."""
    def grams(value):
        clean=''.join(ch.lower() for ch in str(value or '') if ch.isalnum() or '\u4e00'<=ch<='\u9fff')
        return {clean[i:i+2] for i in range(max(0,len(clean)-1))}
    current=grams(text)
    if len(current)<3:return False
    for value in recent:
        other=grams(value)
        if not other:continue
        score=len(current & other)/max(1,len(current | other))
        sequence=SequenceMatcher(None,str(text or ''),str(value or ''),autojunk=False).ratio()
        if score>=threshold or sequence>=threshold:return True
    return False


def question_opener(sequence=0, profile=None):
    if str((profile or {}).get('agent_id', '001')) != '001':
        return '让我想一想。'
    return ('让本鹅想一想。', '这道问题倒是有点意思。', '嗯，本鹅琢磨一下。')[sequence % 3]


def plain_answer(value):
    """Filter partial hidden blocks as well as completed ones before any UI emit."""
    text = str(value or '')
    text = re.sub(r'<(?:think|analysis)\b[^>]*>.*?(?:</(?:think|analysis)>|$)', '', text, flags=re.S | re.I)
    text = re.sub(r'^\s*[|｜]+(?=(?:normal|happy|sleepy|sad|surprised|playful)\s*[|｜])', '', text)
    text = re.sub(r'^\s*```[^\n]*\n?', '', text, flags=re.M)
    text = re.sub(r'^\s*```\s*$', '', text, flags=re.M)
    text = re.sub(r'^\s{0,3}#{1,6}\s+', '', text, flags=re.M)
    text = re.sub(r'\*\*(.+?)\*\*|__(.+?)__', lambda m: m.group(1) or m.group(2), text, flags=re.S)
    text = re.sub(r'`([^`\n]+)`', r'\1', text)
    return text.strip()


def display_answer(value):
    """Keep Markdown for the rich chat window; strip only hidden blocks/prefix."""
    text = str(value or '')
    text = re.sub(r'<(?:think|analysis)\b[^>]*>.*?(?:</(?:think|analysis)>|$)', '', text, flags=re.S | re.I)
    text = re.sub(r'^\s*[|｜]+(?=(?:normal|happy|sleepy|sad|surprised|playful)\s*[|｜])', '', text)
    return text.strip()


def speech_answer(value):
    text = str(value or '').split('\n参考来源：', 1)[0]
    text = re.sub(r'\[([^\]]+)\]\(https?://[^\s)]+\)', r'\1', text)
    text = re.sub(r'https?://\S+|\[\d+\]', '', text)
    return plain_answer(text)


def needs_web_search(prompt, image=False, mode='auto'):
    """Use public search for substantive Q&A, without uploading local memory.

    mode: 'auto' decides from the question, 'always' forces it for this turn,
    'never' skips it.  The chat shell owns that switch.
    """
    choice = str(mode or 'auto').lower()
    if choice in ('always', 'on', 'force', 'true', '1'):
        return True
    if choice in ('never', 'off', 'false', '0'):
        return False
    from core.agent_memory import safe_text
    text = str(prompt or '').strip()
    if image or not text or safe_text(text) != text:
        return False
    if re.fullmatch(r'(?:咕嘎[，, ]*)?(?:你好|您好|嗨|在吗|谢谢|好的|嗯|晚安|早安|你还在吗)[。！!？?～~ ]*', text):
        return False
    if any(x in text for x in ('我喜欢', '我不喜欢', '我的职业', '我的工作', '记得我', '你记得',
                               '陪我', '心情', '好累', '难过', '讲个笑话', '写一段', '润色', '翻译',
                               '你喜欢', '你是谁', '自我介绍', '你叫什么')):
        return False
    return bool(re.search(r'最新|新闻|实时|今天.*(?:发生|新闻|价格|天气)|现在.*(?:价格|天气|版本)|'
                          r'搜索|搜一下|查一下|查查|查找|查资料|联网|上网|天气|股价|汇率|赛程|政策|售价|购买|推荐|哪里买|'
                          r'谁是.{0,8}(?:总统|总理|CEO)|版本|更新|发布|多少钱|排名|'
                          r'20(?:2[4-9]|[3-9]\d)年|刚刚', text, re.I))


def is_task_followup(text):
    """Explicit edits to an ongoing job are steering, unrelated Q&A stays chat."""
    value=re.sub(r'^(?:请你?|麻烦你?|帮我|咕嘎[，, ]*)','',str(text).strip())
    return bool(re.search(r'^(?:补充|追加|改成|改为|加上|去掉|别用|不要用|继续|按刚才|还是按|先别|先不要|取消|停止)|'
                          r'(?:这个任务|当前任务|刚才的任务|那份报告|那个文件).{0,12}(?:改|加|用|删|保留)', value))


def is_task_status(text):
    return any(x in str(text) for x in ('任务进', '进度', '进展', '做到哪', '怎么样了', '在做什么',
        '在干什么', '卡住', '卡在', '还要多久', '完成了吗', '做完没', '当前任务', '汇报任务', '为什么失败'))
