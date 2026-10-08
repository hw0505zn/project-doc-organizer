"""
core/classify.py —— M3-4 核心：给 M3-2 的结果 JSON 追加「资料类型 / 关键摘要 / 标签」
禁令（§3.5）：不碰解析(M3-1) / 不碰字段提取(M3-2) / 不碰批量(M3-3) / 不 print
"""
import os
import sys
import json
import re
import time
import requests
from dotenv import load_dotenv
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
load_dotenv()                    # 把 .env 里的 DEEPSEEK_API_KEY 注入环境变量
                                 # ★M3-5 补：单独跑 `python core/classify.py` 时也要能拿到 key
                                 #   （走 batch.py 不用它：batch 会先 import extract，那里已经 load 过一次）
API_KEY = os.environ.get("DEEPSEEK_API_KEY")
PROMPT_DIR = Path(__file__).parent.parent / "prompts"

# 受约束枚举：必须与 M3-0/M3-4 分类标准完全一致（卡里 §3.1）
CATEGORIES = ["合同", "可研报告", "初步设计", "会议纪要", "招标文件", "设计说明", "其他"]

# ── Step 8 摘要闸门配置 ──────────────────────────────
# 字数上限：必须与 prompts/summary_v1.md 里写死的「不超过 120 字」保持一致，否则闸门白装
MAX_SUMMARY_LEN = 120
# 摘要要素：key 必须是 _fields.md 字段清单里【实际的字段名】
# （对应 prompt 里的「项目名 / 金额 / 时间」，至少命中 2 项）
REQUIRED_ELEMENTS = ["项目名称", "总投资", "建设期", "文件日期"]

# Step 5 的结论：选 "one"（一次调用全要）或 "three"（分三次，各专注）
MODE = "three"

CN_NUM = {"零":0,"一":1,"二":2,"三":3,"四":4,"五":5,"六":6,"七":7,"八":8,"九":9,"〇":0,"两":2}


def load_prompt(name):
    """读取 prompts/ 下对应 prompt 文件。"""
    return (PROMPT_DIR / name).read_text(encoding="utf-8")


def _strip(s: str) -> str:
    """剥 ```json ``` 包裹（客户端防线③）。"""
    s = s.strip()
    if s.startswith("```"):
        s = s.split("\n", 1)[1] if "\n" in s else s[3:]
        if s.endswith("```"):
            s = s[:-3]
    return s.strip()


def _chat(system_text, user_text):
    """打一次 DeepSeek，返回 (ok, dict_or_error)。三级防线同 M3-2。"""
    if not API_KEY:
        return False, "缺少 DEEPSEEK_API_KEY"
    body = {
        "model": "deepseek-chat",
        "messages": [
            {"role": "system", "content": system_text},
            {"role": "user", "content": user_text},
        ],
        "response_format": {"type": "json_object"},
        "temperature": 0.1,
        "max_tokens": 2048,
        "thinking": {"type": "disabled"},        # 先关思考，温度才生效
    }
    for attempt in range(1, 4):                   # 指数退避，上限 3 次
        try:
            r = requests.post("https://api.deepseek.com/v1/chat/completions",
                              json=body, headers={"Authorization": f"Bearer {API_KEY}"}, timeout=60)
            if r.status_code in {429, 500, 502, 503}:
                time.sleep(2 ** attempt); continue
            r.raise_for_status()
            content = r.json()["choices"][0]["message"]["content"]
            return True, json.loads(_strip(content))
        except Exception as e:
            return False, f"{type(e).__name__}: {e}"
    return False, "重试 3 次仍失败"


def normalize_year(v):
    """年份规范化（Step 7）：'2024年'/'24年'/'二〇二四年' → '2024'。"""
    if v is None:
        return None
    s = str(v).strip()
    if re.fullmatch(r"\d{4}", s):
        return s                                 # 已是四位数字
    m = re.search(r"(\d{2})年", s)              # 处理 '24年'
    if m:
        return "20" + m.group(1)
    # 中文数字年份：逐字映射（'二〇二四' → '2024'）
    if "年" in s and any(c in s for c in CN_NUM):
        digits = "".join(str(CN_NUM[c]) for c in s if c in CN_NUM)
        if len(digits) == 4:
            return digits
    return s                                     # 改不动原样返回，并会在标签里标记


