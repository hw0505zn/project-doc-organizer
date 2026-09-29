import pymupdf  # 就是 pymupdf
from rapidocr import RapidOCR

engine = RapidOCR()          # 模型已在包内，无需联网下载

def ocr_pdf(pdf_path, dpi=200):
    """扫描件 PDF → 逐页渲染成图 → OCR → 文本"""
    doc = pymupdf.open(pdf_path)
    texts = []
    for page in doc:
        pix = page.get_pixmap(dpi=dpi)      # PDF 页 → 位图（这一步只有 pymupdf 能干）
        img_bytes = pix.tobytes("png")      # 位图 → PNG 字节，直接喂 OCR
        result = engine(img_bytes)          # RapidOCR 识别
        if result.txts:                     # result.txts 是文字元组（按检测框顺序）
            texts.append("\n".join(result.txts))
    doc.close()
    return "\n".join(texts)

if __name__ == "__main__":
    print(ocr_pdf("data/raw/设计分包报审资料.pdf"))