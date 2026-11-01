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

# ---- ملف الإكسل: بيحتوي الخامات المحددة بس، وإعادة حساب LibreOffice لازم تطابق بايثون
def check_excel(qty, prices, label):
    out = os.path.join(tempfile.mkdtemp(), "mix.xlsx")
    build_workbook(T, qty, out, recalc=True, prices=prices)
    res = compute(qty, feeds, prices); n = len(res["lines"]); tot = 5 + n
    wb = openpyxl.load_workbook(out, data_only=True); ws, dm = wb.worksheets[0], wb.worksheets[1]
    assert [ws.cell(5 + i, 1).value.split("\n")[0] for i in range(n)] == [f.name_ar for f, *_ in res["lines"]]   # أسماء المختار بس
    dm_total = sum(q * f.dm for f, q, _, _ in res["lines"])
    for i, (f, q, p, share) in enumerate(res["lines"]):
        r = 5 + i
        assert ws.cell(r, 2).value == q and abs(ws.cell(r, 5).value - p) < 1e-9, f.name_en
        assert abs(ws.cell(r, 9).value - share) < 1e-9 and abs(ws.cell(r, 10).value - share * 1000) < 1e-6   # نسبة المكون وكجم/طن
        assert abs(dm.cell(r, 5).value - p) < 1e-9 and abs(dm.cell(r, 9).value - q * f.dm / dm_total) < 1e-9
    assert abs(ws.cell(tot, 9).value - 1) < 1e-9 and abs(ws.cell(tot, 10).value - 1000) < 1e-6       # المجموع 100%
    assert abs(ws["O5"].value - res["total_weight"]) < 1e-9 and abs(ws["O6"].value - res["protein_kg"]) < 1e-9
    assert abs(ws["O7"].value * 100 - res["cp_pct"]) < 1e-9 and abs(ws["O13"].value - 1) < 1e-9
    assert abs(dm.cell(tot, 9).value - 1) < 1e-9 and abs(dm["M6"].value - res["protein_kg"]) < 1e-9
    if res["cost_per_ton"] is not None:
        assert abs(ws.cell(tot, 12).value - res["cost_per_ton"]) < 1e-6 and abs(ws["O11"].value - res["cost_per_ton"]) < 1e-6
    for sh, cols in ((ws, 12), (dm, 10)):
        assert not [c.coordinate for row in sh.iter_rows(min_row=tot + 1, max_row=30, max_col=cols) for c in row if c.value is not None]
        assert not [c.coordinate for row in sh.iter_rows() for c in row if isinstance(c.value, str) and c.value.startswith("#")]
    wf = openpyxl.load_workbook(out).worksheets[0]
    assert wf.protection.sheet and not wf["B5"].protection.locked and not wf.cell(5, 11).protection.locked
    print(f"excel [{label}]: {n} rows, total row {tot}, CP {ws['O7'].value * 100:.2f}%, "
          f"shares {[round(ws.cell(5 + i, 9).value * 100, 2) for i in range(n)]}")

check_excel(qty, prices, "mixed feeds + default additives")
only = {f.row: 0.0 for f in feeds}; only[by_en["Soybean Meal (44%)"].row] = 100; only[by_en["Molasses"].row] = 40
check_excel(only, {}, "two ingredients only")
check_excel({f.row: 10.0 + f.row for f in feeds}, {f.row: 1000.0 + f.row for f in feeds}, "all 21 ingredients")
# ---- فوتر الإكسل (رابط البوت): موجود بعد الجدول بصفين، وما بيبوظش باقي الملف
out = os.path.join(tempfile.mkdtemp(), "f.xlsx")
build_workbook(T, only, out, recalc=True, footer=("🤖 اتحسبت بواسطة @feedbot_test", "https://t.me/feedbot_test?start=xlsx"))
w = openpyxl.load_workbook(out).worksheets[0]
assert w["A9"].value.endswith("@feedbot_test") and "t.me/feedbot_test" in w["A9"].hyperlink.target and w["B7"].value == "=SUM(B5:B6)"
v = openpyxl.load_workbook(out, data_only=True).worksheets[0]; assert abs(v["I7"].value - 1) < 1e-9
print("excel footer + hyperlink:", w["A9"].value, "->", w["A9"].hyperlink.target)
print("ALL CORE TESTS OK")
