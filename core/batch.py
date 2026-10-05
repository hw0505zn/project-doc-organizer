"""
batch.py —— M3-3 批量编排层（薄壳，不含任何提取逻辑）
只干四件事：扫清单 → 逐份 parse+extract → 立即落盘 → 失败记原因；重启跳过已完成
"""
import os
import sys
import json
import time
from pathlib import Path

sys.path.insert(0, "core")          # 让下面能 import 到 core 里的模块
from parse import parse_file        # M3-1：文件 → ParseResult（text/meta/ok/empty，OCR 链路也在内）
from extract import extract          # M3-2：文本 → 字段字典 + 元信息

RAW_DIR = Path("data/raw")
RES_DIR = Path("results")
FAILED_LOG = "failed.log"            # 失败清单（根目录，Step 3-B）
PROGRESS = Path("progress.json")     # 进度状态（Step 7-C，显式进度文件；主判据仍是结果文件）

RES_DIR.mkdir(exist_ok=True)         # 已存在也不报错（M3-3 Step 3-B）


def is_done(name: str) -> bool:
    """断点续跑判据（Step 2）：results/{name}.json 存在且能 json.load 成功 = 这份做完了。"""
    p = RES_DIR / f"{name}.json"
    if not p.exists():
        return False
    try:
        json.loads(p.read_text(encoding="utf-8"))
        return True
    except json.JSONDecodeError:
        return False                 # 文件在但坏了 → 当没做完，重跑


def load_progress() -> dict:
    """★Step 7-C 读进度文件。文件不存在 / 坏了，都返回空 dict——不能因为进度文件坏了整批起不来。"""
    if not PROGRESS.exists():
        return {}
    try:
        return json.loads(PROGRESS.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}                                   # 坏了就当没有，结果文件会兜底（双保险）


def mark_done(name: str, ok: bool):
    """★Step 7-C 更新进度。注意：必须在【结果文件写完】之后调用（Step 2-B 顺序不能反）。"""
    prog = load_progress()
    prog[name] = "ok" if ok else "failed"
    tmp = PROGRESS.with_suffix(".json.tmp")           # ① 先写临时文件
    tmp.write_text(json.dumps(prog, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, PROGRESS)                         # ② 再原子替换：崩在中间也只会是"旧的完整文件"


def record_failed(name, stage, reason):
    """失败清单：路径 + 阶段 + 原因（坑 3：绝不许静默跳过）。"""
    with open(FAILED_LOG, "a", encoding="utf-8") as f:
        f.write(f"{name}\t{stage}\t{reason}\n")


def process_one(path: Path) -> bool:
    """处理一份：parse → extract → 先落盘再记进度（Step 2-B 顺序）。返回是否成功。"""
    name = path.stem
    # 阶段1：解析（★M3-1 的三态：ok / empty / failed，别一声 if not text 抹平了）
    try:
        pr = parse_file(path)         # 返回 ParseResult，不是字符串
    except Exception as e:            # parse_file 内部已兜底，这里是最后一道保险
        record_failed(path.name, "解析", f"{type(e).__name__}: {e}")
        return False
    if not pr.ok:                     # ① 解析失败：打不开 / 不支持的格式 / 抛异常
        record_failed(path.name, "解析", pr.meta.get("error", "解析器未知失败"))
        return False
    if pr.empty or not pr.text.strip():   # ② 成功但没内容（扫描件没走 OCR / 空文档）
        record_failed(path.name, "解析", "解析成功但内容为空（疑似扫描件未走 OCR）")
        return False
    text = pr.text                    # ③ 真成功：拿到正文，进提取阶段
    # 阶段2：提取
    try:
        res = extract(str(text))
    except Exception as e:
        record_failed(path.name, "提取", f"{type(e).__name__}: {e}")
        return False
    if not res["ok"]:                  # extract 自己判了失败
        record_failed(path.name, "提取", res["error"])
        return False
    # 阶段3：先写结果，再更新进度（反了会丢数据，Step 2-B）
    out = {"source": path.name, "fields": res["fields"], "usage": res["usage"]}
    (RES_DIR / f"{name}.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    return True


def main():
    # ★防线1：站错目录要立刻喊出来——否则 rglob 返回空、脚本"干净跑完"，其实一份没动
    if not RAW_DIR.exists():
        raise SystemExit(f"❌ 找不到 {RAW_DIR.resolve()}\n"
                         f"   请在【项目根目录】执行：python core\\batch.py")
    files = sorted(p for p in RAW_DIR.rglob("*") if p.is_file())   # 递归扫全部资料
    # ★防线2：目录站对了但里面是空的（或素材还没放进来），同样不能静默通过
    if not files:
        raise SystemExit(f"❌ {RAW_DIR.resolve()} 里一个文件都没有，先把资料放进 data\\raw")
    total = len(files)
    ok = skip = fail = 0
    t0 = time.time()
    for i, f in enumerate(files, 1):
        if is_done(f.stem):           # 断点续跑：跳过已完成（不重复调 API）
            skip += 1
            continue
        start = time.time()
        success = process_one(f)
        cost = time.time() - start
        if success:
            ok += 1
            # 回填耗时到结果文件
            p = RES_DIR / f"{f.stem}.json"
            d = json.loads(p.read_text(encoding="utf-8"))
            d["cost_seconds"] = round(cost, 1)
            p.write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8")
        else:
            fail += 1
        # ★Step 7-C：结果文件已落盘 / 失败已记账 之后，才更新进度（顺序反了会丢数据）
        mark_done(f.stem, success)
        # 进度显示（Step 6-A）：第几份 / 还剩多少 / 已用 / 预计剩余
        eta = (time.time() - t0) / max(1, i) * (total - i)
        print(f"[{i}/{total}] {'✅' if success else '❌'} {f.name}  "
              f"用时 {cost:.1f}s  ETA {eta/60:.1f}min  累计 成功{ok}/失败{fail}/跳过{skip}")
    # 跑完汇总（Step 6-B）：直接就是 Step 11 的交付表
    print(f"\n汇总：总 {total} | 成功 {ok} | 失败 {fail} | 跳过 {skip}")


if __name__ == "__main__":
    main()