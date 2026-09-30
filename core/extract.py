"""
core/extract.py —— M3-2 核心：纯函数，文本进 → 字段字典 + 元信息出
设计要点（卡里要求）：
  · 输入只能是纯文本字符串：不读文件、不读界面、不调 parse.py
  · 输出含「字段抽取结果 + 元信息（成功没 / 失败原因 / token 用量）」
  · 失败有明确原因，绝不停默返回 None（卡里 §3.2）
  · core 层无 print（打印是壳层 try_extract.py 的事）
"""
import os
import json
import time
import requests
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()                       # 把 .env 里的 DEEPSEEK_API_KEY 注入环境变量（M3-5 安全死线：Key 不写死）

API_URL = "https://api.deepseek.com/v1/chat/completions"
# Key 只从环境变量读，绝不写死在代码里（M3-5 Step 8 安全死线）
API_KEY = os.environ.get("DEEPSEEK_API_KEY")
# prompt 从独立文件加载（M3-2 Step 3：核心资产单独版本管理、能 diff）
PROMPT_PATH = Path(__file__).parent.parent / "prompts" / "extract_v1.md"

# 默认字段清单——与你的 README / _fields.md 的 13 个字段名完全一致（卡里 §3.1）
DEFAULT_FIELDS = ["项目名称", "项目地点", "项目规模", "总投资", "建设期",
                  "建设单位", "设计单位", "施工单位", "监理单位", "编制单位",
                  "文件日期", "资料类型", "关键摘要"]

# 字段期望类型——与 DEFAULT_FIELDS 一一对应，供 _run10.py 稳定性测试做类型校验。
# 绝大多数字段是文本（str）；"总投资"是金额，应当是数值（int/float）。
# 注意：AI 常把总投资抽成字符串 "15800"，这时 isinstance 判 ❌，正好暴露「类型被带偏」的抖动。
FIELD_TYPES = {
    "项目名称": str, "项目地点": str, "项目规模": str,
    "总投资": (int, float), "建设期": str,
    "建设单位": str, "设计单位": str, "施工单位": str, "监理单位": str,
    "编制单位": str, "文件日期": str, "资料类型": str, "关键摘要": str,
}

RETRYABLE = {429, 500, 502, 503}   # 可重试状态码（M2-06）
MAX_RETRY = 3                       # 指数退避上限


def load_prompt() -> str:
    """读取 prompts/extract_v1.md，作为 system 指令。"""
    return PROMPT_PATH.read_text(encoding="utf-8")


def call_llm(text: str):
    """
    真正打 API 的唯一一层（唯一会碰网络的地方）。
    返回三元组 (ok, payload_or_error, usage)：
      ok=True  → payload 是模型返回的原文字符串
      ok=False → payload 是失败原因字符串
    """
    if not API_KEY:
        return False, "缺少 DEEPSEEK_API_KEY 环境变量", None

    system_prompt = load_prompt()
    # 拼请求体：关思考 + 压温度 + 强制 json_object + 必含 'json' 字样
    body = {
        "model": "deepseek-chat",
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": f"请从下面这段工程资料原文提取字段，只输出 JSON：\n\n{text}"},
        ],
        "response_format": {"type": "json_object"},   # API 层防线①：约束语法合法
        "temperature": 0.1,                            # 压低温度，稳定一致（M3-2 Step 5）
        "max_tokens": 4096,                            # 放得下完整 JSON（坑 6：太小会被截断）
        "thinking": {"type": "disabled"},              # 先关思考，否则 temperature 被吞且不报错
    }

    last_err = ""
    for attempt in range(1, MAX_RETRY + 1):
        try:
            resp = requests.post(API_URL, json=body,
                                 headers={"Authorization": f"Bearer {API_KEY}"},
                                 timeout=60)
            if resp.status_code in RETRYABLE:          # 429/5xx 退避重试
                last_err = f"HTTP {resp.status_code}（可重试）"
                time.sleep(2 ** attempt)               # 指数退避：1s,2s,4s
                continue
            if resp.status_code == 401:                # 401 不重试，直接判失败
                return False, "API Key 失效（401）", None
            resp.raise_for_status()                    # 其他异常抛出来统一处理
            data = resp.json()
            usage = data.get("usage")                  # 输入/输出 token
            content = data["choices"][0]["message"]["content"]
            return True, content, usage
        except requests.RequestException as e:
            last_err = f"网络异常：{e}"
            time.sleep(2 ** attempt)
    return False, f"重试 {MAX_RETRY} 次仍失败：{last_err}", None


def _strip_code_block(s: str) -> str:
    """客户端层防线③：剥掉 ```json ``` 包裹（M3-2 Step 7 的常见半合法情况）。"""
    s = s.strip()
    if s.startswith("```"):
        s = s.split("\n", 1)[1] if "\n" in s else s[3:]   # 去掉首行 ```json
        if s.endswith("```"):
            s = s[:-3]                                    # 去掉末行 ```
    return s.strip()


def extract(text: str) -> dict:
    """
    纯函数入口：文本 → 结果 dict。
    返回结构固定：
      {"ok": bool, "fields": dict|None, "error": str|None, "usage": dict|None}
    """
    ok, payload, usage = call_llm(text)
    if not ok:
        # 失败有明确原因，不是静默 None（卡里 §3.2）
        return {"ok": False, "fields": None, "error": payload, "usage": None}

    # 客户端层防线③：json.loads 包 try/except，失败把 AI 原文打出来留痕迹
    try:
        raw = _strip_code_block(payload)
        fields = json.loads(raw)
    except json.JSONDecodeError as e:
        return {
            "ok": False,
            "fields": None,
            "error": f"JSON 解析失败：{e}；AI 原文前 200 字：{payload[:200]}",
            "usage": usage,
        }

    # 字段校验：AI 多给/少给都要标出来（坑 8：不许顺手收下、不许漏记）
    extra = [k for k in fields if k not in DEFAULT_FIELDS]
    missing = [k for k in DEFAULT_FIELDS if k not in fields]
    if extra:
        fields["_warning_extra"] = extra        # 多给的标记，不偷偷收
    if missing:
        fields["_warning_missing"] = missing    # 少给的标记，评测要算进去

    return {"ok": True, "fields": fields, "error": None, "usage": usage}