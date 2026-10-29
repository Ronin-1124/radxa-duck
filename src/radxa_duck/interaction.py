"""Small, validated interaction decisions; no arbitrary device or shell calls."""

import json
import re
from urllib.parse import urlsplit

from .cloud_client import CloudClientError


SOUNDS = {
    "greet": "问候、见面", "chirp": "简短肯定、愉快回应", "coo": "温柔、安慰、陪伴",
    "inquire": "疑问、困惑、需要澄清", "peck": "好奇、活泼", "alarm": "表达警觉；不是硬件报警系统",
}


def image_support(config):
    # Provider declarations are deliberately narrow. Unknown models are not
    # assumed to accept (or actually use) images just because HTTP succeeds.
    return (urlsplit(config.get("base_url", "")).hostname == "api.deepseek.com"
            and config.get("model") in {"deepseek-flash", "deepseek-v4-flash-vision-exp"})


def requests_image(text):
    """Conservative one-turn consent gate, in addition to the semantic decision.

    No model can opt in on its own or reuse consent from previous history.
    Negations fail closed; the user can rephrase a positive one-shot request.
    """
    if re.search(r"不要|别|不许|禁止|不用|无需|不想|do\s+not|don't|without|never", text, re.I):
        return False
    return bool(re.search(r"看看|看一下|看一眼|这是什么|拍[一张个]*照|你(?:能)?看[到见]|"
                          r"(?:看|观察|识别|描述|分析)(?:一下|一眼)?(?:我|这|前面|周围|眼前|面前|摄像头|镜头|画面)|"
                          r"(?:眼前|面前|周围|画面).{0,12}(?:有什么|是什么|看到什么)|"
                          r"\b(?:look (?:at|around|here)|(?:take|describe|inspect|analy[sz]e).{0,20}(?:photo|picture|scene)|"
                          r"what (?:is|are) (?:this|these)|what.{0,20}see)\b", text, re.I))


def decision_prompt(*, can_look, has_image):
    return ("\n本轮以官方 Microduck 鸭叫表达情绪，不使用 TTS。必须只输出 JSON 对象，恰好包含 reply、sound、look 三个字段。"
            "reply 为最多120字的自然语言文字答复，sound 从下列声音中按语义选一个，用户要求安静时选 none。"
            "look 只有在用户本轮明确请求观察当前画面、can_look=true 且尚未收到图片时才能为 true。"
            "本地检测标签不是完整图像理解；没看到图片不能编造颜色、文字或场景细节。"
            "look=true 时 reply 只表示准备查看，不要提前回答画面内容；收到图片后必须 look=false。"
            "未收到板端成功反馈前，不能声称播放或身体动作已完成，实际执行结果由板端报告。"
            + json.dumps({"sounds": SOUNDS, "none": "保持安静", "can_look": can_look,
                          "has_image": has_image}, ensure_ascii=False)
            + '\nJSON 示例：{"reply":"你好呀！","sound":"greet","look":false}')


def parse_decision(raw, *, can_look):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError()
            result[key] = value
        return result
    try:
        value = json.loads(raw, object_pairs_hook=unique)
        if (not isinstance(value, dict) or set(value) != {"reply", "sound", "look"}
                or not isinstance(value["reply"], str) or not 1 <= len(value["reply"].strip()) <= 1200
                or any(ord(c) < 32 and c not in "\n\t" or 0xD800 <= ord(c) <= 0xDFFF for c in value["reply"])
                or not isinstance(value["sound"], str) or value["sound"] not in (*SOUNDS, "none")
                or type(value["look"]) is not bool or value["look"] and not can_look):
            raise ValueError()
        return value
    except (ValueError, TypeError, RecursionError):
        raise CloudClientError("invalid_response") from None
