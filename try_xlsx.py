import openpyxl                                                        # 导入 openpyxl 库
wb = openpyxl.load_workbook("data/raw/施工方专项设计及厂家深化进度计划2026年3月.xlsx")  # 打开 .xlsx 工作簿
ws = wb.active                                                        # 取当前激活的工作表（默认第一个 sheet）
for row in ws.iter_rows(values_only=True):                            # 逐行迭代；values_only=True 只取单元格的值（不含样式）
    print(row)                                                        # 打印这一行（元组：每个元素是一列的值）