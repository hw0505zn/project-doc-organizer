import sys
from pathlib import Path

from core.parse import parse_file


def main():
    if len(sys.argv) < 2:
        print("用法：python try_parse.py <文件路径>")
        sys.exit(1)

    path = sys.argv[1]

    # 9-C 第 1 条：文件不存在 → 明确报错，不是崩溃看不到原因
    if not Path(path).exists():
        print(f"[解析失败] 文件不存在：{path}")
        sys.exit(2)

    r = parse_file(path)

    # 把"三种情况"一句话打出来
    print("=" * 60)
    print(f"路径     : {r.meta.get('path', path)}")
    print(f"状态     : {r.status}")            # ok / empty / failed
    print(f"解析器   : {r.meta.get('parser')}")
    print(f"是否 OCR : {r.meta.get('ocr')}")
    print(f"字符数   : {len(r.text)}")
    print(f"元信息   : {r.meta}")
    print("-" * 60)
    print("【文本内容】")
    print(r.text if r.text else "（空）")
    print("=" * 60)

    # 9-C 第 2/3 条：失败 / 成功但空 都亮出来，不静默
    if r.status == "failed":
        print(f"[注意] 解析失败：{r.meta.get('error')}")
    elif r.status == "empty":
        print("[注意] 成功但内容为空（静默失败已被拦住，不会当成'真没字'）")


if __name__ == "__main__":
    main()