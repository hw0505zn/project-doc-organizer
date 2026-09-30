import sys
from pathlib import Path

# Windows 控制台默认 GBK，遇到 • 等字符会 UnicodeEncodeError
# 这里强制 stdout/stderr 用 UTF-8；errors="replace" 保证实在打不出的字符不至于崩
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

from core.parse import parse_file


def main():
    # 用法：python try_parse.py <文件路径> [-o 输出文件]
    # PowerShell 的 > 重定向会先把 Python 输出的 UTF-8 字节按 GBK 解码、再写成 UTF-16，
    # 结果就是乱码。要落盘请直接用 -o，由 Python 自己按 UTF-8 写，绕开 shell。
    args = [a for a in sys.argv[1:] if a not in ("-o", "--out")]
    out_file = None
    if "-o" in sys.argv or "--out" in sys.argv:
        flag = "-o" if "-o" in sys.argv else "--out"
        i = sys.argv.index(flag)
        if i + 1 >= len(sys.argv):
            print("用法：python try_parse.py <文件路径> [-o 输出文件]")
            sys.exit(1)
        out_file = sys.argv[i + 1]

    if not args:
        print("用法：python try_parse.py <文件路径> [-o 输出文件]")
        sys.exit(1)

    path = args[0]

    # 9-C 第 1 条：文件不存在 → 明确报错，不是崩溃看不到原因
    if not Path(path).exists():
        print(f"[解析失败] 文件不存在：{path}")
        sys.exit(2)

    r = parse_file(path)

    lines = []
    def emit(s=""):
        lines.append(s)

    # 把"三种情况"一句话打出来
    emit("=" * 60)
    emit(f"路径     : {r.meta.get('path', path)}")
    emit(f"状态     : {r.status}")              # ok / empty / failed
    emit(f"解析器   : {r.meta.get('parser')}")
    emit(f"是否 OCR : {r.meta.get('ocr')}")
    emit(f"字符数   : {len(r.text)}")
    emit(f"元信息   : {r.meta}")
    emit("-" * 60)
    emit("【文本内容】")
    emit(r.text if r.text else "（空）")
    emit("=" * 60)

    # 9-C 第 2/3 条：失败 / 成功但空 都亮出来，不静默
    if r.status == "failed":
        emit(f"[注意] 解析失败：{r.meta.get('error')}")
    elif r.status == "empty":
        emit("[注意] 成功但内容为空（静默失败已被拦住，不会当成'真没字'）")

    if out_file:
        # 只落「纯文本正文」r.text，绝不把调试信息/元信息写进去（1-B 红线：喂给 extract 的必须是纯文本）
        Path(out_file).write_text(r.text, encoding="utf-8")
        print(f"[已写出] {out_file}（纯文本正文，UTF-8 无 BOM，{len(r.text)} 字符）")
    else:
        print("\n".join(lines))


if __name__ == "__main__":
    main()
