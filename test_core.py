import os, tempfile, openpyxl
from feed_core import *

T = os.path.join(os.path.dirname(__file__), "Feed_Calculator_Bilingual.xlsx")
feeds = load_feeds(T)
print(len(feeds), "feeds;", [f.name_ar for f in feeds][:3])

msg = """ذرة صفراء 500
soybean meal: 250
قطن 250
ردة ١٠٠ كجم
كسب 40
ذرة 5
زيت
xyz 20
ملح 7,5"""
upd, err = parse_message(msg, feeds)
print("updates:", {feeds[r-5].name_en: q for r, q in upd.items()})
print("errors:", *err, sep="\n  ")

qty = default_quantities(feeds); qty.update(upd)
res = compute(qty, feeds)
print({k: v for k, v in res.items() if k != "lines"})

# التحقق: ملف حقيقي + إعادة حساب LibreOffice لازم يطابق حساب بايثون
out = os.path.join(tempfile.mkdtemp(), "mix.xlsx")
build_workbook(T, qty, out, recalc=True)
ws = openpyxl.load_workbook(out, data_only=True).worksheets[0]
print("sheet  : weight=%.4f protein=%.4f cp=%.6f" % (ws["K5"].value, ws["K6"].value, ws["K7"].value))
print("python : weight=%.4f protein=%.4f cp=%.6f" % (res["total_weight"], res["protein_kg"], res["cp_pct"]/100))
assert abs(ws["K6"].value - res["protein_kg"]) < 1e-6 and abs(ws["K5"].value - res["total_weight"]) < 1e-6
wf = openpyxl.load_workbook(out)
print("formulas kept:", wf.worksheets[0]["E7"].value, "| protected:", wf.worksheets[0].protection.sheet,
      "| rtl:", wf.worksheets[0].sheet_view.rightToLeft, "| DM link:", wf.worksheets[1]["B7"].value[:30])
dm = openpyxl.load_workbook(out, data_only=True).worksheets[1]
print("DM sheet protein:", dm["K6"].value, "(نفس الكمية لأنها مادة جافة×نسبة البروتين)")
print("OK")
