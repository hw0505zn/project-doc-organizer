"""
core/evaluate.py —— M3-6 评测脚本（★只测量：不改进系统、不调 prompt、不改数据、不用模型自负分）

输入：evalset/gold/*.json（人工标答） + results/*.json（同一批资料的系统输出）
输出：总体 / 分字段 / 分难度准确率 + 错误清单（顺手落成 evals_errors.json，给 Step 8 用）

★写成"可导入模块 + main()"的原因：Step 5 的 _sanity.py 要 `from core.evaluate import is_empty, CRITERIA`，
  如果主流程裸奔在模块层，import 的瞬间整段评测就跑了一遍。
"""
import glob
import json
import os
import re
import sys
from collections import defaultdict
from pathlib import Path

# Windows 控制台默认 GBK，中文会 UnicodeEncodeError；强制 UTF-8 + 替换兜底
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

sys.path.insert(0, str(Path(__file__).parent))     # 让 `python core\evaluate.py` 也能 import 到同层模块
from extract import DEFAULT_FIELDS                 # 字段名只从 M3-2 拿，两边 key 一定一致

GOLD_DIR = "evalset/gold"        # 人工标注的标准答案
OUT_DIR = "results"              # 同一批资料的系统输出（★必须同一批，坑 7）
ERRORS_OUT = "evals_errors.json"  # 错误清单存档：Step 8 的错因分类要从这里读

# ---------- 口径：docs/07_评测口径.md 的可执行版本，改口径只改这里（改完必须在文档记一笔） ----------
CRITERIA = {
    "字符串": "strip",          # strict(严格相等) / strip(去首尾空格) / lower(再忽略大小写) / fuzzy(模糊)
    "数字容差": 0.0,            # 0.0 = 严格；0.01 = 允许 1% 误差（1580.5 vs 1580.50 用 0.0 就够）
    "日期归一化": True,         # True = "2024年3月15日" 先归一成 "2024-03-15" 再比
    "部分匹配算对": False,      # False = "某灌区改造工程" ≠ 全名（最严）
    "空值计入分母": True,       # True = 原文有、系统没抽到 → 算错（最严）
    "双方都空则跳过": True,     # True = 原文也没有、系统也空着 → 不计入分母
    "不确定样本": "单独列出",   # 标不清的样本：不算进分母，但必须记录
}

# ★比对的字段 = 字段清单去掉「关键摘要」：摘要是自由文本，按卡里坑 6 单独评（Step 9），不混进字段准确率
FIELD_KEYS = [k for k in DEFAULT_FIELDS if k != "关键摘要"]


def load_json(path):
    """读 JSON，坏了要喊出来（禁令 2：异常不能静默跳过，否则分母悄悄变小 = 自欺）。"""
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception as e:
        print(f"❌ 读不了 {path}：{type(e).__name__}: {e}")
        return None


def is_empty(v):
    """统一判断"原文没有 / 系统没抽到"——空值归一。"""
    return v is None or v == "" or v == "null" or v == "缺失"


def _norm_date(v):
    """日期归一：2024年3月15日 / 2024/3/15 / 2024.3.15 → 2024-03-15（只有年月就补到月）。"""
    s = str(v)
    m = re.search(r"(\d{4})\D{1,3}(\d{1,2})\D{1,3}(\d{1,2})", s)
    if m:
        return f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"
    m = re.search(r"(\d{4})\D{1,3}(\d{1,2})", s)
    if m:
        return f"{m.group(1)}-{int(m.group(2)):02d}"
    return s


def _norm_str(v):
    """字符串归一：按口径决定"去空格"还是"再忽略大小写"。"""
    s = str(v).strip()
    if CRITERIA["字符串"] == "lower":
        s = s.lower()
    return s


def _as_num(v):
    """转数字失败返回 None（千分位先去掉）。"""
    try:
        return float(str(v).replace(",", "").strip())
    except Exception:
        return None


def _looks_like_date(v):
    """像不像日期：★年份限定 19xx/20xx——否则 "1580.50" 会被误判成"1580年05月"（实测踩过）。"""
    return bool(re.search(r"(?:19|20)\d{2}\D{1,3}\d{1,2}", str(v)))


def match(g_val, p_val) -> bool:
    """一个格子算不算"对"——口径全部收在这里，别散落到别处。"""
    # ① 数字优先（★必须在日期之前）：1580.5 vs "1580.50" 两边都能转数字 → 按数字比
    gn, pn = _as_num(g_val), _as_num(p_val)
    if gn is not None and pn is not None:
        tol = CRITERIA["数字容差"]
        if tol == 0:
            return gn == pn                     # 严格：1580.5 == 1580.50，但 1580.5 != 15805
        return abs(gn - pn) <= tol * max(1.0, abs(gn))

    # ② 日期：两边都不是数字，且至少一边像日期 → 归一后比（2024年3月15日 vs 2024-03-15）
    if CRITERIA["日期归一化"] and (_looks_like_date(g_val) or _looks_like_date(p_val)):
        return _norm_date(g_val) == _norm_date(p_val)

    # ③ 字符串：按口径归一后比；开了"部分匹配算对"才允许子串命中
    gs, ps = _norm_str(g_val), _norm_str(p_val)
    if gs == ps:
        return True
    return bool(CRITERIA["部分匹配算对"]) and (gs in ps or ps in gs)


