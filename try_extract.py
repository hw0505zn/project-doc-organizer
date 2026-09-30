"""try_extract.py —— 命令行验证脚本（壳层，允许 print）"""
import sys
import json
from pathlib import Path
from core.extract import extract          # 直接调 core 层的纯函数


def read_text_any(path):
    """壳层读文件：容错解码（UTF-8 / UTF-8-BOM / UTF-16 / GBK），避免卡在编码上。
    注意：编码处理只发生在壳层——core/extract.py 永远只收纯文本字符串，不读文件。"""
    raw = Path(path).read_bytes()
    for enc in ("utf-8-sig", "utf-16", "gbk", "utf-8"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")   # 最后兜底，绝不崩


def main():
    # 没传参数就默认读 _sample.txt（Step 1-B 落地的纯文本）
    path = sys.argv[1] if len(sys.argv) > 1 else "_sample.txt"
    text = read_text_any(path)                     # 读纯文本（容错编码）
    result = extract(text)                         # 调纯函数，拿到 {ok, fields, error, usage}
    # ensure_ascii=False → 中文正常显示；indent=2 → 层级清晰看得懂
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()