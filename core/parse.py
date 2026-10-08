from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


# ---------- 输入归一化：磁盘路径 / 文件对象（界面层的 UploadedFile）两条路都要能吃 ----------
# ★M3-5 Step 5-B 补接口的三条规矩：
#   ① UploadedFile 的 .name 官方未做净化 → 只取最后一段文件名，绝不拿它拼磁盘路径；
#   ② 同一个文件对象会被读多次（扫描件判定 → 抽字 → OCR）→ 每次交出去前先 seek(0)；
#   ③ pymupdf/fitz 只认"路径"或"字节流"，不认 BytesIO → 文件对象要先转成 bytes 再传。
def _is_fileobj(src) -> bool:
    """判断是"文件对象"还是"路径"：带 read / getvalue 的当文件对象，其余当路径。"""
    return hasattr(src, "read") or hasattr(src, "getvalue")


def _safe_name(name) -> str:
    """只取文件名的最后一段：丢掉 ../ 、盘符、目录分隔符，避免被拼成任意磁盘路径。"""
    return Path(str(name or "")).name


def _rewind(src):
    """把文件对象拉回开头。为什么要：read() 读到尾部后再读返回空（"跑通了但输出是空的"）。"""
    try:
        src.seek(0)
    except Exception:
        pass                                          # 不是可定位的流就算了，后面读失败会有明确报错
    return src


def _read_bytes(src) -> bytes:
    """一次拿到全部字节：优先 getvalue()（不受流位置影响），其次 read()（先回到开头，防读到空）。"""
    if hasattr(src, "getvalue"):
        return src.getvalue()                         # UploadedFile / BytesIO 都有 getvalue()
    _rewind(src)
    return src.read()


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
        with pdfplumber.open(_rewind(src)) as pdf:     # 先回到开头：这份流后面还要再读一次
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


def _open_pdf_doc(src):
    """打开 PDF 给 OCR 用（pymupdf/fitz.open 的两张脸）：
       · 路径        → open("a.pdf")
       · 文件对象    → open(stream=字节, filetype="pdf")   ★直接塞 BytesIO 会 FileNotFoundError
    """
    mupdf = _load_pymupdf()
    if _is_fileobj(src):
        return mupdf.open(stream=_read_bytes(src), filetype="pdf")
    return mupdf.open(str(src))


def _parse_pdf(src):
    import pdfplumber

    # rapidocr / pymupdf 都推迟到真正要 OCR 时才导入：
    # 没装 OCR 依赖时，文字型 PDF 照样能抽，不会因为缺依赖整份失败

    # 1) 先判断是不是扫描件
    scanned = _looks_like_scanned(src)

    # 2) 文字型：pdfplumber 直接抽
    if not scanned:
        try:
            with pdfplumber.open(_rewind(src)) as pdf:     # 扫描件判定已经读过一次，这里必须回到开头
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
        doc = _open_pdf_doc(src)                     # 路径 / 文件对象两种输入都能开（见 _open_pdf_doc）
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
        d = docx.Document(_rewind(src))              # python-docx 吃路径也吃二进制文件对象
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
        # openpyxl 同样吃二进制文件对象（要求是"可读、可 seek"的流，UploadedFile 满足）
        wb = openpyxl.load_workbook(_rewind(src), read_only=True, data_only=True)
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
        raw = src.read_bytes() if isinstance(src, Path) else _read_bytes(src)   # 路径 / 文件对象都行
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


# ---------- 图片（.png / .jpg / .jpeg）：直接 OCR，不用 pymupdf ----------
def _parse_image(src):
    """扫描图 / 照片 / 截图：RapidOCR 直接吃图片字节（pymupdf 只是"PDF 转图"用的，这里不需要）。"""
    try:
        from rapidocr import RapidOCR
        engine = RapidOCR()                          # 模型已在包内，无需联网
        # 路径就按路径读；文件对象走 _read_bytes（先 seek(0)，防读到空）
        raw = src.read_bytes() if isinstance(src, Path) else _read_bytes(src)
        res = engine(raw)                            # ★传字节：RapidOCR 认 bytes / 路径 / ndarray
        text = "\n".join(res.txts) if res.txts else ""   # .txts 是文字元组（按检测框顺序）
        return ParseResult(
            text=text, meta={"parser": "rapidocr", "ocr": True},
            empty=(text.strip() == ""),              # 图是空白的 → 静默失败要标出来
        )
    except Exception as e:
        return ParseResult(text="", meta={"parser": "rapidocr", "error": repr(e)}, ok=False)


# ---------- 入口：分发 + 支持文件路径 / 文件对象（§3.3）----------
_SUFFIX_MAP = {
    ".pdf": _parse_pdf,
    ".docx": _parse_docx,
    ".xlsx": _parse_xlsx,
    ".xlsm": _parse_xlsx,
    ".txt": _parse_text,
    ".md": _parse_text,
    ".png": _parse_image,
    ".jpg": _parse_image,
    ".jpeg": _parse_image,
}

# 界面层 st.file_uploader(type=...) 直接读这张表，别自己再抄一份
# （否则会出现"界面允许传、核心层不支持"的静默不一致，见 M3-5 Step 5-D）
SUPPORTED_SUFFIXES = tuple(sorted(_SUFFIX_MAP))


def parse_file(source):
    """文件 → 文本 + 元信息（纯函数）。

    source: 文件路径(str / Path) 或 已打开的二进制文件对象（如界面层上传的 UploadedFile / BytesIO）。
    返回 ParseResult：ok 区分“解析失败”，empty 区分“成功但为空”，status 一眼看出三种情况。
    """
    if isinstance(source, (str, Path)):
        src = Path(source)
        suffix = src.suffix.lower()
        meta_path = str(src)
    else:                                               # 文件对象：后缀只能从 .name 来，且只取最后一段
        f = source
        name = _safe_name(getattr(f, "name", ""))       # ★未净化的名字（可能带 ..\）不能拿去拼路径
        suffix = Path(name).suffix.lower()
        src = f
        meta_path = name or "<fileobj>"

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