def flatten_pred(pred) -> dict:
    """系统输出是【两层】：fields{} 里是 M3-2 抽的字段，顶层是 M3-4 追加的（资料类型 / 关键摘要 / 标签）。
    ★不摊平的话，pred.get("项目名称") 永远是 None → 全被当成"双方都空"跳过，分母只剩个位数（实测踩过）。"""
    flat = dict(pred)
    flat.update(pred.get("fields") or {})       # fields 里的覆盖顶层同名键
    return flat


def compare(gold, pred) -> tuple:
    """逐字段比对一份资料，返回 (正确格数, 总格数, 错误清单)。"""
    total = correct = 0
    errors = []
    pred = flatten_pred(pred)
    for key in FIELD_KEYS:                      # ★只比字段清单里的键：gold 里的"文件名"不该被当成字段打分
        g_val = gold.get(key)
        p_val = pred.get(key, "缺失")           # 系统缺这个字段 → 记"缺失"，不静默
        both_empty = is_empty(g_val) and is_empty(p_val)
        if both_empty and CRITERIA["双方都空则跳过"]:
            continue                            # 双方都空：按口径不计入分母
        if is_empty(g_val) and not CRITERIA["空值计入分母"]:
            continue                            # 口径放宽：原文没有的格子不算分母
        total += 1
        if match(g_val, p_val):
            correct += 1
        else:
            errors.append((key, p_val, g_val))  # (字段, 系统值, 标准值)
    return correct, total, errors


def difficulty(stem: str) -> str:
    """这份是扫描件还是电子文档？★用机器判定，不用文件名——
    实测过：'…（2025.6.20扫描件）(OCR).pdf' 文件名写着扫描件，其实已被 OCR 过、有文字层，判定是电子。"""
    from parse import _looks_like_scanned        # 推迟导入：只有要判难度时才依赖 pdfplumber
    src = next((p for p in Path("evalset/docs").rglob("*")
                if p.is_file() and p.stem == stem), None)
    if src is None:
        return "电子" if "扫描件" not in stem else "扫描件"   # 找不到原文才退回看文件名
    if src.suffix.lower() in (".png", ".jpg", ".jpeg"):
        return "扫描件"                          # 图片没有文字层，只能走 OCR
    if src.suffix.lower() != ".pdf":
        return "电子"
    return "扫描件" if _looks_like_scanned(src) else "电子"   # ★只数每页字数，不启动 OCR


def main():
    per_field = defaultdict(lambda: [0, 0])      # 字段名 -> [错误数, 总数]
    by_diff = defaultdict(lambda: [0, 0])        # 难度 -> [正确, 总数]
    all_c = all_t = 0
    all_errors = {}                              # 文件名 -> [(字段, 系统值, 标准值)]，给 Step 8 用
    missing_out = []

    for gold_path in sorted(glob.glob(f"{GOLD_DIR}/*.json")):
        name = os.path.basename(gold_path)
        pred_path = Path(OUT_DIR) / name
        gold = load_json(gold_path)
        if gold is None:
            continue
        if not pred_path.exists():
            # 禁令 3：不能"文件对不上就跳过"——要喊出来（坑 7：评测集和跑分那批必须同一批）
            missing_out.append(name)
            continue
        pred = load_json(pred_path)
        if pred is None:
            continue
        c, t, errs = compare(gold, pred)
        all_c += c
        all_t += t
        if errs:
            all_errors[name] = errs
        kind = difficulty(Path(name).stem)        # 只判一次：它内部会跑 pdfplumber，别每份跑两遍
        by_diff[kind][0] += c
        by_diff[kind][1] += t
        flat = flatten_pred(pred)
        for key in FIELD_KEYS:                   # 字段级：只统计进了分母的格子
            if is_empty(gold.get(key)) and is_empty(flat.get(key)) and CRITERIA["双方都空则跳过"]:
                continue
            per_field[key][1] += 1
        for (key, _p, _g) in errs:
            per_field[key][0] += 1

    # ---------- 输出（★“总体字段准确率”这一行的格式被 _regress.py 的正则抓，别乱改）----------
    if all_t == 0:
        print("⚠️ 分母是 0 —— gold 还没标注（模板里全是 null）或所有格子都被口径跳过了。")
        print("   先去填 evalset/gold/，再跑；填之前跑 _sanity.py 看分母体检。")
    else:
        print(f"总体字段准确率：{all_c}/{all_t} = {all_c / all_t:.1%}")
        print("\n分字段准确率：")
        for key in FIELD_KEYS:
            err, tot = per_field.get(key, [0, 0])
            if tot:
                print(f"  {key}: 正确 {tot - err}/{tot} = {(tot - err) / tot:.1%}")
        print("\n分难度准确率（★机器判定，不是看文件名）：")
        for kind in ("电子", "扫描件"):
            c, t = by_diff.get(kind, [0, 0])
            if t:
                print(f"  {kind}文档字段准确率：{c}/{t} = {c / t:.1%}")

    if missing_out:
        print(f"\n⚠️ 缺系统输出 {len(missing_out)} 份（评测集和跑分那批不是同一批，坑 7）：")
        for n in missing_out[:10]:
            print("   ·", n)

    # 错误清单存档：Step 8 的错因分类直接读，不用每次重算（存档才能复算）
    Path(ERRORS_OUT).write_text(json.dumps(all_errors, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n错误清单已落盘：{ERRORS_OUT}（{len(all_errors)} 份有错）")


if __name__ == "__main__":
    main()
