from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


# ---------- 返回结构：把三种情况分开（§3.2 空结果检测）----------
@dataclass
class ParseResult:
    text: str = ""                                   # 提取到的文本（可能为空串）
    meta: dict = field(default_factory=dict)         # 元信息：解析器 / 是否 OCR / 页数 / 编码 …
    ok: bool = True                                  # False = 解析失败（打不开 / 异常）
    empty: bool = False                              # True  = 成功但内容为空（静默失败标记）

    @property
    def status(self) -> str:
        if not self.ok:
            return "failed"                          # ③ 解析失败
        return "empty" if self.empty else "ok"       # ② 成功但空 / ① 真成功有内容


# ---------- 扫描件判定阈值（对应 Step 5 的指标）----------
# 文字型 PDF 每页能抽出很多字；扫描件每页几乎没字。
# 一页抽到的"非空白字符"少于这个数，就认为这页"没字"。
MIN_CHARS_PER_PAGE = 30


# ---------- PDF：文字型走 pdfplumber，扫描件走 OCR 兜底 ----------
def _looks_like_scanned(src) -> bool:
    """用 pdfplumber 抽一遍：多数页都没字 → 判定为扫描件，交给 OCR。"""
    import pdfplumber
    try:
        with pdfplumber.open(src) as pdf:
            pages = pdf.pages
            if not pages:
                return False
            low = sum(
                1 for pg in pages
                if len((pg.extract_text() or "").strip()) < MIN_CHARS_PER_PAGE
            )
        return (low / len(pages)) >= 0.8             # 超过 80% 的页都没字 → 扫描件
    except Exception:
        return True                                  # 抽不出字：当扫描件，让 OCR 试


def _load_pymupdf():
    """新版 PyMuPDF 用 pymupdf，旧版（<1.24.3）只有 fitz，两种都兼容。"""
    try:
        import pymupdf
        return pymupdf
    except ModuleNotFoundError:
        import fitz
        return fitz


def _parse_pdf(src):
    import pdfplumber

    # rapidocr / pymupdf 都推迟到真正要 OCR 时才导入：
    # 没装 OCR 依赖时，文字型 PDF 照样能抽，不会因为缺依赖整份失败

    # 1) 先判断是不是扫描件
    scanned = _looks_like_scanned(src)

    # 2) 文字型：pdfplumber 直接抽
    if not scanned:
        try:
            with pdfplumber.open(src) as pdf:
                parts = [p.extract_text() or "" for p in pdf.pages]
            text = "\n".join(parts)
            return ParseResult(
                text=text,
                meta={"parser": "pdfplumber", "ocr": False, "pages": len(parts)},
                empty=(text.strip() == ""),
            )
        except Exception:
            scanned = True                           # 文字型也抽失败 → 走 OCR 兜底

    # 3) 扫描件 / 兜底：PDF → 图 → OCR（复用 Step 6-D 写法）
    try:
        from rapidocr import RapidOCR
        engine = RapidOCR()                          # 模型已在包内，无需联网
        doc = _load_pymupdf().open(src)              # 只有 OCR 路线才需要 pymupdf
        n_pages = doc.page_count
        lines = []
        for page in doc:
            pix = page.get_pixmap(dpi=200)           # PDF 页 → 位图（只有 pymupdf 能干）
            res = engine(pix.tobytes("png"))         # RapidOCR 识别，返回结果对象
            if res.txts:                            # .txts 是文字元组（按检测框顺序）
                lines.append("\n".join(res.txts))
        doc.close()
        text = "\n".join(lines)
        return ParseResult(
            text=text,
            meta={"parser": "rapidocr+pymupdf", "ocr": True, "pages": n_pages},
            empty=(text.strip() == ""),
        )
    except Exception as e:
        return ParseResult(
            text="", meta={"parser": "rapidocr+pymupdf", "error": repr(e)}, ok=False
        )


# ---------- Word（.docx）----------
def _parse_docx(src):
    import docx
    try:
        d = docx.Document(src)
        parts = [p.text for p in d.paragraphs]       # 段落取正文
        for t in d.tables:                           # 表格内容需单独取
            for row in t.rows:
                parts.append(" ".join(c.text for c in row.cells))
        text = "\n".join(parts)
        return ParseResult(
            text=text, meta={"parser": "python-docx", "ocr": False},
            empty=(text.strip() == ""),
        )
    except Exception as e:
        return ParseResult(text="", meta={"parser": "python-docx", "error": repr(e)}, ok=False)


# ---------- Excel（.xlsx / .xlsm）----------
def _parse_xlsx(src):
    import openpyxl
    try:
        wb = openpyxl.load_workbook(src, read_only=True, data_only=True)
        names = wb.sheetnames
        parts = []
        for ws in wb.worksheets:
            for row in ws.iter_rows(values_only=True):
                parts.append(" ".join("" if c is None else str(c) for c in row))
        wb.close()
        text = "\n".join(parts)
        return ParseResult(
            text=text, meta={"parser": "openpyxl", "ocr": False, "sheets": names},
            empty=(text.strip() == ""),
        )
    except Exception as e:
        return ParseResult(text="", meta={"parser": "openpyxl", "error": repr(e)}, ok=False)


# ---------- 纯文本 / Markdown ----------
def _parse_text(src):
    try:
        if isinstance(src, Path):
            raw = src.read_bytes()
        else:                                          # 文件对象
            raw = src.getvalue() if hasattr(src, "getvalue") else src.read()
    except Exception as e:
        return ParseResult(text="", meta={"parser": "text", "error": repr(e)}, ok=False)
    for enc in ("utf-8", "gbk", "utf-8-sig"):          # 先试 utf-8，再试 gbk（常见中文编码）
        try:
            text = raw.decode(enc)
            return ParseResult(
                text=text, meta={"parser": "text", "ocr": False, "encoding": enc},
                empty=(text.strip() == ""),
            )
        except UnicodeDecodeError:
            continue
    return ParseResult(text="", meta={"parser": "text", "error": "decode failed"}, ok=False)


# ---------- 入口：分发 + 支持文件路径 / 文件对象（§3.3）----------
_SUFFIX_MAP = {
    ".pdf": _parse_pdf,
    ".docx": _parse_docx,
    ".xlsx": _parse_xlsx,
    ".xlsm": _parse_xlsx,
    ".txt": _parse_text,
    ".md": _parse_text,
}


def parse_file(source):
    """文件 → 文本 + 元信息（纯函数）。

    source: 文件路径(str / Path) 或 已打开的二进制文件对象（如界面层上传的 BytesIO）。
    返回 ParseResult：ok 区分“解析失败”，empty 区分“成功但为空”，status 一眼看出三种情况。
    """
    if isinstance(source, (str, Path)):
        src = Path(source)
        suffix = src.suffix.lower()
        meta_path = str(src)
    else:                                               # 文件对象
        f = source
        suffix = Path(getattr(f, "name", "")).suffix.lower()
        src = f
        meta_path = getattr(f, "name", "") or "<fileobj>"

    parser = _SUFFIX_MAP.get(suffix)
    if parser is None:
        return ParseResult(
            text="", meta={"path": meta_path, "error": f"unsupported suffix: {suffix}"}, ok=False
        )

    try:
        result = parser(src)
    except Exception as e:                              # 兜底：任何未捕获异常都归为“解析失败”
        return ParseResult(text="", meta={"path": meta_path, "error": repr(e)}, ok=False)

    result.meta["path"] = meta_path
    return result
