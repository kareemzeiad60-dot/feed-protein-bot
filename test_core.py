import os, shutil, subprocess, tempfile, openpyxl
from feed_core import *

T = os.path.join(os.path.dirname(os.path.abspath(__file__)), "Feed_Calculator_Bilingual.xlsx")
feeds = load_feeds(T)
by_en = {f.name_en: f for f in feeds}
assert len(feeds) == 21

# ---- تحليل الرسائل
upd, err = parse_message("ذرة صفراء 500\nsoybean meal: 250\nقطن 250\nردة ١٠٠ كجم\nكسب 40\nزيت\nxyz 20\nملح 7,5", feeds)
assert {feeds[r - 5].name_en: q for r, q in upd.items()} == {
    "Yellow Corn": 500, "Soybean Meal (44%)": 250, "Cottonseed Meal (24%)": 250, "Wheat Bran": 100, "Common Salt (NaCl)": 7.5}
assert len(err) == 3                                        # كسب (غامض) ، زيت (مفيش كمية) ، xyz
assert parse_number("٢٥٠") == 250 and parse_number("7,5 كجم") == 7.5 and parse_number("abc") is None

# ---- الحساب + النسب + التكلفة
qty = default_quantities(feeds); qty.update(upd)
prices = {by_en["Yellow Corn"].row: 12000, by_en["Soybean Meal (44%)"].row: 22000}   # الباقي من غير سعر
res = compute(qty, feeds, prices)
assert abs(sum(sh for *_, sh in res["lines"]) - 1) < 1e-12                           # مجموع النسب = 100%
exp_cost = 12000 * 500 / res["total_weight"] + 22000 * 250 / res["total_weight"]
assert abs(res["cost_per_ton"] - exp_cost) < 1e-9 and len(res["missing_price"]) > 0
print("mix: total=%.2f protein=%.4f cp=%.4f%% cost/ton=%.2f" % (res["total_weight"], res["protein_kg"], res["cp_pct"], res["cost_per_ton"]))

# ---- النصيحة: لو طبّقنا الكمية المقترحة لازم نوصل للهدف بالظبط
for target in (30.0, 12.0):
    st, txt = advice(res, target, feeds)
    x = float(txt.split("حوالي")[1].split()[0].replace(",", ""))
    src = by_en["Soybean Meal (44%)"] if st == "low" else by_en["Yellow Corn"]
    new_cp = (res["protein_kg"] + x * src.cp_as_fed) / (res["total_weight"] + x) * 100
    print(f"advice target {target}: {st}, add {x} kg -> {new_cp:.3f}%")
    assert abs(new_cp - target) < 0.05, new_cp
assert advice(res, round(res["cp_pct"], 1), feeds)[0] == "ok"

# ---- التحجيم
sc = scale_quantities(qty, 1000)
assert abs(sum(sc.values()) - 1000) < 0.1
assert abs(compute(sc, feeds)["cp_pct"] - compute(qty, feeds)["cp_pct"]) < 0.01      # نفس نسبة البروتين

# ---- ملف الإكسل: إعادة حساب LibreOffice لازم تطابق بايثون
out = os.path.join(tempfile.mkdtemp(), "mix.xlsx")
build_workbook(T, qty, out, recalc=True, prices=prices)
wb = openpyxl.load_workbook(out, data_only=True)
ws = wb.worksheets[0]
assert abs(ws["O5"].value - res["total_weight"]) < 1e-6 and abs(ws["O6"].value - res["protein_kg"]) < 1e-6
assert abs(ws["O7"].value * 100 - res["cp_pct"]) < 1e-6
for f, q, p, share in res["lines"]:
    assert abs(ws.cell(f.row, 9).value - share) < 1e-9, f.name_en                    # I = نسبة المكون %
    assert abs(ws.cell(f.row, 10).value - share * 1000) < 1e-6                       # J = كجم/طن
assert abs(ws["I26"].value - 1) < 1e-9 and abs(ws["J26"].value - 1000) < 1e-6        # المجموع 100% و 1000 كجم
assert abs(ws["L26"].value - res["cost_per_ton"]) < 1e-6 and abs(ws["O11"].value - res["cost_per_ton"]) < 1e-6
assert abs(ws["O13"].value - 1) < 1e-9
dm = wb.worksheets[1]; assert abs(dm["I26"].value - 1) < 1e-9 and abs(dm["M6"].value - res["protein_kg"]) < 1e-6
assert not [c.coordinate for w in wb for r in w.iter_rows() for c in r if isinstance(c.value, str) and c.value.startswith("#")]
wf = openpyxl.load_workbook(out).worksheets[0]
print("sheet : weight=%.2f protein=%.2f cp=%.4f | I7=%.4f J7=%.2f L26=%.2f" % (ws["O5"].value, ws["O6"].value, ws["O7"].value, ws["I7"].value, ws["J7"].value, ws["L26"].value))
print("kept  :", wf["E7"].value, wf["I7"].value, "| protected:", wf.protection.sheet, "| B5/K5 unlocked:", not wf["B5"].protection.locked, not wf["K5"].protection.locked)
print("ALL CORE TESTS OK")
