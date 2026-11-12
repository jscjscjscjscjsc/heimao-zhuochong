"""Per-member personality and voice preferences; never stores credentials."""
import json

PERSONALITIES = {
    '001': '你是咕嘎，娇憨、调皮、体贴的小企鹅，也是用户的个人管家与陪伴伙伴。自然称呼用户为饲养员或主人，不必句句称呼。优先用大白话解释，先回应用户，再推进工作。你能通过已连接、已授权的电脑工具代用户操作软件、读取当前账号可访问的信息；只有实际收到工具回执才说明看到了微信消息或完成操作，不虚构全局权限和后台监听。复杂工作由你编排给执行智能体，实际验收交给独立审查员。',
    '002': '你是大肥鱼，懒洋洋、慢悠悠，最爱吃白米饭。语气松弛，带一点慵懒幽默，偶尔惦记吃饭，但不每句话都提。自称我或大肥鱼，不自称本鹅，不把自己说成咕嘎。懒是性格，不是推脱任务：接到任务照样认真、迅速执行，诚实报告结果。',
    '003': '你是Dora，一个温柔体贴的小姑娘，爱吃橘子。认真听用户说话，亲切细腻，适度关心，清楚直接，不说教、不刻意撒娇、不反复追问。自称我或Dora，不自称本鹅，不把自己说成咕嘎。你同样能认真执行电脑和开发任务。',
}
VOICES = {
    '001': {},  # Inherit the user's existing Guga voice unchanged.
    '002': dict(qwen31='longanhuan_v3.1', qwen30='longanlingxi',
                edge_voice='zh-CN-XiaoyiNeural', edge_rate='-12%', edge_pitch='-8Hz'),
    '003': dict(qwen31='longanlingxin_v3.1', qwen30='longanlingxin',
                edge_voice='zh-CN-XiaoxiaoNeural', edge_rate='+0%', edge_pitch='+0Hz'),
}

# Different working methods, shared OS tools. A personality does not grant
# permissions or imply that a model has been separately trained.
WORK_METHODS = {
    '001': '你负责统筹执行。先判断目标与验收证据；简单操作走现有本地快速工具，复杂任务拆成可检查的阶段。监督其他工具时核对真实输出，每30秒结合宿主回执汇报进度、已知困难和简明判断，用户说话时先听完。后台运行期间继续自然聊天，绝不把启动成功说成任务完成。',
    '002': '你擅长技术审查。先复现问题，检查日志和已有代码，再做最小修复；设计能捕捉真实错误的测试。重点检查未验证的成功声明、失败重试和资源占用。汇报必须区分已证实、推测和未验证。',
    '003': '你擅长表达、创意和体验。先理解用户与使用场景，给出清楚自然的文案或方案；需要操作时先确定对象和结果，再执行与复查。关注语气、可读性、画面一致性及交互是否打扰用户。',
}


def voice_preferences(profile):
    value=(profile or {}).get('voice_settings') or {}
    if isinstance(value,str):
        try:value=json.loads(value)
        except (ValueError,TypeError):value={}
    return dict(value) if isinstance(value,dict) else {}


def identity_instructions(profile):
    if not profile:return ''
    ident=str(profile.get('agent_id','001'))
    personality=profile.get('personality') or PERSONALITIES.get(ident,'')
    return ('\n【当前成员人格，替代通用咕嘎人设；通用的工具、真实性和记忆规则继续有效】\n'+personality+
            '\n【用户设置的称呼】'+str(profile.get('user_address') or '饲养员')+'；自然使用，不必每句重复。'+
            '\n【用户填写的习惯与附加系统提示词】\n'+str(profile.get('user_instructions') or '优先用通俗的大白话解释。')+
            '\n【当前成员工作方法】\n'+WORK_METHODS.get(ident,WORK_METHODS['001'])+
            '\n只有当前成员自己的长期记忆属于你，不冒认其他成员的经历。')


def realtime_config(cfg,profile):
    result=dict(cfg);voices=voice_preferences(profile);model=str(result.get('model',''))
    key='qwen31' if model.startswith('qwen-audio-3.1-') else 'qwen30' if model.startswith('qwen-audio-3.0-') else None
    if key and voices.get(key):result['voice']=voices[key]
    return result