def gate_summary(summary, fields, max_len=MAX_SUMMARY_LEN):
    """
    摘要闸门（Step 8 三层）：返回 (通过的摘要, 问题描述列表)。
    ⚠️ 超限不能静默接受——要么重试，要么标记异常（M3-3 那关的教训）。
    """
    problems = []
    if not summary:
        return None, ["摘要为空"]

    if len(summary) > max_len:                        # 闸门①：长度
        problems.append(f"摘要 {len(summary)} 字，超过上限 {max_len}")

    hit = [k for k in REQUIRED_ELEMENTS              # 闸门②：要素命中（字段名来自 _fields.md）
           if (fields.get(k) not in (None, "")) and (str(fields.get(k))[:6] in summary)]
    if len(hit) < 2:
        problems.append(f"要素命中不足：只命中 {hit or '无'}（要求 ≥2 项）")

    for bad in ("本文档", "详见第"):                  # 闸门③：AI 味儿空话
        if bad in summary:
            problems.append(f"疑似空话：含「{bad}」")

    return summary, problems


def enrich(result: dict, text: str = "") -> dict:
    """主函数：在 result 上【追加】三个字段，不改原有字段（坑 6 禁令）。"""
    fields = result.get("fields", {}) or {}

    if MODE == "one":
        # 一次调用同时要 类别+摘要+标签
        ok, out = _chat(load_prompt("classify_v1.md"),
                        f"原文：{text}\n已有字段：{json.dumps(fields, ensure_ascii=False)}")
        cat = out.get("类别") if ok else None
        summary = out.get("摘要") if ok else None
        tags = out.get("标签") if ok else None
        if not ok:
            result["_classify_error"] = out
    else:
        # 三次调用，每段专注、好调、好排查（Step 5 选了三次）
        ok1, c = _chat(load_prompt("classify_v1.md"), f"已有字段：{json.dumps(fields, ensure_ascii=False)}")
        cat = c.get("类别") if ok1 else None
        ok2, s = _chat(load_prompt("summary_v1.md"), f"原文：{text}")
        summary = s.get("摘要") if ok2 else None
        ok3, t = _chat(load_prompt("tag_v1.md"), f"已有字段：{json.dumps(fields, ensure_ascii=False)}\n原文：{text}")
        tags = t.get("标签") if ok3 else None

    # 受约束枚举校验（Step 6 禁令）：不在枚举要标出来，不静默塞进"其他"
    if cat is not None and cat not in CATEGORIES:
        result["_category_warning"] = f"模型返回了未定义类别：{cat}"

    # 标签年份规范化（Step 7）
    if isinstance(tags, dict) and "年份" in tags:
        tags["年份"] = normalize_year(tags["年份"])

    # 摘要闸门（Step 8 三层：长度 / 要素 / 空话）——全部问题留痕，不静默接受
    summary, summary_problems = gate_summary(summary, fields)
    if summary_problems:
        result["_summary_warning"] = summary_problems

    result["资料类型"] = cat                 # 与 M3-0 字段清单 key 对齐
    result["关键摘要"] = summary
    result["标签"] = tags
    return result


# CLI：python core/classify.py results/某文件.json  （摘要需要原文，按 source 重新 parse）
if __name__ == "__main__":
    from parse import parse_file        # M3-1 的入口叫 parse_file，返回 ParseResult（不是字符串）
    rp = sys.argv[1]
    result = json.loads(Path(rp).read_text(encoding="utf-8"))
    src = result.get("source", "")
    # 重新拿原文给摘要用：parse_file 有三态，只有 ok 才有正文（empty/failed 一律当空文本，不喂给摘要）
    pr = parse_file(f"data/raw/{src}") if src else None
    text = pr.text if (pr is not None and pr.ok) else ""
    enriched = enrich(result, text)
    print(json.dumps(enriched, ensure_ascii=False, indent=2))