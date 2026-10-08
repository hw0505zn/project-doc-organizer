"""
ui/app.py —— M3-5 展示层（薄壳）
只做三件事：收文件 → 调核心逻辑(core/) → 显示结果。不写任何业务逻辑。
"""
import sys
from pathlib import Path
import streamlit as st
import pandas as pd

sys.path.insert(0, "core")          # 让 streamlit 进程能 import 到 core
from parse import parse_file, SUPPORTED_SUFFIXES   # M3-1：入口 + 支持列表（Step 5-D：不自己抄一份）
from extract import extract          # M3-2
from classify import enrich          # M3-4

SUPPORTED = [s.lstrip(".") for s in SUPPORTED_SUFFIXES]   # ('.docx','.pdf',…) → ['docx','pdf',…]
# ⚠️ 别再写 BASE/"data"/"raw" 落盘：parse_file 直接吃 UploadedFile（Step 5-B-4）

# 汇总表进哪几列：从"甲方最关心什么"倒推，不是从"我有哪些字段"倒推（Step 6）
# ⚠️ 列只能来自【真实字段清单 prompts/extract_v1.md】+ M3-4 追加的类别/年份 + 处理结果
COLUMNS = ["文件名", "项目名称", "总投资", "资料类型", "年份", "状态"]

# 列名的中文显示（给甲方看，不要直接甩英文字段名）；单位写进表头，别让用户猜
LABELS = {
    "文件名": "文件名",
    "项目名称": "项目名称",
    "总投资": "总投资（万元）",
    "资料类型": "资料类型",
    "年份": "年份",
    "状态": "处理结果",
}

# 界面一次处理多少份：从"甲方能等多久"倒推（单份约 10s × 5 ≈ 1 分钟），不是技术限制（Step 11-B）
MAX_FILES_PER_RUN = 5


def process(uploaded) -> dict:
    """薄壳逻辑：文件对象 → 走核心层 → 返回一行结果 dict（失败也返回，不崩）。"""
    name = Path(uploaded.name).name                 # 只取最后一段：.name 官方未净化
    ext = Path(name).suffix.lower().lstrip(".")     # 用 suffix，别用 split(".")[-1]
    if ext not in SUPPORTED:
        return {"文件名": name, "状态": "失败", "失败原因": f"不支持的格式 .{ext}"}  # 非法格式友好提示
    try:
        # ★直接把 UploadedFile 喂进去：不落盘（Step 5-B-4），返回的是 ParseResult 不是字符串
        pr = parse_file(uploaded)                   # M3-1：文件 → ParseResult
        if not pr.ok:                               # ① 解析失败：打不开 / 不支持 / 抛异常
            return {"文件名": name, "状态": "失败", "失败原因": pr.meta.get("error", "解析器未知失败")}
        if pr.empty or not pr.text.strip():         # ② 成功但没内容（扫描件没走 OCR / 空文档）
            return {"文件名": name, "状态": "失败", "失败原因": "解析成功但内容为空（疑似扫描件未走 OCR）"}
        res = extract(pr.text)                      # M3-2：文本 → 字段（③ 真成功才进提取）
        if not res["ok"]:
            return {"文件名": name, "状态": "失败", "失败原因": res["error"]}
        enriched = enrich(res, pr.text)                       # M3-4：追加类别/摘要/标签
        f = enriched.get("fields", {})
        return {
            "文件名": name, "状态": "成功",
            "项目名称": f.get("项目名称"), "总投资": f.get("总投资"),
            "资料类型": enriched.get("资料类型"),
            "年份": (enriched.get("标签") or {}).get("年份"),
            "关键摘要": (enriched.get("关键摘要") or "")[:120],   # 截断预览，详情区看全文
        }
    except Exception as e:
        # 任何异常都记下来、标红，不让整页崩（M3-3 教训：失败资料也要在表里）
        return {"文件名": name, "状态": "失败", "失败原因": f"{type(e).__name__}: {e}"}


def fmt_cell(key, value):
    """单元格显示：空值写「未提取到」，数字补单位/千分位——界面层只做格式化，不做计算。"""
    if value is None or value == "" or value == "null":
        return "未提取到"                       # ❌ 不要留空、不要写 None
    if isinstance(value, float) and value != value:
        return "未提取到"                       # NaN（reindex 补出来的空格子）：NaN != NaN 是它唯一的自证方式
    if key == "总投资" and isinstance(value, (int, float)):
        return f"{value:,.1f} 万元"             # 千分位 + 明确单位
    if key == "状态" and value == "失败":
        return "❌ 失败"                        # 失败标红的前一步：先让它一眼可见
    return str(value)


def show_table(results):
    """汇总表：失败的那几行也要在，并标出原因（§3.2 硬指标）。"""
    # ★用 reindex 不是 [COLUMNS]：失败行只有「文件名/状态/失败原因」，用 [ ] 取列会 KeyError
    df = pd.DataFrame(results).reindex(columns=COLUMNS)
    for col in COLUMNS:                                     # ★逐格套 fmt_cell（此时表头还是原始 key）
        df[col] = df[col].map(lambda v, c=col: fmt_cell(c, v))
    df = df.rename(columns=LABELS)                          # 最后才换中文表头：fmt_cell 认的是原始 key

    failed = [r for r in results if r.get("状态") == "失败"]
    if failed:
        st.error(f"有 {len(failed)} 份处理失败（明细见下表「处理结果」列）")   # 红色提示，不静默
        with st.expander("看失败原因"):
            for r in failed:
                st.write(f"· {r.get('文件名')} —— {r.get('失败原因')}")
    st.dataframe(df)                            # 自带排序/筛选


def main():
    st.title("工程资料智能整理器")
    st.write("拖入工程资料（PDF/Word/Excel/扫描件/图片），自动抽取、分类、生成摘要。")

    files = st.file_uploader("上传资料", type=SUPPORTED, accept_multiple_files=True)
    if st.button("开始处理") and files:
        # ① 上限：截断并【告知】，不是默默处理前 5 份（Step 11-B 的产品判断）
        if len(files) > MAX_FILES_PER_RUN:
            st.warning(f"一次最多处理 {MAX_FILES_PER_RUN} 份（你选了 {len(files)} 份）。"
                       f"大批量请用命令行：`python core/batch.py`，结果落在 results/，界面可直接查看。")
            files = files[:MAX_FILES_PER_RUN]

        # ② 方案 A：进度条 —— 我们知道总数(len(files))、也算得出已完成，所以用最直观的那种
        progress = st.progress(0)              # 0~100 的进度条
        status = st.empty()                    # 占位容器：后面反复往里写文字
        rows = []
        for i, u in enumerate(files, 1):
            status.write(f"正在处理第 {i}/{len(files)} 份：{u.name}")   # 没有反馈的 5 分钟，甲方会以为挂了
            rows.append(process(u))            # 单份失败被 process 兜住，不会中断整批
            progress.progress(int(i / len(files) * 100))
        status.write("✅ 全部处理完成（成功/失败明细见下表）")

        st.session_state.results = rows        # 存进 session_state，重跑不丢（Step 4）

    if "results" in st.session_state and st.session_state.results:
        show_table(st.session_state.results)   # ★挑列 + 中文表头 + 空值文案 + 失败提示，全在这一处
        for row in st.session_state.results:   # 详情区：展开看完整字段
            with st.expander(f"详情：{row.get('文件名')}"):
                st.json(row)


if __name__ == "__main__":
    main()