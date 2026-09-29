import pdfplumber

with pdfplumber.open("data/raw/设计分包报审资料.pdf") as pdf:
    first_page = pdf.pages[0]
    print(first_page.extract_text())


